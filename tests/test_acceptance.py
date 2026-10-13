"""Playbook sections 33–34, starting without commercial records and staying offline."""

import uuid
from dataclasses import replace
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.campaigns.models import Campaign
from apps.campaigns.services import change_campaign_status, save_channel
from apps.companies.enrichment import enqueue_enrichment
from apps.companies.models import Company, CompanyExternalRef, Contact, WebsiteObservation
from apps.compliance.services import add_suppression, evaluate
from apps.crm.models import Lead, Opportunity
from apps.crm.services import LeadStateService
from apps.jobs.models import Job
from apps.jobs.runner import JobRunner
from apps.outreach.models import MessageEvent
from apps.outreach.services import prepare_message
from apps.proposals.models import Proposal
from apps.proposals.services import enqueue_proposal, transition_proposal
from apps.prospecting.models import SearchRun
from apps.prospecting.services import create_plan, enqueue_item
from apps.scoring.services import enqueue_score
from integrations.common.contracts import PlaceSearchPage
from integrations.email.mock import MockEmailProvider
from integrations.google_places.mock import PLACES
from tests.factories import AgencyProfileFactory, MembershipFactory, ServiceOfferingFactory

pytestmark = pytest.mark.django_db


def api_url(workspace_id, name, pk=None):
    path = reverse(f"api:{name}", kwargs={"pk": pk} if pk else {})
    return f"{path}?workspace_id={workspace_id}"


def api_post(client, url, data):
    return client.post(
        url,
        data,
        content_type="application/json",
        HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
    )


def complete(job):
    processed = JobRunner().run_once(kinds=[job.kind])
    assert processed is not None and processed.pk == job.pk and processed.status == "SUCCEEDED"
    return processed


