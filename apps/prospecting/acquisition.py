"""Short budget reservations and normalized cache reads, surrounding provider-only I/O."""

from dataclasses import asdict
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.audit.services import record_event
from apps.campaigns.models import Campaign
from integrations.common.contracts import PlaceSearchPage, PlacesProvider, PlaceTextSearchRequest
from integrations.common.errors import ProviderBudgetError, ProviderPermanentError

from .models import PlacesCache, SearchRun


@transaction.atomic
def reserve_request(run: SearchRun) -> None:
    """Count before dispatch, including errors and uncertain outcomes; never refund or reset."""
    campaign_id = run.plan_item.plan.campaign_id
    changed = Campaign.objects.filter(
        pk=campaign_id,
        workspace_id=run.workspace_id,
        places_requests_used__lt=F("places_request_limit"),
    ).update(places_requests_used=F("places_requests_used") + 1)
    if changed != 1:
        raise ProviderBudgetError
    SearchRun.objects.filter(pk=run.pk, workspace_id=run.workspace_id).update(
        requests_count=F("requests_count") + 1
    )
    record_event(
        actor=run.job.created_by,
        obj=run,
        action="PLACES_REQUEST_RESERVED",
        metadata={"campaign_id": str(campaign_id), "job_id": str(run.job_id)},
    )


def page_rows(page: PlaceSearchPage, limit: int) -> list[dict]:
    """Serialize only the normalized dataclass; never store raw responses or tokens."""
    if len(page.places) > limit or page.next_cursor is not None:
        raise ProviderPermanentError
    rows = []
    for index, place in enumerate(page.places, 1):
        data = asdict(place)
        external_id = data.pop("external_id")
        data["country"] = data.pop("country_code")
        rows.append({"line": index, "external_id": external_id, "data": data})
    return rows


def acquire_google(*, run: SearchRun, provider: PlacesProvider) -> tuple[list[dict], dict]:
    """Use tenant-local cache or reserve one request, then call outside any transaction."""
    now = timezone.now()
    seconds = max(0, min(3600, settings.PLACES_CACHE_SECONDS))
    PlacesCache.objects.filter(workspace_id=run.workspace_id, expires_at__lte=now).delete()
    cache = PlacesCache.objects.filter(
        workspace_id=run.workspace_id, fingerprint=run.request_fingerprint, expires_at__gt=now
    ).first()
    if seconds and cache:
        SearchRun.objects.filter(pk=run.pk).update(cache_hit=True)
        return cache.rows, {"simulated": False, "truncated": cache.truncated}
    reserve_request(run)
    item = run.plan_item
    page = provider.text_search(
        PlaceTextSearchRequest(
            query=item.query_text,
            location=item.location_text,
            limit=item.max_results,
            region_code=item.plan.country_code,
        )
    )
    rows = page_rows(page, item.max_results)
    if seconds:
        PlacesCache.objects.update_or_create(
            workspace_id=run.workspace_id,
            fingerprint=run.request_fingerprint,
            defaults={
                "rows": rows,
                "truncated": page.truncated,
                "expires_at": timezone.now() + timedelta(seconds=seconds),
            },
        )
    return rows, {"simulated": False, "truncated": page.truncated}
