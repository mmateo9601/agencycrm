"""Bounded scoring batches with a durable, idempotent expansion manifest."""

from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from apps.campaigns.models import Campaign
from apps.crm.models import Lead
from apps.jobs.execution import job_for_context
from apps.jobs.handlers import ExecutionContext
from apps.jobs.models import Job
from common.access import workspace_access
from common.selectors import scoped_record

from .selectors import rebuild_lead_ids
from .services import enqueue_score


def authorize_rebuild(*, user: User, workspace_id: UUID, payload: dict) -> Campaign:
    """Validate campaign/cursor in the current workspace at enqueue and execution."""
    workspace_access(user=user, workspace_id=workspace_id, write=True)
    campaign = scoped_record(
        model=Campaign, user=user, workspace_id=workspace_id, pk=payload["campaign_id"]
    )
    if campaign.status == "ARCHIVED":
        raise ValidationError("No se recalculan campañas archivadas.")
    if (
        payload["after"]
        and not Lead.objects.filter(
            pk=payload["after"], workspace_id=workspace_id, campaign=campaign
        ).exists()
    ):
        raise ValidationError("El cursor no pertenece a esta campaña y workspace.")
    return campaign


@transaction.atomic
def run_rebuild(context: ExecutionContext) -> dict:
    """Commit child jobs and their manifest together; replay never selects a different batch."""
    job = job_for_context(context, kind=Job.Kind.REBUILD_SCORES)
    workspace_access(user=job.created_by, workspace_id=context.workspace_id, write=True)
    previous = AuditEvent.objects.filter(
        workspace_id=context.workspace_id,
        object_id=job.pk,
        object_type=job._meta.label,
        action="SCORE_REBUILD_EXPANDED",
    ).first()
    if previous:
        context.progress(previous.metadata["count"], previous.metadata["count"])
        return previous.metadata
    campaign = authorize_rebuild(
        user=job.created_by, workspace_id=context.workspace_id, payload=context.payload
    )
    limit = context.payload["limit"]
    ids = rebuild_lead_ids(
        workspace_id=context.workspace_id,
        campaign_id=campaign.pk,
        after=context.payload["after"],
        limit=limit,
    )
    selected = ids[:limit]
    children = []
    context.progress(0, len(selected))
    for index, lead_id in enumerate(selected, start=1):
        child = enqueue_score(
            user=job.created_by,
            workspace_id=context.workspace_id,
            lead_id=lead_id,
            idempotency_key=f"rebuild:{job.pk}:{lead_id}",
        )
        children.append(str(child.pk))
        context.progress(index, len(selected))
    result = {
        "campaign_id": str(campaign.pk),
        "count": len(children),
        "job_ids": children,
        "next_after": str(selected[-1]) if len(ids) > limit else None,
    }
    record_event(actor=job.created_by, obj=job, action="SCORE_REBUILD_EXPANDED", metadata=result)
    return result
