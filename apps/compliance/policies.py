import hashlib
import json
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import Membership
from apps.crm.models import Lead
from apps.outreach.models import MessageEvent
from apps.outreach.settings import current_settings

from .models import SuppressionEntry

POLICY_VERSION = "outreach-v2"
AUTOMATED = {"EMAIL", "WHATSAPP"}
REASON_LABELS = {
    "WORKSPACE_INACTIVE": "Espacio de trabajo inactivo.",
    "CAMPAIGN_OR_COMPANY_INACTIVE": "Campaña no preparada/pausada o empresa archivada.",
    "LEAD_NOT_READY": "El lead debe estar preparado para contacto y sin respuesta o cierre.",
    "SUPPRESSED": "Empresa o contacto bloqueado por supresión o no contactar.",
    "NOT_BUSINESS_CONTACT": "El contacto no está identificado como empresarial.",
    "RECIPIENT_MISSING": "Faltan datos del canal; WhatsApp necesita su número explícito.",
    "RECIPIENT_CHANGED": "El destinatario cambió; prepara y aprueba un nuevo mensaje.",
    "PROPOSAL_APPROVAL_REQUIRED": "La versión de propuesta requiere aprobación vigente.",
    "MESSAGE_APPROVAL_REQUIRED": "El mensaje requiere aprobación humana.",
    "APPROVER_INACTIVE": "El aprobador ya no es un propietario/administrador activo.",
    "CONTENT_CHANGED": "El contenido o adjuntos no coinciden con lo aprobado.",
    "POLICY_REVIEW_REQUIRED": "Falta confirmación humana de la política aplicable.",
    "EMERGENCY_PAUSE": "Todos los contactos están pausados por el operador.",
    "LIVE_PROVIDER_DISABLED": "Modo/proveedor no habilitado o falta doble activación del email.",
    "EMAIL_CONNECTION_INVALID": "Conexión SMTP desactivada, incompleta o distinta de la aprobada.",
    "DELIVERY_UNCERTAIN": "Entrega pendiente de reconciliación; no se permite reenviar.",
    "CHANNEL_DISABLED": "El canal no está habilitado en la campaña.",
    "NOT_DUE": "Todavía no ha llegado la fecha programada.",
    "ATTEMPT_LIMIT": "Se alcanzó el máximo de intentos para esta empresa/canal.",
    "COOLDOWN": "No ha transcurrido el periodo mínimo desde el último contacto.",
    "DAILY_LIMIT": "Se alcanzó el límite diario del canal.",
}


def describe_reasons(codes: list[str]) -> list[str]:
    return [REASON_LABELS.get(code, code) for code in codes]


def recipient_for(lead, contact, channel: str) -> str:
    """WhatsApp requires its explicit field; a normal phone never implies WhatsApp."""
    if channel == "EMAIL":
        return contact.normalized_email if contact else ""
    if channel == "WHATSAPP":
        return contact.whatsapp_e164 if contact else ""
    if channel == "PHONE":
        return (contact.phone_e164 if contact else "") or lead.company.phone_e164
    if channel == "CONTACT_FORM":
        return lead.company.website
    if channel in {"LINKEDIN", "INSTAGRAM"}:
        return (
            lead.company.social_profiles.filter(workspace_id=lead.workspace_id, platform=channel)
            .order_by("id")
            .values_list("url", flat=True)
            .first()
            or ""
        )
    return lead.company.name


def approval_digest(message) -> str:
    data = {
        "proposal": str(message.proposal_id),
        "contact": str(message.contact_id),
        "channel": message.channel,
        "recipient": message.recipient,
        "subject": message.subject,
        "body": message.body_text,
        "files": list(message.attachments.order_by("filename").values_list("filename", "sha256")),
    }
    # Preserve mock approvals; real approvals also bind the frozen sender and transport.
    if message.provider == "smtp":
        data.update(
            provider=message.provider,
            connection=str(message.email_connection_id),
            connection_revision=message.connection_revision,
            sender=message.sender_email,
            sender_name=message.sender_name,
        )
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def suppressed(message, now) -> bool:
    company, contact = message.lead.company, message.contact
    emails = {contact.normalized_email} if contact and contact.normalized_email else set()
    phones = {company.phone_e164} - {""}
    if contact:
        phones |= {contact.phone_e164, contact.whatsapp_e164} - {""}
    domains = {company.normalized_domain} - {""}
    domains |= {
        email.rsplit("@", 1)[-1].encode("idna").decode("ascii").strip("[]").lower()
        for email in emails
    }
    entries = SuppressionEntry.objects.filter(workspace_id=message.workspace_id).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=now)
    )
    if entries.filter(
        Q(scope="COMPANY", normalized_value=str(company.pk))
        | Q(scope="EMAIL", normalized_value__in=emails)
        | Q(scope="PHONE", normalized_value__in=phones)
    ).exists():
        return True
    return any(
        domain == blocked or domain.endswith("." + blocked)
        for domain in domains
        for blocked in entries.filter(scope="DOMAIN").values_list("normalized_value", flat=True)
    )


