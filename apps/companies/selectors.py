from uuid import UUID

from django.db.models import Model, Q, QuerySet

from apps.accounts.models import User
from common.selectors import scoped_records

from .models import Company


def company_records(
    *, model: type[Model], user: User, workspace_id: UUID, filters: dict
) -> QuerySet:
    """Read companies and children within one authorized workspace."""
    records = scoped_records(model=model, user=user, workspace_id=workspace_id)
    if model is Company:
        if filters.get("q"):
            records = records.filter(
                Q(name__icontains=filters["q"])
                | Q(normalized_name__icontains=filters["q"])
                | Q(normalized_domain__icontains=filters["q"])
            )
        for field, key in (("city", "city"), ("primary_sector", "sector")):
            if filters.get(key):
                records = records.filter(**{f"{field}__icontains": filters[key]})
        if filters.get("active"):
            records = records.filter(is_active=filters["active"] == "yes")
    else:
        records = records.select_related("company")
        if filters.get("company"):
            records = records.filter(company=filters["company"])
        if filters.get("q"):
            records = records.filter(company__name__icontains=filters["q"])
    return records
