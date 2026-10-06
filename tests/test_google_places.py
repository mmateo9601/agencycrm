import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from email.utils import format_datetime
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from apps.campaigns.services import save_campaign
from apps.companies.models import Company, CompanyExternalRef
from apps.crm.models import Lead
from apps.jobs.models import Job
from apps.jobs.runner import JobRunner
from apps.jobs.services import JobService
from apps.prospecting.models import PlacesCache, SearchPlan, SearchRun
from integrations.common.contracts import PlaceNearbySearchRequest, PlaceTextSearchRequest
from integrations.common.errors import (
    ProviderAuthError,
    ProviderConfigurationError,
    ProviderPermanentError,
    ProviderRateLimitError,
    ProviderTemporaryError,
    ProviderTimeoutError,
)
from integrations.common.factory import outbound_provider, places_provider
from integrations.google_places.provider import (
    FIELD_MASK,
    GooglePlacesProvider,
    normalize_place,
    retry_after_seconds,
)
from tests.factories import MembershipFactory
from tests.test_prospecting import campaign_for, csv_bytes, plan_for, rerun

PLACE = {
    "id": "fictional-place-1",
    "displayName": {"text": "Clínica Ficticia"},
    "websiteUri": "https://clinic.example.invalid/",
    "internationalPhoneNumber": "+34 981 12 34 56",
    "formattedAddress": "Calle Ficticia 1",
    "location": {"latitude": 43.3623456, "longitude": -8.4112345},
    "addressComponents": [
        {"longText": "A Coruña", "types": ["locality"]},
        {"longText": "A Coruña", "types": ["administrative_area_level_2"]},
        {"longText": "15001", "types": ["postal_code"]},
        {"shortText": "ES", "longText": "España", "types": ["country"]},
    ],
    "rating": 4.6,
    "userRatingCount": 42,
    "googleMapsUri": "https://maps.google.com/?cid=fictional",
    "unused_secret": "MUST_NOT_STORE",
}


def provider(handler):
    return GooglePlacesProvider(
        api_key="FAKE_KEY_NOT_A_SECRET", transport=httpx.MockTransport(handler)
    )


@pytest.fixture
def google_enabled(settings):
    settings.PLACES_PROVIDER = "google"
    settings.GOOGLE_PLACES_ENABLED = True
    settings.GOOGLE_PLACES_API_KEY = "FAKE_KEY_NOT_A_SECRET"
    settings.PLACES_CACHE_SECONDS = 300


@pytest.fixture
def member(tmp_path, monkeypatch):
    monkeypatch.setattr("apps.jobs.locking.worker_lock_path", lambda: tmp_path / "worker.lock")
    return MembershipFactory(role="OWNER")


def test_text_search_request_mask_normalization_and_truncation():
    calls = []

    def handler(request):
        calls.append(request)
        assert (
            request.method == "POST"
            and str(request.url) == "https://places.googleapis.com/v1/places:searchText"
        )
        assert request.headers["X-Goog-Api-Key"] == "FAKE_KEY_NOT_A_SECRET"
        assert request.headers["X-Goog-FieldMask"] == FIELD_MASK + ",nextPageToken"
        assert "*" not in FIELD_MASK and "photos" not in FIELD_MASK and "reviews" not in FIELD_MASK
        assert "FAKE_KEY" not in str(request.url)
        assert json.loads(request.content) == {
            "textQuery": "dentistas A Coruña",
            "pageSize": 2,
            "languageCode": "es",
            "regionCode": "ES",
        }
        assert request.extensions["timeout"]["read"] == 10
        return httpx.Response(200, json={"places": [PLACE], "nextPageToken": "DO_NOT_PERSIST"})

    page = provider(handler).text_search(PlaceTextSearchRequest("dentistas", "A Coruña", 2))
    assert len(calls) == 1 and page.truncated and page.next_cursor is None
    place = page.places[0]
    assert place.latitude == "43.362346" and place.longitude == "-8.411234"
    assert place.rating == "4.6" and place.review_count == "42" and place.postal_code == "15001"
    assert place.country_code == "ES" and place.city == "A Coruña"
    assert "MUST_NOT_STORE" not in repr(place)


