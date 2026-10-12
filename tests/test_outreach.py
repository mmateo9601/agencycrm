import uuid
from datetime import timedelta
from unittest.mock import Mock, patch

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.campaigns.models import CampaignChannel
from apps.companies.models import Contact
from apps.compliance.models import SuppressionEntry
from apps.compliance.policies import eligibility
from apps.compliance.services import add_suppression, expire_suppression
from apps.crm.services import LeadStateService
from apps.jobs.models import Job
from apps.jobs.runner import JobRunner
from apps.outreach.models import MessageEvent, OutreachMessage, OutreachSettings, SalesTask
from apps.outreach.services import (
    approve_message,
    complete_task,
    configure,
    enqueue_message,
    pause,
    prepare_message,
)
from integrations.email.mock import MockEmailProvider
from tests.test_phase1 import domain as domain
from tests.test_proposals import generate, transition
from tests.test_proposals import proposals as proposals

pytestmark = pytest.mark.django_db


@pytest.fixture
def outreach(proposals, settings):
    settings.CONTACT_COOLDOWN_DAYS = 0
    settings.MAX_EMAILS_PER_DAY, settings.MAX_WHATSAPP_PER_DAY = 25, 10
    member, lead = proposals["member"], proposals["lead"]
    type(lead.campaign).objects.filter(pk=lead.campaign_id).update(status="READY")
    for status in ("QUALIFIED", "READY"):
        LeadStateService.transition(
            user=member.user, workspace_id=member.workspace_id, pk=lead.pk, target=status
        )
    for channel in ("EMAIL", "WHATSAPP", "PHONE", "OTHER"):
        CampaignChannel.objects.create(
            workspace=member.workspace,
            campaign=lead.campaign,
            channel=channel,
            max_attempts=3,
            cooldown_days=0,
        )
    contact = Contact.objects.create(
        workspace=member.workspace,
        company=lead.company,
        full_name="Contacto empresarial ficticio",
        email="info@clinic.example.invalid",
        phone="+34981000000",
        whatsapp_e164="+34600123456",
    )
    proposal = transition(
        proposals, transition(proposals, generate(proposals), "REVIEWED"), "APPROVED"
    )
    return {**proposals, "contact": contact, "proposal": proposal}


def prepare(env, channel="EMAIL", key=None, **kwargs):
    member = env["member"]
    return prepare_message(
        user=member.user,
        workspace_id=member.workspace_id,
        proposal_id=env["proposal"].pk,
        contact_id=env["contact"].pk,
        channel=channel,
        idempotency_key=key or str(uuid.uuid4()),
        **kwargs,
    )


def approve(env, message):
    member = env["member"]
    return approve_message(
        user=member.user,
        workspace_id=member.workspace_id,
        pk=message.pk,
        expected_revision=message.revision,
        policy_review_confirmed=True,
    )


def queue(env, message):
    member = env["member"]
    return enqueue_message(user=member.user, workspace_id=member.workspace_id, pk=message.pk)


def fresh_proposal(env):
    return transition(env, transition(env, generate(env), "REVIEWED"), "APPROVED")


def test_message_send_is_idempotent(outreach):
    message = approve(outreach, prepare(outreach, key="stable"))
    job = queue(outreach, message)
    assert queue(outreach, message).pk == job.pk
    with patch(
        "apps.outreach.services.outbound_provider",
        wraps=__import__(
            "integrations.common.factory", fromlist=["outbound_provider"]
        ).outbound_provider,
    ) as provider:
        assert JobRunner().run_once().status == "SUCCEEDED"
        message.refresh_from_db()
        assert message.status == "SENT" and message.provider_message_id.startswith("mock-email-")
        Job.objects.filter(pk=job.pk).update(status="PENDING")
        assert JobRunner().run_once().status == "SUCCEEDED"
        assert provider.call_count == 1
    assert message.events.filter(event_type="SENT").count() == 1
    outreach["lead"].refresh_from_db()
    assert outreach["lead"].status == "CONTACTED" and outreach["lead"].last_contact_at
    outreach["proposal"].refresh_from_db()
    assert outreach["proposal"].status == "SENT"


