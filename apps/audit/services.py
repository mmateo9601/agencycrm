from django.db.models import Model

from apps.accounts.models import User

from .models import AuditEvent


def filtered_metadata(value):
    """Redact secret-bearing keys recursively before durable audit storage."""
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if any(
                word in key.lower().replace("_", "").replace("-", "")
                for word in ("password", "secret", "token", "authorization", "apikey")
            )
            else filtered_metadata(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [filtered_metadata(item) for item in value]
    return value


def record_event(
    *, actor: User | None, obj: Model, action: str, metadata: dict | None = None
) -> AuditEvent:
    """Record identifiers and explicit non-sensitive metadata inside the caller's transaction."""
    return AuditEvent.objects.create(
        workspace_id=obj.workspace_id,
        actor=actor,
        action=action,
        object_type=obj._meta.label,
        object_id=obj.pk,
        metadata=filtered_metadata(metadata or {}),
    )
