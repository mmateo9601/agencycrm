"""Explicit workspace scoping, including for staff and superusers in the application."""

from uuid import UUID

from django.db.models import QuerySet
from django.shortcuts import get_object_or_404

from .models import AgencyProfile, ServiceOffering, User, Workspace


def workspaces_for_user(*, user: User) -> QuerySet[Workspace]:
    """Return only active workspaces with an active membership for this user."""
    if not user.is_authenticated or not user.is_active:
        return Workspace.objects.none()
    return Workspace.objects.filter(
        is_active=True,
        memberships__user=user,
        memberships__is_active=True,
    ).order_by("name", "id")


def workspace_for_user(*, user: User, workspace_id: UUID) -> Workspace:
    """Resolve an authorized workspace or return the same 404 as a missing object."""
    return get_object_or_404(workspaces_for_user(user=user), pk=workspace_id)


def services_for_user(*, user: User, workspace_id: UUID) -> QuerySet[ServiceOffering]:
    """List services in one authorized workspace, never across all memberships."""
    workspace = workspace_for_user(user=user, workspace_id=workspace_id)
    return ServiceOffering.objects.filter(workspace=workspace)


def agency_for_user(*, user: User, workspace_id: UUID) -> AgencyProfile:
    """Retrieve the agency profile only after checking workspace membership."""
    workspace = workspace_for_user(user=user, workspace_id=workspace_id)
    return get_object_or_404(AgencyProfile, workspace=workspace)
