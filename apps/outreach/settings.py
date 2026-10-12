from django.conf import settings

from apps.audit.services import record_event

from .models import OutreachSettings


def defaults() -> dict:
    return {
        "daily_email_limit": settings.MAX_EMAILS_PER_DAY,
        "daily_whatsapp_limit": settings.MAX_WHATSAPP_PER_DAY,
        "default_cooldown_days": settings.CONTACT_COOLDOWN_DAYS,
    }


def current_settings(workspace) -> OutreachSettings:
    """Read defaults without creating rows from a GET or preview."""
    return OutreachSettings.objects.filter(workspace=workspace).first() or OutreachSettings(
        workspace=workspace, **defaults()
    )


def ensure_settings(workspace, user) -> OutreachSettings:
    """Initialize workspace settings only in explicit write operations."""
    obj, created = OutreachSettings.objects.get_or_create(workspace=workspace, defaults=defaults())
    if created:
        record_event(actor=user, obj=obj, action="OUTREACH_SETTINGS_INITIALIZED")
    return obj
