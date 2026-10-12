import uuid
from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.companies.enrichment import enqueue_enrichment
from apps.companies.models import Company, Contact, SocialProfile, WebsiteObservation
from apps.jobs.models import Job
from apps.jobs.runner import JobRunner
from integrations.websites.errors import CrawlError
from tests.factories import MembershipFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup_company(tmp_path, monkeypatch):
    monkeypatch.setattr("apps.jobs.locking.worker_lock_path", lambda: tmp_path / "worker.lock")
    member = MembershipFactory(role="OWNER")
    company = Company.objects.create(
        workspace=member.workspace,
        name="Fictional clinic",
        website="https://clinic.example.invalid/",
    )
    return member, company


def enqueue(member, company, key=None):
    return enqueue_enrichment(
        user=member.user,
        workspace_id=member.workspace_id,
        company_id=company.pk,
        idempotency_key=key or str(uuid.uuid4()),
    )


def test_mock_enrichment_is_idempotent_and_preserves_manual_data(setup_company):
    member, company = setup_company
    job = enqueue(member, company, "same")
    assert enqueue(member, company, "same").pk == job.pk
    assert JobRunner().run_once().status == "SUCCEEDED"
    assert WebsiteObservation.objects.count() == 1
    assert Contact.objects.count() == 2 and SocialProfile.objects.count() == 1
    contact = Contact.objects.get(email="info@clinic.example.invalid")
    contact.do_not_contact, contact.full_name = True, "Nombre manual"
    contact.save()
    enqueue(member, company)
    assert JobRunner().run_once().status == "SUCCEEDED"
    assert Contact.objects.count() == 2 and SocialProfile.objects.count() == 1
    contact.refresh_from_db()
    assert contact.do_not_contact and contact.full_name == "Nombre manual"
    assert not Contact.objects.exclude(whatsapp_e164="").exists()
    assert "<html" not in str(WebsiteObservation.objects.first().findings)


def test_enrichment_rechecks_workspace_and_company_before_execution(setup_company):
    member, company = setup_company
    outsider = MembershipFactory(role="OWNER")
    with pytest.raises(Http404):
        enqueue(outsider, company)
    enqueue(member, company)
    company.website = "https://changed.example.invalid/"
    company.save()
    assert JobRunner().run_once().status == "FAILED"
    assert not WebsiteObservation.objects.exists()
    enqueue(member, company)
    member.is_active = False
    member.save()
    assert JobRunner().run_once().status == "FAILED"
    assert not Contact.objects.exists()


def test_failed_site_does_not_stop_other_jobs(setup_company, monkeypatch):
    member, company = setup_company
    enqueue(member, company)
    other = Company.objects.create(
        workspace=member.workspace, name="Other", website="https://other.example.invalid/"
    )
    enqueue(member, other)
    from integrations.websites.mock import mock_crawl

    def crawl(url, region):
        if "clinic." in url:
            raise CrawlError("ROBOTS_DENIED")
        return mock_crawl(url, region)

    monkeypatch.setattr("integrations.websites.mock.mock_crawl", crawl)
    assert JobRunner().run_once().status == "FAILED"
    assert JobRunner().run_once().status == "SUCCEEDED"
    assert WebsiteObservation.objects.get(company=company).error_code == "ROBOTS_DENIED"
    assert not Contact.objects.filter(company=company).exists()
    assert Contact.objects.filter(company=other).exists()


def test_temporary_error_retries_with_observation_history(setup_company, monkeypatch):
    member, company = setup_company
    enqueue(member, company)
    from integrations.websites.mock import mock_crawl

    monkeypatch.setattr(
        "integrations.websites.mock.mock_crawl",
        lambda *args: (_ for _ in ()).throw(
            CrawlError("RATE_LIMIT", retryable=True, retry_after=120)
        ),
    )
    failed = JobRunner().run_once()
    assert failed.status == "PENDING"
    assert failed.available_at > timezone.now() + timedelta(seconds=110)
    monkeypatch.setattr("integrations.websites.mock.mock_crawl", mock_crawl)
    Job.objects.filter(pk=failed.pk).update(available_at=timezone.now())
    assert JobRunner().run_once().status == "SUCCEEDED"
    assert WebsiteObservation.objects.count() == 2
    assert Contact.objects.count() == 2


def test_live_enrichment_requires_explicit_configuration(setup_company, settings):
    member, company = setup_company
    settings.WEBSITE_PROVIDER = "web"
    with pytest.raises(ValidationError):
        enqueue(member, company)
    settings.WEB_CRAWLER_ENABLED = True
    job = enqueue(member, company)
    assert job.payload["provider"] == "web"
    settings.WEB_CRAWLER_ENABLED = False
    assert JobRunner().run_once().status == "FAILED"


