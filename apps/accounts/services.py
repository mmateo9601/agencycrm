from django.db import transaction

from apps.audit.models import AuditEvent

from .models import Membership, User


@transaction.atomic
def record_login(*, user: User) -> None:
    """Audit successful application logins without passwords, IPs or session identifiers."""
    for workspace_id in Membership.objects.filter(
        user=user, is_active=True, workspace__is_active=True
    ).values_list("workspace_id", flat=True):
        AuditEvent.objects.create(
            workspace_id=workspace_id,
            actor=user,
            action="LOGIN",
            object_type=User._meta.label,
            object_id=user.pk,
            metadata={},
        )
