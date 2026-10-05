import uuid

import pytest
from django.contrib.auth.models import AnonymousUser
from django.db import IntegrityError, transaction
from django.http import Http404

from apps.accounts.models import Membership
from apps.accounts.selectors import (
    agency_for_user,
    services_for_user,
    workspace_for_user,
    workspaces_for_user,
)
from tests.factories import (
    AgencyProfileFactory,
    MembershipFactory,
    ServiceOfferingFactory,
    UserFactory,
    WorkspaceFactory,
)

pytestmark = pytest.mark.django_db


def test_workspace_data_is_not_visible_to_other_workspace():
    member = MembershipFactory()
    own_service = ServiceOfferingFactory(workspace=member.workspace)
    own_agency = AgencyProfileFactory(workspace=member.workspace)
    other_service = ServiceOfferingFactory()
    AgencyProfileFactory(workspace=other_service.workspace)
    assert list(workspaces_for_user(user=member.user)) == [member.workspace]
    assert list(services_for_user(user=member.user, workspace_id=member.workspace_id)) == [
        own_service
    ]
    assert agency_for_user(user=member.user, workspace_id=member.workspace_id) == own_agency
    for selector in (workspace_for_user, services_for_user, agency_for_user):
        with pytest.raises(Http404):
            selector(user=member.user, workspace_id=other_service.workspace_id)
    with pytest.raises(own_service.DoesNotExist):
        services_for_user(user=member.user, workspace_id=member.workspace_id).get(
            pk=other_service.pk
        )


@pytest.mark.parametrize("inactive", ["user", "workspace", "membership"])
def test_inactive_access_is_denied(inactive):
    membership = MembershipFactory()
    obj = {"user": membership.user, "workspace": membership.workspace, "membership": membership}[
        inactive
    ]
    obj.is_active = False
    obj.save()
    assert not workspaces_for_user(user=membership.user).exists()
    with pytest.raises(Http404):
        services_for_user(user=membership.user, workspace_id=membership.workspace_id)


def test_anonymous_and_superuser_do_not_bypass_application_scope():
    workspace = WorkspaceFactory()
    for user in (AnonymousUser(), UserFactory(is_superuser=True, is_staff=True)):
        assert not workspaces_for_user(user=user).exists()
        with pytest.raises(Http404):
            workspace_for_user(user=user, workspace_id=workspace.pk)


def test_multiple_memberships_still_require_one_workspace():
    first = MembershipFactory()
    second = MembershipFactory(user=first.user)
    service = ServiceOfferingFactory(workspace=first.workspace)
    ServiceOfferingFactory(workspace=second.workspace)
    assert list(services_for_user(user=first.user, workspace_id=first.workspace_id)) == [service]


def test_database_constraints():
    member = MembershipFactory()
    service = ServiceOfferingFactory(workspace=member.workspace)
    AgencyProfileFactory(workspace=member.workspace)
    for create in (
        lambda: MembershipFactory(workspace=member.workspace, user=member.user),
        lambda: MembershipFactory(role="INVALID"),
        lambda: WorkspaceFactory(slug=member.workspace.slug),
        lambda: AgencyProfileFactory(workspace=member.workspace),
        lambda: ServiceOfferingFactory(workspace=member.workspace, code=service.code),
    ):
        with pytest.raises(IntegrityError), transaction.atomic():
            create()
    assert ServiceOfferingFactory(code=service.code).workspace_id != member.workspace_id


def test_uuid_timestamps_and_display_names():
    membership = MembershipFactory(role=Membership.Role.OWNER)
    agency = AgencyProfileFactory(workspace=membership.workspace)
    service = ServiceOfferingFactory(workspace=membership.workspace)
    for obj in (membership.user, membership.workspace, membership, agency, service):
        assert isinstance(obj.pk, uuid.UUID)
    for obj in (membership.workspace, membership, agency, service):
        assert obj.created_at.tzinfo is not None
        assert obj.updated_at.tzinfo is not None
    assert str(agency) == agency.trading_name
    assert str(service) == service.name
    assert str(membership.workspace) == membership.workspace.name
