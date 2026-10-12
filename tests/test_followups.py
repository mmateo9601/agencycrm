from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.http import Http404
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Membership
from apps.audit.models import AuditEvent
from apps.campaigns.models import Campaign, CampaignChannel
from apps.campaigns.services import change_campaign_status
from apps.companies.services import save_contact
from apps.compliance.policies import eligibility
from apps.compliance.services import add_suppression, expire_suppression
from apps.crm.models import Lead
from apps.crm.services import LeadStateService, save_lead
from apps.jobs.runner import JobRunner
from apps.outreach.followups import act_on_task, reconcile, save_rule, schedule_source
from apps.outreach.models import MessageEvent, OutreachMessage, SalesTask
from apps.outreach.services import configure, pause
from tests.test_outreach import approve, prepare, queue
from tests.test_outreach import domain as domain
from tests.test_outreach import outreach as outreach
from tests.test_outreach import proposals as proposals

pytestmark = pytest.mark.django_db


def rule(env, **changes):
    member = env["member"]
    return save_rule(
        user=member.user,
        workspace_id=member.workspace_id,
        data={
            "name": "Revisión prudente",
            "enabled": True,
            "delay_days": 1,
            "interval_days": 7,
            "max_steps": 2,
            **changes,
        },
    )


def sent(env):
    message = approve(env, prepare(env))
    queue(env, message)
    assert JobRunner().run_once().status == "SUCCEEDED"
    message.refresh_from_db()
    return message


@pytest.fixture
def followups(outreach, settings):
    settings.CONTACT_COOLDOWN_DAYS = 14
    configured = rule(outreach)
    source = sent(outreach)
    return {**outreach, "rule": configured, "source": source}


def act(env, task, action="complete", **data):
    return act_on_task(
        user=env["member"].user,
        workspace_id=env["member"].workspace_id,
        pk=task.pk,
        action=action,
        expected_revision=task.revision,
        outcome="Revisado; decidir manualmente el próximo contacto",
        **data,
    )


def schedule(env, source=None):
    return schedule_source(
        user=env["member"].user,
        workspace_id=env["member"].workspace_id,
        source_id=(source or env["source"]).pk,
    )


def test_confirmed_contact_schedules_bounded_idempotent_reviews(followups):
    env = followups
    tasks = list(SalesTask.objects.order_by("sequence"))
    assert len(tasks) == 2 and tasks[0].message_id is None
    assert all(task.source_message_id == env["source"].pk for task in tasks)
    assert tasks[0].due_at >= env["source"].sent_at + timedelta(days=14)
    assert tasks[1].due_at == tasks[0].due_at + timedelta(days=7)
    assert schedule(env) == 0
    env["lead"].refresh_from_db()
    assert env["lead"].next_action_at == tasks[0].due_at
    assert env["lead"].next_action_managed
    assert OutreachMessage.objects.count() == 1
    assert MessageEvent.objects.filter(event_type="SEND_ATTEMPT").count() == 1


def test_rules_disabled_by_default_and_unconfirmed_source_rejected(outreach):
    rule(outreach, enabled=False)
    message = prepare(outreach)
    with pytest.raises(ValidationError):
        schedule_source(
            user=outreach["member"].user,
            workspace_id=outreach["member"].workspace_id,
            source_id=message.pk,
        )
    approve(outreach, message)
    queue(outreach, message)
    JobRunner().run_once()
    assert not SalesTask.objects.exists()


def test_review_completion_never_sends_and_advances_next_action(followups):
    task = SalesTask.objects.order_by("sequence").first()
    before = followups["source"].sent_at
    with (
        patch("django.utils.timezone.now", return_value=task.due_at + timedelta(seconds=1)),
        patch(
            "apps.outreach.services.outbound_provider",
            side_effect=AssertionError("No transport allowed"),
        ),
    ):
        assert act(followups, task).status == "COMPLETED"
    followups["lead"].refresh_from_db()
    assert followups["lead"].last_contact_at == before
    assert followups["lead"].next_action_at == SalesTask.objects.get(sequence=2).due_at
    assert MessageEvent.objects.filter(event_type="SEND_ATTEMPT").count() == 1


