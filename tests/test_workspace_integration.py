"""Exercise the assembled local UI and recorded eligibility across HTML/API/export."""

from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.demo import DEMO_ACTOR_ID
from apps.accounts.models import Membership, User
from apps.campaigns.models import Campaign
from apps.companies.models import Company, Contact
from apps.compliance.models import ContactEligibilityDecision
from apps.crm.models import Lead
from apps.jobs.models import Job
from apps.jobs.runner import JobRunner
from apps.outreach.models import OutreachMessage
from apps.proposals.models import Proposal
from common.resources import RESOURCES
from integrations.email.mock import MockEmailProvider
from tests.factories import (
    AgencyProfileFactory,
    MembershipFactory,
    ServiceOfferingFactory,
    UserFactory,
)
from tests.test_seed_demo import demo_files as demo_files
from tests.test_seed_demo import seeded as seeded

pytestmark = pytest.mark.django_db


def test_commercial_flow_through_html_forms_with_csrf_and_worker(demo_files):
    """From empty domain data, every commercial mutation goes through the real UI."""
    assert Company.objects.count() == Campaign.objects.count() == 0
    member = MembershipFactory(role="OWNER")
    workspace_id = member.workspace_id
    AgencyProfileFactory(workspace=member.workspace)
    service = ServiceOfferingFactory(workspace=member.workspace)
    client = Client(enforce_csrf_checks=True)
    client.get(reverse("login"))
    login = client.post(
        reverse("login"),
        {
            "username": member.user.username,
            "password": "Test-password-42!",
            "csrfmiddlewaretoken": client.cookies["csrftoken"].value,
        },
    )
    assert login.status_code == 302

    def post(name, data, *args):
        result = client.post(
            reverse(name, args=[workspace_id, *args]),
            {
                **data,
                "csrfmiddlewaretoken": client.cookies["csrftoken"].value,
            },
        )
        assert result.status_code == 302, result.content.decode()
        assert client.get(result.url).status_code == 200
        return result

    def run(kind):
        result = JobRunner().run_once(kinds=[kind])
        assert result is not None and result.status == "SUCCEEDED"
        return result

    campaign_data = {
        "name": "Dentistas Galicia UI",
        "target_query": "dentistas",
        "target_sector": "Dentistas",
        "target_location_text": "A Coruña",
        "target_country_code": "ES",
        "desired_leads": 3,
        "minimum_score": 0,
        "require_manual_approval": "on",
        "services": [str(service.pk)],
    }
    assert (
        client.post(
            reverse("workspace:create", args=[workspace_id, "campaigns"]), campaign_data
        ).status_code
        == 403
    )
    assert Campaign.objects.count() == 0
    post("workspace:create", campaign_data, "campaigns")
    campaign = Campaign.objects.get()
    post("workspace:state", {"target": "READY"}, "campaigns", campaign.pk)
    post(
        "workspace:create",
        {
            "campaign": campaign.pk,
            "channel": "EMAIL",
            "enabled": "on",
            "priority": 1,
            "max_attempts": 2,
            "cooldown_days": 0,
        },
        "channels",
    )
    plan_key = uuid4()
    post("prospecting:create", {"campaign": campaign.pk, "request_key": plan_key})
    run("RUN_SEARCH_ITEM")
    assert Company.objects.count() == Lead.objects.count() == 3
    post("prospecting:create", {"campaign": campaign.pk, "request_key": plan_key})
    assert JobRunner().run_once(kinds=["RUN_SEARCH_ITEM"]) is None
    lead = Lead.objects.get(company__name="Clínica Atlántico")
    post("enrichment:company", {"idempotency_key": uuid4()}, lead.company_id)
    run("ENRICH_COMPANY")
    contact = Contact.objects.filter(company_id=lead.company_id).exclude(normalized_email="").get()
    post("scoring:lead", {"idempotency_key": uuid4()}, lead.pk)
    run("SCORE_LEAD")
    lead.refresh_from_db()
    assert lead.score > 0 and lead.current_score_run_id
    for target in ("QUALIFIED", "READY"):
        post(
            "workspace:state",
            {
                "target": target,
                "expected_revision": lead.status_revision,
                "reason": "Revisión comercial ficticia desde UI",
            },
            "leads",
            lead.pk,
        )
        lead.refresh_from_db()
    post(
        "proposals:generate", {"lead": lead.pk, "provider": "template", "idempotency_key": uuid4()}
    )
    run("GENERATE_PROPOSAL")
    proposal = Proposal.objects.get(lead=lead)
    for target in ("REVIEWED", "APPROVED"):
        post(
            "proposals:detail",
            {"action": target, "expected_revision": proposal.revision},
            proposal.pk,
        )
        proposal.refresh_from_db()
    document = client.get(reverse("proposals:download", args=[workspace_id, proposal.pk]))
    assert document.status_code == 200
    assert b"".join(document.streaming_content).startswith(b"%PDF-")
    document.close()
    message_data = {
        "proposal": proposal.pk,
        "contact": contact.pk,
        "channel": "EMAIL",
        "idempotency_key": uuid4(),
        "template_parameters": "[]",
    }
    post("outreach:prepare", message_data)
    message = OutreachMessage.objects.get(lead=lead)
    post("outreach:message", {"action": "evaluate"}, message.pk)
    assert message.eligibility_decisions.first().decision != "ELIGIBLE"
    post(
        "outreach:message",
        {
            "action": "approve",
            "expected_revision": message.revision,
            "policy_review_confirmed": "on",
        },
        message.pk,
    )
    with patch(
        "integrations.email.mock.MockEmailProvider.send",
        autospec=True,
        side_effect=MockEmailProvider.send,
    ) as send:
        post("outreach:message", {"action": "enqueue"}, message.pk)
        run("SEND_OUTREACH")
        post("outreach:prepare", message_data)
        post("outreach:message", {"action": "enqueue"}, message.pk)
        assert JobRunner().run_once(kinds=["SEND_OUTREACH"]) is None
        assert send.call_count == 1
    message.refresh_from_db()
    lead.refresh_from_db()
    assert message.status == "SENT" and lead.status == "CONTACTED"
    assert Job.objects.filter(kind="SEND_OUTREACH").count() == 1
    assert client.get(reverse("dashboard", args=[workspace_id])).status_code == 200
    assert client.get(reverse("audit:events", args=[workspace_id])).status_code == 200