def test_unapproved_message_cannot_be_sent(outreach):
    message = prepare(outreach)
    with pytest.raises(ValidationError):
        queue(outreach, message)
    member = outreach["member"]
    with pytest.raises(ValidationError):
        approve_message(
            user=member.user,
            workspace_id=member.workspace_id,
            pk=message.pk,
            expected_revision=message.revision,
            policy_review_confirmed=False,
        )
    assert not Job.objects.filter(kind="SEND_OUTREACH").exists()


def test_suppressed_contact_cannot_be_sent_message(outreach):
    member = outreach["member"]
    message = approve(outreach, prepare(outreach))
    add_suppression(
        user=member.user,
        workspace_id=member.workspace_id,
        scope="EMAIL",
        value=" INFO@CLINIC.EXAMPLE.INVALID ",
        reason="UNSUBSCRIBED",
        source="Prueba ficticia",
    )
    with patch(
        "apps.outreach.services.outbound_provider", side_effect=AssertionError("provider forbidden")
    ):
        assert queue(outreach, message) is None
    message.refresh_from_db()
    assert message.status == "SUPPRESSED"
    assert "SUPPRESSED" in message.eligibility_decisions.first().reasons["codes"]


def test_new_suppression_after_enqueue_blocks_worker(outreach):
    member = outreach["member"]
    message = approve(outreach, prepare(outreach))
    queue(outreach, message)
    add_suppression(
        user=member.user,
        workspace_id=member.workspace_id,
        scope="COMPANY",
        value="",
        company_id=outreach["company"].pk,
        reason="DO_NOT_CONTACT",
        source="Operador",
    )
    with patch(
        "apps.outreach.services.outbound_provider", side_effect=AssertionError("provider forbidden")
    ):
        assert JobRunner().run_once().status == "SUCCEEDED"
    message.refresh_from_db()
    assert message.status == "SUPPRESSED" and not message.sent_at


def test_simulation_mode_never_calls_live_provider(outreach, settings):
    message = approve(outreach, prepare(outreach))
    queue(outreach, message)
    settings.OUTREACH_MODE = "live"
    settings.EMAIL_PROVIDER = "gmail"
    OutreachSettings.objects.filter(workspace=outreach["member"].workspace).update(
        live_mode_enabled=True
    )
    with patch(
        "apps.outreach.services.outbound_provider", side_effect=AssertionError("live forbidden")
    ):
        assert JobRunner().run_once().status == "SUCCEEDED"
    message.refresh_from_db()
    assert "LIVE_PROVIDER_DISABLED" in message.error_code


@pytest.mark.parametrize("scope,value", [("DOMAIN", "example.invalid"), ("PHONE", "+34981000000")])
def test_normalized_scope_blocks_across_channels_and_expiry_is_explicit(outreach, scope, value):
    member = outreach["member"]
    message = approve(outreach, prepare(outreach, channel="WHATSAPP"))
    block = add_suppression(
        user=member.user,
        workspace_id=member.workspace_id,
        scope=scope,
        value=value,
        reason="MANUAL_BLOCK",
        source="Manual",
    )
    assert "SUPPRESSED" in eligibility(message)[1]
    expire_suppression(user=member.user, workspace_id=member.workspace_id, pk=block.pk)
    assert "SUPPRESSED" not in eligibility(message)[1]
    add_suppression(
        user=member.user,
        workspace_id=member.workspace_id,
        scope=scope,
        value=value,
        reason="MANUAL_BLOCK",
        source="Reactivado",
    )
    assert SuppressionEntry.objects.count() == 1
    assert "SUPPRESSED" in eligibility(message)[1]


def test_cooldown_daily_and_company_attempt_limits(outreach):
    message = approve(outreach, prepare(outreach))
    queue(outreach, message)
    assert JobRunner().run_once().status == "SUCCEEDED"
    outreach["proposal"] = fresh_proposal(outreach)
    next_message = approve(outreach, prepare(outreach))
    member = outreach["member"]
    config = OutreachSettings.objects.get(workspace=member.workspace)
    for data, reason in (
        ({"default_cooldown_days": 14}, "COOLDOWN"),
        ({"default_cooldown_days": 0, "daily_email_limit": 1}, "DAILY_LIMIT"),
        ({"daily_email_limit": 25, "max_attempts_per_company": 1}, "ATTEMPT_LIMIT"),
    ):
        config = configure(
            user=member.user,
            workspace_id=member.workspace_id,
            data=data,
            expected_revision=config.revision,
        )
        assert reason in eligibility(next_message)[1]
    assert queue(outreach, next_message) is None