def test_nearby_and_details_have_fixed_origins_and_specific_masks():
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == "POST":
            assert request.url.path == "/v1/places:searchNearby"
            payload = json.loads(request.content)
            assert payload["locationRestriction"]["circle"]["radius"] == 1000
            return httpx.Response(200, json={"places": [PLACE]})
        assert request.url.path == "/v1/places/fictional-place-1"
        assert request.headers["X-Goog-FieldMask"] == "id,websiteUri"
        return httpx.Response(
            200, json={"id": "fictional-place-1", "websiteUri": PLACE["websiteUri"]}
        )

    adapter = provider(handler)
    assert adapter.nearby_search(PlaceNearbySearchRequest(43, -8)).places
    assert adapter.get_details("fictional-place-1", {"website"}).website == PLACE["websiteUri"]
    for identifier, fields in (
        ("https://evil.invalid", {"name"}),
        ("../keys", {"name"}),
        ("id", {"reviews"}),
        ("id", set()),
    ):
        with pytest.raises(ProviderPermanentError):
            adapter.get_details(identifier, fields)
    assert len(calls) == 2


@pytest.mark.parametrize(
    "status,error",
    [
        (400, ProviderPermanentError),
        (401, ProviderAuthError),
        (403, ProviderAuthError),
        (404, ProviderPermanentError),
        (429, ProviderRateLimitError),
        (500, ProviderTemporaryError),
        (503, ProviderTemporaryError),
        (302, ProviderPermanentError),
    ],
)
def test_http_failures_are_sanitized_and_never_follow_redirects(status, error, caplog):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status,
            headers={"Retry-After": "45", "Location": "http://127.0.0.1/private"},
            json={"error": {"message": "SECRET_PROVIDER_PAYLOAD"}},
        )

    with pytest.raises(error) as captured:
        provider(handler).text_search(PlaceTextSearchRequest("fixture"))
    assert len(calls) == 1
    assert "SECRET_PROVIDER_PAYLOAD" not in str(captured.value) + caplog.text
    assert "FAKE_KEY" not in caplog.text
    if status == 429:
        assert captured.value.retry_after == 45


@pytest.mark.parametrize(
    "failure,error",
    [(httpx.ReadTimeout, ProviderTimeoutError), (httpx.ConnectError, ProviderTemporaryError)],
)
def test_network_errors_are_mapped_without_raw_exception(failure, error):
    def handler(request):
        raise failure("SECRET_TRANSPORT", request=request)

    with pytest.raises(error) as captured:
        provider(handler).text_search(PlaceTextSearchRequest("fixture"))
    assert "SECRET_TRANSPORT" not in str(captured.value)


@pytest.mark.parametrize(
    "body,content_type",
    [
        (b"not-json", "application/json"),
        (b"[]", "application/json"),
        (b"{}", "text/html"),
        (b"x" * 1000001, "application/json"),
    ],
    ids=["malformed", "shape", "mime", "size"],
)
def test_invalid_or_oversized_responses_fail_closed(body, content_type):
    with pytest.raises(ProviderPermanentError):
        provider(
            lambda _: httpx.Response(200, content=body, headers={"Content-Type": content_type})
        ).text_search(PlaceTextSearchRequest("fixture"))


@pytest.mark.parametrize(
    "change",
    [
        {"id": ""},
        {"displayName": []},
        {"addressComponents": {}},
        {"location": {"latitude": 100}},
        {"rating": "NaN"},
        {"userRatingCount": True},
        {"websiteUri": 2},
        {"addressComponents": [{"types": [5]}]},
    ],
)
def test_invalid_normalized_places_are_rejected(change):
    with pytest.raises(ProviderPermanentError):
        normalize_place(PLACE | change)


def test_request_validation_and_retry_after_formats():
    adapter = provider(lambda _: pytest.fail("Invalid input performed HTTP"))
    for request in (
        PlaceTextSearchRequest(""),
        PlaceTextSearchRequest("x", limit=21),
        PlaceTextSearchRequest("x", region_code="bad"),
    ):
        with pytest.raises(ProviderPermanentError):
            adapter.text_search(request)
    with pytest.raises(ProviderPermanentError):
        adapter.nearby_search(PlaceNearbySearchRequest(100, 0))
    with pytest.raises(ProviderConfigurationError):
        GooglePlacesProvider(api_key="")
    assert retry_after_seconds("bad") == 30
    assert retry_after_seconds("-1") == 1
    assert retry_after_seconds("999999") == 86400
    assert (
        30 <= retry_after_seconds(format_datetime(datetime.now(UTC) + timedelta(seconds=40))) <= 40
    )


