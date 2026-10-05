import csv
import io
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, transaction
from django.http import Http404
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.campaigns.services import change_campaign_status, save_campaign
from apps.companies.models import Company, CompanyExternalRef, Contact
from apps.crm.models import Lead, LeadStatusHistory
from apps.jobs.execution import claim_next
from apps.jobs.locking import WorkerLock
from apps.jobs.models import Job
from apps.jobs.recovery import requeue_stale
from apps.jobs.runner import JobRunner
from apps.jobs.services import JobService
from apps.prospecting.ingestion import ingest_row
from apps.prospecting.models import (
    DuplicateCandidate,
    SearchPlan,
    SearchPlanItem,
    SearchResult,
    SearchRun,
)
from apps.prospecting.planner import CSV_FIELDS, parse_csv, text_items
from apps.prospecting.services import create_plan, enqueue_item
from integrations.common.errors import ProviderTemporaryError
from integrations.google_places.mock import MockPlacesProvider
from tests.factories import MembershipFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def worker_lock(tmp_path, monkeypatch):
    monkeypatch.setattr("apps.jobs.locking.worker_lock_path", lambda: tmp_path / "worker.lock")


@pytest.fixture
def member():
    return MembershipFactory(role="OWNER")


def campaign_for(member):
    campaign = save_campaign(
        user=member.user,
        workspace_id=member.workspace_id,
        data={
            "name": "Dentistas Galicia",
            "target_query": "dentistas",
            "target_location_text": "A Coruña",
            "desired_leads": 20,
        },
    )
    return change_campaign_status(
        user=member.user, workspace_id=member.workspace_id, pk=campaign.pk, target="READY"
    )


def plan_for(member, campaign=None, **kwargs):
    return create_plan(
        user=member.user,
        workspace_id=member.workspace_id,
        campaign_id=(campaign or campaign_for(member)).pk,
        **({"request_key": uuid.uuid4()} | kwargs),
    )


def csv_bytes(rows):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def rerun(member, item, **kwargs):
    return enqueue_item(
        user=member.user,
        workspace_id=member.workspace_id,
        pk=item.pk,
        **({"request_key": uuid.uuid4()} | kwargs),
    )


def url(member, name, pk=None):
    kwargs = {"workspace_id": member.workspace_id}
    if pk:
        kwargs["pk"] = pk
    return reverse(f"prospecting:{name}", kwargs=kwargs)


def test_campaign_plan_worker_creates_companies_and_leads(member):
    plan = plan_for(member, locations=["A Coruña", "Oleiros"])
    assert plan.items.count() == 2 and Job.objects.count() == 2
    assert not Company.objects.exists()
    assert sum(plan.items.values_list("max_results", flat=True)) == 20
    first = JobRunner().run_once()
    assert first.status == "SUCCEEDED", first.last_error
    plan.refresh_from_db()
    assert plan.status == "PENDING"
    second = JobRunner().run_once()
    assert second.status == "SUCCEEDED", second.last_error
    plan.refresh_from_db()
    assert plan.status == "SUCCEEDED" and plan.completed_at
    assert Company.objects.count() == Lead.objects.count() == LeadStatusHistory.objects.count() == 3
    assert CompanyExternalRef.objects.filter(provider="MOCK_PLACES").count() == 3
    assert SearchResult.objects.count() == 6
    assert sum(SearchRun.objects.values_list("duplicates", flat=True)) == 3
    assert AuditEvent.objects.filter(action="SEARCH_EXECUTED").count() == 2


def test_duplicate_place_id_does_not_create_duplicate_company(member):
    campaign = campaign_for(member)
    plan = plan_for(member, campaign)
    assert JobRunner().run_once().status == "SUCCEEDED"
    item = plan.items.get()
    key = uuid.uuid4()
    job = rerun(member, item, request_key=key)
    assert rerun(member, item, request_key=key).pk == job.pk
    assert JobRunner().run_once().status == "SUCCEEDED"
    assert rerun(member, item, request_key=key).pk == job.pk
    assert Company.objects.count() == 3 and Lead.objects.count() == 3
    assert SearchRun.objects.count() == 2
    other_campaign = campaign_for(member)
    plan_for(member, other_campaign)
    assert JobRunner().run_once().status == "SUCCEEDED"
    assert Company.objects.count() == 3 and Lead.objects.count() == 6


