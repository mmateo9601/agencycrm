import hashlib

from .contracts import SendMessageRequest, SendMessageResult
from .errors import ProviderPermanentError, ProviderTemporaryError


class MockProvider:
    """Explicit failure injection with no network access or persistence."""

    def __init__(self, *, failure: str = "") -> None:
        if failure not in {"", "temporary", "permanent"}:
            raise ValueError("Unknown mock failure mode")
        self.failure = failure

    def check_failure(self) -> None:
        if self.failure == "temporary":
            raise ProviderTemporaryError
        if self.failure == "permanent":
            raise ProviderPermanentError


class MockOutboundProvider(MockProvider):
    channel: str

    def send(self, request: SendMessageRequest) -> SendMessageResult:
        """Return a stable simulated receipt; never send or save a message."""
        self.check_failure()
        if not request.idempotency_key or not request.recipient:
            raise ProviderPermanentError
        identity = f"{request.workspace_id}:{self.channel}:{request.idempotency_key}"
        digest = hashlib.sha256(identity.encode()).hexdigest()[:32]
        return SendMessageResult(provider_message_id=f"mock-{self.channel.lower()}-{digest}")
