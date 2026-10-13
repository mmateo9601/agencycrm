from uuid import UUID

from django.db.models import Model, Q, QuerySet

from apps.accounts.models import User
from common.selectors import scoped_records

from .models import Campaign


def campaigns_for_api(*, user: User, workspace_id: UUID, filters: dict) -> QuerySet:
    """Prefetch service IDs once for the paginated internal API."""
    return campaign_records(
        model=Campaign, user=user, workspace_id=workspace_id, filters=filters
    ).prefetch_related("services")


def campaign_records(
    *, model: type[Model], user: User, workspace_id: UUID, filters: dict
) -> QuerySet:
    """Return scoped campaigns or configured channels with validated filters."""
    records = scoped_records(model=model, user=user, workspace_id=workspace_id)
    if model is Campaign:
        if filters.get("q"):
            records = records.filter(
                Q(name__icontains=filters["q"]) | Q(target_sector__icontains=filters["q"])
            )
        if filters.get("status"):
            records = records.filter(status=filters["status"])
    else:
        records = records.select_related("campaign")
        if filters.get("campaign"):
            records = records.filter(campaign=filters["campaign"])
        if filters.get("q"):
            records = records.filter(campaign__name__icontains=filters["q"])
    return records