def test_pause_and_resume_preserve_data_but_block_pending_jobs(outreach):
    member = outreach["member"]
    message = approve(outreach, prepare(outreach))
    job = queue(outreach, message)
    pause(user=member.user, workspace_id=member.workspace_id, paused=True)
    with patch("apps.outreach.services.outbound_provider", side_effect=AssertionError("paused")):
        assert JobRunner().run_once().status == "SUCCEEDED"
    message.refresh_from_db()
    assert message.status == "SUPPRESSED" and "EMERGENCY_PAUSE" in message.error_code
    assert Job.objects.filter(pk=job.pk).exists()
    pause(user=member.user, workspace_id=member.workspace_id, paused=False)
    assert OutreachMessage.objects.count() == 1


def test_whatsapp_never_infers_phone_and_contact_changes_invalidate_approval(outreach):
    contact = outreach["contact"]
    contact.whatsapp_e164 = ""
    contact.save()
    message = approve(outreach, prepare(outreach, channel="WHATSAPP"))
    assert "RECIPIENT_MISSING" in eligibility(message)[1]
    email = approve(outreach, prepare(outreach))
    contact.email = "changed@clinic.example.invalid"
    contact.save()
    assert "RECIPIENT_CHANGED" in eligibility(email)[1]
    OutreachMessage.objects.filter(pk=email.pk).update(body_text="changed")
    email.refresh_from_db()
    assert "CONTENT_CHANGED" in eligibility(email)[1]


@pytest.mark.parametrize("channel", ["PHONE", "CONTACT_FORM", "LINKEDIN", "INSTAGRAM", "OTHER"])
def test_manual_channels_create_tasks_without_provider_calls(outreach, channel):
    from apps.companies.models import SocialProfile

    member = outreach["member"]
    if channel not in {"PHONE", "OTHER"}:
        CampaignChannel.objects.create(
            workspace=member.workspace,
            campaign=outreach["campaign"],
            channel=channel,
            max_attempts=3,
            cooldown_days=0,
        )
    if channel == "CONTACT_FORM":
        outreach["company"].website = "https://clinic.example.invalid/"
        outreach["company"].save()
    if channel in {"LINKEDIN", "INSTAGRAM"}:
        SocialProfile.objects.create(
            workspace=member.workspace,
            company=outreach["company"],
            platform=channel,
            url=f"https://{channel.lower()}.com/agencycrm_fixture",
        )
    message = approve(outreach, prepare(outreach, channel=channel))
    queue(outreach, message)
    with patch(
        "apps.outreach.services.outbound_provider", side_effect=AssertionError("manual only")
    ):
        assert JobRunner().run_once().status == "SUCCEEDED"
    task = SalesTask.objects.get()
    assert task.type == ("CALL" if channel == "PHONE" else channel) and task.status == "PENDING"
    task = complete_task(
        user=member.user,
        workspace_id=member.workspace_id,
        pk=task.pk,
        outcome="Llamada de prueba realizada manualmente.",
    )
    assert task.status == "COMPLETED"
    message.refresh_from_db()
    assert message.status == "SENT" and message.provider_message_id.startswith("manual:")
    assert (
        complete_task(
            user=member.user, workspace_id=member.workspace_id, pk=task.pk, outcome="Duplicado"
        ).status
        == "COMPLETED"
    )
    assert message.events.filter(event_type="SENT").count() == 1


def test_mock_retry_is_bounded_and_records_failure(outreach):
    message = approve(outreach, prepare(outreach))
    job = queue(outreach, message)
    with patch(
        "apps.outreach.services.outbound_provider",
        return_value=MockEmailProvider(failure="temporary"),
    ):
        assert JobRunner().run_once().status == "PENDING"
    message.refresh_from_db()
    assert message.status == "FAILED" and message.events.filter(event_type="FAILED").count() == 1
    Job.objects.filter(pk=job.pk).update(available_at=timezone.now())
    assert JobRunner().run_once().status == "SUCCEEDED"
    message.refresh_from_db()
    assert message.status == "SENT" and message.send_attempts == 2


