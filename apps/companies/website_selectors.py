from uuid import UUID

from django.db.models import QuerySet

from apps.accounts.models import User
from common.selectors import scoped_record

from .models import Company, WebsiteObservation


def company_observations(
    *, user: User, workspace_id: UUID, company_id: UUID
) -> tuple[Company, QuerySet]:
    """Return history only after authorizing the company in its workspace."""
    company = scoped_record(model=Company, user=user, workspace_id=workspace_id, pk=company_id)
    observations = WebsiteObservation.objects.filter(
        workspace_id=workspace_id, company=company
    ).select_related("job")
    return company, observations
