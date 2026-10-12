import hashlib
import hmac
import json
import re

from django.conf import settings


def configuration_ready() -> bool:
    """Local completeness only; no requests or assumptions about the deployed Graph version."""
    return bool(
        settings.WHATSAPP_ACCESS_TOKEN
        and re.fullmatch(r"[0-9]{1,30}", settings.WHATSAPP_PHONE_NUMBER_ID)
        and re.fullmatch(r"[0-9]{1,30}", settings.WHATSAPP_WABA_ID)
        and re.fullmatch(r"v[0-9]{1,3}\.0", settings.WHATSAPP_GRAPH_API_VERSION)
        and len(settings.WHATSAPP_APP_SECRET) >= 32
        and len(settings.WHATSAPP_VERIFY_TOKEN) >= 32
        and 0 < settings.WHATSAPP_HTTP_TIMEOUT <= 120
    )


def configuration_fingerprint() -> str:
    values = [
        settings.WHATSAPP_ACCESS_TOKEN,
        settings.WHATSAPP_PHONE_NUMBER_ID,
        settings.WHATSAPP_WABA_ID,
        settings.WHATSAPP_GRAPH_API_VERSION,
        settings.WHATSAPP_APP_SECRET,
        settings.WHATSAPP_VERIFY_TOKEN,
    ]
    return hmac.new(
        settings.SECRET_KEY.encode(), json.dumps(values).encode(), hashlib.sha256
    ).hexdigest()
