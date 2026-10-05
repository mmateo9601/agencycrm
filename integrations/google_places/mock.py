from integrations.common.contracts import (
    PlaceDetails,
    PlaceNearbySearchRequest,
    PlaceSearchPage,
    PlaceTextSearchRequest,
)
from integrations.common.errors import ProviderPermanentError
from integrations.common.mock import MockProvider

PLACES = (
    PlaceDetails("mock-norte", "Clínica Norte", "https://norte.example.invalid/", "A Coruña"),
    PlaceDetails(
        "mock-atlantico", "Clínica Atlántico", "https://atlantico.example.invalid/", "A Coruña"
    ),
    PlaceDetails("mock-demo", "Dental Demo", "https://dental.example.invalid/", "A Coruña"),
)


class MockPlacesProvider(MockProvider):
    """Fixed fictional fixtures, not real Google Places observations."""

    def text_search(self, request: PlaceTextSearchRequest) -> PlaceSearchPage:
        """Return a bounded fixture page without performing a search."""
        self.check_failure()
        if not request.query.strip() or not 1 <= request.limit <= 100:
            raise ProviderPermanentError
        return PlaceSearchPage(places=PLACES[: request.limit])

    def nearby_search(self, request: PlaceNearbySearchRequest) -> PlaceSearchPage:
        """Validate coordinates and return deterministic test fixtures."""
        self.check_failure()
        if not (
            -90 <= request.latitude <= 90
            and -180 <= request.longitude <= 180
            and 1 <= request.radius_m <= 50000
            and 1 <= request.limit <= 100
        ):
            raise ProviderPermanentError
        return PlaceSearchPage(places=PLACES[: request.limit])

    def get_details(self, external_id: str, fields: set[str]) -> PlaceDetails:
        """Resolve a mock ID; field selection is reserved for a future adapter."""
        self.check_failure()
        for place in PLACES:
            if place.external_id == external_id:
                return place
        raise ProviderPermanentError
