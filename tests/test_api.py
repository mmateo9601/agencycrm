import uuid
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.models import Membership
from apps.audit.models import AuditEvent
from apps.campaigns.models import Campaign, CampaignService
from apps.campaigns.services import save_campaign
from apps.companies.models import Company
from apps.companies.services import save_company
from apps.compliance.services import add_suppression
from apps.jobs.models import Job
from apps.jobs.services import JobService
from apps.outreach.models import OutreachMessage
from apps.proposals.models import Proposal
from common.services import save_record
from tests.factories import MembershipFactory, ServiceOfferingFactory, UserFactory
from tests.test_outreach import approve, prepare
from tests.test_outreach import outreach as outreach
from tests.test_phase1 import domain as domain
from tests.test_proposals import generate, transition
from tests.test_proposals import proposals as proposals

pytestmark = pytest.mark.django_db


def api_url(env, name, pk=None, workspace_id=None):
    path = reverse(f"api:{name}", kwargs={"pk": pk} if pk else {})
    return f"{path}?workspace_id={workspace_id or env['member'].workspace_id}"


@pytest.fixture
def session_client(domain):
    client = Client(enforce_csrf_checks=True)
    client.force_login(domain["member"].user)
    client.get(reverse("login"))
    return client


def post(client, url, data, **headers):
    return client.post(
        url,
        data,
        content_type="application/json",
        HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
        **headers,
    )


def campaign_payload(**extra):
    return {"name": "Campaña API ficticia", "idempotency_key": str(uuid.uuid4()), **extra}


def job_for(member):
    return JobService.enqueue(
        user=member.user,
        workspace_id=member.workspace_id,
        kind="CHECK_MOCK_PROVIDERS",
        payload={"scenario": "success"},
        idempotency_key=str(uuid.uuid4()),
    )


@pytest.mark.parametrize("resource", ["companies", "leads", "campaigns", "jobs"])
def test_lists_require_session_and_explicit_workspace(domain, client, session_client, resource):
    url = api_url(domain, resource)
    assert client.get(url).status_code == 403
    assert client.get(url, HTTP_AUTHORIZATION="Basic ignored").status_code == 403
    assert session_client.get(reverse(f"api:{resource}")).status_code == 400
    assert session_client.get(reverse(f"api:{resource}") + "?workspace_id=bad").status_code == 400
    assert (
        session_client.get(url + "&workspace_id=" + str(domain["foreign"].workspace_id)).status_code
        == 400
    )
    assert (
        session_client.get(
            api_url(domain, resource, workspace_id=domain["foreign"].workspace_id)
        ).status_code
        == 404
    )
    response = session_client.get(url)
    assert response.status_code == 200 and response.headers["Content-Type"].startswith(
        "application/json"
    )
    assert "no-store" in response.headers["Cache-Control"]
    assert "Content-Security-Policy" in response.headers
    assert all(
        row["workspace"] == str(domain["member"].workspace_id) for row in response.json()["results"]
    )


def test_detail_ids_are_scoped_and_jobs_omit_payloads(domain, session_client):
    own_job, foreign_job = job_for(domain["member"]), job_for(domain["foreign"])
    for name, own, foreign in (
        ("company", domain["company"], domain["other_company"]),
        ("lead", domain["lead"], domain["other_lead"]),
        ("job", own_job, foreign_job),
    ):
        result = session_client.get(api_url(domain, name, own.pk))
        assert result.status_code == 200 and result.json()["id"] == str(own.pk)
        wrong = session_client.get(api_url(domain, name, foreign.pk))
        missing = session_client.get(api_url(domain, name, uuid.uuid4()))
        assert wrong.status_code == missing.status_code == 404
        assert wrong.json() == missing.json()
    data = session_client.get(api_url(domain, "job", own_job.pk)).json()
    assert not {"payload", "result", "run_token", "created_by", "idempotency_key"} & set(data)
    assert (
        session_client.get(api_url(domain, "job", own_job.pk) + "&include_payload=true").status_code
        == 400
    )


@pytest.mark.parametrize("role", Membership.Role.values)
def test_role_permissions_for_reads_and_campaign_creation(domain, session_client, role):
    Membership.objects.filter(pk=domain["member"].pk).update(role=role)
    assert session_client.get(api_url(domain, "companies")).status_code == 200
    before = Campaign.objects.count()
    response = post(session_client, api_url(domain, "campaigns"), campaign_payload())
    assert response.status_code == (403 if role == "VIEWER" else 201)
    assert Campaign.objects.count() == before + int(role != "VIEWER")


