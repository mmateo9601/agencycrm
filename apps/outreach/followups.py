"""Controlled internal reminders. This module never prepares, approves or sends messages."""

from datetime import timedelta
from types import SimpleNamespace

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max, Q
from django.http import Http404
from django.utils import timezone

from apps.accounts.models import Membership, Workspace
from apps.audit.services import record_event
from apps.compliance.policies import suppressed
from apps.crm.models import Lead
from common.access import workspace_access
from common.selectors import scoped_record

from .models import FollowUpRule, InboundWhatsAppMessage, OutreachMessage, SalesTask
from .settings import current_settings

STOP_STATUSES = {"REPLIED", "MEETING", "PROPOSAL", "WON", "LOST", "DISQUALIFIED"}


def lock_workspace(workspace_id):
    """Serialize plans and task actions on SQLite and future PostgreSQL alike."""
    Workspace.objects.select_for_update().get(pk=workspace_id)
    Workspace.objects.filter(pk=workspace_id).update(updated_at=timezone.now())


def stop_reason(source, rule=None):
    """Read fresh company-wide facts, always restricted to the source's workspace."""
    lead = Lead.objects.select_related("workspace", "company", "campaign").get(
        pk=source.lead_id, workspace_id=source.workspace_id
    )
    config = current_settings(lead.workspace)
    source = OutreachMessage.objects.get(pk=source.pk, workspace_id=lead.workspace_id)
    if source.status not in {"SENT", "DELIVERED", "READ"} or not source.sent_at:
        return "SOURCE_NOT_CONFIRMED"
    if OutreachMessage.objects.filter(
        workspace_id=lead.workspace_id, lead__company_id=lead.company_id, sent_at__gt=source.sent_at
    ).exists():
        return "NEW_CONTACT"
    if not lead.workspace.is_active or config.is_paused:
        return "PAUSED"
    if (
        lead.is_archived
        or not lead.company.is_active
        or lead.campaign.status not in {"READY", "RUNNING"}
    ):
        return "INACTIVE"
    if lead.status != "CONTACTED":
        return "LEAD_NOT_CONTACTED"
    if Lead.objects.filter(
        workspace_id=lead.workspace_id, company_id=lead.company_id, status__in=STOP_STATUSES
    ).exists():
        return "RESPONSE_MEETING_OR_CLOSED"
    if OutreachMessage.objects.filter(
        workspace_id=lead.workspace_id, lead__company_id=lead.company_id, status="REPLIED"
    ).exists():
        return "RESPONSE"
    if InboundWhatsAppMessage.objects.filter(
        workspace_id=lead.workspace_id, contact__company_id=lead.company_id
    ).exists():
        return "RESPONSE"
    contacts = list(lead.company.contacts.filter(workspace_id=lead.workspace_id))
    if source.contact_id and not any(contact.pk == source.contact_id for contact in contacts):
        return "CONTACT_CHANGED"
    if lead.company.do_not_contact or any(contact.do_not_contact for contact in contacts):
        return "SUPPRESSED"
    if any(
        suppressed(
            SimpleNamespace(lead=lead, contact=contact, workspace_id=lead.workspace_id),
            timezone.now(),
        )
        for contact in contacts or [None]
    ):
        return "SUPPRESSED"
    if rule:
        rule = FollowUpRule.objects.get(pk=rule.pk, workspace_id=lead.workspace_id)
        if not rule.enabled or (rule.campaign_id and rule.campaign_id != lead.campaign_id):
            return "RULE_DISABLED"
        if not Membership.objects.filter(
            workspace_id=lead.workspace_id,
            user_id=rule.created_by_id,
            is_active=True,
            user__is_active=True,
            role__in=["OWNER", "ADMIN"],
        ).exists():
            return "RULE_AUTHOR_INACTIVE"
    return ""


def earliest_action(source):
    """Respect every enabled campaign channel and every contact with this company."""
    lead = Lead.objects.select_related("workspace", "campaign").get(
        pk=source.lead_id, workspace_id=source.workspace_id
    )
    channel_days = (
        lead.campaign.channels.filter(workspace_id=lead.workspace_id, enabled=True).aggregate(
            days=Max("cooldown_days")
        )["days"]
        or 0
    )
    days = max(current_settings(lead.workspace).default_cooldown_days, channel_days)
    last = Lead.objects.filter(
        workspace_id=lead.workspace_id, company_id=lead.company_id
    ).aggregate(stamp=Max("last_contact_at"))["stamp"]
    return (last or source.sent_at or timezone.now()) + timedelta(days=days)