def test_workspace_data_and_approval_roles_are_enforced(outreach):
    from django.http import Http404

    member, foreign = outreach["member"], outreach["foreign"]
    message = prepare(outreach)
    with pytest.raises(Http404):
        enqueue_message(user=foreign.user, workspace_id=foreign.workspace_id, pk=message.pk)
    member.role = "MEMBER"
    member.save()
    with pytest.raises(PermissionDenied):
        approve(outreach, message)
    member.role = "OWNER"
    member.save()
    message = approve(outreach, message)
    queue(outreach, message)
    member.is_active = False
    member.save()
    assert JobRunner().run_once().status == "FAILED"
    message.refresh_from_db()
    assert message.status == "FAILED"


def test_message_ui_csrf_and_operator_controls(outreach):
    member = outreach["member"]
    client = Client(enforce_csrf_checks=True)
    url = reverse("outreach:prepare", args=[member.workspace_id])
    assert client.get(url).status_code == 302
    client.force_login(member.user)
    assert client.get(url).status_code == 200
    token = client.cookies["csrftoken"].value
    data = {
        "proposal": outreach["proposal"].pk,
        "contact": outreach["contact"].pk,
        "channel": "EMAIL",
        "idempotency_key": uuid.uuid4(),
        "body_text": "<script>untrusted</script>",
    }
    assert client.post(url, data).status_code == 403
    data["csrfmiddlewaretoken"] = token
    assert client.post(url, data).status_code == 302
    message = OutreachMessage.objects.get()
    detail = reverse("outreach:message", args=[member.workspace_id, message.pk])
    assert b"<script>" not in client.get(detail).content
    assert (
        client.post(
            detail,
            {
                "action": "approve",
                "expected_revision": 0,
                "policy_review_confirmed": "on",
                "csrfmiddlewaretoken": token,
            },
        ).status_code
        == 302
    )
    assert (
        client.post(detail, {"action": "enqueue", "csrfmiddlewaretoken": token}).status_code == 302
    )
    assert JobRunner().run_once().status == "SUCCEEDED"
    for route in ("outbox", "suppression", "settings", "tasks"):
        assert (
            client.get(reverse(f"outreach:{route}", args=[member.workspace_id])).status_code == 200
        )
    client.force_login(outreach["foreign"].user)
    assert client.get(detail).status_code == 404


@pytest.mark.parametrize(
    "change,reason",
    [
        ("not_business", "NOT_BUSINESS_CONTACT"),
        ("do_not_contact", "SUPPRESSED"),
        ("campaign_paused", "CAMPAIGN_OR_COMPANY_INACTIVE"),
        ("replied", "LEAD_NOT_READY"),
        ("channel_disabled", "CHANNEL_DISABLED"),
        ("approver_inactive", "APPROVER_INACTIVE"),
        ("proposal_changed", "PROPOSAL_APPROVAL_REQUIRED"),
        ("not_due", "NOT_DUE"),
    ],
)
def test_policy_uses_current_facts(outreach, change, reason):
    from apps.crm.models import Lead
    from apps.proposals.models import Proposal

    message = approve(outreach, prepare(outreach))
    if change == "not_business":
        Contact.objects.filter(pk=outreach["contact"].pk).update(is_business_contact=False)
    elif change == "do_not_contact":
        Contact.objects.filter(pk=outreach["contact"].pk).update(do_not_contact=True)
    elif change == "campaign_paused":
        type(outreach["campaign"]).objects.filter(pk=outreach["campaign"].pk).update(
            status="PAUSED"
        )
    elif change == "replied":
        Lead.objects.filter(pk=outreach["lead"].pk).update(status="REPLIED")
    elif change == "channel_disabled":
        CampaignChannel.objects.filter(campaign=outreach["campaign"], channel="EMAIL").update(
            enabled=False
        )
    elif change == "approver_inactive":
        outreach["member"].role = "MEMBER"
        outreach["member"].save()
    elif change == "proposal_changed":
        Proposal.objects.filter(pk=outreach["proposal"].pk).update(status="SUPERSEDED")
    elif change == "not_due":
        OutreachMessage.objects.filter(pk=message.pk).update(
            scheduled_at=timezone.now() + timedelta(days=1)
        )
    message.refresh_from_db()
    assert reason in eligibility(message)[1]


