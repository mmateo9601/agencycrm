from django.db.models import Model

from apps.accounts.models import User

from .models import AuditEvent


def record_event(
    *, actor: User, obj: Model, action: str, metadata: dict | None = None
) -> AuditEvent:
    """Record identifiers and explicit non-sensitive metadata inside the caller's transaction."""
    return AuditEvent.objects.create(
        workspace_id=obj.workspace_id,
        actor=actor,
        action=action,
        object_type=obj._meta.label,
        object_id=obj.pk,
        metadata=metadata or {},
    )
