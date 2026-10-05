from integrations.common.mock import MockOutboundProvider


class MockWhatsAppProvider(MockOutboundProvider):
    """WhatsApp simulation without browser or remote API access."""

    channel = "WHATSAPP"