def eligibility(message, *, now=None, reserved: bool = False) -> tuple[str, list[str]]:
    """Compute conservative eligibility from current tenant facts without mutation or I/O."""
    now = now or timezone.now()
    lead, contact, proposal = message.lead, message.contact, message.proposal
    config = current_settings(message.workspace)
    reasons = []
    if not message.workspace.is_active:
        reasons.append("WORKSPACE_INACTIVE")
    if (
        lead.is_archived
        or not lead.company.is_active
        or lead.campaign.status not in {"READY", "RUNNING"}
    ):
        reasons.append("CAMPAIGN_OR_COMPANY_INACTIVE")
    if lead.status not in {"READY", "CONTACTED"}:
        reasons.append("LEAD_NOT_READY")
    if (
        lead.company.do_not_contact
        or (contact and contact.do_not_contact)
        or suppressed(message, now)
    ):
        reasons.append("SUPPRESSED")
    if contact and not contact.is_business_contact:
        reasons.append("NOT_BUSINESS_CONTACT")
    current = recipient_for(lead, contact, message.channel)
    if not current:
        reasons.append("RECIPIENT_MISSING")
    elif current != message.recipient:
        reasons.append("RECIPIENT_CHANGED")
    if proposal.status != "APPROVED" or not proposal.approved_at or not proposal.approved_by_id:
        reasons.append("PROPOSAL_APPROVAL_REQUIRED")
    if not message.approved_at or not message.approved_by_id:
        reasons.append("MESSAGE_APPROVAL_REQUIRED")
    else:
        if not Membership.objects.filter(
            workspace_id=message.workspace_id,
            user_id=message.approved_by_id,
            user__is_active=True,
            is_active=True,
            role__in=["OWNER", "ADMIN"],
        ).exists():
            reasons.append("APPROVER_INACTIVE")
        if message.approval_hash != approval_digest(message):
            reasons.append("CONTENT_CHANGED")
    if not message.policy_review_confirmed:
        reasons.append("POLICY_REVIEW_REQUIRED")
    if config.is_paused:
        reasons.append("EMERGENCY_PAUSE")
    real_email = message.channel == "EMAIL" and message.provider == "smtp"
    live_allowed = (
        real_email
        and settings.OUTREACH_MODE == "live"
        and settings.EMAIL_PROVIDER == "smtp"
        and config.live_mode_enabled
    )
    simulation_allowed = settings.OUTREACH_MODE == "simulation" and (
        message.channel not in AUTOMATED
        or message.provider == "mock"
        and (settings.EMAIL_PROVIDER if message.channel == "EMAIL" else settings.WHATSAPP_PROVIDER)
        == "mock"
    )
    manual_allowed = message.channel not in AUTOMATED and settings.OUTREACH_MODE == "live"
    if not (live_allowed or simulation_allowed or manual_allowed):
        reasons.append("LIVE_PROVIDER_DISABLED")
    if real_email:
        from apps.outreach.email_selectors import message_connection_ready

        if not message_connection_ready(message):
            reasons.append("EMAIL_CONNECTION_INVALID")
    if message.status == "UNKNOWN":
        reasons.append("DELIVERY_UNCERTAIN")
    channel = lead.campaign.channels.filter(
        workspace_id=message.workspace_id, channel=message.channel, enabled=True
    ).first()
    if not channel:
        reasons.append("CHANNEL_DISABLED")
    if message.scheduled_at > now:
        reasons.append("NOT_DUE")
    attempts = MessageEvent.objects.filter(
        workspace_id=message.workspace_id, event_type="SEND_ATTEMPT"
    )
    if reserved:
        attempts = attempts.exclude(
            message=message, provider_event_id=f"attempt:{message.send_attempts}"
        )
    company_attempts = attempts.filter(message__lead__company_id=lead.company_id)
    if company_attempts.count() >= config.max_attempts_per_company or (
        channel
        and company_attempts.filter(message__channel=message.channel).count()
        >= channel.max_attempts
    ):
        reasons.append("ATTEMPT_LIMIT")
    cooldown = max(config.default_cooldown_days, channel.cooldown_days if channel else 0)
    if (
        not reserved
        and Lead.objects.filter(
            company_id=lead.company_id,
            workspace_id=message.workspace_id,
            last_contact_at__gt=now - timedelta(days=cooldown),
        ).exists()
    ):
        reasons.append("COOLDOWN")
    if message.channel in AUTOMATED:
        start = datetime.combine(
            timezone.localtime(now, ZoneInfo(settings.TIME_ZONE)).date(),
            time.min,
            ZoneInfo(settings.TIME_ZONE),
        )
        limit = (
            config.daily_email_limit if message.channel == "EMAIL" else config.daily_whatsapp_limit
        )
        if (
            attempts.filter(
                message__channel=message.channel,
                occurred_at__gte=start,
                occurred_at__lt=start + timedelta(days=1),
            ).count()
            >= limit
        ):
            reasons.append("DAILY_LIMIT")
    if not reasons:
        return "ELIGIBLE", []
    review = {"MESSAGE_APPROVAL_REQUIRED", "PROPOSAL_APPROVAL_REQUIRED", "POLICY_REVIEW_REQUIRED"}
    return ("REVIEW_REQUIRED" if set(reasons) <= review else "INELIGIBLE"), reasons
