from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.db.models import QuerySet
from django.utils import timezone

from apps.accounts.models import User
from common.selectors import scoped_record, scoped_records

from .models import Job


def jobs_for_user(*, user: User, workspace_id: UUID, filters: dict | None = None) -> QuerySet:
    """List only the selected workspace, with validated status and kind filters."""
    jobs = scoped_records(model=Job, user=user, workspace_id=workspace_id).select_related(
        "created_by"
    )
    for field in ("status", "kind"):
        if (filters or {}).get(field):
            jobs = jobs.filter(**{field: filters[field]})
    return jobs


def job_for_user(*, user: User, workspace_id: UUID, pk: UUID) -> Job:
    """Resolve job IDs exclusively inside the authorized workspace."""
    return scoped_record(model=Job, user=user, workspace_id=workspace_id, pk=pk)


def is_stale(job: Job) -> bool:
    return (
        job.status == Job.Status.RUNNING
        and job.started_at is not None
        and job.started_at < timezone.now() - timedelta(seconds=settings.JOB_STALE_AFTER_SECONDS)
    )


def progress_snapshot(job: Job) -> dict:
    """Expose progress without payloads, user data or provider internals."""
    return {
        "status": job.status,
        "status_label": job.get_status_display(),
        "current": job.progress_current,
        "total": job.progress_total,
        "attempts": job.attempts,
        "last_error": job.last_error,
        "terminal": job.status in {Job.Status.SUCCEEDED, Job.Status.FAILED, Job.Status.CANCELLED},
    }
