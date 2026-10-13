from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from apps.jobs.execution import job_for_context
from apps.jobs.handlers import ExecutionContext
from apps.jobs.models import Job
from common.access import workspace_access

from .selectors import dashboard_for_user


@transaction.atomic
def run_metrics(context: ExecutionContext) -> dict:
    """Persist one aggregate snapshot per Job; live dashboard reads remain uncached."""
    job = job_for_context(context, kind=Job.Kind.REFRESH_METRICS)
    workspace_access(user=job.created_by, workspace_id=context.workspace_id, write=True)
    previous = AuditEvent.objects.filter(
        workspace_id=context.workspace_id,
        object_id=job.pk,
        object_type=job._meta.label,
        action="DASHBOARD_METRICS_REFRESHED",
    ).first()
    if previous:
        context.progress(1, 1)
        return previous.metadata
    snapshot = dashboard_for_user(user=job.created_by, workspace_id=context.workspace_id)
    result = {
        "workspace_id": str(context.workspace_id),
        "companies_total": snapshot.companies_total,
        "leads_total": snapshot.leads_total,
        "lead_counts": {row.status: row.value for row in snapshot.lead_counts},
        "jobs_failed": snapshot.jobs_failed,
        "messages_pending": snapshot.messages_pending,
        "measured_at": timezone.now().isoformat(),
    }
    context.progress(1, 1)
    record_event(
        actor=job.created_by, obj=job, action="DASHBOARD_METRICS_REFRESHED", metadata=result
    )
    return result
