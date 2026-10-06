"""Google Places API (New). Pure normalized transport; no ORM or domain mutations."""

import json
import math
import re
import time
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime

import httpx

from integrations.common.contracts import (
    PlaceDetails,
    PlaceNearbySearchRequest,
    PlaceSearchPage,
    PlaceTextSearchRequest,
)
from integrations.common.errors import (
    ProviderAuthError,
    ProviderConfigurationError,
    ProviderPermanentError,
    ProviderRateLimitError,
    ProviderTemporaryError,
    ProviderTimeoutError,
)

BASE_URL = "https://places.googleapis.com/v1/"
FIELDS = {
    "external_id": "id",
    "name": "displayName",
    "address": "formattedAddress",
    "city": "addressComponents",
    "province": "addressComponents",
    "postal_code": "addressComponents",
    "country_code": "addressComponents",
    "latitude": "location",
    "longitude": "location",
    "phone": "internationalPhoneNumber",
    "website": "websiteUri",
    "rating": "rating",
    "review_count": "userRatingCount",
    "external_url": "googleMapsUri",
}
SEARCH_FIELDS = sorted(set(FIELDS.values()))
FIELD_MASK = ",".join(f"places.{field}" for field in SEARCH_FIELDS)
MAX_BYTES = 1_000_000


