from uuid import UUID

from django.db.models import QuerySet

from apps.accounts.models import User
from common.access import require_capability

from .models import AuditEvent


def events_for_user(*, user: User, workspace_id: UUID, filters: dict | None = None) -> QuerySet:
    """Review only the authorized workspace; metadata remains escaped plain data."""
    require_capability(user=user, workspace_id=workspace_id, capability="audit")
    records = AuditEvent.objects.filter(workspace_id=workspace_id).select_related("actor")
    for field in ("action", "object_id", "correlation_id"):
        if (filters or {}).get(field):
            records = records.filter(**{field: filters[field]})
    return records
