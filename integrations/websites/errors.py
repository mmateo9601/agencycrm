from integrations.common.errors import ProviderError


class CrawlError(ProviderError):
    public_message = "No se pudo analizar la web con la política de rastreo."

    def __init__(self, code: str, *, retryable: bool = False, retry_after: int = 30):
        self.code = code
        self.retryable = retryable
        self.retry_after = retry_after
        super().__init__(self.public_message)