def test_playbook_v1_acceptance_from_empty_database(settings, tmp_path, monkeypatch):
    settings.MEDIA_ROOT = tmp_path / "media"
    monkeypatch.setattr("apps.jobs.locking.worker_lock_path", lambda: tmp_path / "worker.lock")
    assert (
        Company.objects.count()
        == Lead.objects.count()
        == Proposal.objects.count()
        == Job.objects.count()
        == 0
    )
    member = MembershipFactory(role="OWNER")
    user, workspace_id = member.user, member.workspace_id
    AgencyProfileFactory(workspace=member.workspace, trading_name="Agencia Demo V1")
    seo = ServiceOfferingFactory(workspace=member.workspace, code="SEO_LOCAL", name="SEO Local")
    ads = ServiceOfferingFactory(workspace=member.workspace, code="GOOGLE_ADS", name="Google Ads")
    client = Client(enforce_csrf_checks=True)
    client.get(reverse("login"))
    login = client.post(
        reverse("login"),
        {
            "username": user.username,
            "password": "Test-password-42!",
            "csrfmiddlewaretoken": client.cookies["csrftoken"].value,
        },
    )
    assert login.status_code == 302
    request = {
        "name": "Dentistas Galicia",
        "target_query": "dentistas",
        "target_sector": "Dentistas",
        "target_location_text": "A Coruña",
        "desired_leads": 3,
        "services": [str(seo.pk), str(ads.pk)],
        "idempotency_key": str(uuid.uuid4()),
    }
    response = api_post(client, api_url(workspace_id, "campaigns"), request)
    assert response.status_code == 201
    campaign = Campaign.objects.get(pk=response.json()["id"])
    change_campaign_status(user=user, workspace_id=workspace_id, pk=campaign.pk, target="READY")
    save_channel(
        user=user,
        workspace_id=workspace_id,
        data={"campaign": campaign, "channel": "EMAIL", "max_attempts": 3},
    )
    page = PlaceSearchPage(tuple(replace(place, review_count="5") for place in PLACES))
    with patch("integrations.google_places.mock.MockPlacesProvider.text_search", return_value=page):
        key = uuid.uuid4()
        plan = create_plan(
            user=user, workspace_id=workspace_id, campaign_id=campaign.pk, request_key=key
        )
        item = plan.items.get()
        complete(item.job)
        assert (
            create_plan(
                user=user, workspace_id=workspace_id, campaign_id=campaign.pk, request_key=key
            ).pk
            == plan.pk
        )
        complete(
            enqueue_item(user=user, workspace_id=workspace_id, pk=item.pk, request_key=uuid.uuid4())
        )
    assert set(Company.objects.values_list("name", flat=True)) == {
        "Clínica Norte",
        "Clínica Atlántico",
        "Dental Demo",
    }
    assert (
        Company.objects.count() == Lead.objects.count() == CompanyExternalRef.objects.count() == 3
    )
    assert SearchRun.objects.count() == 2
    lead = Lead.objects.get(company__name="Clínica Atlántico")
    complete(
        enqueue_enrichment(
            user=user,
            workspace_id=workspace_id,
            company_id=lead.company_id,
            idempotency_key="acceptance:enrich",
        )
    )
    assert WebsiteObservation.objects.get(company=lead.company).provider == "mock"
    contact = Contact.objects.filter(company=lead.company).exclude(normalized_email="").get()
    complete(
        enqueue_score(
            user=user,
            workspace_id=workspace_id,
            lead_id=lead.pk,
            idempotency_key="acceptance:score",
        )
    )
    lead.refresh_from_db()
    assert 0 < lead.score <= 100 and lead.current_score_run.items.count() == 9
    assert lead.current_score_run.items.filter(matched=True, points__gt=0).exists()
    assert Opportunity.objects.filter(
        lead=lead, scoring_run=lead.current_score_run, type="REVIEWS"
    ).exists()
    for status in ("QUALIFIED", "READY"):
        lead = LeadStateService.transition(
            user=user, workspace_id=workspace_id, pk=lead.pk, target=status
        )
    complete(
        enqueue_proposal(
            user=user,
            workspace_id=workspace_id,
            lead_id=lead.pk,
            idempotency_key="acceptance:proposal",
        )
    )
    proposal = Proposal.objects.get(lead=lead)
    proposal = transition_proposal(
        user=user,
        workspace_id=workspace_id,
        pk=proposal.pk,
        target="REVIEWED",
        expected_revision=proposal.revision,
    )
    assert (
        api_post(
            client,
            api_url(workspace_id, "approve-proposal", proposal.pk),
            {"expected_revision": proposal.revision},
        ).status_code
        == 200
    )
    download = client.get(reverse("proposals:download", args=[workspace_id, proposal.pk]))
    assert download.status_code == 200
    assert b"".join(download.streaming_content).startswith(b"%PDF-")
    download.close()
    message_key = "acceptance:email"
    message = prepare_message(
        user=user,
        workspace_id=workspace_id,
        proposal_id=proposal.pk,
        contact_id=contact.pk,
        channel="EMAIL",
        idempotency_key=message_key,
    )
    assert (
        evaluate(user=user, workspace_id=workspace_id, message_id=message.pk).decision != "ELIGIBLE"
    )
    approved = api_post(
        client,
        api_url(workspace_id, "approve-message", message.pk),
        {"expected_revision": message.revision, "policy_review_confirmed": True},
    )
    assert approved.status_code == 200
    assert (
        evaluate(user=user, workspace_id=workspace_id, message_id=message.pk).decision == "ELIGIBLE"
    )
    original_send = MockEmailProvider.send
    with (
        patch(
            "integrations.email.mock.MockEmailProvider.send",
            autospec=True,
            side_effect=original_send,
        ) as transport,
        patch(
            "integrations.email.smtp.SMTPEmailProvider.send",
            side_effect=AssertionError("Live SMTP"),
        ),
        patch(
            "integrations.whatsapp.provider.WhatsAppCloudProvider.send",
            side_effect=AssertionError("Live WhatsApp"),
        ),
    ):
        queued = api_post(client, api_url(workspace_id, "enqueue-message", message.pk), {})
        assert queued.status_code == 202
        sent_job = complete(Job.objects.get(pk=queued.json()["job"]["id"]))
        message.refresh_from_db()
        assert message.status == "SENT" and message.provider_message_id.startswith("mock-email-")
        assert (
            prepare_message(
                user=user,
                workspace_id=workspace_id,
                proposal_id=proposal.pk,
                contact_id=contact.pk,
                channel="EMAIL",
                idempotency_key=message_key,
            ).pk
            == message.pk
        )
        again = api_post(client, api_url(workspace_id, "enqueue-message", message.pk), {})
        assert again.status_code == 202 and again.json()["job"]["id"] == str(sent_job.pk)
        assert JobRunner().run_once(kinds=["SEND_OUTREACH"]) is None
        assert transport.call_count == 1
    assert MessageEvent.objects.filter(message=message, event_type="SENT").count() == 1
    complete(
        enqueue_proposal(
            user=user,
            workspace_id=workspace_id,
            lead_id=lead.pk,
            idempotency_key="acceptance:proposal-next",
        )
    )
    next_proposal = Proposal.objects.filter(lead=lead).first()
    next_proposal = transition_proposal(
        user=user,
        workspace_id=workspace_id,
        pk=next_proposal.pk,
        target="REVIEWED",
        expected_revision=next_proposal.revision,
    )
    assert (
        api_post(
            client,
            api_url(workspace_id, "approve-proposal", next_proposal.pk),
            {"expected_revision": next_proposal.revision},
        ).status_code
        == 200
    )
    next_message = prepare_message(
        user=user,
        workspace_id=workspace_id,
        proposal_id=next_proposal.pk,
        contact_id=contact.pk,
        channel="EMAIL",
        idempotency_key="acceptance:email-next",
    )
    assert (
        api_post(
            client,
            api_url(workspace_id, "approve-message", next_message.pk),
            {"expected_revision": next_message.revision, "policy_review_confirmed": True},
        ).status_code
        == 200
    )
    add_suppression(
        user=user,
        workspace_id=workspace_id,
        scope="COMPANY",
        value="",
        company_id=lead.company_id,
        reason="MANUAL_BLOCK",
        source="Fixture de aceptación V1",
    )
    blocked = api_post(client, api_url(workspace_id, "enqueue-message", next_message.pk), {})
    assert blocked.status_code == 200 and blocked.json()["job"] is None
    assert blocked.json()["message"]["status"] == "SUPPRESSED"
    assert Job.objects.filter(kind="SEND_OUTREACH").count() == 1
    dashboard = client.get(reverse("dashboard", args=[workspace_id]))
    assert dashboard.status_code == 200
    snapshot = dashboard.context["snapshot"]
    assert snapshot.companies_total == snapshot.leads_total == 3
    assert {row.status: row.value for row in snapshot.lead_counts}["CONTACTED"] == 1
    assert snapshot.jobs_failed == snapshot.messages_pending == 0
    sequence = list(
        AuditEvent.objects.filter(workspace_id=workspace_id)
        .order_by("created_at", "id")
        .values_list("action", flat=True)
    )
    required = [
        "LOGIN",
        "CAMPAIGN_CREATED",
        "SEARCH_PLAN_CREATED",
        "SEARCH_EXECUTED",
        "WEBSITE_ENRICHED",
        "SCORE_CALCULATED",
        "PROPOSAL_GENERATED",
        "PROPOSAL_APPROVED",
        "MESSAGE_PREPARED",
        "MESSAGE_APPROVED",
        "MESSAGE_SEND_ATTEMPT",
        "MESSAGE_SENT",
        "SUPPRESSION_ADDED",
        "MESSAGE_SUPPRESSED",
    ]
    positions = [sequence.index(action) for action in required]
    assert positions == sorted(positions)
    assert client.get(reverse("audit:events", args=[workspace_id])).status_code == 200