def test_daily_zero_and_whatsapp_limit_are_enforced(outreach):
    member = outreach["member"]
    message = approve(outreach, prepare(outreach, channel="WHATSAPP"))
    config = OutreachSettings.objects.get(workspace=member.workspace)
    configure(
        user=member.user,
        workspace_id=member.workspace_id,
        data={"daily_whatsapp_limit": 0},
        expected_revision=config.revision,
    )
    assert "DAILY_LIMIT" in eligibility(message)[1]
    assert queue(outreach, message) is None


def test_approval_hash_and_events_are_immutable(outreach):
    message = approve(outreach, prepare(outreach))
    with pytest.raises(ValidationError):
        message.save()
    with pytest.raises(ValidationError):
        message.delete()
    queue(outreach, message)
    recorded = message.events.get(event_type="QUEUED")
    with pytest.raises(ValidationError):
        recorded.save()
    with pytest.raises(ValidationError):
        message.events.all().delete()


def test_manual_completion_rechecks_pause_and_cancels_task(outreach):
    member = outreach["member"]
    message = approve(outreach, prepare(outreach, channel="OTHER"))
    queue(outreach, message)
    JobRunner().run_once()
    task = SalesTask.objects.get()
    pause(user=member.user, workspace_id=member.workspace_id, paused=True)
    assert (
        complete_task(
            user=member.user, workspace_id=member.workspace_id, pk=task.pk, outcome="Intento manual"
        ).status
        == "CANCELLED"
    )
    assert not MessageEvent.objects.filter(event_type="SEND_ATTEMPT").exists()


def test_cancellation_is_projected_to_message(outreach):
    from apps.jobs.services import JobService

    member = outreach["member"]
    message = approve(outreach, prepare(outreach))
    job = queue(outreach, message)
    JobService.cancel(user=member.user, workspace_id=member.workspace_id, pk=job.pk)
    message.refresh_from_db()
    assert message.status == "CANCELLED"
    assert message.events.filter(event_type="CANCELLED").exists()


@pytest.mark.django_db(transaction=True)
def test_mock_transport_is_outside_transaction_and_late_pause_blocks_receipt(outreach):
    from django.db import connection

    member = outreach["member"]
    message = approve(outreach, prepare(outreach))
    queue(outreach, message)
    original = MockEmailProvider()

    def send(request):
        assert not connection.in_atomic_block
        assert request.attachments[0].data.startswith(b"%PDF-")
        result = original.send(request)
        pause(user=member.user, workspace_id=member.workspace_id, paused=True)
        return result

    fake = Mock()
    fake.send.side_effect = send
    with patch("apps.outreach.services.outbound_provider", return_value=fake):
        assert JobRunner().run_once().status == "SUCCEEDED"
    message.refresh_from_db()
    assert message.status == "SUPPRESSED" and not message.sent_at