def test_total_elapsed_budget_and_invalid_success_payloads():
    ticks = iter([0, 21])
    with patch(
        "integrations.google_places.provider.time", SimpleNamespace(monotonic=lambda: next(ticks))
    ):
        with pytest.raises(ProviderTimeoutError):
            provider(lambda _: httpx.Response(200, json={})).text_search(
                PlaceTextSearchRequest("x")
            )
    for payload in ({"error": "hidden"}, {"places": {}}, {"places": [PLACE, PLACE]}):
        with pytest.raises(ProviderPermanentError):
            provider(lambda _, data=payload: httpx.Response(200, json=data)).text_search(
                PlaceTextSearchRequest("x", limit=1)
            )


@pytest.mark.django_db
def test_cache_respects_country_snapshot_and_disabled_setting(member, google_enabled, settings):
    campaign = campaign_for(member)
    calls = []

    def handler(request):
        calls.append(json.loads(request.content)["regionCode"])
        return httpx.Response(200, json={"places": [PLACE]})

    with patch("apps.prospecting.execution.places_provider", return_value=provider(handler)):
        plan_for(member, campaign)
        assert JobRunner().run_once().status == "SUCCEEDED"
        save_campaign(
            user=member.user,
            workspace_id=member.workspace_id,
            pk=campaign.pk,
            data={"target_country_code": "PT"},
        )
        plan = plan_for(member, campaign)
        assert plan.country_code == "PT"
        assert JobRunner().run_once().status == "SUCCEEDED"
        settings.PLACES_CACHE_SECONDS = 0
        rerun(member, plan.items.get())
        assert JobRunner().run_once().status == "SUCCEEDED"
        assert not SearchRun.objects.first().cache_hit
    assert calls == ["ES", "PT", "PT"]
    assert PlacesCache.objects.count() == 2


@pytest.mark.django_db
def test_google_requires_explicit_configuration_and_csv_still_works(member, settings, client):
    settings.PLACES_PROVIDER = "google"
    for enabled, key in ((False, ""), (True, ""), (False, "fake")):
        settings.GOOGLE_PLACES_ENABLED, settings.GOOGLE_PLACES_API_KEY = enabled, key
        with pytest.raises(ProviderConfigurationError):
            places_provider()
        with pytest.raises(ValidationError):
            plan_for(member)
    assert not SearchPlan.objects.exists()
    plan_for(member, csv_content=csv_bytes([{"name": "Local CSV"}]))
    assert JobRunner().run_once().status == "SUCCEEDED"
    assert Company.objects.get().name == "Local CSV"
    client.force_login(member.user)
    response = client.get(reverse("prospecting:create", args=[member.workspace_id]))
    assert "Places desactivado" in response.content.decode()
    assert "fake" not in response.content.decode()


