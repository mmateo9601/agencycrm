"""Cloud API transport with a fixed HTTPS origin and no domain mutations."""

import re
from uuid import UUID

import httpx
from django.conf import settings

from integrations.common.contracts import SendMessageRequest, SendMessageResult
from integrations.common.errors import (
    ProviderAuthError,
    ProviderDeliveryUnknownError,
    ProviderPermanentError,
    ProviderRateLimitError,
    ProviderTemporaryError,
    WhatsAppConfigurationError,
)
from integrations.common.http import retry_after_seconds

from .configuration import configuration_ready


class WhatsAppCloudProvider:
    channel = "WHATSAPP"

    def __init__(self, *, transport: httpx.BaseTransport | None = None):
        self.transport = transport

    def _request(self, client: httpx.Client, path: str, *, submission: bool, **kwargs) -> dict:
        """Limit response size; never retry an ambiguous message submission automatically."""
        try:
            with client.stream("POST", path, **kwargs) as response:
                if response.status_code >= 500 or response.is_redirect:
                    raise ProviderDeliveryUnknownError if submission else ProviderTemporaryError
                if response.status_code in {401, 403}:
                    raise ProviderAuthError
                if response.status_code == 429:
                    raise ProviderRateLimitError(
                        retry_after_seconds(response.headers.get("Retry-After"))
                    )
                if not 200 <= response.status_code < 300:
                    raise ProviderPermanentError
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > 65536:
                        raise ValueError
                import json

                value = json.loads(data)
                if not isinstance(value, dict):
                    raise ValueError
                return value
        except httpx.ConnectError, httpx.ConnectTimeout:
            raise ProviderTemporaryError from None
        except httpx.TransportError, ValueError:
            failure = ProviderDeliveryUnknownError if submission else ProviderTemporaryError
            raise failure from None

    def send(self, request: SendMessageRequest) -> SendMessageResult:
        """Send one approved template, optionally uploading its one private proposal PDF."""
        if (
            settings.OUTREACH_MODE != "live"
            or settings.WHATSAPP_PROVIDER != "cloud"
            or not configuration_ready()
        ):
            raise WhatsAppConfigurationError
        template = request.whatsapp_template
        if (
            template is None
            or not re.fullmatch(r"[a-z0-9_]{1,100}", template.name)
            or not re.fullmatch(r"[a-z]{2,3}(?:_[A-Z]{2})?", template.language)
            or not re.fullmatch(r"\+[1-9][0-9]{7,14}", request.recipient)
            or len(template.parameters) > 10
            or any(
                not isinstance(value, str) or not 1 <= len(value) <= 1000
                for value in template.parameters
            )
        ):
            raise ProviderPermanentError
        try:
            UUID(request.correlation_id)
        except ValueError, TypeError:
            raise ProviderPermanentError from None
        components = []
        if template.parameters:
            components.append(
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": value} for value in template.parameters
                    ],
                }
            )
        if template.document_header:
            if (
                len(request.attachments) != 1
                or request.attachments[0].mime_type != "application/pdf"
                or not 1 <= len(request.attachments[0].data) <= 2_000_000
            ):
                raise ProviderPermanentError
        elif request.attachments:
            raise ProviderPermanentError
        root = f"https://graph.facebook.com/{settings.WHATSAPP_GRAPH_API_VERSION}/{settings.WHATSAPP_PHONE_NUMBER_ID}/"
        with httpx.Client(
            base_url=root,
            headers={"Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}"},
            timeout=settings.WHATSAPP_HTTP_TIMEOUT,
            follow_redirects=False,
            trust_env=False,
            transport=self.transport,
        ) as client:
            if template.document_header:
                attachment = request.attachments[0]
                result = self._request(
                    client,
                    "media",
                    submission=False,
                    data={"messaging_product": "whatsapp", "type": "application/pdf"},
                    files={"file": (attachment.filename, attachment.data, "application/pdf")},
                )
                media_id = result.get("id")
                if not isinstance(media_id, str) or not re.fullmatch(r"[0-9]{1,100}", media_id):
                    raise ProviderPermanentError
                components.append(
                    {
                        "type": "header",
                        "parameters": [
                            {
                                "type": "document",
                                "document": {"id": media_id, "filename": attachment.filename},
                            }
                        ],
                    }
                )
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": request.recipient.lstrip("+"),
                "type": "template",
                "template": {
                    "name": template.name,
                    "language": {"code": template.language},
                    "components": components,
                },
                "biz_opaque_callback_data": request.correlation_id,
            }
            result = self._request(client, "messages", submission=True, json=payload)
            messages = result.get("messages")
            identity = (
                messages[0].get("id")
                if isinstance(messages, list)
                and len(messages) == 1
                and isinstance(messages[0], dict)
                else None
            )
            if not isinstance(identity, str) or not re.fullmatch(
                r"wamid\.[A-Za-z0-9_+=/.-]{1,390}", identity
            ):
                raise ProviderDeliveryUnknownError
            return SendMessageResult(identity, simulated=False)
