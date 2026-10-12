import hashlib
import hmac
import re
from types import SimpleNamespace

from django.conf import settings
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from apps.outreach.models import WhatsAppConnection


class MetaWebhookAuthentication(BaseAuthentication):
    """Machine-only endpoint: raw-body HMAC, no cookies, sessions or human user privileges."""

    def authenticate(self, request):
        if not settings.WHATSAPP_WEBHOOK_ENABLED or len(settings.WHATSAPP_APP_SECRET) < 32:
            raise AuthenticationFailed("Webhook no disponible.")
        connection = WhatsAppConnection.objects.filter(
            workspace_id=request.parser_context["kwargs"]["workspace_id"]
        ).first()
        if not connection:
            raise AuthenticationFailed("Webhook no disponible.")
        if request.method == "GET":
            token = request.query_params.get("hub.verify_token", "")
            if (
                len(settings.WHATSAPP_VERIFY_TOKEN) < 32
                or request.query_params.get("hub.mode") != "subscribe"
                or not hmac.compare_digest(token.encode(), settings.WHATSAPP_VERIFY_TOKEN.encode())
                or not re.fullmatch(r"[0-9]{1,100}", request.query_params.get("hub.challenge", ""))
            ):
                raise AuthenticationFailed("Verificación no válida.")
        elif request.method == "POST":
            try:
                length = int(request.META.get("CONTENT_LENGTH", "0"))
            except ValueError:
                raise AuthenticationFailed("Tamaño no válido.") from None
            if not 0 < length <= 131072:
                raise AuthenticationFailed("Tamaño no válido.")
            body = request.body
            if len(body) > 131072:
                raise AuthenticationFailed("Tamaño no válido.")
            signature = request.headers.get("X-Hub-Signature-256", "")
            expected = (
                "sha256="
                + hmac.new(settings.WHATSAPP_APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
            )
            if not hmac.compare_digest(signature.encode(), expected.encode()):
                raise AuthenticationFailed("Firma no válida.")
        else:
            raise AuthenticationFailed("Método no válido.")
        return SimpleNamespace(is_authenticated=True, connection=connection), None