def test_plan_idempotency_and_rollback(member):
    campaign = campaign_for(member)
    key = uuid.uuid4()
    plan = plan_for(member, campaign, request_key=key)
    assert plan_for(member, campaign, request_key=key).pk == plan.pk
    with pytest.raises(ValidationError):
        plan_for(member, campaign, request_key=key, locations=["Madrid"])
    with (
        patch("apps.prospecting.services.record_event", side_effect=RuntimeError),
        pytest.raises(RuntimeError),
    ):
        plan_for(member, campaign)
    assert SearchPlan.objects.count() == SearchPlanItem.objects.count() == Job.objects.count() == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"query": ""},
        {"query": "x" * 251},
        {"locations": []},
        {"locations": [""]},
        {"locations": ["A"] * 21},
        {"locations": [None]},
        {"target": 0},
        {"target": 501},
    ],
)
def test_planning_rejects_invalid_or_unbounded_input(kwargs):
    with pytest.raises(ValidationError):
        text_items(**({"query": "dentistas", "locations": ["A Coruña"], "target": 20} | kwargs))


def test_planning_is_deterministic_and_bounded():
    assert text_items(" dentistas ", [" A Coruña ", "A Coruña", "Oleiros"], 3) == [
        {"query_text": "dentistas", "location_text": "A Coruña", "max_results": 2},
        {"query_text": "dentistas", "location_text": "Oleiros", "max_results": 1},
    ]
    assert len(text_items("dentistas", ["A", "B"], 1)) == 1
    assert text_items("dentistas", ["A"], 500)[0]["max_results"] == 100


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"name\nfoo",
        b"\xff",
        b"\x00",
        b"x" * 200001,
        csv_bytes([]),
        csv_bytes([{"name": "A"}] * 201),
    ],
    ids=["empty", "headers", "encoding", "nul", "size", "no_rows", "row_limit"],
)
def test_csv_rejects_invalid_files(content):
    with pytest.raises(ValidationError):
        parse_csv(content)


def test_csv_partial_errors_and_repeat_are_reported_per_line(member, client):
    content = csv_bytes(
        [
            {
                "name": "Clínica CSV",
                "website": "https://csv.example.invalid",
                "email": "INFO@example.invalid",
            },
            {"name": ""},
            {"name": "Teléfono inválido", "phone": "123"},
            {"name": "URL inválida", "website": "file:///private"},
            {"name": "Email inválido", "email": "bad"},
            {"name": "Segunda clínica", "city": "Madrid"},
        ]
    )
    campaign = campaign_for(member)
    plan = plan_for(member, campaign, csv_content=content)
    with patch("apps.prospecting.execution.places_provider", side_effect=AssertionError):
        assert JobRunner().run_once().status == "SUCCEEDED"
    run = SearchRun.objects.get()
    assert (run.results_seen, run.errors, run.companies_created, run.leads_created) == (6, 4, 2, 2)
    assert list(run.rows.exclude(error="").values_list("line", flat=True)) == [3, 4, 5, 6]
    assert Contact.objects.get().normalized_email == "info@example.invalid"
    assert Contact.objects.get().whatsapp_e164 == ""
    assert CompanyExternalRef.objects.filter(provider="MANUAL_IMPORT").count() == 2
    assert parse_csv(b"\xef\xbb\xbf" + content)[0]["line"] == 2
    plan_for(member, campaign, csv_content=content)
    assert JobRunner().run_once().status == "SUCCEEDED"
    assert Company.objects.count() == Lead.objects.count() == 2
    assert Contact.objects.count() == 1
    client.force_login(member.user)
    for path in (
        url(member, "list"),
        url(member, "detail", plan.pk),
        url(member, "run", run.pk),
        url(member, "duplicates"),
    ):
        assert client.get(path).status_code == 200
    assert "Fila no válida" in client.get(url(member, "run", run.pk)).content.decode()


def test_csv_malformed_column_count_does_not_drop_other_rows(member):
    content = (",".join(CSV_FIELDS) + "\nA,,,,,,,ES,EXTRA\nB,,,,,,,ES\nC\n").encode()
    plan_for(member, csv_content=content)
    assert JobRunner().run_once().status == "SUCCEEDED"
    run = SearchRun.objects.get()
    assert run.results_seen == 3 and run.errors == 2 and Company.objects.get().name == "B"


def test_conservative_deduplication_and_preservation_of_manual_values(member):
    original = Company.objects.create(
        workspace=member.workspace,
        name="Clínica Norte",
        website="https://norte.example.invalid",
        do_not_contact=True,
        description="Manual",
        phone="",
        city="",
    )
    plan_for(member)
    assert JobRunner().run_once().status == "SUCCEEDED"
    original.refresh_from_db()
    assert (
        original.city == "A Coruña" and original.description == "Manual" and original.do_not_contact
    )
    assert Company.objects.count() == 3
    assert SearchRun.objects.get().companies_updated == 1
    ambiguous = Company.objects.create(
        workspace=member.workspace, name="Otro nombre", website="https://shared.example.invalid"
    )
    plan_for(
        member, csv_content=csv_bytes([{"name": "Nombre diferente", "website": ambiguous.website}])
    )
    assert JobRunner().run_once().status == "SUCCEEDED"
    assert Company.objects.filter(normalized_domain="shared.example.invalid").count() == 2
    assert DuplicateCandidate.objects.count() == 1


