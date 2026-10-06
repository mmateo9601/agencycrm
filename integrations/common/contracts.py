from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class PlaceTextSearchRequest:
    query: str
    location: str = ""
    limit: int = 20
    region_code: str = "ES"


@dataclass(frozen=True)
class PlaceNearbySearchRequest:
    latitude: float
    longitude: float
    radius_m: int = 1000
    limit: int = 20


@dataclass(frozen=True)
class PlaceDetails:
    external_id: str
    name: str
    website: str
    city: str
    country_code: str = "ES"
    phone: str = ""
    address: str = ""
    province: str = ""
    postal_code: str = ""
    latitude: str = ""
    longitude: str = ""
    rating: str = ""
    review_count: str = ""
    external_url: str = ""


@dataclass(frozen=True)
class PlaceSearchPage:
    places: tuple[PlaceDetails, ...]
    next_cursor: str | None = None
    truncated: bool = False


class PlacesProvider(Protocol):
    """Return normalized snapshots without persisting domain entities."""

    def text_search(self, request: PlaceTextSearchRequest) -> PlaceSearchPage: ...
    def nearby_search(self, request: PlaceNearbySearchRequest) -> PlaceSearchPage: ...
    def get_details(self, external_id: str, fields: set[str]) -> PlaceDetails: ...


@dataclass(frozen=True)
class SendMessageRequest:
    workspace_id: UUID
    idempotency_key: str
    recipient: str
    body_text: str
    subject: str = ""


@dataclass(frozen=True)
class SendMessageResult:
    provider_message_id: str
    simulated: bool = True


class OutboundProvider(Protocol):
    """Transport only; eligibility and approval belong to application services."""

    channel: str

    def send(self, request: SendMessageRequest) -> SendMessageResult: ...
