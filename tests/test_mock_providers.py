import uuid

import pytest

from integrations.common.contracts import (
    PlaceNearbySearchRequest,
    PlaceTextSearchRequest,
    SendMessageRequest,
)
from integrations.common.errors import (
    ProviderPermanentError,
    ProviderRateLimitError,
    ProviderTemporaryError,
)
from integrations.common.factory import outbound_provider, places_provider
from integrations.google_places.mock import MockPlacesProvider


def test_mock_places_are_fictional_deterministic_and_bounded():
    provider = MockPlacesProvider()
    page = provider.text_search(PlaceTextSearchRequest(query="Dentistas Galicia", limit=2))
    assert len(page.places) == 2 and page.next_cursor is None
    assert all(".invalid/" in place.website for place in page.places)
    assert provider.text_search(PlaceTextSearchRequest(query="Dentistas Galicia", limit=2)) == page
    assert provider.get_details(page.places[0].external_id, {"name"}) == page.places[0]
    assert len(provider.nearby_search(PlaceNearbySearchRequest(43.36, -8.41)).places) == 3
    with pytest.raises(ProviderPermanentError):
        provider.get_details("missing", set())
    with pytest.raises(ProviderPermanentError):
        provider.nearby_search(PlaceNearbySearchRequest(100, -8))
    with pytest.raises(ProviderPermanentError):
        provider.text_search(PlaceTextSearchRequest(query=""))


def test_mock_outbound_receipts_are_stable_scoped_and_channel_specific():
    workspace = uuid.uuid4()
    request = SendMessageRequest(workspace, "key", "fixture@example.invalid", "Fictitious")
    email = outbound_provider("EMAIL")
    first = email.send(request)
    assert first.simulated and first == email.send(request)
    assert (
        outbound_provider("WHATSAPP").send(request).provider_message_id != first.provider_message_id
    )
    other = SendMessageRequest(uuid.uuid4(), "key", request.recipient, request.body_text)
    assert email.send(other).provider_message_id != first.provider_message_id
    with pytest.raises(ProviderPermanentError):
        email.send(SendMessageRequest(workspace, "", "", ""))


def test_simulation_mode_never_calls_live_provider(settings):
    # Socket and DNS access are blocked by the global autouse fixture.
    assert places_provider().text_search(PlaceTextSearchRequest("fixture")).places
    request = SendMessageRequest(uuid.uuid4(), "fixture", "fixture@example.invalid", "Fixture")
    assert outbound_provider("EMAIL").send(request).simulated
    assert outbound_provider("WHATSAPP").send(request).simulated
    settings.EMAIL_PROVIDER = "gmail"
    settings.WHATSAPP_PROVIDER = "cloud"
    settings.PLACES_PROVIDER = "google"
    for factory in (
        places_provider,
        lambda: outbound_provider("EMAIL"),
        lambda: outbound_provider("WHATSAPP"),
    ):
        with pytest.raises(ProviderPermanentError):
            factory()
    settings.EMAIL_PROVIDER = settings.WHATSAPP_PROVIDER = settings.PLACES_PROVIDER = "mock"
    settings.OUTREACH_MODE = "live"
    with pytest.raises(ProviderPermanentError):
        outbound_provider("EMAIL")


def test_mock_failure_modes_and_invalid_configuration():
    with pytest.raises(ProviderTemporaryError):
        places_provider(failure="temporary").text_search(PlaceTextSearchRequest("fixture"))
    with pytest.raises(ProviderPermanentError):
        places_provider(failure="permanent").text_search(PlaceTextSearchRequest("fixture"))
    with pytest.raises(ValueError):
        places_provider(failure="unknown")
    with pytest.raises(ProviderPermanentError):
        outbound_provider("SMS")
    with pytest.raises(ValueError):
        ProviderRateLimitError(retry_after=-1)
