from uuid import UUID

from apps.accounts.models import User
from common.selectors import scoped_record, scoped_records

from .models import OutreachMessage, SalesTask


def message_for_user(*, user: User, workspace_id: UUID, pk: UUID) -> OutreachMessage:
    return scoped_record(model=OutreachMessage, user=user, workspace_id=workspace_id, pk=pk)


def messages_for_user(*, user: User, workspace_id: UUID):
    return scoped_records(
        model=OutreachMessage, user=user, workspace_id=workspace_id
    ).select_related("lead__company", "contact", "proposal")


def tasks_for_user(*, user, workspace_id, status="", mine=False):
    records = scoped_records(model=SalesTask, user=user, workspace_id=workspace_id).select_related(
        "lead__company", "assigned_to", "message", "source_message", "followup_rule"
    )
    if status in {"PENDING", "COMPLETED", "CANCELLED"}:
        records = records.filter(status=status)
    if mine:
        records = records.filter(assigned_to=user)
    return records.order_by("due_at", "id")
