from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.jobs.locking import WorkerAlreadyRunning
from common.backups import backup_database


class Command(BaseCommand):
    help = (
        "Offline platform backup: stop HTTP server and worker; "
        "includes all workspaces and private PDFs."
    )

    def add_arguments(self, parser):
        parser.add_argument("--output", type=Path, required=True)

    def handle(self, *args, **options):
        try:
            result = backup_database(destination=options["output"])
        except (ValidationError, WorkerAlreadyRunning) as error:
            raise CommandError(str(error)) from None
        self.stdout.write(self.style.SUCCESS(f"Copia verificada: {len(result['files'])} archivos."))
