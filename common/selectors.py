from uuid import UUID

from django.db.models import Model, QuerySet
from django.shortcuts import get_object_or_404

from apps.accounts.models import User
from common.access import workspace_access


def scoped_records(*, model: type[Model], user: User, workspace_id: UUID) -> QuerySet:
    """Resolve an authorized workspace before constructing any domain query."""
    workspace = workspace_access(user=user, workspace_id=workspace_id)
    return model.objects.filter(workspace=workspace)


def scoped_record(*, model: type[Model], user: User, workspace_id: UUID, pk: UUID) -> Model:
    """Never resolve a supplied UUID globally."""
    records = scoped_records(model=model, user=user, workspace_id=workspace_id)
    return get_object_or_404(with_related(records), pk=pk)


def with_related(records: QuerySet) -> QuerySet:
    """Fetch explicit forward relations, without deprecated wildcard selection or N+1 labels."""
    related = [
        field.name
        for field in records.model._meta.fields
        if field.is_relation and (field.many_to_one or field.one_to_one)
    ]
    return records.select_related(*related) if related else records