@pytest.mark.parametrize("inactive", ["membership", "workspace", "user"])
def test_api_rechecks_revoked_access(domain, session_client, inactive):
    member = domain["member"]
    obj = {"membership": member, "workspace": member.workspace, "user": member.user}[inactive]
    obj.is_active = False
    obj.save()
    response = session_client.get(api_url(domain, "companies"))
    assert response.status_code == (403 if inactive == "user" else 404)
    response = post(session_client, api_url(domain, "campaigns"), campaign_payload())
    assert response.status_code == (403 if inactive == "user" else 404)
    assert Campaign.objects.count() == 2


def test_multiple_memberships_and_superuser_do_not_bypass_scope(domain, session_client):
    user = domain["member"].user
    MembershipFactory(user=user, workspace=domain["foreign"].workspace)
    assert session_client.get(api_url(domain, "companies")).json()["count"] == 1
    foreign = api_url(domain, "companies", workspace_id=domain["foreign"].workspace_id)
    assert session_client.get(foreign).json()["results"][0]["id"] == str(domain["other_company"].pk)
    session_client.force_login(UserFactory(is_superuser=True, is_staff=True))
    assert session_client.get(api_url(domain, "companies")).status_code == 404


def test_campaign_csrf_content_type_and_strict_input(domain, session_client):
    url = api_url(domain, "campaigns")
    payload = campaign_payload()
    assert session_client.post(url, payload, content_type="application/json").status_code == 403
    assert (
        post(session_client, url, payload, HTTP_ORIGIN="https://outside.invalid").status_code == 403
    )
    assert (
        session_client.post(
            url, payload, HTTP_X_CSRFTOKEN=session_client.cookies["csrftoken"].value
        ).status_code
        == 415
    )
    for extra in (
        {"workspace": str(domain["foreign"].workspace_id)},
        {"status": "RUNNING"},
        {"created_by": str(domain["member"].user_id)},
        {"id": str(uuid.uuid4())},
    ):
        assert post(session_client, url, {**payload, **extra}).status_code == 400
    assert post(session_client, url, [payload]).status_code == 400
    assert post(session_client, url, {"name": "No key"}).status_code == 400
    assert post(session_client, url + "&force=true", payload).status_code == 400
    assert Campaign.objects.count() == 2


def test_campaign_creation_replay_preserves_edits_and_rejects_changed_intent(
    domain, session_client
):
    service = ServiceOfferingFactory(workspace=domain["member"].workspace)
    data = campaign_payload(services=[str(service.pk), str(service.pk)])
    url = api_url(domain, "campaigns")
    first = post(session_client, url, data)
    assert first.status_code == 201
    campaign = Campaign.objects.get(pk=first.json()["id"])
    assert campaign.workspace_id == domain["member"].workspace_id and campaign.status == "DRAFT"
    assert first.json()["require_manual_approval"] is True
    assert list(campaign.services.all()) == [service]
    assert campaign.created_by == domain["member"].user
    save_campaign(
        user=domain["member"].user,
        workspace_id=domain["member"].workspace_id,
        pk=campaign.pk,
        data={"name": "Edited after creation"},
    )
    events = AuditEvent.objects.count()
    replay = post(session_client, url, {**data, "services": [str(service.pk)]})
    assert replay.status_code == 201 and replay.json()["id"] == first.json()["id"]
    assert replay.json()["name"] == "Edited after creation"
    assert AuditEvent.objects.count() == events
    assert post(session_client, url, {**data, "description": "Changed intent"}).status_code == 400
    assert Campaign.objects.count() == 3
    foreign = ServiceOfferingFactory(workspace=domain["foreign"].workspace)
    assert (
        post(session_client, url, campaign_payload(services=[str(foreign.pk)])).status_code == 404
    )
    assert Campaign.objects.count() == 3


def test_stable_creation_id_never_overwrites_an_existing_record(domain):
    before = domain["other_company"].name
    with pytest.raises(ValidationError):
        save_record(
            model=Company,
            user=domain["member"].user,
            workspace_id=domain["member"].workspace_id,
            data={"name": "Overwrite attempt"},
            fields=("name",),
            creation_id=domain["other_company"].pk,
        )
    domain["other_company"].refresh_from_db()
    assert domain["other_company"].name == before


