from django.conf import settings

from integrations.email.mock import MockEmailProvider
from integrations.google_places.mock import MockPlacesProvider
from integrations.whatsapp.mock import MockWhatsAppProvider

from .contracts import OutboundProvider, PlacesProvider
from .errors import ProviderConfigurationError, ProviderPermanentError


def _require_simulation(provider: str) -> None:
    if settings.OUTREACH_MODE != "simulation" or provider != "mock":
        raise ProviderPermanentError("Live providers are unavailable in this phase")


def places_provider(*, failure: str = "", provider: str | None = None) -> PlacesProvider:
    """Respect the plan's fixed provider; real acquisition is independent of messaging."""
    selected = provider or settings.PLACES_PROVIDER
    if selected == "mock":
        _require_simulation("mock")
        return MockPlacesProvider(failure=failure)
    if (
        selected != "google"
        or settings.PLACES_PROVIDER != "google"
        or not settings.GOOGLE_PLACES_ENABLED
        or not settings.GOOGLE_PLACES_API_KEY
        or failure
    ):
        raise ProviderConfigurationError
    from integrations.google_places.provider import GooglePlacesProvider

    return GooglePlacesProvider(api_key=settings.GOOGLE_PLACES_API_KEY)


def outbound_provider(channel: str, *, failure: str = "") -> OutboundProvider:
    """Select local mock implementations; there is no live fallback."""
    if channel == "EMAIL":
        _require_simulation(settings.EMAIL_PROVIDER)
        return MockEmailProvider(failure=failure)
    if channel == "WHATSAPP":
        _require_simulation(settings.WHATSAPP_PROVIDER)
        return MockWhatsAppProvider(failure=failure)
    raise ProviderPermanentError("Unsupported mock channel")
