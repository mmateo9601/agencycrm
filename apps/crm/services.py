from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.services import record_event
from common.access import workspace_access
from common.selectors import scoped_record
from common.services import save_record

from .models import Lead, LeadStatusHistory, Opportunity
from .policies import allowed_transitions
from .schema import LEAD_FIELDS, OPPORTUNITY_FIELDS


@transaction.atomic
def save_lead(*, user: User, workspace_id: UUID, data: dict, pk: UUID | None = None) -> Lead:
    """Create one lead per campaign/company, or edit its non-state fields."""

    def initialize(lead: Lead) -> None:
        if not pk:
            if not lead.company.is_active or lead.campaign.status == "ARCHIVED":
                raise ValidationError("No se pueden crear leads en empresas o campañas archivadas.")
            LeadStatusHistory.objects.create(
                lead=lead,
                from_status="",
                to_status=lead.status,
                revision=0,
                reason="Lead creado",
                actor=user,
            )

    return save_record(
        model=Lead,
        user=user,
        workspace_id=workspace_id,
        data=data,
        fields=LEAD_FIELDS,
        pk=pk,
        after_save=initialize,
    )


def save_opportunity(
    *, user: User, workspace_id: UUID, data: dict, pk: UUID | None = None
) -> Opportunity:
    """Store a manually observed opportunity; detection and scoring are future phases."""
    return save_record(
        model=Opportunity,
        user=user,
        workspace_id=workspace_id,
        data=data,
        fields=OPPORTUNITY_FIELDS,
        pk=pk,
    )


class LeadStateService:
    @staticmethod
    @transaction.atomic
    def transition(
        *,
        user: User,
        workspace_id: UUID,
        pk: UUID,
        target: str,
        reason: str = "",
        expected_revision: int | None = None,
    ) -> Lead:
        """Compare-and-swap a permitted transition and append its history atomically."""
        workspace_access(user=user, workspace_id=workspace_id, write=True)
        lead = scoped_record(model=Lead, user=user, workspace_id=workspace_id, pk=pk)
        if target == lead.status:
            return lead
        if expected_revision is not None and expected_revision != lead.status_revision:
            raise ValidationError("El lead ha cambiado. Recarga la página antes de continuar.")
        if target not in dict(allowed_transitions(lead)):
            raise ValidationError("Transición de estado no permitida.")
        if target in {Lead.Status.LOST, Lead.Status.DISQUALIFIED} and not reason.strip():
            raise ValidationError("Indica el motivo de pérdida o descarte.")
        previous = lead.status
        revision = lead.status_revision + 1
        changes = {"status": target, "status_revision": revision, "updated_at": timezone.now()}
        if target == Lead.Status.LOST:
            changes["lost_reason"] = reason.strip()
        if target == Lead.Status.CONTACTED:
            changes["last_contact_at"] = timezone.now()
        changed = Lead.objects.filter(
            pk=pk, workspace_id=workspace_id, status=previous, status_revision=lead.status_revision
        ).update(**changes)
        if changed != 1:
            raise ValidationError("El lead ha cambiado. Recarga la página.")
        LeadStatusHistory.objects.create(
            lead=lead,
            from_status=previous,
            to_status=target,
            revision=revision,
            reason=reason.strip(),
            actor=user,
        )
        record_event(
            actor=user,
            obj=lead,
            action="LEAD_STATUS_CHANGED",
            metadata={"from": previous, "to": target, "revision": revision},
        )
        lead.refresh_from_db()
        return lead


@transaction.atomic
def archive_lead(*, user: User, workspace_id: UUID, pk: UUID) -> None:
    """Archive without deleting append-only status history."""
    workspace_access(user=user, workspace_id=workspace_id, delete=True)
    lead = scoped_record(model=Lead, user=user, workspace_id=workspace_id, pk=pk)
    if not lead.is_archived:
        lead.is_archived = True
        lead.save(update_fields=["is_archived", "updated_at"])
        record_event(actor=user, obj=lead, action="LEAD_ARCHIVED")
