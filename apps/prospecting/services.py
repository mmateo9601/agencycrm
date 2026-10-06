from uuid import UUID

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.models import User
from apps.audit.services import record_event
from apps.campaigns.models import Campaign
from apps.jobs.models import Job
from apps.jobs.services import JobService
from common.access import workspace_access
from common.selectors import scoped_record
from integrations.common.errors import ProviderError
from integrations.common.factory import places_provider

from .models import SearchPlan, SearchPlanItem
from .planner import fingerprint, parse_csv, text_items


def eligible_campaign(*, user: User, workspace_id: UUID, pk: UUID) -> Campaign:
    """Authorize a prepared campaign before planning, enqueueing or ingesting results."""
    workspace_access(user=user, workspace_id=workspace_id, write=True)
    campaign = scoped_record(model=Campaign, user=user, workspace_id=workspace_id, pk=pk)
    if campaign.status not in {
        Campaign.Status.READY,
        Campaign.Status.RUNNING,
        Campaign.Status.COMPLETED,
    }:
        raise ValidationError("Prepara la campaña antes de iniciar la prospección.")
    return campaign


@transaction.atomic
def create_plan(
    *,
    user: User,
    workspace_id: UUID,
    campaign_id: UUID,
    request_key: UUID,
    locations: list[str] | None = None,
    csv_content: bytes | None = None,
) -> SearchPlan:
    """Build a small immutable plan and enqueue its items atomically; no ingestion in HTTP."""
    campaign = eligible_campaign(user=user, workspace_id=workspace_id, pk=campaign_id)
    try:
        request_key = UUID(str(request_key))
    except (ValueError, TypeError) as exc:
        raise ValidationError("Clave de petición no válida.") from exc
    if csv_content is not None:
        rows = parse_csv(csv_content)
        items = [{"import_rows": rows, "max_results": len(rows)}]
        strategy, provider = SearchPlan.Strategy.MANUAL_IMPORT, "MANUAL_IMPORT"
        target = len(rows)
    else:
        items = text_items(
            campaign.target_query,
            locations or [campaign.target_location_text],
            campaign.desired_leads,
        )
        strategy = SearchPlan.Strategy.TEXT_QUERIES
        try:
            # Construction checks configuration without performing I/O.
            places_provider()
        except ProviderError as error:
            raise ValidationError(error.public_message) from None
        provider = "GOOGLE_PLACES" if settings.PLACES_PROVIDER == "google" else "MOCK_PLACES"
        if provider == "GOOGLE_PLACES":
            for item in items:
                item["max_results"] = min(20, item["max_results"])
        target = campaign.desired_leads
    digest = fingerprint(
        {
            "campaign": str(campaign.pk),
            "items": items,
            "provider": provider,
            **({"country": campaign.target_country_code} if provider == "GOOGLE_PLACES" else {}),
        }
    )
    previous = SearchPlan.objects.filter(workspace_id=workspace_id, request_key=request_key).first()
    if previous:
        if previous.request_fingerprint != digest:
            raise ValidationError("Esta clave identifica otro plan.")
        return previous
    plan = SearchPlan.objects.create(
        workspace_id=workspace_id,
        campaign=campaign,
        provider=provider,
        strategy=strategy,
        target_count=target,
        request_key=request_key,
        request_fingerprint=digest,
        country_code=campaign.target_country_code,
    )
    for sequence, values in enumerate(items, 1):
        item = SearchPlanItem.objects.create(
            workspace_id=workspace_id, plan=plan, sequence=sequence, **values
        )
        enqueue_item(user=user, workspace_id=workspace_id, pk=item.pk, request_key=request_key)
    record_event(actor=user, obj=plan, action="SEARCH_PLAN_CREATED", metadata={"items": len(items)})
    return plan


@transaction.atomic
def enqueue_item(*, user: User, workspace_id: UUID, pk: UUID, request_key: UUID) -> Job:
    """Enqueue a new run, or reuse active work and the same idempotent request."""
    item = scoped_record(model=SearchPlanItem, user=user, workspace_id=workspace_id, pk=pk)
    eligible_campaign(user=user, workspace_id=workspace_id, pk=item.plan.campaign_id)
    try:
        key = str(UUID(str(request_key)))
    except (ValueError, TypeError) as exc:
        raise ValidationError("Clave de petición no válida.") from exc
    if item.job_id and item.job.status in {"PENDING", "RUNNING"}:
        return item.job
    job = JobService.enqueue(
        user=user,
        workspace_id=workspace_id,
        kind=Job.Kind.RUN_SEARCH_ITEM,
        payload={"item_id": str(item.pk)},
        idempotency_key=f"search:{item.pk}:{key}",
    )
    if item.job_id and job.created_at < item.job.created_at:
        return job
    item.job = job
    item.status = job.status
    item.save(update_fields=["job", "status", "updated_at"])
    from .lifecycle import sync_job

    sync_job(job)
    return job


def authorize_payload(*, user: User, workspace_id: UUID, payload: dict) -> None:
    """Validate every queued item ID at the workspace boundary, including direct API calls."""
    item = scoped_record(
        model=SearchPlanItem, user=user, workspace_id=workspace_id, pk=payload["item_id"]
    )
    eligible_campaign(user=user, workspace_id=workspace_id, pk=item.plan.campaign_id)
