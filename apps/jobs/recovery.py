from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.services import record_event

from .lifecycle import sync_related_job
from .locking import WorkerLock
from .models import Job, JobAttempt


def requeue_stale(
    *,
    workspace_id: UUID | None = None,
    job_id: UUID | None = None,
    actor: User | None = None,
    older_than: int | None = None,
) -> dict:
    """Local operator recovery. The worker must be stopped; attempts are never reset."""
    seconds = settings.JOB_STALE_AFTER_SECONDS if older_than is None else older_than
    if type(seconds) is not int or seconds < 1:
        raise ValueError("La antigüedad mínima debe ser positiva.")
    counts = {"requeued": 0, "failed": 0}
    with WorkerLock():
        now = timezone.now()
        stale = Job.objects.filter(
            status=Job.Status.RUNNING, started_at__lt=now - timedelta(seconds=seconds)
        )
        if workspace_id is not None:
            stale = stale.filter(workspace_id=workspace_id)
        if job_id is not None:
            stale = stale.filter(pk=job_id)
        for job in stale.iterator():
            with transaction.atomic():
                retry = job.attempts < job.max_attempts
                status = Job.Status.PENDING if retry else Job.Status.FAILED
                message = "Ejecución interrumpida; recuperada por el operador."
                changed = Job.objects.filter(
                    pk=job.pk, status=Job.Status.RUNNING, run_token=job.run_token
                ).update(
                    status=status,
                    run_token=None,
                    available_at=now,
                    started_at=None,
                    finished_at=None if retry else now,
                    last_error=message,
                    progress_current=0,
                    progress_total=0,
                    updated_at=now,
                )
                if not changed:
                    continue
                JobAttempt.objects.filter(
                    job=job, attempt_number=job.attempts, finished_at__isnull=True
                ).update(
                    finished_at=now,
                    succeeded=False,
                    error_type="WorkerInterrupted",
                    error_message=message,
                )
                sync_related_job(job)
                record_event(
                    actor=actor,
                    obj=job,
                    action="JOB_RECOVERED" if retry else "JOB_FAILED",
                    metadata={"attempt": job.attempts, "reason": "worker_interrupted"},
                )
                counts["requeued" if retry else "failed"] += 1
    return counts