def test_full_mock_pipeline_and_second_attempt_suppression(outreach):
    from apps.companies.enrichment import enqueue_enrichment
    from apps.crm.models import Lead
    from apps.dashboard.selectors import dashboard_for_user
    from apps.proposals.services import enqueue_proposal
    from apps.scoring.services import enqueue_score
    from tests.test_prospecting import plan_for

    member, campaign = outreach["member"], outreach["campaign"]
    type(campaign).objects.filter(pk=campaign.pk).update(
        target_query="dentistas", target_location_text="A Coruña", desired_leads=3
    )
    plan_for(member, campaign)
    assert JobRunner().run_once().status == "SUCCEEDED"
    lead = Lead.objects.filter(
        workspace=member.workspace, campaign=campaign, company__name="Clínica Atlántico"
    ).get()
    enqueue_enrichment(
        user=member.user,
        workspace_id=member.workspace_id,
        company_id=lead.company_id,
        idempotency_key="e2e-enrich",
    )
    assert JobRunner().run_once().status == "SUCCEEDED"
    enqueue_score(
        user=member.user,
        workspace_id=member.workspace_id,
        lead_id=lead.pk,
        idempotency_key="e2e-score",
    )
    assert JobRunner().run_once().status == "SUCCEEDED"
    enqueue_proposal(
        user=member.user,
        workspace_id=member.workspace_id,
        lead_id=lead.pk,
        idempotency_key="e2e-proposal",
    )
    assert JobRunner().run_once().status == "SUCCEEDED"
    prop = lead.proposals.first()
    prop = transition(outreach, transition(outreach, prop, "REVIEWED"), "APPROVED")
    for status in ("QUALIFIED", "READY"):
        LeadStateService.transition(
            user=member.user, workspace_id=member.workspace_id, pk=lead.pk, target=status
        )
    contact = lead.company.contacts.exclude(normalized_email="").get()
    env = {**outreach, "lead": lead, "company": lead.company, "contact": contact, "proposal": prop}
    message = approve(env, prepare(env))
    queue(env, message)
    assert JobRunner().run_once().status == "SUCCEEDED"
    dashboard = dashboard_for_user(user=member.user, workspace_id=member.workspace_id)
    assert dashboard.companies_total == dashboard.leads_total == 4
    assert {count.status: count.value for count in dashboard.lead_counts}["CONTACTED"] == 1
    assert dashboard.jobs_failed == dashboard.messages_pending == 0
    env["proposal"] = fresh_proposal(env)
    next_message = approve(env, prepare(env))
    add_suppression(
        user=member.user,
        workspace_id=member.workspace_id,
        scope="COMPANY",
        value="",
        company_id=lead.company_id,
        reason="MANUAL_BLOCK",
        source="Fixture E2E",
    )
    assert queue(env, next_message) is None
    next_message.refresh_from_db()
    assert next_message.status == "SUPPRESSED"


def test_idempotent_preparation_validation_and_stale_settings(outreach):
    member = outreach["member"]
    first = prepare(outreach, key="same")
    assert prepare(outreach, key="same").pk == first.pk
    with pytest.raises(ValidationError):
        prepare(outreach, key="same", body_text="different intent")
    with pytest.raises(ValidationError):
        prepare(outreach, key=" ")
    config = OutreachSettings.objects.get(workspace=member.workspace)
    with pytest.raises(ValidationError):
        configure(
            user=member.user,
            workspace_id=member.workspace_id,
            data={"live_mode_enabled": True},
            expected_revision=config.revision,
        )
    approved = approve(outreach, first)
    assert approve(outreach, approved).pk == first.pk


def test_suppression_settings_and_tasks_ui(outreach, client):
    member = outreach["member"]
    client.force_login(member.user)
    url = reverse("outreach:suppression", args=[member.workspace_id])
    assert (
        client.post(
            url,
            {
                "scope": "EMAIL",
                "value": "info@clinic.example.invalid",
                "source": "Operador",
                "reason": "MANUAL_BLOCK",
            },
        ).status_code
        == 302
    )
    message = prepare(outreach)
    detail = reverse("outreach:message", args=[member.workspace_id, message.pk])
    assert client.post(detail, {"action": "evaluate"}).status_code == 302
    assert client.get(detail).status_code == 200
    block = SuppressionEntry.objects.get()
    assert (
        client.post(reverse("outreach:expire", args=[member.workspace_id, block.pk])).status_code
        == 302
    )
    assert (
        client.post(
            url,
            {"scope": "EMAIL", "value": "invalid", "source": "Operador", "reason": "MANUAL_BLOCK"},
        ).status_code
        == 400
    )
    settings_url = reverse("outreach:settings", args=[member.workspace_id])
    config = OutreachSettings.objects.get(workspace=member.workspace)
    assert (
        client.post(
            settings_url,
            {
                "daily_email_limit": 20,
                "daily_whatsapp_limit": 10,
                "default_cooldown_days": 0,
                "max_attempts_per_company": 3,
                "expected_revision": config.revision,
            },
        ).status_code
        == 302
    )
    assert client.post(settings_url, {"action": "pause"}).status_code == 302
    assert client.post(settings_url, {"action": "resume"}).status_code == 302
    message = approve(outreach, prepare(outreach, channel="PHONE"))
    queue(outreach, message)
    assert JobRunner().run_once().status == "SUCCEEDED"
    task = SalesTask.objects.get()
    tasks_url = reverse("outreach:tasks", args=[member.workspace_id])
    assert client.post(tasks_url, {"task_id": "bad", "outcome": "test"}).status_code == 400
    assert (
        client.post(
            tasks_url, {"task_id": task.pk, "outcome": "Acción manual de prueba"}
        ).status_code
        == 302
    )
    assert client.get(tasks_url).status_code == 200


