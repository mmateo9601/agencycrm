"""Live, workspace-scoped aggregates; reading the dashboard never schedules work."""

from dataclasses import dataclass
from uuid import UUID

from django.db.models import Count, Q

from apps.accounts.models import User, Workspace
from apps.accounts.selectors import workspace_for_user
from apps.companies.models import Company
from apps.crm.models import Lead
from apps.jobs.models import Job
from apps.outreach.models import OutreachMessage


@dataclass(frozen=True)
class LeadCount:
    status: str
    label: str
    value: int


@dataclass(frozen=True)
class DashboardSnapshot:
    workspace: Workspace
    companies_total: int
    leads_total: int
    lead_counts: tuple[LeadCount, ...]
    jobs_failed: int
    messages_pending: int


def dashboard_for_user(*, user: User, workspace_id: UUID) -> DashboardSnapshot:
    """Check active access first, then count current states with four aggregate queries.

    Companies include inactive records. Leads exclude archived records; each belongs
    to exactly one current state. Counts are not cumulative conversion statistics.
    Jobs and pending approvals include the whole authorized workspace history.
    """
    workspace = workspace_for_user(user=user, workspace_id=workspace_id)
    counts = Lead.objects.filter(workspace=workspace, is_archived=False).aggregate(
        total=Count("pk"),
        **{status: Count("pk", filter=Q(status=status)) for status in Lead.Status.values},
    )
    return DashboardSnapshot(
        workspace=workspace,
        companies_total=Company.objects.filter(workspace=workspace).count(),
        leads_total=counts["total"],
        lead_counts=tuple(
            LeadCount(status, label, counts[status]) for status, label in Lead.Status.choices
        ),
        jobs_failed=Job.objects.filter(workspace=workspace, status=Job.Status.FAILED).count(),
        messages_pending=OutreachMessage.objects.filter(
            workspace=workspace, status=OutreachMessage.Status.PENDING_APPROVAL
        ).count(),
    )
