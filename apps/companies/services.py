from uuid import UUID

from django.db import transaction

from apps.accounts.models import User
from apps.audit.services import record_event
from common.access import workspace_access
from common.selectors import scoped_record
from common.services import save_record

from .models import Company, CompanyExternalRef, Contact, SocialProfile
from .schema import COMPANY_FIELDS, CONTACT_FIELDS, EXTERNAL_REF_FIELDS, SOCIAL_FIELDS


def save_company(*, user: User, workspace_id: UUID, data: dict, pk: UUID | None = None) -> Company:
    """Create or edit a company without automatic merging by name."""
    return save_record(
        model=Company,
        user=user,
        workspace_id=workspace_id,
        data=data,
        fields=COMPANY_FIELDS,
        pk=pk,
        after_save=lambda company: reconcile_followups(workspace_id, user),
    )


def save_contact(*, user: User, workspace_id: UUID, data: dict, pk: UUID | None = None) -> Contact:
    """Persist a contact; normal phone numbers never imply WhatsApp availability."""
    return save_record(
        model=Contact,
        user=user,
        workspace_id=workspace_id,
        data=data,
        fields=CONTACT_FIELDS,
        pk=pk,
        after_save=lambda contact: reconcile_followups(workspace_id, user),
    )


def save_external_ref(
    *, user: User, workspace_id: UUID, data: dict, pk: UUID | None = None
) -> CompanyExternalRef:
    """Enforce provider identity uniqueness within the workspace."""
    return save_record(
        model=CompanyExternalRef,
        user=user,
        workspace_id=workspace_id,
        data=data,
        fields=EXTERNAL_REF_FIELDS,
        pk=pk,
    )


def save_social_profile(
    *, user: User, workspace_id: UUID, data: dict, pk: UUID | None = None
) -> SocialProfile:
    """Persist a normalized social profile in the authorized company scope."""
    return save_record(
        model=SocialProfile,
        user=user,
        workspace_id=workspace_id,
        data=data,
        fields=SOCIAL_FIELDS,
        pk=pk,
    )


@transaction.atomic
def archive_company(*, user: User, workspace_id: UUID, pk: UUID) -> None:
    """Archive the company while preserving its leads and provenance."""
    workspace_access(user=user, workspace_id=workspace_id, delete=True)
    company = scoped_record(model=Company, user=user, workspace_id=workspace_id, pk=pk)
    if company.is_active:
        company.is_active = False
        company.save(update_fields=["is_active", "updated_at"])
        record_event(actor=user, obj=company, action="COMPANY_ARCHIVED")
        reconcile_followups(workspace_id, user)


def reconcile_followups(workspace_id, user):
    from apps.outreach.followups import reconcile

    reconcile(workspace_id=workspace_id, actor=user)