def test_filters_fail_closed_and_pagination_remains_bounded(domain, session_client):
    for index in range(26):
        save_company(
            user=domain["member"].user,
            workspace_id=domain["member"].workspace_id,
            data={"name": f"API page fixture {index}", "city": "Vigo"},
        )
    url = api_url(domain, "companies")
    first = session_client.get(url).json()
    assert first["count"] == 27 and len(first["results"]) == 25
    second = session_client.get(first["next"]).json()
    assert len(second["results"]) == 2 and second["next"] is None
    assert "workspace_id=" in first["next"]
    assert session_client.get(url + "&city=Vigo").json()["count"] == 26
    for query in ("&q=" + "x" * 201, "&page_size=1000", "&active=bad", "&city=a&city=b"):
        assert session_client.get(url + query).status_code == 400
    assert session_client.get(url + "&page=999").status_code == 404
    lead_url = api_url(domain, "leads")
    for query in (
        "&min_score=100&max_score=10",
        "&status=bad",
        "&campaign=" + str(domain["other_campaign"].pk),
    ):
        assert session_client.get(lead_url + query).status_code == 400
    assert session_client.get(lead_url + "&status=NEW&city=A+Coru%C3%B1a").json()["count"] == 1
    assert session_client.get(api_url(domain, "campaigns") + "&status=DRAFT").json()["count"] == 1
    job_for(domain["member"])
    assert session_client.get(api_url(domain, "jobs") + "&status=PENDING").json()["count"] == 1
    assert session_client.get(api_url(domain, "jobs") + "&kind=bad").status_code == 400


def test_campaign_list_has_no_n_plus_one_queries(domain, session_client):
    service = ServiceOfferingFactory(workspace=domain["member"].workspace)
    CampaignService.objects.create(
        workspace=domain["member"].workspace, campaign=domain["campaign"], service=service
    )
    url = api_url(domain, "campaigns")
    with CaptureQueriesContext(connection) as small:
        assert session_client.get(url).status_code == 200
    for index in range(20):
        save_campaign(
            user=domain["member"].user,
            workspace_id=domain["member"].workspace_id,
            data={"name": f"API campaign {index}", "services": [service]},
        )
    with CaptureQueriesContext(connection) as large:
        response = session_client.get(url)
    assert response.json()["count"] == 21
    assert len(small) == len(large)


def test_proposal_approval_requires_review_and_current_revision(outreach, session_client):
    proposal = generate(outreach)
    url = api_url(outreach, "approve-proposal", proposal.pk)
    assert (
        session_client.post(
            url, {"expected_revision": proposal.revision}, content_type="application/json"
        ).status_code
        == 403
    )
    assert post(session_client, url, {"expected_revision": proposal.revision}).status_code == 400
    proposal = transition(outreach, proposal, "REVIEWED")
    assert post(session_client, url, {"expected_revision": 0}).status_code == 400
    result = post(session_client, url, {"expected_revision": proposal.revision})
    assert result.status_code == 200 and result.json()["status"] == "APPROVED"
    assert not {"pdf", "snapshot", "internal_notes", "services_json"} & set(result.json())
    events = AuditEvent.objects.filter(action="PROPOSAL_APPROVED").count()
    assert post(session_client, url, {"expected_revision": proposal.revision}).status_code == 200
    assert AuditEvent.objects.filter(action="PROPOSAL_APPROVED").count() == events


@pytest.mark.parametrize("role", Membership.Role.values)
def test_approval_roles_and_no_provider_calls(outreach, session_client, role):
    proposal = transition(outreach, generate(outreach), "REVIEWED")
    message = prepare({**outreach, "proposal": proposal})
    Membership.objects.filter(pk=outreach["member"].pk).update(role=role)
    with patch("apps.outreach.services.outbound_provider", side_effect=AssertionError("provider")):
        response = post(
            session_client,
            api_url(outreach, "approve-proposal", proposal.pk),
            {"expected_revision": proposal.revision},
        )
        assert response.status_code == (200 if role in {"OWNER", "ADMIN"} else 403)
        response = post(
            session_client,
            api_url(outreach, "approve-message", message.pk),
            {"expected_revision": message.revision, "policy_review_confirmed": True},
        )
        assert response.status_code == (200 if role in {"OWNER", "ADMIN"} else 403)
    message.refresh_from_db()
    assert message.status == ("APPROVED" if role in {"OWNER", "ADMIN"} else "PENDING_APPROVAL")


@pytest.mark.parametrize("role", Membership.Role.values)
def test_enqueue_rechecks_current_write_role(outreach, session_client, role):
    message = approve(outreach, prepare(outreach))
    writer = MembershipFactory(workspace=outreach["member"].workspace, role=role)
    session_client.force_login(writer.user)
    result = post(session_client, api_url(outreach, "enqueue-message", message.pk), {})
    assert result.status_code == (403 if role == "VIEWER" else 202)
    assert Job.objects.filter(kind="SEND_OUTREACH").count() == int(role != "VIEWER")


