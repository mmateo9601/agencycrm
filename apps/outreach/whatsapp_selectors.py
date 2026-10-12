from common.selectors import scoped_records
from integrations.whatsapp.configuration import configuration_fingerprint, configuration_ready

from .models import WhatsAppConnection, WhatsAppTemplate


def connection_for_user(*, user, workspace_id):
    return scoped_records(model=WhatsAppConnection, user=user, workspace_id=workspace_id).first()


def connection_ready(connection) -> bool:
    return bool(
        connection
        and connection.is_enabled
        and configuration_ready()
        and connection.configuration_fingerprint == configuration_fingerprint()
    )


def message_connection_ready(message) -> bool:
    connection = WhatsAppConnection.objects.filter(
        pk=message.whatsapp_connection_id,
        workspace_id=message.workspace_id,
        revision=message.connection_revision,
    ).first()
    return bool(
        connection_ready(connection)
        and WhatsAppTemplate.objects.filter(
            pk=message.whatsapp_template_id,
            workspace_id=message.workspace_id,
            connection=connection,
            is_active=True,
            approved_in_meta=True,
            revision=message.template_snapshot.get("revision"),
        ).exists()
    )
