"""Authenticated TLS SMTP; no database access, logging of secrets, or blind DATA retries."""

import hashlib
import hmac
import json
import smtplib
import ssl
from contextlib import suppress
from email.headerregistry import Address
from email.message import EmailMessage
from email.policy import SMTP

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from integrations.common.contracts import SendMessageRequest, SendMessageResult
from integrations.common.errors import (
    EmailConfigurationError,
    ProviderAuthError,
    ProviderDeliveryUnknownError,
    ProviderPermanentError,
    ProviderTemporaryError,
)


def configuration_ready() -> bool:
    """Inspect configuration only; readiness does not claim a verified remote login."""
    try:
        validate_email(settings.OUTREACH_SMTP_SENDER)
        host = settings.OUTREACH_SMTP_HOST
        return bool(
            host
            and not any(char.isspace() for char in host)
            and not any(char in host for char in "/@\\")
            and settings.OUTREACH_SMTP_USERNAME
            and settings.OUTREACH_SMTP_PASSWORD
            and settings.OUTREACH_SMTP_SECURITY in {"starttls", "ssl"}
            and 1 <= settings.OUTREACH_SMTP_PORT <= 65535
            and 0 < settings.OUTREACH_SMTP_TIMEOUT <= 120
        )
    except ValidationError, TypeError, ValueError:
        return False


def configuration_fingerprint() -> str:
    """Bind prepared messages to server-managed credentials without storing those secrets."""
    data = [
        settings.OUTREACH_SMTP_HOST,
        settings.OUTREACH_SMTP_PORT,
        settings.OUTREACH_SMTP_USERNAME,
        settings.OUTREACH_SMTP_PASSWORD,
        settings.OUTREACH_SMTP_SENDER,
        settings.OUTREACH_SMTP_SECURITY,
    ]
    return hmac.new(
        settings.SECRET_KEY.encode(), json.dumps(data).encode(), hashlib.sha256
    ).hexdigest()


def message_id(request: SendMessageRequest) -> str:
    """Stable RFC Message-ID for reconciliation; SMTP offers no deduplication guarantee."""
    digest = hashlib.sha256(
        f"{request.workspace_id}:{request.idempotency_key}".encode()
    ).hexdigest()
    return f"<{digest}@agencycrm.local>"


class SMTPEmailProvider:
    channel = "EMAIL"

    def send(self, request: SendMessageRequest) -> SendMessageResult:
        """Submit once; transport failures during DATA are uncertain and nonretryable."""
        if (
            settings.OUTREACH_MODE != "live"
            or settings.EMAIL_PROVIDER != "smtp"
            or not configuration_ready()
            or request.sender_email != settings.OUTREACH_SMTP_SENDER
        ):
            raise EmailConfigurationError
        try:
            validate_email(request.recipient)
            if any(
                "\r" in value or "\n" in value
                for value in (
                    request.recipient,
                    request.sender_email,
                    request.sender_name,
                    request.subject,
                )
            ):
                raise ValueError
            mail = EmailMessage(policy=SMTP)
            mail["From"] = Address(display_name=request.sender_name, addr_spec=request.sender_email)
            mail["To"] = request.recipient
            mail["Subject"] = request.subject
            mail["Message-ID"] = identity = message_id(request)
            mail.set_content(request.body_text)
            if len(request.attachments) > 6:
                raise ValueError
            for attachment in request.attachments:
                if (
                    attachment.mime_type != "application/pdf"
                    or len(attachment.data) > 2_000_000
                    or any(char in attachment.filename for char in "\r\n/\\")
                ):
                    raise ValueError
                mail.add_attachment(
                    attachment.data,
                    maintype="application",
                    subtype="pdf",
                    filename=attachment.filename,
                )
        except ValidationError, ValueError, TypeError:
            raise ProviderPermanentError from None
        client = None
        submitting = False
        try:
            context = ssl.create_default_context()
            if settings.OUTREACH_SMTP_SECURITY == "ssl":
                client = smtplib.SMTP_SSL(
                    settings.OUTREACH_SMTP_HOST,
                    settings.OUTREACH_SMTP_PORT,
                    timeout=settings.OUTREACH_SMTP_TIMEOUT,
                    context=context,
                )
            else:
                client = smtplib.SMTP(
                    settings.OUTREACH_SMTP_HOST,
                    settings.OUTREACH_SMTP_PORT,
                    timeout=settings.OUTREACH_SMTP_TIMEOUT,
                )
                client.ehlo()
                client.starttls(context=context)
                client.ehlo()
            client.login(settings.OUTREACH_SMTP_USERNAME, settings.OUTREACH_SMTP_PASSWORD)
            submitting = True
            refused = client.send_message(
                mail, from_addr=request.sender_email, to_addrs=[request.recipient]
            )
            if refused:
                raise ProviderPermanentError
            return SendMessageResult(provider_message_id=identity, simulated=False)
        except smtplib.SMTPAuthenticationError:
            raise ProviderAuthError from None
        except smtplib.SMTPRecipientsRefused as error:
            codes = [response[0] for response in error.recipients.values()]
            failure = (
                ProviderTemporaryError
                if codes and all(400 <= c < 500 for c in codes)
                else ProviderPermanentError
            )
            raise failure from None
        except smtplib.SMTPResponseException as error:
            failure = (
                ProviderTemporaryError if 400 <= error.smtp_code < 500 else ProviderPermanentError
            )
            raise failure from None
        except ssl.SSLError:
            failure = ProviderDeliveryUnknownError if submitting else ProviderPermanentError
            raise failure from None
        except smtplib.SMTPServerDisconnected:
            failure = ProviderDeliveryUnknownError if submitting else ProviderTemporaryError
            raise failure from None
        except smtplib.SMTPException:
            failure = ProviderDeliveryUnknownError if submitting else ProviderPermanentError
            raise failure from None
        except OSError:
            failure = ProviderDeliveryUnknownError if submitting else ProviderTemporaryError
            raise failure from None
        finally:
            if client is not None:
                # close() never submits QUIT and cannot lose an already accepted receipt.
                with suppress(OSError):
                    client.close()
