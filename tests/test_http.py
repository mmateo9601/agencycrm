from unittest.mock import patch

import pytest
from django.contrib.auth.models import Permission
from django.db import OperationalError
from django.test import Client
from django.urls import reverse
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.settings import api_settings

from tests.factories import (
    AgencyProfileFactory,
    MembershipFactory,
    ServiceOfferingFactory,
    UserFactory,
    WorkspaceFactory,
)

pytestmark = pytest.mark.django_db


def test_health_checks_database_and_hides_errors(client):
    response = client.get(reverse("health"))
    assert response.status_code == 200
    assert response.json() == {"app": "ok", "database": "ok"}
    with patch("common.health.connection.cursor", side_effect=OperationalError("secret detail")):
        response = client.get(reverse("health"))
    assert response.status_code == 503
    assert response.json() == {"app": "ok", "database": "unavailable"}
    assert client.post(reverse("health")).status_code == 405


def test_session_login_logout_and_csrf():
    user = UserFactory()
    client = Client(enforce_csrf_checks=True)
    assert client.get(reverse("home")).status_code == 302
    response = client.get(reverse("login"))
    assert response.status_code == 200
    assert b"/static/vendor/bootstrap/bootstrap.min.css" in response.content
    credentials = {"username": user.username, "password": "Test-password-42!"}
    assert client.post(reverse("login"), credentials).status_code == 403
    credentials["csrfmiddlewaretoken"] = client.cookies["csrftoken"].value
    response = client.post(reverse("login"), credentials)
    assert response.status_code == 302
    assert client.session["_auth_user_id"] == str(user.pk)
    assert client.get(reverse("home")).status_code == 200
    assert client.get(reverse("logout")).status_code == 405
    assert client.post(reverse("logout")).status_code == 403
    response = client.post(
        reverse("logout"),
        {
            "csrfmiddlewaretoken": client.cookies["csrftoken"].value,
        },
    )
    assert response.status_code == 302
    assert "_auth_user_id" not in client.session


def test_bad_login_and_external_redirect(client):
    user = UserFactory()
    assert (
        client.post(
            reverse("login"),
            {
                "username": user.username,
                "password": "wrong",
            },
        ).status_code
        == 200
    )
    assert "_auth_user_id" not in client.session
    response = client.post(
        reverse("login"),
        {
            "username": user.username,
            "password": "Test-password-42!",
            "next": "https://outside.invalid/",
        },
    )
    assert response.url == reverse("home")


def test_inactive_user_cannot_login(client):
    user = UserFactory(is_active=False)
    client.post(reverse("login"), {"username": user.username, "password": "Test-password-42!"})
    assert "_auth_user_id" not in client.session


def test_api_uses_session_authentication_and_requires_user():
    assert api_settings.DEFAULT_AUTHENTICATION_CLASSES == [SessionAuthentication]
    assert api_settings.DEFAULT_PERMISSION_CLASSES == [IsAuthenticated]


@pytest.mark.parametrize("superuser", [False, True])
def test_admin_access_and_guessed_ids(client, superuser):
    user = UserFactory(is_staff=True, is_superuser=superuser)
    user.user_permissions.set(Permission.objects.all())
    client.force_login(user)
    workspace = WorkspaceFactory()
    objects = [
        workspace,
        MembershipFactory(workspace=workspace),
        AgencyProfileFactory(workspace=workspace),
        ServiceOfferingFactory(workspace=workspace),
        UserFactory(),
    ]
    assert client.get(reverse("admin:index")).status_code == (200 if superuser else 302)
    for obj in reversed(objects):
        prefix = f"admin:accounts_{obj._meta.model_name}"
        response = client.get(reverse(f"{prefix}_changelist"))
        assert response.status_code == (200 if superuser else 302)
        response = client.get(reverse(f"{prefix}_add"))
        assert response.status_code == (200 if superuser else 302)
        response = client.get(reverse(f"{prefix}_change", args=[obj.pk]))
        assert response.status_code == (200 if superuser else 302)
        response = client.post(reverse(f"{prefix}_delete", args=[obj.pk]), {"post": "yes"})
        assert response.status_code == 302
        assert type(obj).objects.filter(pk=obj.pk).exists() is (not superuser)


def test_admin_requires_login(client):
    assert client.get(reverse("admin:index")).status_code == 302


@pytest.mark.parametrize("superuser", [False, True])
def test_admin_login_requires_platform_superuser(client, superuser):
    user = UserFactory(is_staff=True, is_superuser=superuser)
    response = client.post(
        reverse("admin:login"),
        {
            "username": user.username,
            "password": "Test-password-42!",
            "next": reverse("admin:index"),
        },
    )
    assert response.status_code == (302 if superuser else 200)
    assert ("_auth_user_id" in client.session) is superuser


def test_settings_and_security_headers(client, settings):
    assert settings.TIME_ZONE == "Europe/Madrid"
    assert settings.OUTREACH_MODE == "simulation"
    for provider in ("EMAIL_PROVIDER", "WHATSAPP_PROVIDER", "AI_PROVIDER", "PLACES_PROVIDER"):
        assert getattr(settings, provider) == "mock"
    response = client.get(reverse("login"))
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
