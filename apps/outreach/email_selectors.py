from common.selectors import scoped_records
from integrations.common.contracts import SendMessageRequest
from integrations.email.smtp import configuration_fingerprint, configuration_ready, message_id

from .models import EmailConnection


def connection_for_user(*, user, workspace_id):
    """Read tenant connection without testing credentials against an external server."""
    return scoped_records(model=EmailConnection, user=user, workspace_id=workspace_id).first()


def connection_ready(connection) -> bool:
    return bool(
        connection
        and connection.is_enabled
        and configuration_ready()
        and connection.configuration_fingerprint == configuration_fingerprint()
    )


def message_connection_ready(message) -> bool:
    connection = EmailConnection.objects.filter(
        pk=message.email_connection_id,
        workspace_id=message.workspace_id,
        revision=message.connection_revision,
        sender_email=message.sender_email,
        sender_name=message.sender_name,
    ).first()
    return connection_ready(connection)


def expected_message_id(message) -> str:
    return message_id(
        SendMessageRequest(
            message.workspace_id,
            message.idempotency_key,
            message.recipient,
            "",
            sender_email=message.sender_email,
        )
    )
