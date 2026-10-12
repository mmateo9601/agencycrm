from uuid import UUID

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.services import record_event
from common.access import workspace_access
from common.selectors import scoped_record
from integrations.whatsapp.configuration import configuration_fingerprint, configuration_ready

from .models import WhatsAppConnection, WhatsAppTemplate
from .settings import ensure_settings
from .whatsapp_selectors import connection_for_user, connection_ready


@transaction.atomic
def configure_connection(
    *, user: User, workspace_id: UUID, enabled: bool, expected_revision: int
) -> WhatsAppConnection:
    """Authorize the server-managed phone; its assignment is permanent to avoid callback leakage."""
    workspace = workspace_access(user=user, workspace_id=workspace_id, delete=True)
    if type(enabled) is not bool or enabled and not configuration_ready():
        raise ValidationError("Completa primero la configuración Cloud API segura en el entorno.")
    ensure_settings(workspace, user)
    connection = connection_for_user(user=user, workspace_id=workspace_id)
    if not connection:
        if (
            not enabled
            or expected_revision != 0
            or WhatsAppConnection.objects.filter(
                phone_number_id=settings.WHATSAPP_PHONE_NUMBER_ID
            ).exists()
        ):
            raise ValidationError("Conexión no disponible o número ya asignado a otro workspace.")
        connection = WhatsAppConnection.objects.create(
            workspace=workspace,
            phone_number_id=settings.WHATSAPP_PHONE_NUMBER_ID,
            waba_id=settings.WHATSAPP_WABA_ID,
            api_version=settings.WHATSAPP_GRAPH_API_VERSION,
            configuration_fingerprint=configuration_fingerprint(),
            is_enabled=True,
            revision=1,
        )
    else:
        if enabled and (
            connection.phone_number_id != settings.WHATSAPP_PHONE_NUMBER_ID
            or connection.waba_id != settings.WHATSAPP_WABA_ID
        ):
            raise ValidationError("No reasignes una conexión histórica a otro número o WABA.")
        if not WhatsAppConnection.objects.filter(
            pk=connection.pk, workspace_id=workspace_id, revision=expected_revision
        ).update(
            is_enabled=enabled,
            api_version=settings.WHATSAPP_GRAPH_API_VERSION if enabled else connection.api_version,
            configuration_fingerprint=configuration_fingerprint(),
            revision=expected_revision + 1,
            updated_at=timezone.now(),
        ):
            raise ValidationError("Revisión de conexión antigua.")
    connection.refresh_from_db()
    record_event(
        actor=user,
        obj=connection,
        action="WHATSAPP_CONNECTION_CHANGED",
        metadata={"enabled": enabled},
    )
    return connection


@transaction.atomic
def create_template(*, user: User, workspace_id: UUID, data: dict) -> WhatsAppTemplate:
    """Register an operator-verified Meta template without creating or approving it remotely."""
    workspace = workspace_access(user=user, workspace_id=workspace_id, delete=True)
    connection = connection_for_user(user=user, workspace_id=workspace_id)
    allowed = {
        "name",
        "language",
        "category",
        "body_template",
        "document_header",
        "approved_in_meta",
    }
    if (
        not connection_ready(connection)
        or set(data) != allowed
        or data["approved_in_meta"] is not True
    ):
        raise ValidationError("Autoriza la conexión y confirma la plantilla aprobada en Meta.")
    template = WhatsAppTemplate.objects.create(workspace=workspace, connection=connection, **data)
    record_event(actor=user, obj=template, action="WHATSAPP_TEMPLATE_REGISTERED")
    return template


@transaction.atomic
def disable_template(*, user: User, workspace_id: UUID, pk: UUID) -> None:
    workspace_access(user=user, workspace_id=workspace_id, delete=True)
    template = scoped_record(model=WhatsAppTemplate, user=user, workspace_id=workspace_id, pk=pk)
    if template.is_active:
        WhatsAppTemplate.objects.filter(pk=pk, workspace_id=workspace_id).update(
            is_active=False, revision=template.revision + 1, updated_at=timezone.now()
        )
        record_event(actor=user, obj=template, action="WHATSAPP_TEMPLATE_DISABLED")
