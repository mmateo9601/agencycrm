"""Conservative tenant-scoped retention; business evidence and suppressions are preserved."""

from datetime import timedelta

from django.db import transaction

from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from apps.jobs.handlers import ExecutionContext
from apps.jobs.payloads import validate_payload
from apps.outreach.models import InboundWhatsAppMessage
from apps.outreach.services import owned_job
from apps.prospecting.models import PlacesCache
from common.access import require_capability

BATCH_SIZE = 1000


@transaction.atomic
def run_retention(context: ExecutionContext) -> dict:
    """Recheck operator rights in the worker and commit cleanup with its audit record."""
    job = owned_job(context)
    require_capability(
        user=job.created_by, workspace_id=context.workspace_id, capability="retention"
    )
    payload = validate_payload(job.kind, context.payload)
    previous = AuditEvent.objects.filter(
        workspace_id=context.workspace_id, object_id=job.pk, action="RETENTION_APPLIED"
    ).first()
    if previous:
        return previous.metadata
    cutoff = job.created_at - timedelta(days=payload["inbound_days"])
    caches = list(
        PlacesCache.objects.filter(workspace_id=context.workspace_id, expires_at__lt=job.created_at)
        .order_by("expires_at", "id")
        .values_list("pk", flat=True)[:BATCH_SIZE]
    )
    messages = list(
        InboundWhatsAppMessage.objects.filter(
            workspace_id=context.workspace_id, received_at__lt=cutoff, text_redacted_at__isnull=True
        )
        .exclude(body_text="")
        .order_by("received_at", "id")
        .values_list("pk", flat=True)[:BATCH_SIZE]
    )
    deleted, _ = PlacesCache.objects.filter(
        workspace_id=context.workspace_id, pk__in=caches
    ).delete()
    redacted = InboundWhatsAppMessage.objects.all()._redact_text(
        workspace_id=context.workspace_id, ids=messages, cutoff=cutoff
    )
    result = {
        "caches_deleted": deleted,
        "texts_redacted": redacted,
        "batch_limit_reached": len(caches) == BATCH_SIZE or len(messages) == BATCH_SIZE,
    }
    record_event(actor=job.created_by, obj=job, action="RETENTION_APPLIED", metadata=result)
    return result
