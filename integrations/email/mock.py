from integrations.common.mock import MockOutboundProvider


class MockEmailProvider(MockOutboundProvider):
    """Email simulation without SMTP, HTTP or a Django mail backend."""

    channel = "EMAIL"
