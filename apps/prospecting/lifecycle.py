"""Synchronize search projections inside the job's state-transition transaction."""

from django.utils import timezone

from apps.jobs.models import Job

from .models import SearchPlan, SearchPlanItem, SearchRun


def sync_job(job: Job) -> None:
    if job.kind != Job.Kind.RUN_SEARCH_ITEM:
        return
    job = Job.objects.get(pk=job.pk, workspace_id=job.workspace_id)
    finished = job.status in {"SUCCEEDED", "FAILED", "CANCELLED"}
    SearchRun.objects.filter(job=job, workspace_id=job.workspace_id).update(
        status=job.status,
        finished_at=timezone.now() if finished else None,
        error_code="JOB_FAILED" if job.status == "FAILED" else "",
        error_message=job.last_error,
    )
    for item in SearchPlanItem.objects.filter(job=job, workspace_id=job.workspace_id):
        SearchPlanItem.objects.filter(pk=item.pk).update(status=job.status)
        statuses = set(item.plan.items.values_list("status", flat=True))
        active = bool(statuses & {"PENDING", "RUNNING"})
        status = (
            ("RUNNING" if "RUNNING" in statuses else "PENDING")
            if active
            else ("SUCCEEDED" if statuses == {"SUCCEEDED"} else "FAILED")
        )
        SearchPlan.objects.filter(pk=item.plan_id, workspace_id=job.workspace_id).update(
            status=status,
            completed_at=None if active else timezone.now(),
        )
