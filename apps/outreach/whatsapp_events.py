"""Trusted domain mutations called only after authentication of the Meta webhook."""

import re
from datetime import UTC, datetime
from uuid import UUID

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.utils import timezone

from apps.audit.services import record_event
from apps.companies.models import Contact
from apps.crm.models import Lead, LeadStatusHistory
from apps.jobs.models import Job
from apps.proposals.services import transition_proposal
from common.normalization import normalize_phone
from integrations.common.errors import ProviderDeliveryUnknownError

from .models import InboundWhatsAppMessage, MessageEvent, OutreachMessage, WhatsAppConnection


def valid_provider_id(value) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"wamid\.[A-Za-z0-9_+=/.-]{1,390}", value))


def observed_time(value) -> datetime:
    try:
        number = int(value)
        if not 946684800 <= number <= 4102444800:
            raise ValueError
        return datetime.fromtimestamp(number, UTC)
    except ValueError, TypeError, OverflowError:
        raise ValidationError("Fecha de webhook no válida.") from None


def add_event(message, kind: str, identity: str, stamp: datetime, payload=None) -> bool:
    return MessageEvent.objects.get_or_create(
        workspace_id=message.workspace_id,
        message=message,
        event_type=kind,
        provider_event_id=identity,
        defaults={"occurred_at": stamp, "payload": payload or {}},
    )[1]


def transition_verified_lead(message, target: str) -> None:
    """System transition backed by a verified event; closed leads never reopen."""
    lead = Lead.objects.get(pk=message.lead_id, workspace_id=message.workspace_id)
    previous = "READY" if target == "CONTACTED" else "CONTACTED"
    if lead.status != previous or lead.is_archived:
        return
    revision = lead.status_revision + 1
    if Lead.objects.filter(
        pk=lead.pk,
        workspace_id=message.workspace_id,
        status=previous,
        status_revision=lead.status_revision,
    ).update(status=target, status_revision=revision, updated_at=timezone.now()):
        LeadStatusHistory.objects.create(
            lead=lead,
            from_status=previous,
            to_status=target,
            revision=revision,
            actor=None,
            reason="Evento WhatsApp firmado y verificado",
        )
        record_event(
            actor=None,
            obj=lead,
            action="LEAD_STATUS_CHANGED",
            metadata={
                "from": previous,
                "to": target,
                "revision": revision,
                "source": "whatsapp_webhook",
            },
        )


def record_cloud_receipt(
    message: OutreachMessage, user, receipt: str, occurred_at: datetime | None = None
) -> None:
    """Record acceptance once without overwriting a later delivered/read/replied status."""
    if not valid_provider_id(receipt):
        raise ProviderDeliveryUnknownError
    message.refresh_from_db()
    if message.provider_message_id and message.provider_message_id != receipt:
        raise ProviderDeliveryUnknownError
    stamp = message.sent_at or occurred_at or timezone.now()
    preserve = bool(message.provider_message_id) and message.status in {
        "DELIVERED",
        "READ",
        "REPLIED",
        "FAILED",
    }
    changes = {
        "provider_message_id": receipt,
        "sent_at": message.sent_at or stamp,
        "updated_at": timezone.now(),
    }
    if not preserve:
        changes.update(status="SENT", failed_at=None, error_code="", error_message="")
    OutreachMessage.objects.filter(pk=message.pk, workspace_id=message.workspace_id).update(
        **changes
    )
    add_event(message, "PROVIDER_ACCEPTED", receipt, stamp, {"simulated": False})
    accepted = add_event(message, "SENT", receipt, stamp, {"simulated": False})
    if accepted:
        record_event(actor=user, obj=message, action="MESSAGE_SENT", metadata={"simulated": False})
    Lead.objects.filter(pk=message.lead_id, workspace_id=message.workspace_id).filter(
        Q(last_contact_at__isnull=True) | Q(last_contact_at__lt=stamp)
    ).update(last_contact_at=stamp)
    transition_verified_lead(message, "CONTACTED")
    if accepted:
        try:
            with transaction.atomic():
                if message.approved_by is None:
                    raise ValidationError("Aprobador no disponible.")
                proposal = message.proposal
                transition_proposal(
                    user=message.approved_by,
                    workspace_id=message.workspace_id,
                    pk=proposal.pk,
                    target="SENT",
                    expected_revision=proposal.revision,
                )
        except PermissionDenied, ValidationError, Http404:
            record_event(actor=None, obj=message, action="MESSAGE_STATE_RECONCILIATION_REQUIRED")
        if message.job_id and Job.objects.filter(
            pk=message.job_id, workspace_id=message.workspace_id, status="FAILED"
        ).update(
            status="SUCCEEDED",
            last_error="",
            finished_at=timezone.now(),
            result={"message_id": str(message.pk), "receipt": receipt, "reconciled": True},
        ):
            record_event(actor=None, obj=message, action="WHATSAPP_JOB_RECONCILED")
    message.refresh_from_db()
    if accepted:
        from .followups import contact_confirmed

        contact_confirmed(message)


