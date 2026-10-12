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


def outbound_provider(channel: str, *, failure: str = "", message=None) -> OutboundProvider:
    """Simulation selects mocks; SMTP requires the authorized frozen workspace identity."""
    if channel == "EMAIL":
        if settings.OUTREACH_MODE == "live":
            from apps.outreach.email_selectors import message_connection_ready
            from apps.outreach.settings import current_settings
            from integrations.email.smtp import SMTPEmailProvider

            from .errors import EmailConfigurationError

            if (
                settings.EMAIL_PROVIDER != "smtp"
                or failure
                or message is None
                or message.provider != "smtp"
                or message.channel != "EMAIL"
                or not current_settings(message.workspace).live_mode_enabled
                or not message_connection_ready(message)
            ):
                raise EmailConfigurationError
            return SMTPEmailProvider()
        _require_simulation(settings.EMAIL_PROVIDER)
        return MockEmailProvider(failure=failure)
    if channel == "WHATSAPP":
        if settings.OUTREACH_MODE == "live":
            from apps.outreach.settings import current_settings
            from apps.outreach.whatsapp_selectors import message_connection_ready
            from integrations.whatsapp.provider import WhatsAppCloudProvider

            from .errors import WhatsAppConfigurationError

            if (
                settings.WHATSAPP_PROVIDER != "cloud"
                or failure
                or message is None
                or message.provider != "cloud"
                or message.channel != "WHATSAPP"
                or not current_settings(message.workspace).live_mode_enabled
                or not message_connection_ready(message)
            ):
                raise WhatsAppConfigurationError
            return WhatsAppCloudProvider()
        _require_simulation(settings.WHATSAPP_PROVIDER)
        return MockWhatsAppProvider(failure=failure)
    raise ProviderPermanentError("Unsupported mock channel")
