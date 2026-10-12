from uuid import UUID

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.services import record_event
from common.access import workspace_access
from integrations.email.smtp import configuration_fingerprint, configuration_ready

from .email_selectors import connection_for_user, connection_ready
from .models import EmailConnection
from .settings import ensure_settings


@transaction.atomic
def configure_connection(
    *, user: User, workspace_id: UUID, sender_name: str, enabled: bool, expected_revision: int
) -> EmailConnection:
    """Authorize the server-configured identity for this workspace; no secrets or remote I/O."""
    workspace = workspace_access(user=user, workspace_id=workspace_id, delete=True)
    if type(enabled) is not bool or len(sender_name) > 150 or any(c in sender_name for c in "\r\n"):
        raise ValidationError("Identidad o estado no válido.")
    if enabled and not configuration_ready():
        raise ValidationError("Completa primero la configuración SMTP con TLS en el entorno.")
    ensure_settings(workspace, user)
    connection = connection_for_user(user=user, workspace_id=workspace_id)
    if connection is None:
        if not enabled or expected_revision != 0:
            raise ValidationError("Conexión inexistente o revisión antigua.")
        connection = EmailConnection.objects.create(
            workspace=workspace,
            sender_email=settings.OUTREACH_SMTP_SENDER,
            sender_name=sender_name.strip(),
            configuration_fingerprint=configuration_fingerprint(),
            is_enabled=True,
            revision=1,
        )
    elif not EmailConnection.objects.filter(pk=connection.pk, revision=expected_revision).update(
        sender_email=settings.OUTREACH_SMTP_SENDER if enabled else connection.sender_email,
        sender_name=sender_name.strip(),
        configuration_fingerprint=configuration_fingerprint(),
        is_enabled=enabled,
        revision=expected_revision + 1,
        updated_at=timezone.now(),
    ):
        raise ValidationError("La conexión ha cambiado; recarga la página.")
    connection.refresh_from_db()
    record_event(
        actor=user,
        obj=connection,
        action="EMAIL_CONNECTION_ENABLED" if enabled else "EMAIL_CONNECTION_DISABLED",
    )
    return connection


@transaction.atomic
def set_live_mode(*, user: User, workspace_id: UUID, enabled: bool, expected_revision: int) -> None:
    """Independent explicit workspace switch; never alter the global environment switch."""
    workspace = workspace_access(user=user, workspace_id=workspace_id, delete=True)
    if type(enabled) is not bool:
        raise ValidationError("Estado no válido.")
    if enabled and not connection_ready(connection_for_user(user=user, workspace_id=workspace_id)):
        raise ValidationError("Autoriza primero una conexión SMTP configurada.")
    config = ensure_settings(workspace, user)
    if (
        not type(config)
        .objects.filter(pk=config.pk, revision=expected_revision)
        .update(live_mode_enabled=enabled, revision=F("revision") + 1, updated_at=timezone.now())
    ):
        raise ValidationError("Los límites han cambiado; recarga la página.")
    config.refresh_from_db()
    record_event(actor=user, obj=config, action="LIVE_MODE_CHANGED", metadata={"enabled": enabled})
