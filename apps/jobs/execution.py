"""Short atomic state transitions; handlers run outside these transactions."""

import logging
import traceback
import uuid
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.audit.services import record_event
from common.models import limited_metadata
from integrations.common.errors import ProviderError

from .lifecycle import sync_related_job
from .locking import WorkerLock
from .models import Job, JobAttempt

logger = logging.getLogger(__name__)


class StaleExecution(Exception):
    """A callback no longer owns the running job token."""


def _require_lock(lock: WorkerLock) -> None:
    if not lock.acquired:
        raise RuntimeError("Execution requires the worker lock")


def _owned(job: Job) -> models.QuerySet:
    return Job.objects.filter(
        pk=job.pk, workspace_id=job.workspace_id, status=Job.Status.RUNNING, run_token=job.run_token
    )


@transaction.atomic
def claim_next(*, lock: WorkerLock, kinds: list[str] | None = None) -> Job | None:
    """Atomically claim the highest priority due job, with a fresh ownership token."""
    _require_lock(lock)
    now = timezone.now()
    pending = Job.objects.filter(
        status=Job.Status.PENDING,
        available_at__lte=now,
        workspace__is_active=True,
        attempts__lt=models.F("max_attempts"),
    )
    if kinds is not None:
        pending = pending.filter(kind__in=kinds)
    job = pending.order_by("-priority", "available_at", "created_at", "id").first()
    if job is None:
        return None
    token = uuid.uuid4()
    if (
        Job.objects.filter(pk=job.pk, status=Job.Status.PENDING, attempts=job.attempts).update(
            status=Job.Status.RUNNING,
            run_token=token,
            attempts=job.attempts + 1,
            started_at=now,
            finished_at=None,
            last_error="",
            result={},
            progress_current=0,
            progress_total=0,
            updated_at=now,
        )
        != 1
    ):
        return None
    job.refresh_from_db()
    JobAttempt.objects.create(job=job, attempt_number=job.attempts, started_at=now)
    sync_related_job(job)
    record_event(
        actor=job.created_by, obj=job, action="JOB_STARTED", metadata={"attempt": job.attempts}
    )
    logger.info(
        "job_started job_id=%s workspace_id=%s attempt=%s", job.pk, job.workspace_id, job.attempts
    )
    return job


def update_progress(*, job: Job, current: int, total: int) -> None:
    """Persist monotonic progress only while this execution owns the job."""
    if type(current) is not int or type(total) is not int or not 0 <= current <= total <= 1_000_000:
        raise ValidationError("Progreso no válido.")
    if (
        _owned(job)
        .filter(progress_current__lte=current)
        .update(
            progress_current=current,
            progress_total=total,
            updated_at=timezone.now(),
        )
        != 1
    ):
        raise StaleExecution("El trabajo ya no pertenece a esta ejecución.")


@transaction.atomic
def finish_success(*, lock: WorkerLock, job: Job, result: dict) -> None:
    """Complete job, attempt and audit together after a successful handler."""
    _require_lock(lock)
    limited_metadata(result)
    now = timezone.now()
    if (
        _owned(job).update(
            status=Job.Status.SUCCEEDED,
            result=result,
            run_token=None,
            progress_current=models.F("progress_total"),
            finished_at=now,
            last_error="",
            updated_at=now,
        )
        != 1
    ):
        raise StaleExecution
    JobAttempt.objects.filter(
        job=job, attempt_number=job.attempts, finished_at__isnull=True
    ).update(
        finished_at=now,
        succeeded=True,
    )
    sync_related_job(job)
    record_event(
        actor=job.created_by, obj=job, action="JOB_SUCCEEDED", metadata={"attempt": job.attempts}
    )
    logger.info("job_succeeded job_id=%s workspace_id=%s", job.pk, job.workspace_id)


@transaction.atomic
def finish_failure(*, lock: WorkerLock, job: Job, error: Exception) -> None:
    """Retry only declared temporary errors, within budget, without logging raw exceptions."""
    _require_lock(lock)
    now = timezone.now()
    retryable = isinstance(error, ProviderError) and error.retryable
    retry = retryable and job.attempts < job.max_attempts
    message = (
        error.public_message
        if isinstance(error, ProviderError)
        else "Error interno durante la ejecución."
    )
    error_type = type(error).__name__[:100]
    frames = traceback.extract_tb(error.__traceback__)[-8:]
    excerpt = "\n".join(
        f"{Path(frame.filename).name}:{frame.lineno} in {frame.name}" for frame in frames
    )[:1500]
    delay = min(300, settings.JOB_RETRY_BASE_SECONDS * 2 ** (job.attempts - 1))
    delay = max(delay, getattr(error, "retry_after", 0))
    changes = {
        "status": Job.Status.PENDING if retry else Job.Status.FAILED,
        "run_token": None,
        "last_error": message,
        "finished_at": None if retry else now,
        "updated_at": now,
    }
    if retry:
        changes.update(
            available_at=now + timedelta(seconds=delay),
            started_at=None,
            progress_current=0,
            progress_total=0,
        )
    if _owned(job).update(**changes) != 1:
        raise StaleExecution
    JobAttempt.objects.filter(
        job=job, attempt_number=job.attempts, finished_at__isnull=True
    ).update(
        finished_at=now,
        succeeded=False,
        error_type=error_type,
        error_message=message,
        traceback_excerpt=excerpt,
    )
    action = "JOB_RETRY_SCHEDULED" if retry else "JOB_FAILED"
    sync_related_job(job)
    record_event(
        actor=job.created_by,
        obj=job,
        action=action,
        metadata={"attempt": job.attempts, "error_type": error_type},
    )
    logger.warning(
        "%s job_id=%s workspace_id=%s error_type=%s",
        action.lower(),
        job.pk,
        job.workspace_id,
        error_type,
    )
