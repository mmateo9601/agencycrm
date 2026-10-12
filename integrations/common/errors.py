"""Failures expose fixed safe messages, never exception payloads or credentials."""


class ProviderError(Exception):
    public_message = "El proveedor no pudo completar la operación."
    retryable = False


class ProviderTemporaryError(ProviderError):
    public_message = "Fallo temporal del proveedor."
    retryable = True


class ProviderPermanentError(ProviderError):
    public_message = "Fallo permanente del proveedor."


class EmailConfigurationError(ProviderPermanentError):
    public_message = "Email real desactivado o configuración SMTP segura incompleta."


class WhatsAppConfigurationError(ProviderPermanentError):
    public_message = "WhatsApp Cloud desactivado o configuración segura incompleta."


class ProviderDeliveryUnknownError(ProviderPermanentError):
    public_message = (
        "Aceptación del proveedor incierta. Revisa el resultado antes de resolver; "
        "no se reenvía automáticamente."
    )


class ProviderAuthError(ProviderPermanentError):
    public_message = "Credenciales o permisos del proveedor no válidos."


class ProviderConfigurationError(ProviderPermanentError):
    public_message = "Google Places está desactivado o no tiene credenciales configuradas."


class ProviderBudgetError(ProviderPermanentError):
    public_message = "Se ha agotado el presupuesto de peticiones de esta campaña."


class ProviderTimeoutError(ProviderTemporaryError):
    public_message = "El proveedor ha superado el tiempo de espera."


class ProviderRateLimitError(ProviderTemporaryError):
    public_message = "Límite temporal del proveedor."

    def __init__(self, retry_after: int = 30):
        if type(retry_after) is not int or not 1 <= retry_after <= 86400:
            raise ValueError("Invalid retry interval")
        self.retry_after = retry_after
        super().__init__(self.public_message)
