from uuid import UUID

from django.core.exceptions import PermissionDenied

from apps.accounts.models import Membership, User, Workspace
from apps.accounts.selectors import workspace_for_user

WRITE_ROLES = {
    Membership.Role.OWNER,
    Membership.Role.ADMIN,
    Membership.Role.SALES,
    Membership.Role.MEMBER,
}
DELETE_ROLES = {Membership.Role.OWNER, Membership.Role.ADMIN}
CAPABILITY_ROLES = {
    "audit": DELETE_ROLES,
    "export": DELETE_ROLES,
    "retention": DELETE_ROLES,
    "configure": DELETE_ROLES,
    "approve": DELETE_ROLES,
}


def require_capability(*, user: User, workspace_id: UUID, capability: str) -> Workspace:
    """Authorize each sensitive operation from the current, active membership."""
    workspace = workspace_access(user=user, workspace_id=workspace_id)
    role = Membership.objects.get(user=user, workspace=workspace, is_active=True).role
    if role not in CAPABILITY_ROLES.get(capability, set()):
        raise PermissionDenied
    return workspace


def workspace_access(
    *, user: User, workspace_id: UUID, write: bool = False, delete: bool = False
) -> Workspace:
    """Check current membership on every operation, including direct service calls."""
    workspace = workspace_for_user(user=user, workspace_id=workspace_id)
    role = Membership.objects.get(user=user, workspace=workspace, is_active=True).role
    if (write and role not in WRITE_ROLES) or (delete and role not in DELETE_ROLES):
        raise PermissionDenied
    return workspace


def workspace_permissions(*, user: User, workspace: Workspace) -> dict:
    """Presentation permissions; services independently recheck authorization."""
    role = Membership.objects.get(user=user, workspace=workspace, is_active=True).role
    return {
        "can_write": role in WRITE_ROLES,
        "can_delete": role in DELETE_ROLES,
        **{f"can_{name}": role in roles for name, roles in CAPABILITY_ROLES.items()},
    }
