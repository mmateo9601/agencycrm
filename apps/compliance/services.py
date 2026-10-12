from uuid import UUID

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.services import record_event
from apps.companies.models import Company
from apps.outreach.selectors import message_for_user
from common.access import workspace_access
from common.selectors import scoped_record

from .models import ContactEligibilityDecision, SuppressionEntry
from .policies import POLICY_VERSION, eligibility


@transaction.atomic
def add_suppression(
    *,
    user: User,
    workspace_id: UUID,
    scope: str,
    value: str,
    reason: str,
    source: str,
    company_id: UUID | None = None,
    expires_at=None,
) -> SuppressionEntry:
    """Normalize an operator block and retain its audit trail, including reactivated blocks."""
    workspace_access(user=user, workspace_id=workspace_id, delete=True)
    company = (
        scoped_record(model=Company, user=user, workspace_id=workspace_id, pk=company_id)
        if company_id
        else None
    )
    obj = SuppressionEntry(
        workspace_id=workspace_id,
        scope=scope,
        normalized_value=value,
        reason=reason,
        source=source,
        company=company,
        created_by=user,
        expires_at=expires_at,
    )
    obj.clean()
    existing = SuppressionEntry.objects.filter(
        workspace_id=workspace_id, scope=scope, normalized_value=obj.normalized_value
    ).first()
    if existing:
        existing.reason, existing.source, existing.expires_at = reason, source, expires_at
        obj = existing
    obj.save()
    record_event(actor=user, obj=obj, action="SUPPRESSION_ADDED")
    from apps.outreach.followups import reconcile

    reconcile(workspace_id=workspace_id, actor=user)
    return obj


@transaction.atomic
def expire_suppression(*, user: User, workspace_id: UUID, pk: UUID) -> None:
    workspace_access(user=user, workspace_id=workspace_id, delete=True)
    obj = scoped_record(model=SuppressionEntry, user=user, workspace_id=workspace_id, pk=pk)
    obj.expires_at = timezone.now()
    obj.save(update_fields=["expires_at", "updated_at"])
    record_event(actor=user, obj=obj, action="SUPPRESSION_EXPIRED")


def evaluate_message(message, user, *, reserved: bool = False) -> ContactEligibilityDecision:
    """Persist the decision in the caller's transaction before any transport invocation."""
    decision, reasons = eligibility(message, reserved=reserved)
    return ContactEligibilityDecision.objects.create(
        workspace_id=message.workspace_id,
        lead=message.lead,
        contact=message.contact,
        message=message,
        channel=message.channel,
        decision=decision,
        reasons={"codes": reasons},
        policy_version=POLICY_VERSION,
        evaluated_by=user,
    )


@transaction.atomic
def evaluate(*, user: User, workspace_id: UUID, message_id: UUID) -> ContactEligibilityDecision:
    workspace_access(user=user, workspace_id=workspace_id, write=True)
    return evaluate_message(
        message_for_user(user=user, workspace_id=workspace_id, pk=message_id), user
    )


class EligibilityService:
    evaluate = staticmethod(evaluate)