def refresh_next_action(lead_id, workspace_id):
    lead = Lead.objects.get(pk=lead_id, workspace_id=workspace_id)
    if lead.next_action_at and not lead.next_action_managed:
        return
    due = (
        SalesTask.objects.filter(workspace_id=workspace_id, lead_id=lead_id, status="PENDING")
        .order_by("due_at")
        .values_list("due_at", flat=True)
        .first()
    )
    Lead.objects.filter(pk=lead_id, workspace_id=workspace_id).update(
        next_action_at=due, next_action_managed=due is not None, updated_at=timezone.now()
    )


def cancel_task(task, reason, actor=None):
    if SalesTask.objects.filter(
        pk=task.pk, workspace_id=task.workspace_id, status="PENDING"
    ).update(
        status="CANCELLED",
        cancellation_reason=reason,
        revision=task.revision + 1,
        updated_at=timezone.now(),
    ):
        record_event(
            actor=actor, obj=task, action="FOLLOWUP_CANCELLED", metadata={"reason": reason}
        )
        refresh_next_action(task.lead_id, task.workspace_id)


@transaction.atomic
def reconcile(*, workspace_id, company_id=None, actor=None, force_reason=""):
    """Explicit service hook and periodic sweep; cancelled tasks are never resurrected."""
    lock_workspace(workspace_id)
    tasks = SalesTask.objects.filter(
        workspace_id=workspace_id, status="PENDING", followup_rule__isnull=False
    ).select_related("source_message", "followup_rule")
    if company_id:
        tasks = tasks.filter(lead__company_id=company_id)
    for task in tasks:
        reason = force_reason or stop_reason(task.source_message, task.followup_rule)
        if reason:
            cancel_task(task, reason, actor)
        else:
            due = max(
                task.due_at,
                earliest_action(task.source_message)
                + timedelta(days=task.schedule_snapshot["interval_days"] * (task.sequence - 1)),
            )
            if due != task.due_at:
                SalesTask.objects.filter(
                    pk=task.pk, workspace_id=workspace_id, status="PENDING", revision=task.revision
                ).update(due_at=due, revision=task.revision + 1, updated_at=timezone.now())
                record_event(actor=actor, obj=task, action="FOLLOWUP_COOLDOWN_EXTENDED")
            refresh_next_action(task.lead_id, workspace_id)


@transaction.atomic
def save_rule(*, user, workspace_id, data, pk=None, expected_revision=0):
    workspace_access(user=user, workspace_id=workspace_id, delete=True)
    lock_workspace(workspace_id)
    fields = {"name", "campaign", "enabled", "delay_days", "interval_days", "max_steps"}
    if set(data) - fields:
        raise ValidationError("Campos de regla no permitidos.")
    rule = (
        scoped_record(model=FollowUpRule, user=user, workspace_id=workspace_id, pk=pk)
        if pk
        else FollowUpRule(workspace_id=workspace_id)
    )
    if rule.revision != expected_revision:
        raise ValidationError("La regla ha cambiado; recarga la página.")
    # Resolve relation IDs again in the service; form scoping is not an authorization boundary.
    if data.get("campaign"):
        from apps.campaigns.models import Campaign

        data = {
            **data,
            "campaign": scoped_record(
                model=Campaign,
                user=user,
                workspace_id=workspace_id,
                pk=getattr(data["campaign"], "pk", data["campaign"]),
            ),
        }
    for field, value in data.items():
        setattr(rule, field, value)
    if (
        rule.enabled
        and FollowUpRule.objects.filter(workspace_id=workspace_id, enabled=True)
        .exclude(pk=rule.pk)
        .count()
        >= 10
    ):
        raise ValidationError("Máximo diez reglas habilitadas por espacio.")
    rule.created_by = user
    rule.revision += 1
    rule.save()
    record_event(actor=user, obj=rule, action="FOLLOWUP_RULE_SAVED")
    reconcile(workspace_id=workspace_id, actor=user)
    return rule


