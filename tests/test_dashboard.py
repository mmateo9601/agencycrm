import uuid

import pytest
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.http import Http404
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.models import Membership
from apps.audit.models import AuditEvent
from apps.campaigns.models import CampaignService
from apps.companies.models import Company
from apps.crm.models import Lead
from apps.dashboard.selectors import dashboard_for_user
from apps.jobs.models import Job
from apps.outreach.models import OutreachMessage
from tests.factories import (
    AgencyProfileFactory,
    MembershipFactory,
    ServiceOfferingFactory,
    UserFactory,
)
from tests.test_outreach import outreach as outreach
from tests.test_outreach import prepare
from tests.test_phase1 import domain as domain
from tests.test_proposals import generate
from tests.test_proposals import proposals as proposals

pytestmark = pytest.mark.django_db


def snapshot(env):
    member = env["member"]
    return dashboard_for_user(user=member.user, workspace_id=member.workspace_id)


def test_empty_workspace_has_zero_counters():
    member = MembershipFactory()
    data = dashboard_for_user(user=member.user, workspace_id=member.workspace_id)
    assert (data.companies_total, data.leads_total, data.jobs_failed, data.messages_pending) == (
        0,
        0,
        0,
        0,
    )
    assert all(item.value == 0 for item in data.lead_counts)


def test_counts_current_states_excludes_archived_and_keeps_inactive_companies(domain):
    member = domain["member"]
    for status in Lead.Status.values:
        company = Company.objects.create(workspace=member.workspace, name=f"Demo {status}")
        Lead.objects.create(
            workspace=member.workspace,
            campaign=domain["campaign"],
            company=company,
            status=status,
        )
    Company.objects.filter(pk=domain["company"].pk).update(is_active=False)
    Lead.objects.filter(pk=domain["lead"].pk).update(is_archived=True)
    data = snapshot(domain)
    assert data.companies_total == len(Lead.Status.values) + 1
    assert data.leads_total == len(Lead.Status.values)
    assert sum(item.value for item in data.lead_counts) == data.leads_total
    assert all(item.value == 1 for item in data.lead_counts)
    # Changes appear on the next read; there is no stale cache or refresh job.
    Lead.objects.filter(workspace=member.workspace, status="NEW").update(status="QUALIFIED")
    counts = {item.status: item.value for item in snapshot(domain).lead_counts}
    assert counts["NEW"] == 0 and counts["QUALIFIED"] == 2


def test_workspace_counts_do_not_combine_multiple_memberships(domain):
    MembershipFactory(user=domain["member"].user, workspace=domain["foreign"].workspace)
    Company.objects.create(workspace=domain["foreign"].workspace, name="Another foreign record")
    data = snapshot(domain)
    assert data.workspace == domain["member"].workspace
    assert data.companies_total == data.leads_total == 1
    assert {item.status: item.value for item in data.lead_counts}["NEW"] == 1


def test_job_and_approval_counts_are_scoped_and_status_specific(outreach):
    for member in (outreach["member"], outreach["foreign"]):
        for status in ("FAILED", "SUCCEEDED", "PENDING"):
            Job.objects.create(
                workspace=member.workspace, kind="CHECK_MOCK_PROVIDERS", status=status
            )
    message = prepare(outreach, key="dashboard-pending")
    draft = prepare(outreach, key="dashboard-draft")
    OutreachMessage.objects.filter(pk=draft.pk).update(status="DRAFT")
    OutreachMessage.objects.filter(pk=message.pk).update(status="PENDING_APPROVAL")
    foreign = outreach["foreign"]
    AgencyProfileFactory(workspace=foreign.workspace)
    service = ServiceOfferingFactory(workspace=foreign.workspace)
    CampaignService.objects.create(
        workspace=foreign.workspace, campaign=outreach["other_campaign"], service=service
    )
    foreign_proposal = generate({"member": foreign, "lead": outreach["other_lead"]})
    for index in range(3):
        OutreachMessage.objects.create(
            workspace=foreign.workspace,
            lead=outreach["other_lead"],
            proposal=foreign_proposal,
            channel="EMAIL",
            body_text="Fictional foreign pending message",
            status="PENDING_APPROVAL",
            idempotency_key=f"foreign-pending-{index}",
            created_by=foreign.user,
        )
    data = snapshot(outreach)
    assert data.jobs_failed == 1 and data.messages_pending == 1
    OutreachMessage.objects.filter(pk=message.pk).update(status="CANCELLED")
    assert snapshot(outreach).messages_pending == 0


@pytest.mark.parametrize("inactive", ["user", "membership", "workspace"])
def test_selector_rechecks_active_access(domain, inactive):
    member = domain["member"]
    obj = {"user": member.user, "membership": member, "workspace": member.workspace}[inactive]
    obj.is_active = False
    obj.save()
    with pytest.raises(Http404):
        snapshot(domain)


def test_unknown_foreign_anonymous_and_superuser_access_is_denied(domain, client):
    member = domain["member"]
    client.force_login(member.user)
    for workspace_id in (domain["foreign"].workspace_id, uuid.uuid4()):
        with pytest.raises(Http404):
            dashboard_for_user(user=member.user, workspace_id=workspace_id)
        assert client.get(reverse("dashboard", args=[workspace_id])).status_code == 404
    for user in (AnonymousUser(), UserFactory(is_superuser=True, is_staff=True)):
        with pytest.raises(Http404):
            dashboard_for_user(user=user, workspace_id=member.workspace_id)


@pytest.mark.parametrize("role", Membership.Role.values)
def test_ui_read_access_links_security_and_no_side_effects(domain, client, role):
    member = domain["member"]
    Membership.objects.filter(pk=member.pk).update(role=role)
    client.force_login(member.user)
    url = reverse("dashboard", args=[member.workspace_id])
    before = (Job.objects.count(), OutreachMessage.objects.count(), AuditEvent.objects.count())
    response = client.get(url)
    assert response.status_code == 200
    assert response.context["snapshot"].companies_total == 1
    assert response.context["snapshot"].leads_total == 1
    assert b"Secreto ajeno" not in response.content
    assert "no-store" in response.headers["Cache-Control"]
    assert "Content-Security-Policy" in response.headers
    for card in response.context["cards"]:
        assert client.get(card["url"]).status_code == 200
    assert before == (
        Job.objects.count(),
        OutreachMessage.objects.count(),
        AuditEvent.objects.count(),
    )
    assert url.encode() in client.get(reverse("home")).content
    assert client.head(url).status_code == 200
    assert client.post(url).status_code == 405


def test_login_and_revoked_membership_are_enforced(domain, client):
    member = domain["member"]
    url = reverse("dashboard", args=[member.workspace_id])
    assert client.get(url).status_code == 302
    client.force_login(member.user)
    Membership.objects.filter(pk=member.pk).update(is_active=False)
    assert client.get(url).status_code == 404


def test_aggregate_query_count_is_constant(domain):
    with CaptureQueriesContext(connection) as small:
        snapshot(domain)
    for index in range(20):
        company = Company.objects.create(
            workspace=domain["member"].workspace, name=f"Volume fixture {index}"
        )
        Lead.objects.create(
            workspace=domain["member"].workspace, campaign=domain["campaign"], company=company
        )
    with CaptureQueriesContext(connection) as large:
        data = snapshot(domain)
    assert len(small) == len(large) == 5
    assert data.leads_total == 21
    assert all(query["sql"].lstrip().upper().startswith("SELECT") for query in large)
