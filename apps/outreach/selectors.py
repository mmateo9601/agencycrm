from uuid import UUID

from apps.accounts.models import User
from common.selectors import scoped_record, scoped_records

from .models import OutreachMessage


def message_for_user(*, user: User, workspace_id: UUID, pk: UUID) -> OutreachMessage:
    return scoped_record(model=OutreachMessage, user=user, workspace_id=workspace_id, pk=pk)


def messages_for_user(*, user: User, workspace_id: UUID):
    return scoped_records(
        model=OutreachMessage, user=user, workspace_id=workspace_id
    ).select_related("lead__company", "contact", "proposal")