@pytest.mark.django_db(transaction=True)
def test_google_pipeline_provenance_caching_and_no_automatic_details(
    member, google_enabled, client
):
    campaign = campaign_for(member)
    calls = []

    def handler(request):
        assert not connection.in_atomic_block
        calls.append(request)
        return httpx.Response(200, json={"places": [PLACE], "nextPageToken": "PRIVATE_CURSOR"})

    adapter = provider(handler)
    with patch("apps.prospecting.execution.places_provider", return_value=adapter):
        plan = plan_for(member, campaign)
        assert plan.provider == "GOOGLE_PLACES" and plan.items.get().max_results == 20
        job = JobRunner().run_once()
        assert job.status == "SUCCEEDED", job.last_error
        run = SearchRun.objects.get()
        assert run.requests_count == 1 and not run.cache_hit
        assert run.provider_metadata["truncated"]
        campaign.refresh_from_db()
        assert campaign.places_requests_used == 1
        company = Company.objects.get()
        assert company.google_rating == Decimal("4.6") and company.google_review_count == 42
        assert company.latitude == Decimal("43.362346") and company.postal_code == "15001"
        source = CompanyExternalRef.objects.get()
        assert source.provider == "GOOGLE_PLACES" and source.external_id == PLACE["id"]
        assert source.external_url == PLACE["googleMapsUri"]
        rerun(member, plan.items.get())
        assert JobRunner().run_once().status == "SUCCEEDED"
        newest = SearchRun.objects.first()
        assert newest.cache_hit and newest.requests_count == 0
        assert len(calls) == 1 and Company.objects.count() == Lead.objects.count() == 1
        assert "PRIVATE_CURSOR" not in json.dumps(newest.snapshot)
        assert "MUST_NOT_STORE" not in json.dumps(newest.snapshot)
        PlacesCache.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        rerun(member, plan.items.get())
        assert JobRunner().run_once().status == "SUCCEEDED"
        assert len(calls) == 2
    client.force_login(member.user)
    response = client.get(reverse("prospecting:run", args=[member.workspace_id, run.pk]))
    assert "Peticiones HTTP reservadas: 1" in response.content.decode()
    assert "una sola página" in response.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status,error_type",
    [(429, "ProviderRateLimitError"), (503, "ProviderTemporaryError"), (403, "ProviderAuthError")],
)
def test_errors_reach_job_attempts_and_requests_are_counted(
    member, google_enabled, status, error_type
):
    plan = plan_for(member)
    adapter = provider(
        lambda _: httpx.Response(status, headers={"Retry-After": "90"}, json={"error": "SECRET"})
    )
    before = timezone.now()
    with patch("apps.prospecting.execution.places_provider", return_value=adapter):
        job = JobRunner().run_once()
    assert job.attempt_history.get().error_type == error_type
    assert "SECRET" not in job.last_error + job.attempt_history.get().traceback_excerpt
    assert job.status == ("FAILED" if status == 403 else "PENDING")
    if status == 429:
        assert job.available_at >= before + timedelta(seconds=90)
    run = SearchRun.objects.get()
    assert run.requests_count == 1
    plan.campaign.refresh_from_db()
    assert plan.campaign.places_requests_used == 1


@pytest.mark.django_db
def test_budget_blocks_before_http_and_preserves_counter_when_campaign_edited(
    member, google_enabled, settings
):
    settings.PLACES_CACHE_SECONDS = 0
    campaign = campaign_for(member)
    save_campaign(
        user=member.user,
        workspace_id=member.workspace_id,
        pk=campaign.pk,
        data={"places_request_limit": 1},
    )
    plan = plan_for(member, campaign)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503)

    with patch("apps.prospecting.execution.places_provider", return_value=provider(handler)):
        job = JobRunner().run_once()
        assert job.status == "PENDING"
        save_campaign(
            user=member.user,
            workspace_id=member.workspace_id,
            pk=campaign.pk,
            data={"name": "Edited"},
        )
        Job.objects.filter(pk=job.pk).update(available_at=timezone.now() - timedelta(seconds=1))
        job = JobRunner().run_once()
    assert job.status == "FAILED" and len(calls) == 1
    assert job.attempt_history.first().error_type == "ProviderBudgetError"
    campaign.refresh_from_db()
    assert campaign.places_requests_used == 1 and campaign.name == "Edited"
    assert plan.items.get().runs.get().requests_count == 1


@pytest.mark.django_db
def test_cache_never_crosses_workspaces_and_old_mock_jobs_remain_mock(
    member, google_enabled, settings
):
    settings.PLACES_PROVIDER = "mock"
    old_plan = plan_for(member)
    settings.PLACES_PROVIDER = "google"
    with patch(
        "integrations.google_places.provider.GooglePlacesProvider._request",
        side_effect=AssertionError("Mock became live"),
    ):
        assert JobRunner().run_once().status == "SUCCEEDED"
        JobService.enqueue(
            user=member.user,
            workspace_id=member.workspace_id,
            kind="CHECK_MOCK_PROVIDERS",
            payload={},
        )
        assert JobRunner().run_once().status == "SUCCEEDED"
    assert old_plan.provider == "MOCK_PLACES"
    other = MembershipFactory(role="OWNER")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"places": [PLACE]})

    with patch("apps.prospecting.execution.places_provider", return_value=provider(handler)):
        plan_for(member)
        assert JobRunner().run_once().status == "SUCCEEDED"
        plan_for(other)
        assert JobRunner().run_once().status == "SUCCEEDED"
    assert len(calls) == PlacesCache.objects.count() == 2
    assert CompanyExternalRef.objects.filter(provider="GOOGLE_PLACES").count() == 2
    assert outbound_provider("EMAIL").channel == "EMAIL"
