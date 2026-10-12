from .models import Job


def sync_related_job(job: Job) -> None:
    """Update domain projections in the caller's job transaction; never perform external I/O."""
    if job.kind == Job.Kind.RUN_SEARCH_ITEM:
        from apps.prospecting.lifecycle import sync_job

        sync_job(job)
    if job.kind == Job.Kind.SEND_OUTREACH:
        from apps.outreach.lifecycle import sync_job

        sync_job(job)