def test_ui_is_scoped_and_csrf_protected(setup_company):
    member, company = setup_company
    client = Client(enforce_csrf_checks=True)
    url = reverse("enrichment:company", args=[member.workspace_id, company.pk])
    assert client.get(url).status_code == 302
    client.force_login(member.user)
    assert client.get(url).status_code == 200
    assert client.post(url, {"idempotency_key": str(uuid.uuid4())}).status_code == 403
    token = client.cookies["csrftoken"].value
    response = client.post(
        url, {"idempotency_key": str(uuid.uuid4()), "csrfmiddlewaretoken": token}
    )
    assert response.status_code == 302 and Job.objects.count() == 1
    JobRunner().run_once()
    assert b"info@clinic.example.invalid" in client.get(url).content
    outsider = MembershipFactory()
    client.force_login(outsider.user)
    assert client.get(url).status_code in {403, 404}
    client.force_login(member.user)
    member.role = "VIEWER"
    member.save()
    assert client.get(url).status_code == 200
    assert (
        client.post(
            url, {"idempotency_key": str(uuid.uuid4()), "csrfmiddlewaretoken": token}
        ).status_code
        == 403
    )
    with pytest.raises(PermissionDenied):
        enqueue(member, company)


def test_recovered_success_does_not_acquire_or_import_twice(setup_company, monkeypatch):
    member, company = setup_company
    job = enqueue(member, company)
    assert JobRunner().run_once().status == "SUCCEEDED"
    # Simulate recovery after domain commit but before the runner's success commit.
    Job.objects.filter(pk=job.pk).update(status="PENDING", finished_at=None)
    monkeypatch.setattr(
        "apps.companies.enrichment.website_provider",
        lambda *args: (_ for _ in ()).throw(AssertionError("Must reuse committed observation")),
    )
    assert JobRunner().run_once().status == "SUCCEEDED"
    assert WebsiteObservation.objects.count() == 1 and Contact.objects.count() == 2


def test_contact_and_observation_transaction_rolls_back_together(setup_company, monkeypatch):
    member, company = setup_company
    enqueue(member, company)

    def broken_import(*, company, observation):
        Contact.objects.create(workspace=company.workspace, company=company, full_name="Partial")
        raise RuntimeError("Internal error with private details")

    monkeypatch.setattr("apps.companies.enrichment.import_contacts", broken_import)
    job = JobRunner().run_once()
    assert job.status == "FAILED"
    assert not WebsiteObservation.objects.exists() and not Contact.objects.exists()
    assert "private details" not in job.last_error


def test_permissions_rechecked_after_acquisition(setup_company, monkeypatch):
    member, company = setup_company
    enqueue(member, company)
    from integrations.websites.mock import mock_crawl

    def revoke(url, region):
        result = mock_crawl(url, region)
        member.is_active = False
        member.save()
        return result

    monkeypatch.setattr("integrations.websites.mock.mock_crawl", revoke)
    assert JobRunner().run_once().status == "FAILED"
    assert not WebsiteObservation.objects.exists() and not Contact.objects.exists()


def test_observation_rejects_foreign_job_and_company(setup_company):
    member, company = setup_company
    enqueue(member, company)
    JobRunner().run_once()
    observation = WebsiteObservation.objects.get()
    assert str(observation)
    foreign = MembershipFactory(role="OWNER")
    other = Company.objects.create(
        workspace=foreign.workspace, name="Other", website="https://other.example.invalid/"
    )
    foreign_job = enqueue(foreign, other)
    observation.job = foreign_job
    with pytest.raises(ValidationError):
        observation.save()
    observation.refresh_from_db()
    observation.company = other
    with pytest.raises(ValidationError):
        observation.save()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"company_id": "bad", "root_url": "bad", "provider": "mock"},
        {"company_id": "bad", "root_url": "bad", "provider": "unknown"},
    ],
)
def test_invalid_enrichment_payloads(payload):
    from apps.companies.enrichment import validate_enrichment_payload

    with pytest.raises(ValidationError):
        validate_enrichment_payload(payload)


def test_invalid_company_url_and_form_return_controlled_errors(setup_company, client):
    member, company = setup_company
    company.website = "http://127.0.0.1/"
    company.save()
    client.force_login(member.user)
    url = reverse("enrichment:company", args=[member.workspace_id, company.pk])
    assert client.post(url, {"idempotency_key": "not-a-uuid"}).status_code == 400
    assert client.post(url, {"idempotency_key": str(uuid.uuid4())}).status_code == 400
    assert not Job.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_live_adapter_uses_mock_http_outside_database_transaction(
    setup_company, settings, monkeypatch
):
    import httpx
    from django.db import connection

    from integrations.websites.http import PublicTransport
    from integrations.websites.mock import FIXTURES

    member, company = setup_company
    settings.WEBSITE_PROVIDER, settings.WEB_CRAWLER_ENABLED = "web", True
    settings.WEB_CRAWLER_DELAY_SECONDS = 0.1
    calls = []

    def handle(request):
        assert not connection.in_atomic_block
        calls.append(request.url.path)
        if request.url.path == "/robots.txt":
            text = "User-agent: *\nDisallow: /private"
        else:
            filename = "home.html" if request.url.path == "/" else "contact.html"
            text = (FIXTURES / filename).read_text(encoding="utf-8")
        return httpx.Response(
            200, headers={"content-type": "text/html"}, stream=httpx.ByteStream(text.encode())
        )

    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(
        PublicTransport, "handle_request", lambda self, req: transport.handle_request(req)
    )
    enqueue(member, company)
    assert JobRunner().run_once().status == "SUCCEEDED"
    assert calls == ["/robots.txt", "/", "/contacto", "/reservas"]
    assert WebsiteObservation.objects.get().provider == "web"
    assert Contact.objects.count() == 2
