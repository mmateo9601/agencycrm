from uuid import UUID

from django.core.management.base import BaseCommand, CommandError

from apps.jobs.locking import WorkerAlreadyRunning
from apps.jobs.recovery import requeue_stale


class Command(BaseCommand):
    help = "Recupera jobs RUNNING antiguos con el worker detenido; no reinicia sus intentos."

    def add_arguments(self, parser):
        parser.add_argument(
            "--workspace", type=UUID, help="Limitar a un workspace; por defecto, todos."
        )
        parser.add_argument(
            "--older-than", type=int, help="Antigüedad mínima en segundos; por defecto, 900."
        )

    def handle(self, *args, **options):
        try:
            result = requeue_stale(
                workspace_id=options["workspace"], older_than=options["older_than"]
            )
        except (WorkerAlreadyRunning, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"Reencolados: {result['requeued']}. Agotados: {result['failed']}.")