def recorded_decision(message, verdict, when):
    return ContactEligibilityDecision.objects.create(
        workspace_id=message.workspace_id,
        lead=message.lead,
        message=message,
        contact=message.contact,
        channel=message.channel,
        decision=verdict,
        reasons={"codes": ["MESSAGE_APPROVAL_REQUIRED"]},
        evaluated_by_id=DEMO_ACTOR_ID,
        evaluated_at=when,
    )


@pytest.fixture
def workspace_client(seeded):
    client = Client(enforce_csrf_checks=True)
    client.force_login(User.objects.get(pk=DEMO_ACTOR_ID))
    client.get(reverse("login"))
    return client, seeded.workspace_id


@pytest.mark.parametrize("verdict", ["ELIGIBLE", "INELIGIBLE", "REVIEW_REQUIRED"])
def test_recorded_eligibility_matches_html_api_and_export(workspace_client, verdict):
    client, workspace_id = workspace_client
    message = OutreachMessage.objects.filter(workspace_id=workspace_id).first()
    now = timezone.now()
    latest = recorded_decision(message, verdict, now)
    # A later insert with an older evaluation must not supersede the latest snapshot.
    recorded_decision(
        message, "INELIGIBLE" if verdict == "ELIGIBLE" else "ELIGIBLE", now - timedelta(days=1)
    )
    route = reverse("workspace:list", args=[workspace_id, "leads"])
    before = ContactEligibilityDecision.objects.count()
    response = client.get(route, {"eligibility": verdict})
    assert response.status_code == 200
    assert [row.pk for row in response.context["page_obj"]] == [message.lead_id]
    api = client.get(
        reverse("api:leads"),
        {
            "workspace_id": workspace_id,
            "eligibility": verdict,
        },
    )
    assert api.status_code == 200
    assert [row["id"] for row in api.json()["results"]] == [str(message.lead_id)]
    detail = client.get(reverse("workspace:detail", args=[workspace_id, "leads", message.lead_id]))
    assert detail.context["eligibility_decision"].pk == latest.pk
    assert "evaluación histórica" in detail.content.decode()
    assert reverse("outreach:message", args=[workspace_id, message.pk]) in detail.content.decode()
    export = client.post(
        reverse("audit:export", args=[workspace_id]),
        {
            "resource": "leads",
            "eligibility": verdict,
            "csrfmiddlewaretoken": client.cookies["csrftoken"].value,
        },
    )
    assert export.status_code == 200
    assert str(message.lead_id) in export.content.decode()
    assert len(export.content.decode().splitlines()) == 2
    assert ContactEligibilityDecision.objects.count() == before


