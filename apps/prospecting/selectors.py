from uuid import UUID

from django.db.models import QuerySet

from apps.accounts.models import User
from common.selectors import scoped_record, scoped_records

from .models import DuplicateCandidate, SearchPlan, SearchPlanItem, SearchResult, SearchRun


def plans_for_user(*, user: User, workspace_id: UUID) -> QuerySet:
    """List plans within the caller's selected workspace."""
    return scoped_records(model=SearchPlan, user=user, workspace_id=workspace_id).select_related(
        "campaign"
    )


def plan_for_user(*, user: User, workspace_id: UUID, pk: UUID) -> SearchPlan:
    """Resolve a plan without leaking IDs from another workspace."""
    return scoped_record(model=SearchPlan, user=user, workspace_id=workspace_id, pk=pk)


def plan_search_runs(plan: SearchPlan) -> QuerySet:
    """Read the execution history of an already authorized plan."""
    return SearchRun.objects.filter(
        workspace_id=plan.workspace_id, plan_item__plan=plan
    ).select_related("job")


def plan_items(plan: SearchPlan) -> QuerySet:
    """Read at most twenty items of an already authorized plan."""
    return SearchPlanItem.objects.filter(plan=plan, workspace_id=plan.workspace_id).select_related(
        "job"
    )


def run_for_user(*, user: User, workspace_id: UUID, pk: UUID) -> SearchRun:
    """Resolve an execution only inside the authorized workspace."""
    return scoped_record(model=SearchRun, user=user, workspace_id=workspace_id, pk=pk)


def results_for_user(*, user: User, workspace_id: UUID, run_id: UUID) -> QuerySet:
    """Return row dispositions, without exposing uploaded input or snapshots."""
    run_for_user(user=user, workspace_id=workspace_id, pk=run_id)
    return (
        scoped_records(model=SearchResult, user=user, workspace_id=workspace_id)
        .filter(run_id=run_id)
        .select_related("company", "lead")
    )


def duplicates_for_user(*, user: User, workspace_id: UUID) -> QuerySet:
    """List informational duplicate suggestions inside one workspace."""
    return scoped_records(
        model=DuplicateCandidate, user=user, workspace_id=workspace_id
    ).select_related("company", "candidate")
