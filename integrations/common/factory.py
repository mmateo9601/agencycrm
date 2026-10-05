from django.conf import settings

from integrations.email.mock import MockEmailProvider
from integrations.google_places.mock import MockPlacesProvider
from integrations.whatsapp.mock import MockWhatsAppProvider

from .contracts import OutboundProvider, PlacesProvider
from .errors import ProviderPermanentError


def _require_simulation(provider: str) -> None:
    if settings.OUTREACH_MODE != "simulation" or provider != "mock":
        raise ProviderPermanentError("Live providers are unavailable in this phase")


def places_provider(*, failure: str = "") -> PlacesProvider:
    """Fail closed if configuration requests anything other than simulation."""
    _require_simulation(settings.PLACES_PROVIDER)
    return MockPlacesProvider(failure=failure)


def outbound_provider(channel: str, *, failure: str = "") -> OutboundProvider:
    """Select local mock implementations; there is no live fallback."""
    if channel == "EMAIL":
        _require_simulation(settings.EMAIL_PROVIDER)
        return MockEmailProvider(failure=failure)
    if channel == "WHATSAPP":
        _require_simulation(settings.WHATSAPP_PROVIDER)
        return MockWhatsAppProvider(failure=failure)
    raise ProviderPermanentError("Unsupported mock channel")