def test_missing_eligibility_is_explicit_and_invalid_values_fail(workspace_client):
    client, workspace_id = workspace_client
    route = reverse("api:leads")
    result = client.get(route, {"workspace_id": workspace_id, "eligibility": "NOT_EVALUATED"})
    assert result.status_code == 200 and result.json()["count"] == 10
    for invalid in ("yes", "LIVE", "eligible"):
        assert (
            client.get(route, {"workspace_id": workspace_id, "eligibility": invalid}).status_code
            == 400
        )
    lead = Lead.objects.filter(workspace_id=workspace_id).first()
    detail = client.get(reverse("workspace:detail", args=[workspace_id, "leads", lead.pk]))
    assert detail.context["eligibility_decision"] is None
    assert "Sin evaluación registrada" in detail.content.decode()
    assert ContactEligibilityDecision.objects.count() == 0


def test_eligibility_filter_uses_latest_decision_across_messages(workspace_client):
    client, workspace_id = workspace_client
    first, second = list(OutreachMessage.objects.filter(workspace_id=workspace_id))
    assert first.lead_id == second.lead_id
    now = timezone.now()
    recorded_decision(first, "ELIGIBLE", now - timedelta(minutes=1))
    latest = recorded_decision(second, "REVIEW_REQUIRED", now)
    for verdict, expected in (("ELIGIBLE", 0), ("REVIEW_REQUIRED", 1), ("NOT_EVALUATED", 9)):
        result = client.get(
            reverse("api:leads"), {"workspace_id": workspace_id, "eligibility": verdict}
        )
        assert result.status_code == 200 and result.json()["count"] == expected
    detail = client.get(reverse("workspace:detail", args=[workspace_id, "leads", first.lead_id]))
    assert detail.context["eligibility_decision"].pk == latest.pk


@pytest.mark.parametrize("role", ["OWNER", "VIEWER"])
def test_integrated_workspace_pages_render_without_writes_and_isolate_tenants(
    workspace_client, role
):
    client, workspace_id = workspace_client
    Membership.objects.filter(workspace_id=workspace_id, user_id=DEMO_ACTOR_ID).update(role=role)
    job = Job.objects.filter(workspace_id=workspace_id).first()
    proposal = Proposal.objects.filter(workspace_id=workspace_id).first()
    lead = proposal.lead
    message = OutreachMessage.objects.filter(workspace_id=workspace_id).first()
    public_pages = [
        ("dashboard", []),
        ("jobs:list", []),
        ("jobs:detail", [job.pk]),
        ("jobs:progress", [job.pk]),
        ("prospecting:list", []),
        ("prospecting:duplicates", []),
        ("scoring:rules", []),
        ("scoring:lead", [lead.pk]),
        ("enrichment:company", [lead.company_id]),
        ("proposals:list", []),
        ("proposals:detail", [proposal.pk]),
        ("proposals:print", [proposal.pk]),
        ("proposals:download", [proposal.pk]),
        ("proposals:assets", []),
        ("outreach:outbox", []),
        ("outreach:message", [message.pk]),
        ("outreach:suppression", []),
        ("outreach:tasks", []),
        ("outreach:followup_rules", []),
        ("outreach:email_connection", []),
        ("outreach:whatsapp_connection", []),
        ("outreach:whatsapp_inbox", []),
        ("outreach:settings", []),
    ]
    private_pages = [("audit:events", []), ("audit:export", []), ("audit:retention", [])]
    routes = [(reverse(name, args=[workspace_id, *args]), 200) for name, args in public_pages]
    routes += [
        (reverse(name, args=[workspace_id, *args]), 200 if role == "OWNER" else 403)
        for name, args in private_pages
    ]
    for key, resource in RESOURCES.items():
        routes.append((reverse("workspace:list", args=[workspace_id, key]), 200))
        record = resource.model.objects.filter(workspace_id=workspace_id).first()
        if record:
            routes.append((reverse("workspace:detail", args=[workspace_id, key, record.pk]), 200))
    for route, expected_status in routes:
        with CaptureQueriesContext(connection) as queries:
            response = client.get(route)
            if response.streaming:
                assert b"".join(response.streaming_content).startswith(b"%PDF-")
                response.close()
        assert response.status_code == expected_status, route
        assert not any(
            q["sql"].lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}
            for q in queries.captured_queries
        ), route
        assert "no-store" in response.headers["Cache-Control"]
    # Changing the URL is never a way to gain membership in another workspace.
    foreign = MembershipFactory(role="OWNER")
    for name, args in public_pages + private_pages:
        assert client.get(reverse(name, args=[foreign.workspace_id, *args])).status_code == 404
    client.force_login(UserFactory(is_superuser=True, is_staff=True))
    assert client.get(reverse("dashboard", args=[workspace_id])).status_code == 404
