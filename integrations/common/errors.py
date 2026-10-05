"""Failures expose fixed safe messages, never exception payloads or credentials."""


class ProviderError(Exception):
    public_message = "El proveedor simulado no pudo completar la operación."
    retryable = False


class ProviderTemporaryError(ProviderError):
    public_message = "Fallo temporal del proveedor simulado."
    retryable = True


class ProviderPermanentError(ProviderError):
    public_message = "Fallo permanente del proveedor simulado."


class ProviderRateLimitError(ProviderTemporaryError):
    public_message = "Límite temporal del proveedor."

    def __init__(self, retry_after: int = 30):
        if type(retry_after) is not int or not 1 <= retry_after <= 86400:
            raise ValueError("Invalid retry interval")
        self.retry_after = retry_after
        super().__init__(self.public_message)