def test_enqueue_blocks_a_revoked_approval(outreach, session_client):
    message = approve(outreach, prepare(outreach))
    Membership.objects.filter(pk=outreach["member"].pk).update(role="SALES")
    result = post(session_client, api_url(outreach, "enqueue-message", message.pk), {})
    assert result.status_code == 200 and result.json()["job"] is None
    assert result.json()["message"]["status"] == "SUPPRESSED"
    assert "APPROVER_INACTIVE" in result.json()["message"]["error_code"]
    assert not Job.objects.filter(kind="SEND_OUTREACH").exists()


def test_message_csrf_revision_idempotency_and_enqueue_only(outreach, session_client):
    message = prepare(outreach)
    approve_url = api_url(outreach, "approve-message", message.pk)
    data = {"expected_revision": message.revision, "policy_review_confirmed": True}
    assert (
        session_client.post(approve_url, data, content_type="application/json").status_code == 403
    )
    assert (
        post(session_client, approve_url, {**data, "policy_review_confirmed": False}).status_code
        == 400
    )
    assert post(session_client, approve_url, {**data, "expected_revision": 999}).status_code == 400
    with patch("apps.outreach.services.outbound_provider", side_effect=AssertionError("provider")):
        approved = post(session_client, approve_url, data)
        assert approved.status_code == 200 and approved.json()["status"] == "APPROVED"
        assert post(session_client, approve_url, data).status_code == 200
        url = api_url(outreach, "enqueue-message", message.pk)
        assert session_client.post(url, {}, content_type="application/json").status_code == 403
        first, second = post(session_client, url, {}), post(session_client, url, {})
    assert first.status_code == second.status_code == 202
    assert first.json()["job"]["id"] == second.json()["job"]["id"]
    assert first.json()["message"]["status"] == "QUEUED"
    assert Job.objects.filter(kind="SEND_OUTREACH").count() == 1
    assert not {"body_text", "template_snapshot", "approval_hash", "recipient"} & set(
        approved.json()
    )
    assert Job.objects.get(kind="SEND_OUTREACH").status == "PENDING"
    assert AuditEvent.objects.filter(action="MESSAGE_APPROVED").count() == 1


def test_enqueue_unapproved_and_suppressed_outreach(outreach, session_client):
    message = prepare(outreach)
    url = api_url(outreach, "enqueue-message", message.pk)
    assert post(session_client, url, {}).status_code == 400
    assert not Job.objects.filter(kind="SEND_OUTREACH").exists()
    message = approve(outreach, message)
    member = outreach["member"]
    add_suppression(
        user=member.user,
        workspace_id=member.workspace_id,
        scope="COMPANY",
        value="",
        company_id=outreach["company"].pk,
        reason="MANUAL_BLOCK",
        source="Fictional API test",
    )
    response = post(session_client, url, {})
    assert response.status_code == 200 and response.json()["job"] is None
    assert response.json()["message"]["status"] == "SUPPRESSED"
    assert "SUPPRESSED" in response.json()["message"]["error_code"]
    assert not Job.objects.filter(kind="SEND_OUTREACH").exists()


def test_actions_reject_cross_workspace_ids_and_unknown_fields(outreach, session_client):
    foreign = outreach["foreign"]
    message = prepare(outreach)
    actions = (
        ("approve-proposal", outreach["proposal"].pk, {"expected_revision": 0}),
        ("approve-message", message.pk, {"expected_revision": 0, "policy_review_confirmed": True}),
        ("enqueue-message", message.pk, {}),
    )
    for name, pk, data in actions:
        assert (
            post(session_client, api_url(outreach, name, pk), {**data, "force": True}).status_code
            == 400
        )
        assert (
            post(
                session_client, api_url(outreach, name, pk, workspace_id=foreign.workspace_id), data
            ).status_code
            == 404
        )
        assert post(session_client, api_url(outreach, name, uuid.uuid4()), data).status_code == 404
        assert session_client.get(api_url(outreach, name, pk)).status_code == 405
    assert OutreachMessage.objects.get(pk=message.pk).status == "PENDING_APPROVAL"
    assert Proposal.objects.get(pk=outreach["proposal"].pk).status == "APPROVED"


def test_read_requests_are_side_effect_free(domain, session_client):
    before = (AuditEvent.objects.count(), Job.objects.count(), Campaign.objects.count())
    for name in ("companies", "leads", "campaigns", "jobs"):
        assert session_client.get(api_url(domain, name)).status_code == 200
        assert session_client.head(api_url(domain, name)).status_code == 200
    assert before == (AuditEvent.objects.count(), Job.objects.count(), Campaign.objects.count())
