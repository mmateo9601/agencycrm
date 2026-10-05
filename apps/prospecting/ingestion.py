"""Transactional row ingestion, conservative matching and durable provenance."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.services import record_event
from apps.companies.models import Company, CompanyExternalRef, Contact
from apps.crm.models import Lead
from apps.crm.services import save_lead
from common.access import workspace_access
from common.normalization import normalize_email

from .models import DuplicateCandidate, SearchResult, SearchRun
from .planner import fingerprint


def normalized_company(*, run: SearchRun, data: dict) -> tuple[Company, str]:
    """Validate the complete row before attempting any mutation."""
    fields = {
        "name": "name",
        "website": "website",
        "phone": "phone",
        "address": "address_line",
        "city": "city",
        "province": "province",
        "country": "country_code",
    }
    if any(not isinstance(value, str) for value in data.values()):
        raise ValidationError("Todos los campos deben ser texto.")
    values = {dest: data.get(source, "").strip() for source, dest in fields.items()}
    values["country_code"] = (
        values["country_code"] or run.plan_item.plan.campaign.target_country_code
    ).upper()
    company = Company(
        workspace_id=run.workspace_id,
        primary_sector=run.plan_item.plan.campaign.target_sector,
        **values,
    )
    company.full_clean()
    email = normalize_email(data.get("email", ""))
    # A real Company instance is not required for field/email validation.
    Contact._meta.get_field("email").clean(email, None)
    return company, email


def match_company(incoming: Company) -> tuple[Company | None, list[Company]]:
    """Auto-link only one compatible identity; shared identifiers become suggestions."""
    query = (
        Q(
            normalized_name=incoming.normalized_name,
            address_line=incoming.address_line,
            city=incoming.city,
        )
        if incoming.address_line
        else Q(pk__in=[])
    )
    if incoming.normalized_domain:
        query |= Q(normalized_domain=incoming.normalized_domain)
    if incoming.phone_e164:
        query |= Q(phone_e164=incoming.phone_e164)
    candidates = list(Company.objects.filter(query, workspace_id=incoming.workspace_id)[:21])
    high = [
        candidate
        for candidate in candidates
        if candidate.normalized_name == incoming.normalized_name
        and (
            (
                incoming.normalized_domain
                and incoming.normalized_domain == candidate.normalized_domain
            )
            or (incoming.phone_e164 and incoming.phone_e164 == candidate.phone_e164)
        )
    ]
    # Multiple matches are ambiguous, even when each would look plausible alone.
    return (high[0], []) if len(high) == 1 and len(candidates) == 1 else (None, candidates[:20])


@transaction.atomic
def ingest_row(*, run: SearchRun, user: User, row: dict) -> SearchResult:
    """Commit a company, source, lead and row disposition together or change nothing."""
    workspace_access(user=user, workspace_id=run.workspace_id, write=True)
    existing = run.rows.filter(line=row["line"]).first()
    if existing:
        return existing
    if row.get("error"):
        raise ValidationError(row["error"])
    incoming, email = normalized_company(run=run, data=row["data"])
    # CSV identity is document + physical line, never a name-based company identity.
    external_id = (
        fingerprint({"document": run.request_fingerprint, "line": row["line"]})
        if run.provider == "MANUAL_IMPORT"
        else row.get("external_id")
    )
    if not isinstance(external_id, str) or not external_id.strip() or len(external_id) > 255:
        raise ValidationError("Identificador de origen no válido.")
    source = (
        CompanyExternalRef.objects.filter(
            workspace_id=run.workspace_id, provider=run.provider, external_id=external_id
        )
        .select_related("company")
        .first()
    )
    company, candidates = (source.company, []) if source else match_company(incoming)
    created = company is None
    updated = False
    if created:
        company = incoming
        company.save()
        record_event(actor=user, obj=company, action="COMPANY_CREATED")
        for candidate in candidates:
            first, second = sorted((company, candidate), key=lambda obj: str(obj.pk))
            DuplicateCandidate.objects.get_or_create(
                workspace_id=run.workspace_id,
                company=first,
                candidate=second,
                defaults={
                    "reason": "Identidad compartida o nombre y dirección similares; revisar."
                },
            )
    elif not company.is_active:
        raise ValidationError("La empresa existente está archivada; requiere revisión manual.")
    else:
        # Preserve curated values and all contact/suppression flags.
        for field in ("website", "phone", "address_line", "city", "province", "primary_sector"):
            if not getattr(company, field) and getattr(incoming, field):
                setattr(company, field, getattr(incoming, field))
                updated = True
        if updated:
            company.save()
            record_event(actor=user, obj=company, action="COMPANY_UPDATED_FROM_SEARCH")
    if source is None:
        source = CompanyExternalRef.objects.create(
            workspace_id=run.workspace_id,
            company=company,
            provider=run.provider,
            external_id=external_id,
            metadata={"first_search_run": str(run.pk)},
        )
    else:
        source.observed_at = timezone.now()
        source.save(update_fields=["observed_at", "updated_at"])
    if (
        email
        and not Contact.objects.filter(
            workspace_id=run.workspace_id, company=company, normalized_email=email
        ).exists()
    ):
        contact = Contact.objects.create(
            workspace_id=run.workspace_id,
            company=company,
            full_name=company.name,
            email=email,
            source_url=incoming.website,
        )
        record_event(actor=user, obj=contact, action="CONTACT_CREATED")
    campaign = run.plan_item.plan.campaign
    lead = Lead.objects.filter(
        workspace_id=run.workspace_id, campaign=campaign, company=company
    ).first()
    lead_created = lead is None
    if lead_created:
        lead = save_lead(
            user=user,
            workspace_id=run.workspace_id,
            data={"campaign": campaign, "company": company},
        )
    return SearchResult.objects.create(
        workspace_id=run.workspace_id,
        run=run,
        line=row["line"],
        external_id=external_id,
        company=company,
        lead=lead,
        company_created=created,
        company_updated=updated,
        lead_created=lead_created,
    )
