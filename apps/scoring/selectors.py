from uuid import UUID

from apps.accounts.models import User
from apps.crm.models import Lead, Opportunity
from common.selectors import scoped_record, scoped_records

from .models import ScoringRule


def scoring_lead(*, user: User, workspace_id: UUID, pk: UUID) -> Lead:
    """Resolve the lead before any related history or inputs are queried."""
    return scoped_record(model=Lead, user=user, workspace_id=workspace_id, pk=pk)


def scoring_rules(*, user: User, workspace_id: UUID):
    return scoped_records(model=ScoringRule, user=user, workspace_id=workspace_id)


def input_snapshot(lead: Lead) -> dict:
    """Read only scoped normalized facts; failed/stale observations never prove absence."""
    company = lead.company
    contacts = company.contacts.filter(workspace_id=lead.workspace_id, is_business_contact=True)
    email = contacts.exclude(normalized_email="").exists()
    phone = bool(company.phone_e164) or contacts.exclude(phone_e164="").exists()
    socials = set(
        company.social_profiles.filter(workspace_id=lead.workspace_id).values_list(
            "platform", flat=True
        )
    )
    # Use the latest attempt: a more recent failed crawl must invalidate absence inference.
    latest = company.website_observations.filter(workspace_id=lead.workspace_id).first()
    observation = None
    if (
        latest
        and not latest.error_code
        and latest.root_url == company.website
        and latest.pages_crawled
    ):
        observation = {
            "id": str(latest.pk),
            "root_url": latest.root_url,
            "provider": latest.provider,
            "pages": latest.pages_crawled,
            "contact": latest.has_contact_page,
            "booking": latest.has_booking,
            "contact_form": latest.findings.get("contact_form_observed"),
        }
    opportunities = Opportunity.objects.filter(
        workspace_id=lead.workspace_id, lead=lead, is_active=True, scoring_run__isnull=True
    )
    return {
        "lead_id": str(lead.pk),
        "company_id": str(company.pk),
        "campaign_id": str(lead.campaign_id),
        "website": bool(company.website),
        "email": email,
        "phone": phone,
        "social": bool(socials),
        "review_count": company.google_review_count,
        "observation": observation,
        "high_opportunity": opportunities.filter(severity="HIGH").exists(),
        "medium_opportunity": opportunities.filter(severity="MEDIUM").exists(),
        "available_channels": {
            "EMAIL": email,
            "PHONE": phone,
            "LINKEDIN": "LINKEDIN" in socials,
            "INSTAGRAM": "INSTAGRAM" in socials,
        },
        "campaign_channels": sorted(
            lead.campaign.channels.filter(workspace_id=lead.workspace_id, enabled=True).values_list(
                "channel", flat=True
            )
        ),
        "services": list(
            lead.campaign.services.filter(workspace_id=lead.workspace_id, is_active=True)
            .order_by("code")
            .values("id", "code")
        )[:100],
    }