def test_name_only_and_name_address_never_merge_automatically(member):
    Company.objects.create(
        workspace=member.workspace, name="Same name", address_line="Calle 1", city="Madrid"
    )
    plan_for(
        member,
        csv_content=csv_bytes(
            [
                {"name": "Same name", "address": "Calle 1", "city": "Madrid"},
                {"name": "Same name"},
                {"name": "Same name"},
            ]
        ),
    )
    assert JobRunner().run_once().status == "SUCCEEDED"
    assert Company.objects.count() == 4 and DuplicateCandidate.objects.count() == 1


def test_archived_company_is_not_reactivated(member):
    plan = plan_for(member)
    assert JobRunner().run_once().status == "SUCCEEDED"
    company = Company.objects.first()
    company.is_active = False
    company.save()
    rerun(member, plan.items.get())
    assert JobRunner().run_once().status == "SUCCEEDED"
    assert SearchRun.objects.first().errors == 1
    company.refresh_from_db()
    assert not company.is_active and Company.objects.count() == 3


def test_partial_failure_retry_uses_snapshot_and_durable_rows(member):
    plan_for(member)

    def interrupt_second(*, run, user, row):
        if row["line"] == 2:
            raise ProviderTemporaryError
        return ingest_row(run=run, user=user, row=row)

    with patch("apps.prospecting.execution.ingest_row", side_effect=interrupt_second):
        job = JobRunner().run_once()
    assert job.status == "PENDING" and Company.objects.count() == 1
    assert SearchRun.objects.get().status == "PENDING"
    Job.objects.filter(pk=job.pk).update(available_at=timezone.now() - timedelta(seconds=1))
    with patch(
        "apps.prospecting.execution.places_provider",
        side_effect=AssertionError("Snapshot must be reused"),
    ):
        assert JobRunner().run_once().status == "SUCCEEDED"
    assert SearchRun.objects.count() == 1
    run = SearchRun.objects.get()
    assert run.results_seen == 3 and run.companies_created == 3
    assert Company.objects.count() == Lead.objects.count() == SearchResult.objects.count() == 3


def test_row_writes_roll_back_on_unexpected_failure(member):
    plan_for(member)
    with patch("apps.prospecting.ingestion.save_lead", side_effect=RuntimeError("secret")):
        job = JobRunner().run_once()
    assert job.status == "FAILED"
    assert not Company.objects.exists() and not CompanyExternalRef.objects.exists()
    assert not SearchResult.objects.exists()
    assert SearchRun.objects.get().status == "FAILED"
    assert "secret" not in SearchRun.objects.get().error_message


@pytest.mark.django_db(transaction=True)
def test_places_provider_runs_outside_database_transaction(member):
    plan_for(member)

    class CheckingProvider(MockPlacesProvider):
        def text_search(self, request):
            assert not connection.in_atomic_block
            return super().text_search(request)

    with patch("apps.prospecting.execution.places_provider", return_value=CheckingProvider()):
        assert JobRunner().run_once().status == "SUCCEEDED"


def test_workspace_scope_in_services_views_and_job_payloads(member, client):
    other = MembershipFactory(role="OWNER")
    foreign = plan_for(other)
    MembershipFactory(user=member.user, workspace=other.workspace)
    with pytest.raises(Http404):
        plan_for(member, foreign.campaign)
    with pytest.raises(Http404):
        rerun(member, foreign.items.get())
    with pytest.raises(Http404):
        JobService.enqueue(
            user=member.user,
            workspace_id=member.workspace_id,
            kind="RUN_SEARCH_ITEM",
            payload={"item_id": str(foreign.items.get().pk)},
        )
    assert JobRunner().run_once().status == "SUCCEEDED"
    client.force_login(member.user)
    assert client.get(url(member, "detail", foreign.pk)).status_code == 404
    assert client.get(url(member, "run", SearchRun.objects.get().pk)).status_code == 404
    assert (
        client.post(
            url(member, "rerun", foreign.items.get().pk), {"request_key": uuid.uuid4()}
        ).status_code
        == 404
    )
    assert client.get(url(member, "list")).context["page_obj"].paginator.count == 0
    own = plan_for(member)
    assert JobRunner().run_once().status == "SUCCEEDED"
    assert Company.objects.count() == 6
    assert set(own.items.get().runs.get().rows.values_list("company__workspace_id", flat=True)) == {
        member.workspace_id
    }


