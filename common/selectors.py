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
    return get_object_or_404(
        scoped_records(model=model, user=user, workspace_id=workspace_id), pk=pk
    )