def test_cooldown_checked_again_after_new_company_contact(followups):
    task = SalesTask.objects.order_by("sequence").first()
    future = task.due_at + timedelta(days=1)
    Lead.objects.filter(pk=task.lead_id).update(last_contact_at=future)
    with patch("django.utils.timezone.now", return_value=future):
        with pytest.raises(ValidationError, match="cooldown"):
            act(followups, task)
        reconcile(workspace_id=task.workspace_id)
    task.refresh_from_db()
    assert task.due_at >= future + timedelta(days=14)


@pytest.mark.parametrize("target", ["REPLIED", "MEETING", "WON", "LOST"])
def test_response_meeting_and_closed_cancel_pending_reviews(followups, target):
    member, lead = followups["member"], followups["lead"]
    if target == "WON":
        LeadStateService.transition(
            user=member.user, workspace_id=member.workspace_id, pk=lead.pk, target="MEETING"
        )
    LeadStateService.transition(
        user=member.user,
        workspace_id=member.workspace_id,
        pk=lead.pk,
        target=target,
        reason="Operador confirma estado",
    )
    assert not SalesTask.objects.filter(status="PENDING").exists()
    lead.refresh_from_db()
    assert lead.next_action_at is None
    with pytest.raises(ValidationError):
        schedule(followups)


def test_company_response_in_other_campaign_stops_followup(followups):
    member = followups["member"]
    campaign = Campaign.objects.create(
        workspace=member.workspace, name="Otra campaña", status="READY"
    )
    lead = save_lead(
        user=member.user,
        workspace_id=member.workspace_id,
        data={"campaign": campaign, "company": followups["lead"].company},
    )
    # Historical company response is deliberately on a different campaign.
    Lead.objects.filter(pk=lead.pk).update(status="MEETING")
    task = SalesTask.objects.first()
    assert act(followups, task, action="cancel").status == "CANCELLED"
    reconcile(workspace_id=member.workspace_id)
    assert not SalesTask.objects.filter(status="PENDING").exists()
    assert "COMPANY_RESPONSE_OR_CLOSED" in eligibility(followups["source"])[1]


@pytest.mark.parametrize("scope", ["EMAIL", "PHONE", "COMPANY"])
def test_suppression_cancels_and_expiration_never_resurrects(followups, scope):
    env, member = followups, followups["member"]
    value = {
        "EMAIL": env["contact"].normalized_email,
        "PHONE": env["contact"].whatsapp_e164,
        "COMPANY": str(env["lead"].company_id),
    }[scope]
    entry = add_suppression(
        user=member.user,
        workspace_id=member.workspace_id,
        scope=scope,
        value=value,
        reason="DO_NOT_CONTACT",
        source="operator",
        company_id=env["lead"].company_id if scope == "COMPANY" else None,
    )
    assert not SalesTask.objects.filter(status="PENDING").exists()
    expire_suppression(user=member.user, workspace_id=member.workspace_id, pk=entry.pk)
    assert schedule(env) == 0
    assert SalesTask.objects.filter(status="CANCELLED").count() == 2


@pytest.mark.parametrize("change", ["pause", "archive", "contact", "disable", "author"])
def test_policy_changes_stop_tasks(followups, change):
    env, member = followups, followups["member"]
    if change == "pause":
        pause(user=member.user, workspace_id=member.workspace_id, paused=True)
    elif change == "archive":
        change_campaign_status(
            user=member.user,
            workspace_id=member.workspace_id,
            pk=env["lead"].campaign_id,
            target="ARCHIVED",
        )
    elif change == "contact":
        save_contact(
            user=member.user,
            workspace_id=member.workspace_id,
            pk=env["contact"].pk,
            data={"do_not_contact": True},
        )
    elif change == "disable":
        save_rule(
            user=member.user,
            workspace_id=member.workspace_id,
            pk=env["rule"].pk,
            expected_revision=env["rule"].revision,
            data={"enabled": False},
        )
    else:
        Membership.objects.filter(pk=member.pk).update(role="SALES")
        reconcile(workspace_id=member.workspace_id)
    assert SalesTask.objects.filter(status="CANCELLED").count() == 2


def test_manual_next_action_preserved(followups):
    lead, member = followups["lead"], followups["member"]
    manual = timezone.now() + timedelta(days=50)
    save_lead(
        user=member.user,
        workspace_id=member.workspace_id,
        pk=lead.pk,
        data={"next_action_at": manual},
    )
    for task in SalesTask.objects.all():
        act(followups, task, action="cancel")
    lead.refresh_from_db()
    assert lead.next_action_at == manual and not lead.next_action_managed


