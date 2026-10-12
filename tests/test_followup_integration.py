from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.compliance.policies import eligibility
from apps.jobs.runner import JobRunner
from apps.outreach.models import FollowUpRule, SalesTask
from tests.test_followups import domain as domain
from tests.test_followups import followups as followups
from tests.test_followups import outreach as outreach
from tests.test_followups import proposals as proposals
from tests.test_followups import rule, sent
from tests.test_outreach import fresh_proposal, queue
from tests.test_whatsapp import cloud as cloud
from tests.test_whatsapp import configure as configure_whatsapp
from tests.test_whatsapp import connect, payload, status, webhook
from tests.test_whatsapp import message as whatsapp_message

pytestmark = pytest.mark.django_db


def test_signed_whatsapp_reply_without_context_cancels_reviews(followups, cloud, settings):
    connect(followups)
    response = webhook(
        followups,
        payload(
            followups,
            messages=[
                {
                    "id": "wamid.INTERNAL_RESPONSE",
                    "from": "34600123456",
                    "type": "text",
                    "timestamp": str(int(timezone.now().timestamp())),
                    "text": {"body": "No insistir"},
                }
            ],
        ),
        settings,
    )
    assert response.status_code == 200
    assert SalesTask.objects.filter(status="CANCELLED").count() == 2
    assert "COMPANY_RESPONSE_OR_CLOSED" in eligibility(followups["source"])[1]


def test_cloud_acceptance_schedules_and_failed_delivery_cancels(outreach, cloud, settings):
    rule(outreach)
    _, item = configure_whatsapp(outreach)
    message = whatsapp_message(outreach, item)
    queue(outreach, message)
    assert JobRunner().run_once().status == "SUCCEEDED"
    assert SalesTask.objects.filter(status="PENDING").count() == 2
    response = webhook(outreach, payload(outreach, statuses=[status("failed")]), settings)
    assert response.status_code == 200
    assert SalesTask.objects.filter(status="CANCELLED").count() == 2


def test_rule_ui_creates_and_disables_plan(followups):
    member = followups["member"]
    client = Client()
    client.force_login(member.user)
    url = reverse("outreach:followup_rules", kwargs={"workspace_id": member.workspace_id})
    data = {
        "name": "Regla nueva UI",
        "enabled": "on",
        "delay_days": 14,
        "interval_days": 14,
        "max_steps": 1,
        "expected_revision": 0,
    }
    assert client.post(url, data).status_code == 302
    assert FollowUpRule.objects.filter(name="Regla nueva UI").exists()
    url = reverse(
        "outreach:followup_rule",
        kwargs={"workspace_id": member.workspace_id, "pk": followups["rule"].pk},
    )
    data.update(name=followups["rule"].name, expected_revision=followups["rule"].revision)
    data.pop("enabled")
    assert client.post(url, data).status_code == 302
    assert SalesTask.objects.filter(status="CANCELLED").count() == 2


def test_pending_rule_task_ui_actions(followups):
    client = Client()
    client.force_login(followups["member"].user)
    task = SalesTask.objects.first()
    url = reverse("outreach:tasks", kwargs={"workspace_id": task.workspace_id})
    data = {
        "task_id": str(task.pk),
        "expected_revision": task.revision,
        "action": "complete",
        "outcome": "Revisión aún prematura",
    }
    assert client.post(url, data).status_code == 400
    data["action"] = "cancel"
    assert client.post(url, data).status_code == 302
    task.refresh_from_db()
    assert task.status == "CANCELLED"


def test_new_confirmed_contact_replaces_old_pending_plan(followups):
    future = followups["source"].sent_at + timedelta(days=20)
    with patch("django.utils.timezone.now", return_value=future):
        env = {**followups, "proposal": fresh_proposal(followups)}
        message = sent(env)
    assert (
        SalesTask.objects.filter(source_message=followups["source"], status="CANCELLED").count()
        == 2
    )
    assert SalesTask.objects.filter(source_message=message, status="PENDING").count() == 2


def test_contact_evidence_survives_scheduling_permission_revocation(outreach):
    rule(outreach)
    with patch("apps.outreach.followups.schedule_source", side_effect=PermissionDenied):
        message = sent(outreach)
    assert message.status == "SENT" and message.sent_at and message.provider_message_id
    assert not SalesTask.objects.exists()
    assert AuditEvent.objects.filter(
        object_id=message.pk, action="FOLLOWUP_RECONCILIATION_REQUIRED"
    ).exists()


def test_rule_ui_schedules_prior_contact_and_rejects_foreign_source(outreach):
    message = sent(outreach)
    rule(outreach)
    client = Client()
    client.force_login(outreach["member"].user)
    url = reverse(
        "outreach:followup_rules", kwargs={"workspace_id": outreach["member"].workspace_id}
    )
    assert client.post(url, {"action": "schedule", "source": str(message.pk)}).status_code == 302
    assert client.post(url, {"action": "schedule", "source": str(message.pk)}).status_code == 302
    assert SalesTask.objects.count() == 2
    client.force_login(outreach["foreign"].user)
    url = reverse(
        "outreach:followup_rules", kwargs={"workspace_id": outreach["foreign"].workspace_id}
    )
    assert client.post(url, {"action": "schedule", "source": str(message.pk)}).status_code == 400
    assert SalesTask.objects.count() == 2