def process_status(connection: WhatsAppConnection, value: dict) -> None:
    """Match by tenant, business phone, recipient and provider ID or reserved callback identity."""
    identity, status = value.get("id"), value.get("status")
    if not valid_provider_id(identity) or status not in {"sent", "delivered", "read", "failed"}:
        return
    recipient = normalize_phone("+" + str(value.get("recipient_id", "")).lstrip("+"), "ES")
    records = OutreachMessage.objects.filter(
        workspace_id=connection.workspace_id,
        whatsapp_connection=connection,
        provider="cloud",
        channel="WHATSAPP",
        recipient=recipient,
        approved_at__isnull=False,
    )
    message = records.filter(provider_message_id=identity).first()
    if not message:
        try:
            key = UUID(value.get("biz_opaque_callback_data", ""))
        except ValueError, TypeError, AttributeError:
            return
        message = records.filter(
            pk=key, provider_message_id="", transport_started_at__isnull=False
        ).first()
    if not message:
        return
    stamp = observed_time(value.get("timestamp"))
    # Failed callbacks also prove provider acceptance, not successful delivery.
    record_cloud_receipt(message, None, identity, stamp)
    target = {"sent": "SENT", "delivered": "DELIVERED", "read": "READ", "failed": "FAILED"}[status]
    if target == "SENT":
        return
    payload = {}
    if target == "FAILED":
        errors = value.get("errors", [])
        if isinstance(errors, list):
            payload["error_codes"] = [
                item["code"]
                for item in errors[:5]
                if isinstance(item, dict) and type(item.get("code")) is int
            ]
    if not add_event(message, target, identity, stamp, payload):
        return
    rank = {"SENT": 1, "DELIVERED": 2, "READ": 3, "REPLIED": 4}
    changes = {"updated_at": timezone.now()}
    if target == "FAILED":
        if message.status not in {"DELIVERED", "READ", "REPLIED"}:
            changes.update(
                status="FAILED",
                failed_at=stamp,
                error_code="WHATSAPP_DELIVERY_FAILED",
                error_message="Fallo de entrega notificado por WhatsApp; sin reenvío automático.",
            )
    elif rank.get(target, 0) > rank.get(message.status, 0):
        changes.update(status=target, failed_at=None, error_code="", error_message="")
    if target in {"DELIVERED", "READ"} and not message.delivered_at:
        changes["delivered_at"] = stamp
    OutreachMessage.objects.filter(pk=message.pk, workspace_id=connection.workspace_id).update(
        **changes
    )
    record_event(actor=None, obj=message, action="WHATSAPP_" + target)
    if target == "FAILED":
        from .followups import reconcile

        reconcile(workspace_id=connection.workspace_id, company_id=message.lead.company_id)


def process_inbound(connection: WhatsAppConnection, value: dict) -> None:
    """Retain bounded business content and reply context; never download incoming media."""
    identity = value.get("id")
    sender = normalize_phone("+" + str(value.get("from", "")).lstrip("+"), "ES")
    if not valid_provider_id(identity) or not sender:
        return
    context = value.get("context", {})
    reply = OutreachMessage.objects.filter(
        workspace_id=connection.workspace_id,
        whatsapp_connection=connection,
        provider="cloud",
        recipient=sender,
        provider_message_id=context.get("id", "") if isinstance(context, dict) else "",
    )
    reply = reply.exclude(provider_message_id="").first()
    contacts = list(
        Contact.objects.filter(workspace_id=connection.workspace_id, whatsapp_e164=sender)[:2]
    )
    contact = reply.contact if reply else contacts[0] if len(contacts) == 1 else None
    kind = value.get("type", "other")
    kind = (
        kind
        if kind in {"text", "document", "image", "audio", "video", "button", "interactive"}
        else "other"
    )
    text = value.get("text", {}).get("body", "") if isinstance(value.get("text"), dict) else ""
    text = text[:4000] if isinstance(text, str) and kind == "text" else ""
    inbound, created = InboundWhatsAppMessage.objects.get_or_create(
        workspace_id=connection.workspace_id,
        provider_message_id=identity,
        defaults={
            "connection": connection,
            "sender_e164": sender,
            "received_at": observed_time(value.get("timestamp")),
            "message_type": kind,
            "body_text": text,
            "reply_to": reply,
            "contact": contact,
        },
    )
    if not created:
        return
    record_event(actor=None, obj=inbound, action="WHATSAPP_INBOUND_RECEIVED")
    if reply and add_event(reply, "REPLIED", identity, inbound.received_at):
        OutreachMessage.objects.filter(pk=reply.pk, workspace_id=connection.workspace_id).update(
            status="REPLIED"
        )
        transition_verified_lead(reply, "REPLIED")
    if inbound.contact_id:
        from .followups import reconcile

        reconcile(
            workspace_id=connection.workspace_id,
            company_id=inbound.contact.company_id,
            force_reason="RESPONSE",
        )


@transaction.atomic
def process_webhook(*, connection: WhatsAppConnection, payload: dict) -> None:
    """Process a small signed batch atomically without retaining raw provider payloads."""
    if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
        raise ValidationError("Formato de webhook no válido.")
    entries = payload.get("entry", [])
    if not isinstance(entries, list) or len(entries) > 10:
        raise ValidationError("Lote demasiado grande.")
    count = 0
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("id") != connection.waba_id:
            continue
        changes = entry.get("changes", [])
        if not isinstance(changes, list) or len(changes) > 10:
            raise ValidationError("Cambios no válidos.")
        for change in changes:
            if not isinstance(change, dict) or change.get("field") != "messages":
                continue
            value = change.get("value", {})
            if not isinstance(value, dict) or not isinstance(value.get("metadata"), dict):
                continue
            if value["metadata"].get("phone_number_id") != connection.phone_number_id:
                continue
            for key, handler in (("statuses", process_status), ("messages", process_inbound)):
                items = value.get(key, [])
                if not isinstance(items, list):
                    raise ValidationError("Eventos no válidos.")
                count += len(items)
                if count > 50 or any(not isinstance(item, dict) for item in items):
                    raise ValidationError("Lote de eventos no válido o excesivo.")
                for item in items:
                    handler(connection, item)
