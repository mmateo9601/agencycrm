from uuid import UUID

from django.db.models import CharField, Exists, Model, OuterRef, Q, QuerySet, Subquery, Value
from django.db.models.functions import Coalesce

from apps.accounts.models import User
from apps.companies.models import Contact
from apps.compliance.models import ContactEligibilityDecision
from common.selectors import scoped_records

from .models import Lead, Opportunity


def eligibility_decisions(lead: Lead) -> QuerySet:
    """Read recorded decisions for an already authorized lead, scoped to its workspace."""
    return ContactEligibilityDecision.objects.filter(
        workspace_id=lead.workspace_id, lead_id=lead.pk
    ).order_by("-evaluated_at", "-created_at", "-id")


def crm_records(*, model: type[Model], user: User, workspace_id: UUID, filters: dict) -> QuerySet:
    """Filter leads using EXISTS so contact joins cannot duplicate rows."""
    records = scoped_records(model=model, user=user, workspace_id=workspace_id)
    if model is Opportunity:
        records = records.select_related("lead__company", "lead__campaign")
        if filters.get("lead"):
            records = records.filter(lead=filters["lead"])
        if filters.get("active"):
            records = records.filter(is_active=filters["active"] == "yes")
        if filters.get("q"):
            records = records.filter(title__icontains=filters["q"])
        return records
    records = records.select_related("company", "campaign", "assigned_to", "recommended_service")
    if filters.get("archived") != "all":
        records = records.filter(is_archived=filters.get("archived") == "yes")
    if filters.get("q"):
        records = records.filter(
            Q(company__name__icontains=filters["q"]) | Q(campaign__name__icontains=filters["q"])
        )
    for field in ("status", "campaign", "company", "assigned_to"):
        if filters.get(field):
            records = records.filter(**{field: filters[field]})
    if filters.get("eligibility"):
        latest = ContactEligibilityDecision.objects.filter(
            workspace_id=workspace_id, lead_id=OuterRef("pk")
        ).order_by("-evaluated_at", "-created_at", "-id")
        records = records.annotate(
            _latest_eligibility=Coalesce(
                Subquery(latest.values("decision")[:1]),
                Value("NOT_EVALUATED"),
                output_field=CharField(),
            )
        ).filter(_latest_eligibility=filters["eligibility"])
    for key, lookup in (
        ("city", "company__city__icontains"),
        ("sector", "company__primary_sector__icontains"),
        ("min_score", "score__gte"),
        ("max_score", "score__lte"),
    ):
        if filters.get(key) not in (None, ""):
            records = records.filter(**{lookup: filters[key]})
    contacts = Contact.objects.filter(workspace_id=workspace_id, company_id=OuterRef("company_id"))
    for key, query in (
        ("has_email", contacts.exclude(normalized_email="")),
        ("has_phone", contacts.exclude(phone_e164="")),
        (
            "has_opportunity",
            Opportunity.objects.filter(
                workspace_id=workspace_id, lead_id=OuterRef("pk"), is_active=True
            ),
        ),
    ):
        if filters.get(key):
            records = records.annotate(**{f"_{key}": Exists(query)})
            condition = Q(**{f"_{key}": True})
            if key == "has_phone":
                condition |= ~Q(company__phone_e164="")
            records = records.filter(condition if filters[key] == "yes" else ~condition)
    return records