def test_roles_revocation_cancellation_and_archived_campaign(member, client):
    plan = plan_for(member)
    item = plan.items.get()
    JobService.cancel(user=member.user, workspace_id=member.workspace_id, pk=item.job_id)
    item.refresh_from_db()
    plan.refresh_from_db()
    assert item.status == "CANCELLED" and plan.status == "FAILED"
    job = rerun(member, item)
    member.role = "VIEWER"
    member.save()
    client.force_login(member.user)
    assert client.get(url(member, "list")).status_code == 200
    assert client.get(url(member, "create")).status_code == 403
    with pytest.raises(PermissionDenied):
        rerun(member, item)
    assert JobRunner().run_once().status == "FAILED"
    assert not Company.objects.exists()
    item.refresh_from_db()
    assert item.status == "FAILED"
    member.role = "OWNER"
    member.save()
    change_campaign_status(
        user=member.user, workspace_id=member.workspace_id, pk=plan.campaign_id, target="ARCHIVED"
    )
    with pytest.raises(ValidationError):
        rerun(member, item)
    assert Job.objects.get(pk=job.pk).status == "FAILED"


def test_ui_csv_upload_csrf_and_idempotent_post(member):
    client = Client(enforce_csrf_checks=True)
    client.force_login(member.user)
    campaign = campaign_for(member)
    assert client.get(url(member, "create")).status_code == 200
    data = {"campaign": campaign.pk, "request_key": uuid.uuid4()}
    assert client.post(url(member, "create"), data).status_code == 403
    data["csrfmiddlewaretoken"] = client.cookies["csrftoken"].value
    content = csv_bytes([{"name": "Imported"}])
    for _ in range(2):
        upload = SimpleUploadedFile("companies.csv", content, content_type="text/csv")
        response = client.post(url(member, "create"), data | {"csv_file": upload})
        assert response.status_code == 302
    assert SearchPlan.objects.count() == Job.objects.count() == 1
    assert Company.objects.count() == 0
    assert JobRunner().run_once().status == "SUCCEEDED"
    assert client.get(response.url).status_code == 200
    item = SearchPlanItem.objects.get()
    assert client.get(url(member, "rerun", item.pk)).status_code == 405
    assert (
        client.post(url(member, "rerun", item.pk), {"request_key": uuid.uuid4()}).status_code == 403
    )
    upload = SimpleUploadedFile("evil.html", b"<html>", content_type="text/html")
    assert client.post(url(member, "create"), data | {"csv_file": upload}).status_code == 400


def test_model_constraints_and_cross_workspace_relations(member):
    plan = plan_for(member)
    other = MembershipFactory()
    with pytest.raises(ValidationError):
        SearchPlanItem.objects.create(
            workspace=other.workspace, plan=plan, sequence=2, max_results=1
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        SearchPlan.objects.filter(pk=plan.pk).update(target_count=0)
    with pytest.raises(ValidationError):
        SearchPlanItem.objects.create(
            workspace=member.workspace, plan=plan, sequence=1, max_results=1
        )


def test_recovery_updates_search_status_and_resumes(member):
    plan = plan_for(member)
    with WorkerLock() as lock:
        job = claim_next(lock=lock)
    plan.refresh_from_db()
    assert plan.status == "RUNNING"
    Job.objects.filter(pk=job.pk).update(started_at=timezone.now() - timedelta(hours=1))
    assert requeue_stale(workspace_id=member.workspace_id)["requeued"] == 1
    plan.refresh_from_db()
    assert plan.status == "PENDING" and plan.items.get().status == "PENDING"
    assert JobRunner().run_once().status == "SUCCEEDED"
    assert Company.objects.count() == 3


def test_old_rerun_request_does_not_replace_latest_execution(member):
    plan = plan_for(member)
    assert JobRunner().run_once().status == "SUCCEEDED"
    item = plan.items.get()
    key = uuid.uuid4()
    old = rerun(member, item, request_key=key)
    assert JobRunner().run_once().status == "SUCCEEDED"
    latest = rerun(member, item)
    assert JobRunner().run_once().status == "SUCCEEDED"
    assert rerun(member, item, request_key=key).pk == old.pk
    item.refresh_from_db()
    assert item.job_id == latest.pk


def test_invalid_idempotency_keys_and_direct_payload_ids(member):
    campaign = campaign_for(member)
    with pytest.raises(ValidationError):
        plan_for(member, campaign, request_key="bad")
    plan = plan_for(member, campaign)
    with pytest.raises(ValidationError):
        rerun(member, plan.items.get(), request_key="bad")
    with pytest.raises(ValidationError):
        JobService.enqueue(
            user=member.user,
            workspace_id=member.workspace_id,
            kind="RUN_SEARCH_ITEM",
            payload={"item_id": "bad"},
        )