def test_send_failure_is_sanitized_and_audited(outreach):
    message = approve(outreach, prepare(outreach))
    queue(outreach, message)
    with patch(
        "apps.outreach.services.outbound_provider",
        return_value=MockEmailProvider(failure="permanent"),
    ):
        assert JobRunner().run_once().status == "FAILED"
    message.refresh_from_db()
    assert message.status == "FAILED" and message.error_code == "ProviderPermanentError"


def test_suppression_during_attachment_validation_prevents_transport(outreach):
    from apps.outreach.services import read_attachments

    member = outreach["member"]
    message = approve(outreach, prepare(outreach))
    queue(outreach, message)

    def block_while_reading(message, user):
        files = read_attachments(message, user)
        add_suppression(
            user=member.user,
            workspace_id=member.workspace_id,
            scope="COMPANY",
            value="",
            company_id=outreach["company"].pk,
            reason="MANUAL_BLOCK",
            source="Operador",
        )
        return files

    with (
        patch("apps.outreach.services.read_attachments", side_effect=block_while_reading),
        patch(
            "apps.outreach.services.outbound_provider", side_effect=AssertionError("must not call")
        ),
    ):
        assert JobRunner().run_once().status == "SUCCEEDED"
    message.refresh_from_db()
    assert message.status == "SUPPRESSED"


def test_user_account_revocation_is_rechecked_after_attachment_validation(outreach):
    from apps.outreach.services import read_attachments

    member = outreach["member"]
    message = approve(outreach, prepare(outreach))
    queue(outreach, message)

    def disable(message, user):
        files = read_attachments(message, user)
        type(member.user).objects.filter(pk=member.user.pk).update(is_active=False)
        return files

    with (
        patch("apps.outreach.services.read_attachments", side_effect=disable),
        patch(
            "apps.outreach.services.outbound_provider", side_effect=AssertionError("must not call")
        ),
    ):
        assert JobRunner().run_once().status == "FAILED"
    message.refresh_from_db()
    assert message.status == "FAILED" and not message.sent_at


def test_daily_limit_uses_configured_timezone_not_current_user_timezone(outreach, settings):
    from datetime import datetime, time
    from zoneinfo import ZoneInfo

    settings.TIME_ZONE = "Europe/Madrid"
    member = outreach["member"]
    message = approve(outreach, prepare(outreach))
    config = OutreachSettings.objects.get(workspace=member.workspace)
    configure(
        user=member.user,
        workspace_id=member.workspace_id,
        data={"daily_email_limit": 1},
        expected_revision=config.revision,
    )
    zone = ZoneInfo("Europe/Madrid")
    midnight = datetime.combine(timezone.localtime(timezone.now(), zone).date(), time.min, zone)
    before, after = midnight - timedelta(minutes=1), midnight + timedelta(minutes=1)
    OutreachMessage.objects.filter(pk=message.pk).update(scheduled_at=before)
    message.refresh_from_db()
    MessageEvent.objects.create(
        workspace=member.workspace,
        message=message,
        event_type="SEND_ATTEMPT",
        provider_event_id="previous-day",
        occurred_at=before,
    )
    with timezone.override("America/Los_Angeles"):
        assert "DAILY_LIMIT" not in eligibility(message, now=after)[1]
        MessageEvent.objects.create(
            workspace=member.workspace,
            message=message,
            event_type="SEND_ATTEMPT",
            provider_event_id="current-day",
            occurred_at=after,
        )
        assert "DAILY_LIMIT" in eligibility(message, now=after)[1]


def test_domain_suppression_normalizes_international_email_domain(outreach):
    member = outreach["member"]
    contact = outreach["contact"]
    contact.email = "info@clínica.example.invalid"
    contact.save()
    message = approve(outreach, prepare(outreach))
    add_suppression(
        user=member.user,
        workspace_id=member.workspace_id,
        scope="DOMAIN",
        value="clínica.example.invalid",
        reason="MANUAL_BLOCK",
        source="Operador",
    )
    assert "SUPPRESSED" in eligibility(message)[1]