def test_rule_scope_validation_permissions_and_stale_revision(followups):
    env, member = followups, followups["member"]
    with pytest.raises(Http404):
        rule(env, name="Ajena", campaign=env["other_campaign"])
    with pytest.raises(ValidationError):
        rule(env, name="Inválida", max_steps=11)
    with pytest.raises(ValidationError):
        save_rule(
            user=member.user,
            workspace_id=member.workspace_id,
            pk=env["rule"].pk,
            expected_revision=0,
            data={"name": "Antigua"},
        )
    Membership.objects.filter(pk=member.pk).update(role="VIEWER")
    with pytest.raises(PermissionDenied):
        rule(env, name="Sin permiso")
    with pytest.raises(PermissionDenied):
        act(env, SalesTask.objects.first(), action="cancel")


def test_cannot_schedule_or_act_in_foreign_workspace(followups):
    env = followups
    with pytest.raises(Http404):
        schedule_source(
            user=env["foreign"].user,
            workspace_id=env["foreign"].workspace_id,
            source_id=env["source"].pk,
        )
    with pytest.raises(Http404):
        act_on_task(
            user=env["foreign"].user,
            workspace_id=env["foreign"].workspace_id,
            pk=SalesTask.objects.first().pk,
            action="cancel",
            expected_revision=0,
        )


def test_task_rescheduling_requires_cooldown_and_current_revision(followups):
    task = SalesTask.objects.order_by("sequence").first()
    with pytest.raises(ValidationError):
        act(followups, task, action="reschedule", due_at=timezone.now())
    with pytest.raises(ValidationError):
        act(followups, task, action="reschedule", due_at=timezone.now().replace(tzinfo=None))
    new = task.due_at + timedelta(days=3)
    assert act(followups, task, action="reschedule", due_at=new).due_at == new
    with pytest.raises(ValidationError):
        act(followups, task, action="cancel")


def test_increased_channel_cooldown_extends_dates(followups):
    task = SalesTask.objects.order_by("sequence").first()
    CampaignChannel.objects.filter(campaign_id=task.lead.campaign_id, channel="EMAIL").update(
        cooldown_days=30
    )
    reconcile(workspace_id=task.workspace_id)
    task.refresh_from_db()
    assert task.due_at == followups["source"].sent_at + timedelta(days=30)


def test_scheduler_rollback_on_audit_failure(outreach):
    source = sent(outreach)
    rule(outreach)
    with patch(
        "apps.outreach.followups.record_event", side_effect=RuntimeError("Audit unavailable")
    ):
        with pytest.raises(RuntimeError):
            schedule_source(
                user=outreach["member"].user,
                workspace_id=outreach["member"].workspace_id,
                source_id=source.pk,
            )
    assert not SalesTask.objects.exists()


def test_ui_scoped_readonly_get_and_csrf(followups):
    member = followups["member"]
    client = Client(enforce_csrf_checks=True)
    client.force_login(member.user)
    rules_url = reverse("outreach:followup_rules", kwargs={"workspace_id": member.workspace_id})
    tasks_url = reverse("outreach:tasks", kwargs={"workspace_id": member.workspace_id})
    before = AuditEvent.objects.count()
    assert client.get(rules_url).status_code == 200
    response = client.get(tasks_url)
    assert response.status_code == 200 and "Revisión prudente" in response.content.decode()
    assert AuditEvent.objects.count() == before
    assert client.post(rules_url, {"name": "CSRF"}).status_code == 403
    assert client.post(tasks_url, {"action": "cancel"}).status_code == 403
    client.force_login(followups["foreign"].user)
    assert client.get(tasks_url).status_code == 404


def test_reconcile_command_requires_authorized_actor(followups):
    member = followups["member"]
    call_command(
        "reconcile_followups",
        workspace=member.workspace_id,
        actor=member.user_id,
        stdout=StringIO(),
    )
    with pytest.raises(CommandError):
        call_command(
            "reconcile_followups", workspace=member.workspace_id, actor=followups["foreign"].user_id
        )


def test_limits_update_reconciles_dates(followups):
    member = followups["member"]
    configure(
        user=member.user,
        workspace_id=member.workspace_id,
        data={"default_cooldown_days": 60},
        expected_revision=0,
    )
    assert SalesTask.objects.order_by("sequence").first().due_at == followups[
        "source"
    ].sent_at + timedelta(days=60)