@transaction.atomic
def schedule_source(*, user, workspace_id, source_id):
    """Idempotently schedule at most 100 reviews for a confirmed contact."""
    workspace_access(user=user, workspace_id=workspace_id, write=True)
    lock_workspace(workspace_id)
    source = scoped_record(
        model=OutreachMessage, user=user, workspace_id=workspace_id, pk=source_id
    )
    if (
        not source.sent_at
        or not source.provider_message_id
        or source.status not in {"SENT", "DELIVERED", "READ"}
    ):
        raise ValidationError("El origen requiere un contacto confirmado y sin respuesta.")
    reconcile(workspace_id=workspace_id, company_id=source.lead.company_id, actor=user)
    reason = stop_reason(source)
    if reason:
        raise ValidationError(f"Seguimiento bloqueado: {reason}.")
    rules = FollowUpRule.objects.filter(workspace_id=workspace_id, enabled=True).filter(
        Q(campaign__isnull=True) | Q(campaign_id=source.lead.campaign_id)
    )[:10]
    created = 0
    for rule in rules:
        if stop_reason(source, rule):
            continue
        first = max(source.sent_at + timedelta(days=rule.delay_days), earliest_action(source))
        for step in range(1, rule.max_steps + 1):
            task, new = SalesTask.objects.get_or_create(
                workspace_id=workspace_id,
                followup_rule=rule,
                source_message=source,
                sequence=step,
                defaults={
                    "lead": source.lead,
                    "type": "REVIEW",
                    "assigned_to": user,
                    "due_at": first + timedelta(days=rule.interval_days * (step - 1)),
                    "schedule_snapshot": {
                        "rule_name": rule.name,
                        "rule_revision": rule.revision,
                        "delay_days": rule.delay_days,
                        "interval_days": rule.interval_days,
                    },
                },
            )
            if new:
                record_event(actor=user, obj=task, action="FOLLOWUP_SCHEDULED")
                created += 1
    refresh_next_action(source.lead_id, workspace_id)
    return created


def contact_confirmed(message):
    """Trusted receipt hook. Never infer acceptance and never send from a rule."""
    message.refresh_from_db()
    if not FollowUpRule.objects.filter(workspace_id=message.workspace_id, enabled=True).exists():
        return
    reconcile(workspace_id=message.workspace_id, company_id=message.lead.company_id)
    if stop_reason(message):
        return
    actor_id = (
        Membership.objects.filter(
            workspace_id=message.workspace_id,
            user_id__in=[message.created_by_id, message.approved_by_id],
            is_active=True,
            user__is_active=True,
            role__in=["OWNER", "ADMIN", "SALES", "MEMBER"],
        )
        .values_list("user_id", flat=True)
        .first()
    )
    if actor_id:
        from apps.accounts.models import User

        try:
            with transaction.atomic():
                schedule_source(
                    user=User.objects.get(pk=actor_id),
                    workspace_id=message.workspace_id,
                    source_id=message.pk,
                )
        except PermissionDenied, Http404, ValidationError:
            # Receipt evidence survives a concurrent permission/policy change.
            record_event(actor=None, obj=message, action="FOLLOWUP_RECONCILIATION_REQUIRED")


@transaction.atomic
def act_on_task(*, user, workspace_id, pk, action, expected_revision, outcome="", due_at=None):
    workspace_access(user=user, workspace_id=workspace_id, write=True)
    lock_workspace(workspace_id)
    task = scoped_record(model=SalesTask, user=user, workspace_id=workspace_id, pk=pk)
    if not task.followup_rule_id or task.revision != expected_revision:
        raise ValidationError("Tarea no válida o modificada; recarga la página.")
    if task.status != "PENDING":
        return task
    reason = stop_reason(task.source_message, task.followup_rule)
    if reason:
        cancel_task(task, reason, user)
    elif action == "cancel":
        cancel_task(task, "OPERATOR", user)
    elif action == "reschedule":
        if (
            due_at is None
            or timezone.is_naive(due_at)
            or due_at < max(timezone.now(), earliest_action(task.source_message))
        ):
            raise ValidationError("La fecha debe incluir zona y respetar el cooldown vigente.")
        SalesTask.objects.filter(
            pk=pk, workspace_id=workspace_id, revision=expected_revision
        ).update(due_at=due_at, revision=expected_revision + 1, updated_at=timezone.now())
        record_event(actor=user, obj=task, action="FOLLOWUP_RESCHEDULED")
    elif action == "complete":
        if max(task.due_at, earliest_action(task.source_message)) > timezone.now():
            raise ValidationError("La revisión todavía no vence o continúa el cooldown.")
        if not outcome.strip() or len(outcome) > 2000:
            raise ValidationError("Indica el resultado de la revisión (máximo 2000 caracteres).")
        SalesTask.objects.filter(
            pk=pk, workspace_id=workspace_id, revision=expected_revision
        ).update(
            status="COMPLETED",
            outcome=outcome.strip(),
            revision=expected_revision + 1,
            updated_at=timezone.now(),
        )
        record_event(actor=user, obj=task, action="FOLLOWUP_REVIEW_COMPLETED")
    else:
        raise ValidationError("Acción no válida.")
    refresh_next_action(task.lead_id, workspace_id)
    task.refresh_from_db()
    return task
