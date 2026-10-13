"""CLI transport for scoped jobs; actor membership is always checked by JobService."""

from uuid import UUID

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.http import Http404

from apps.accounts.models import User
from apps.jobs.services import JobService


class ScopedJobCommand(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("--workspace", type=UUID, required=True)
        parser.add_argument("--actor", type=UUID, required=True)
        parser.add_argument("--idempotency-key", type=UUID, required=True)

    def handle(self, *args, **options):
        try:
            actor = User.objects.get(pk=options["actor"], is_active=True)
            job = JobService.enqueue(
                user=actor,
                workspace_id=options["workspace"],
                kind=self.kind,
                payload=self.payload(options),
                idempotency_key=f"{self.kind}:{options['idempotency_key']}",
            )
        except User.DoesNotExist, PermissionDenied, Http404:
            raise CommandError("Actor o workspace/objeto no autorizado.") from None
        except ValidationError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            self.style.SUCCESS(f"Job {job.pk}: {job.kind} · {job.status}. Ejecutar worker aparte.")
        )
