from uuid import UUID

from django.core.exceptions import ValidationError

from apps.accounts.models import AgencyProfile, User
from apps.crm.models import Lead
from common.selectors import scoped_record, scoped_records

from .models import Proposal, SalesAsset


def proposal_for_user(*, user: User, workspace_id: UUID, pk: UUID) -> Proposal:
    """Resolve one proposal only inside the authorized workspace."""
    return scoped_record(model=Proposal, user=user, workspace_id=workspace_id, pk=pk)


def proposals_for_user(*, user: User, workspace_id: UUID):
    return scoped_records(model=Proposal, user=user, workspace_id=workspace_id).select_related(
        "lead__company"
    )


def assets_for_user(*, user: User, workspace_id: UUID):
    return scoped_records(model=SalesAsset, user=user, workspace_id=workspace_id)


def proposal_snapshot(lead: Lead) -> dict:
    """Freeze bounded agency, campaign services and observed opportunities for this version."""
    agency = AgencyProfile.objects.filter(workspace_id=lead.workspace_id).first()
    if agency is None:
        raise ValidationError("Configura primero el perfil de agencia del workspace.")
    services = list(
        lead.campaign.services.filter(workspace_id=lead.workspace_id, is_active=True).order_by(
            "sort_order", "code"
        )[:6]
    )
    if not services or len(services) > 5:
        raise ValidationError(
            "La campaña debe tener de uno a cinco servicios activos para proponer."
        )
    return {
        "template_version": "proposal-v1",
        "lead_id": str(lead.pk),
        "agency": {
            name: str(getattr(agency, name))[:1000]
            for name in ("trading_name", "value_proposition", "meeting_url", "proposal_footer")
        },
        "company": {"id": str(lead.company_id), "name": lead.company.name},
        "campaign": {"id": str(lead.campaign_id), "name": lead.campaign.name},
        "services": [
            {
                "id": str(service.pk),
                "code": service.code,
                "name": service.name,
                "pitch": (service.default_pitch or service.description)[:500],
            }
            for service in services
        ],
        "opportunities": [
            {"title": row["title"], "evidence": row["evidence"][:500]}
            for row in lead.opportunities.filter(workspace_id=lead.workspace_id, is_active=True)
            .order_by("-created_at")
            .values("title", "evidence")[:5]
        ],
    }