def retry_after_seconds(value: str | None) -> int:
    """Accept Retry-After seconds or HTTP dates, bounded to the job retry policy."""
    try:
        seconds = int(value or "30")
    except ValueError:
        try:
            seconds = math.ceil((parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
        except ValueError, TypeError, OverflowError:
            seconds = 30
    return min(86400, max(1, seconds))


def _text(value: dict, key: str, maximum: int = 500) -> str:
    result = value.get(key, "")
    if not isinstance(result, str) or len(result) > maximum:
        raise ProviderPermanentError
    return result


def _number(value: object, low: int, high: int, places: int) -> str:
    if value is None:
        return ""
    try:
        number = Decimal(str(value))
        if not number.is_finite() or not low <= number <= high:
            raise ValueError
        return str(number.quantize(Decimal(10) ** -places))
    except InvalidOperation, ValueError, TypeError:
        raise ProviderPermanentError from None


def normalize_place(value: dict) -> PlaceDetails:
    """Discard unknown provider fields and return only fields used by Company/source."""
    if not isinstance(value, dict):
        raise ProviderPermanentError
    external_id = _text(value, "id", 255)
    display = value.get("displayName", {})
    components = value.get("addressComponents", [])
    location = value.get("location", {})
    if (
        not external_id
        or not isinstance(display, dict)
        or not isinstance(components, list)
        or not isinstance(location, dict)
    ):
        raise ProviderPermanentError
    parts = {}
    for component in components:
        if not isinstance(component, dict) or not isinstance(component.get("types", []), list):
            raise ProviderPermanentError
        for kind in component.get("types", []):
            if not isinstance(kind, str):
                raise ProviderPermanentError
            parts[kind] = _text(component, "shortText" if kind == "country" else "longText", 300)
    reviews = value.get("userRatingCount")
    if reviews is not None and (type(reviews) is not int or not 0 <= reviews <= 2_147_483_647):
        raise ProviderPermanentError
    return PlaceDetails(
        external_id=external_id,
        name=_text(display, "text", 200),
        website=_text(value, "websiteUri"),
        phone=_text(value, "internationalPhoneNumber", 40),
        city=parts.get("locality", parts.get("postal_town", "")),
        country_code=parts.get("country", ""),
        province=parts.get("administrative_area_level_2", ""),
        postal_code=parts.get("postal_code", ""),
        address=_text(value, "formattedAddress", 300),
        latitude=_number(location.get("latitude"), -90, 90, 6),
        longitude=_number(location.get("longitude"), -180, 180, 6),
        rating=_number(value.get("rating"), 0, 5, 1),
        review_count="" if reviews is None else str(reviews),
        external_url=_text(value, "googleMapsUri"),
    )


class GooglePlacesProvider:
    """One request per method, with fixed origin, explicit masks and no hidden retries."""

    def __init__(self, *, api_key: str, transport: httpx.BaseTransport | None = None):
        if (
            not isinstance(api_key, str)
            or not api_key.strip()
            or "\n" in api_key
            or "\r" in api_key
        ):
            raise ProviderConfigurationError
        self._api_key = api_key
        self._transport = transport

    def _request(self, method: str, path: str, mask: str, payload: dict | None = None) -> dict:
        started = time.monotonic()
        try:
            with httpx.Client(
                transport=self._transport,
                timeout=httpx.Timeout(10, connect=5),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                with client.stream(
                    method,
                    BASE_URL + path,
                    json=payload,
                    headers={
                        "X-Goog-Api-Key": self._api_key,
                        "X-Goog-FieldMask": mask,
                        "Accept": "application/json",
                        "Accept-Encoding": "identity",
                    },
                ) as response:
                    if time.monotonic() - started > 20:
                        raise ProviderTimeoutError
                    if response.status_code in {401, 403}:
                        raise ProviderAuthError
                    if response.status_code == 429:
                        raise ProviderRateLimitError(
                            retry_after_seconds(response.headers.get("retry-after"))
                        )
                    if response.status_code >= 500:
                        raise ProviderTemporaryError
                    if (
                        response.status_code != 200
                        or response.headers.get("content-type", "").split(";")[0].strip().lower()
                        != "application/json"
                    ):
                        raise ProviderPermanentError
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise ProviderPermanentError
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        if time.monotonic() - started > 20:
                            raise ProviderTimeoutError
                        if len(data) + len(chunk) > MAX_BYTES:
                            raise ProviderPermanentError
                        data.extend(chunk)
                    result = json.loads(data)
                    if not isinstance(result, dict) or "error" in result:
                        raise ProviderPermanentError
                    return result
        except httpx.TimeoutException:
            raise ProviderTimeoutError from None
        except httpx.RequestError:
            raise ProviderTemporaryError from None
        except ValueError, UnicodeError:
            raise ProviderPermanentError from None

    def text_search(self, request: PlaceTextSearchRequest) -> PlaceSearchPage:
        """Fetch one bounded Text Search page; no automatic paging or Details fan-out."""
        if (
            not request.query.strip()
            or len(request.query) > 250
            or len(request.location) > 250
            or type(request.limit) is not int
            or not 1 <= request.limit <= 20
            or not re.fullmatch(r"[A-Z]{2}", request.region_code)
        ):
            raise ProviderPermanentError
        result = self._request(
            "POST",
            "places:searchText",
            FIELD_MASK + ",nextPageToken",
            {
                "textQuery": " ".join(
                    filter(None, (request.query.strip(), request.location.strip()))
                ),
                "pageSize": request.limit,
                "languageCode": "es",
                "regionCode": request.region_code,
            },
        )
        return self._page(result, request.limit)

    def nearby_search(self, request: PlaceNearbySearchRequest) -> PlaceSearchPage:
        """Fetch one Nearby Search page around an explicit validated circle."""
        if not (
            -90 <= request.latitude <= 90
            and -180 <= request.longitude <= 180
            and 1 <= request.radius_m <= 50000
            and type(request.limit) is int
            and 1 <= request.limit <= 20
        ):
            raise ProviderPermanentError
        result = self._request(
            "POST",
            "places:searchNearby",
            FIELD_MASK,
            {
                "maxResultCount": request.limit,
                "languageCode": "es",
                "locationRestriction": {
                    "circle": {
                        "center": {"latitude": request.latitude, "longitude": request.longitude},
                        "radius": request.radius_m,
                    }
                },
            },
        )
        return self._page(result, request.limit)

    def get_details(self, external_id: str, fields: set[str]) -> PlaceDetails:
        """Request only explicitly supported fields; never accepts a user-provided URL."""
        if (
            not re.fullmatch(r"[A-Za-z0-9_-]{1,255}", external_id)
            or not fields
            or not fields <= FIELDS.keys()
        ):
            raise ProviderPermanentError
        mask = ",".join(sorted({"id"} | {FIELDS[field] for field in fields}))
        return normalize_place(self._request("GET", "places/" + external_id, mask))

    @staticmethod
    def _page(result: dict, limit: int) -> PlaceSearchPage:
        places = result.get("places", [])
        if not isinstance(places, list) or len(places) > limit:
            raise ProviderPermanentError
        # Tokens are not stored or exposed: this phase intentionally uses one page.
        return PlaceSearchPage(
            tuple(normalize_place(place) for place in places),
            truncated=bool(result.get("nextPageToken")),
        )
