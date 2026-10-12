import csv
import io
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Membership
from apps.audit.models import AuditEvent
from apps.audit.selectors import events_for_user
from apps.audit.services import record_event
from apps.companies.models import Contact
from apps.companies.services import save_company
from apps.jobs.models import Job
from apps.jobs.runner import JobRunner
from apps.jobs.services import JobService
from apps.outreach.models import InboundWhatsAppMessage, WhatsAppConnection
from apps.prospecting.models import PlacesCache
from common.access import require_capability
from common.exports import csv_cell, export_csv
from common.private_files import private_file_path
from tests.test_phase1 import domain as domain

pytestmark = pytest.mark.django_db


def route(env, name):
    return reverse(f"audit:{name}", kwargs={"workspace_id": env["member"].workspace_id})


def enqueue(env, key=None):
    member = env["member"]
    return JobService.enqueue(
        user=member.user,
        workspace_id=member.workspace_id,
        kind=Job.Kind.RETENTION,
        payload={"inbound_days": 30},
        max_attempts=1,
        idempotency_key=key or f"retention:{uuid.uuid4()}",
    )


@pytest.fixture
def retention_data(domain):
    for index, member in enumerate([domain["member"], domain["foreign"]]):
        connection = WhatsAppConnection.objects.create(
            workspace=member.workspace,
            phone_number_id=str(index + 1111),
            waba_id="111222",
            api_version="v99.0",
            configuration_fingerprint="f" * 64,
        )
        InboundWhatsAppMessage.objects.create(
            workspace=member.workspace,
            connection=connection,
            provider_message_id=f"wamid.FICTITIOUS_{index}",
            sender_e164="+34600123456",
            received_at=timezone.now() - timedelta(days=100),
            message_type="text",
            body_text="Texto antiguo",
        )
        PlacesCache.objects.create(
            workspace=member.workspace,
            fingerprint="f" * 64,
            rows=[],
            expires_at=timezone.now() - timedelta(days=1),
        )
    return domain


@pytest.mark.parametrize("role", ["OWNER", "ADMIN", "SALES", "MEMBER", "VIEWER"])
def test_sensitive_capabilities_require_current_owner_or_admin(domain, role):
    member = domain["member"]
    Membership.objects.filter(pk=member.pk).update(role=role)
    for capability in ("audit", "export", "retention", "approve", "configure"):
        if role in {"OWNER", "ADMIN"}:
            assert (
                require_capability(
                    user=member.user, workspace_id=member.workspace_id, capability=capability
                ).pk
                == member.workspace_id
            )
        else:
            with pytest.raises(PermissionDenied):
                require_capability(
                    user=member.user, workspace_id=member.workspace_id, capability=capability
                )


def test_csp_cache_headers_and_external_markup_escaping(domain):
    client = Client()
    client.force_login(domain["member"].user)
    response = client.get(
        reverse("workspace:list", args=[domain["member"].workspace_id, "companies"])
    )
    assert response.status_code == 200
    policy = response["Content-Security-Policy"]
    assert "object-src 'none'" in policy and "frame-ancestors 'none'" in policy
    assert "unsafe-inline" not in policy and "unsafe-eval" not in policy
    assert response["Cache-Control"] == "private, no-store"
    assert "Cookie" in response["Vary"]
    record_event(
        actor=domain["member"].user,
        obj=domain["company"],
        action="TEST_ESCAPING",
        metadata={"external": "<script>alert(1)</script>"},
    )
    body = client.get(route(domain, "events")).content.decode()
    assert "<script>alert(1)</script>" not in body and "&lt;script&gt;" in body


def test_audit_metadata_redacts_nested_secrets_and_stays_immutable(domain):
    event = record_event(
        actor=domain["member"].user,
        obj=domain["company"],
        action="TEST_REDACTION",
        metadata={
            "nested": [{"access_token": "SENSITIVE", "safe": 3}],
            "api_key": "ANOTHER_SECRET",
            "camelCase": {"accessToken": "CAMEL_SECRET", "apiKey": "CAMEL_KEY"},
        },
    )
    assert event.metadata["nested"][0]["access_token"] == "[REDACTED]"
    assert "SENSITIVE" not in str(event.metadata)
    assert "CAMEL_SECRET" not in str(event.metadata) and "CAMEL_KEY" not in str(event.metadata)
    with pytest.raises(ValidationError):
        AuditEvent.objects.filter(pk=event.pk).update(action="MUTATED")
    assert (
        not events_for_user(user=domain["member"].user, workspace_id=domain["member"].workspace_id)
        .filter(workspace=domain["foreign"].workspace)
        .exists()
    )


