"""Verify a fresh, disposable SQLite installation without using commercial data or network."""

import io
import os
import secrets
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def block_network(*args, **kwargs):
    raise RuntimeError("Network is forbidden during installation verification")


def main() -> None:
    sys.path.insert(0, str(ROOT))
    temporary_root = ROOT / "tmp"
    temporary_root.mkdir(exist_ok=True)
    require(temporary_root.resolve().is_relative_to(ROOT), "Temporary root escapes the workspace")
    with tempfile.TemporaryDirectory(
        prefix="verify-install-", dir=temporary_root.resolve()
    ) as folder:
        installation = Path(folder)
        os.environ.update(
            DJANGO_SETTINGS_MODULE="config.settings.local",
            DATABASE_NAME=str(installation / "db.sqlite3"),
            SECRET_KEY=secrets.token_urlsafe(50),
            ALLOWED_HOSTS="localhost,127.0.0.1",
            OUTREACH_MODE="simulation",
            WEB_CRAWLER_ENABLED="False",
            GOOGLE_PLACES_ENABLED="False",
            WHATSAPP_WEBHOOK_ENABLED="False",
        )
        for key in (
            "PLACES_PROVIDER",
            "EMAIL_PROVIDER",
            "WHATSAPP_PROVIDER",
            "AI_PROVIDER",
            "WEBSITE_PROVIDER",
        ):
            os.environ[key] = "mock"
        for name in ("connect", "connect_ex", "sendto"):
            setattr(socket.socket, name, block_network)
        socket.create_connection = block_network
        socket.getaddrinfo = block_network

        import django

        django.setup()
        from django.conf import settings
        from django.core.management import call_command
        from django.db import connection, connections
        from django.db.migrations.executor import MigrationExecutor
        from django.test import Client

        from apps.accounts.models import User, Workspace
        from apps.companies.models import Company
        from apps.crm.models import Lead
        from apps.jobs.models import Job

        settings.MEDIA_ROOT = installation / "media"
        try:
            call_command("migrate", verbosity=0)
            executor = MigrationExecutor(connection)
            require(
                not executor.migration_plan(executor.loader.graph.leaf_nodes()),
                "Pending migrations after installing",
            )
            call_command("check")
            for _ in range(2):
                call_command("seed_demo", "--create-user", "verify-demo", stdout=io.StringIO())
            require(Company.objects.count() == Lead.objects.count() == 10, "Seed duplicated data")
            user = User.objects.get(username="verify-demo")
            password = secrets.token_urlsafe(32)
            user.set_password(password)
            user.save(update_fields=["password"])
            # Use an allowed local host; do not relax ALLOWED_HOSTS for the test client.
            client = Client(enforce_csrf_checks=True, HTTP_HOST="localhost")
            require(client.get("/health/").status_code == 200, "Health failed")
            client.get("/accounts/login/")
            login = client.post(
                "/accounts/login/",
                {
                    "username": user.username,
                    "password": password,
                    "csrfmiddlewaretoken": client.cookies["csrftoken"].value,
                },
            )
            require(login.status_code == 302, "Session login with CSRF failed")
            workspace = Workspace.objects.get(slug="demo-agency")
            require(
                client.get(f"/w/{workspace.pk}/dashboard/").status_code == 200, "Dashboard failed"
            )
            response = client.get("/api/v1/leads/", {"workspace_id": workspace.pk})
            require(response.status_code == 200 and response.json()["count"] == 10, "API failed")
            call_command("run_worker", "--once", stdout=io.StringIO())
            require(
                not Job.objects.filter(status__in=["PENDING", "RUNNING"]).exists(), "Worker failed"
            )
            print(
                "Fresh SQLite: migrations, idempotent demo, session/CSRF, health, "
                "dashboard, API and worker OK."
            )
            print("Network blocked. Only disposable data under tmp/ was used.")
        finally:
            connections.close_all()


if __name__ == "__main__":
    main()
