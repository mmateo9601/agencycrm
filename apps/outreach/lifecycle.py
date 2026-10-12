from django.utils import timezone

from apps.audit.services import record_event
from apps.jobs.models import Job

from .models import OutreachMessage


def sync_job(job: Job) -> None:
    """Project current terminal job state after permission errors or cancellation."""
    from .services import event

    status = (
        Job.objects.filter(pk=job.pk, workspace_id=job.workspace_id)
        .values_list("status", flat=True)
        .get()
    )
    if status not in {"FAILED", "CANCELLED"}:
        return
    message = OutreachMessage.objects.filter(
        workspace_id=job.workspace_id, job=job, status__in=["QUEUED", "SENDING"]
    ).first()
    if message:
        if message.provider == "smtp" and message.transport_started_at:
            from .services import delivery_unknown

            delivery_unknown(message, job.created_by)
            return
        OutreachMessage.objects.filter(pk=message.pk, workspace_id=job.workspace_id).update(
            status=status,
            failed_at=timezone.now() if status == "FAILED" else None,
            error_code="JOB_" + status,
            error_message="El trabajo no se completó.",
        )
        event(message, status, str(job.pk))
        record_event(actor=job.created_by, obj=message, action="MESSAGE_" + status)