@pytest.mark.parametrize(
    "value", ["=1+1", "  =HYPERLINK(1)", "+34981000000", "-1", "@SUM(1)", "\tvalue", "\rvalue"]
)
def test_csv_formulas_are_exported_as_text(value):
    assert csv_cell(value) == "'" + value


def test_export_is_scoped_filtered_audited_and_bounded(domain, monkeypatch):
    member = domain["member"]
    save_company(
        user=member.user, workspace_id=member.workspace_id, data={"name": "=1+1", "city": "Vigo"}
    )
    data = export_csv(
        user=member.user,
        workspace_id=member.workspace_id,
        resource="companies",
        filters={"city": "Vigo"},
    )
    rows = list(csv.reader(io.StringIO(data.lstrip("\ufeff"))))
    assert len(rows) == 2 and rows[1][1] == "'=1+1"
    assert "Secreto ajeno" not in data and str(domain["other_company"].pk) not in data
    assert (
        AuditEvent.objects.filter(action="CSV_EXPORTED", workspace_id=member.workspace_id).count()
        == 1
    )
    monkeypatch.setattr("common.exports.MAX_EXPORT_ROWS", 1)
    with pytest.raises(ValidationError):
        export_csv(
            user=member.user, workspace_id=member.workspace_id, resource="companies", filters={}
        )
    with pytest.raises(ValidationError):
        export_csv(user=member.user, workspace_id=member.workspace_id, resource="users", filters={})


def test_operational_views_enforce_csrf_permissions_and_foreign_filters(domain):
    client = Client(enforce_csrf_checks=True)
    member = domain["member"]
    client.force_login(member.user)
    assert client.get(route(domain, "events")).status_code == 200
    response = client.get(route(domain, "export"))
    assert response.status_code == 200
    assert client.post(route(domain, "export"), {"resource": "companies"}).status_code == 403
    token = client.cookies["csrftoken"].value
    data = {
        "resource": "leads",
        "campaign": str(domain["other_campaign"].pk),
        "csrfmiddlewaretoken": token,
    }
    assert client.post(route(domain, "export"), data).status_code == 400
    data.pop("campaign")
    response = client.post(route(domain, "export"), data)
    assert response.status_code == 200 and response["Content-Type"].startswith("text/csv")
    assert response["Cache-Control"] == "private, no-store"
    assert client.post(route(domain, "retention"), {}).status_code == 403
    Membership.objects.filter(pk=member.pk).update(role="VIEWER")
    for name in ("events", "export", "retention"):
        assert client.get(route(domain, name)).status_code == 403
    client.force_login(domain["foreign"].user)
    assert client.get(route(domain, "events")).status_code == 404


def test_retention_job_preserves_identifiers_and_other_workspaces(retention_data):
    env = retention_data
    source = InboundWhatsAppMessage.objects.get(workspace_id=env["member"].workspace_id)
    original_id = source.provider_message_id
    job = enqueue(env, key="retention-once")
    assert enqueue(env, key="retention-once").pk == job.pk
    assert JobRunner().run_once().status == "SUCCEEDED"
    source.refresh_from_db()
    assert source.body_text == "" and source.text_redacted_at
    assert source.provider_message_id == original_id and source.sender_e164 == "+34600123456"
    assert (
        InboundWhatsAppMessage.objects.get(workspace=env["foreign"].workspace).body_text
        == "Texto antiguo"
    )
    assert PlacesCache.objects.filter(workspace=env["foreign"].workspace).count() == 1
    assert not PlacesCache.objects.filter(workspace=env["member"].workspace).exists()
    assert AuditEvent.objects.filter(action="RETENTION_APPLIED").count() == 1
    # Generic mutations still cannot bypass the content-only retention exception.
    with pytest.raises(ValidationError):
        InboundWhatsAppMessage.objects.filter(pk=source.pk).update(sender_e164="+34600111111")


def test_retention_revocation_blocks_execution(retention_data):
    env = retention_data
    enqueue(env)
    Membership.objects.filter(pk=env["member"].pk).update(role="SALES")
    assert JobRunner().run_once().status == "FAILED"
    assert PlacesCache.objects.count() == 2
    assert not InboundWhatsAppMessage.objects.filter(text_redacted_at__isnull=False).exists()


def test_retention_rollback_on_audit_failure(retention_data):
    env = retention_data
    enqueue(env)
    with patch(
        "apps.compliance.retention.record_event", side_effect=RuntimeError("Fixture failure")
    ):
        assert JobRunner().run_once().status == "FAILED"
    assert PlacesCache.objects.count() == 2
    assert not InboundWhatsAppMessage.objects.filter(body_text="").exists()


def test_retention_requires_valid_payload_confirmation_and_role(domain):
    member = domain["member"]
    with pytest.raises(ValidationError):
        JobService.enqueue(
            user=member.user,
            workspace_id=member.workspace_id,
            kind=Job.Kind.RETENTION,
            payload={"inbound_days": 0},
        )
    client = Client()
    client.force_login(member.user)
    assert client.get(route(domain, "retention")).status_code == 200
    data = {"inbound_days": 30, "request_key": str(uuid.uuid4())}
    assert client.post(route(domain, "retention"), data).status_code == 400
    data["confirmed"] = "on"
    assert client.post(route(domain, "retention"), data).status_code == 302
    assert Job.objects.filter(kind=Job.Kind.RETENTION).count() == 1


def test_related_pagination_preserves_filters_and_exposes_all_records(domain):
    env = domain
    for index in range(30):
        Contact.objects.create(
            workspace=env["member"].workspace, company=env["company"], full_name=f"Contacto {index}"
        )
    client = Client()
    client.force_login(env["member"].user)
    response = client.get(
        reverse(
            "workspace:detail", args=[env["member"].workspace_id, "companies", env["company"].pk]
        ),
        {"related_1": 2, "q": "needle"},
    )
    section = response.context["related_sections"][0]
    assert len(section["page"]) == 5 and section["page"].paginator.count == 30
    assert "q=needle" in section["previous_url"]


@pytest.mark.parametrize("kind", ["symlink", "junction", "traversal", "wrong_type"])
def test_private_file_boundary_rejects_aliases_and_invalid_paths(tmp_path, kind):
    name = f"sales_assets/{uuid.uuid4()}/{uuid.uuid4()}/{uuid.uuid4()}.pdf"
    if kind in {"symlink", "junction"}:
        attribute = "is_symlink" if kind == "symlink" else "is_junction"
        with patch(f"pathlib.Path.{attribute}", return_value=True), pytest.raises(ValidationError):
            private_file_path(tmp_path, name)
    else:
        name = "../outside.pdf" if kind == "traversal" else name.replace(".pdf", ".html")
        with pytest.raises(ValidationError):
            private_file_path(tmp_path, name)


def test_successful_login_audits_without_credentials(domain):
    client = Client(enforce_csrf_checks=True)
    client.get(reverse("login"))
    response = client.post(
        reverse("login"),
        {
            "username": domain["member"].user.username,
            "password": "Test-password-42!",
            "csrfmiddlewaretoken": client.cookies["csrftoken"].value,
        },
    )
    assert response.status_code == 302
    event = AuditEvent.objects.get(workspace=domain["member"].workspace, action="LOGIN")
    assert event.actor_id == domain["member"].user_id and event.metadata == {}


def test_list_query_count_is_independent_of_record_count(domain):
    client = Client()
    client.force_login(domain["member"].user)
    url = reverse("workspace:list", args=[domain["member"].workspace_id, "contacts"])
    Contact.objects.create(
        workspace=domain["member"].workspace, company=domain["company"], full_name="First"
    )
    with CaptureQueriesContext(connection) as small:
        assert client.get(url).status_code == 200
    for index in range(20):
        Contact.objects.create(
            workspace=domain["member"].workspace,
            company=domain["company"],
            full_name=f"More {index}",
        )
    with CaptureQueriesContext(connection) as large:
        assert client.get(url).status_code == 200
    assert len(large) <= len(small) + 1


@pytest.mark.parametrize(
    "name",
    [":memory:", "file:memorydb_default?mode=memory&cache=shared", "file::memory:?cache=shared"],
)
def test_memory_database_uri_uses_regular_worker_lock_path(monkeypatch, name):
    from django.conf import settings

    from apps.jobs.locking import worker_lock_path

    monkeypatch.setattr(
        "apps.jobs.locking.settings.DATABASES",
        {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": name}},
    )
    assert worker_lock_path() == settings.BASE_DIR / "agencycrm.worker.lock"
