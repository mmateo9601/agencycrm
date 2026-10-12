from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from common.backups import restore_database


class Command(BaseCommand):
    help = "Verify and restore into a NEW offline folder. Never overwrites the current database."

    def add_arguments(self, parser):
        parser.add_argument("--input", type=Path, required=True)
        parser.add_argument("--destination", type=Path, required=True)

    def handle(self, *args, **options):
        try:
            result = restore_database(
                archive_path=options["input"], destination=options["destination"]
            )
        except ValidationError as error:
            raise CommandError(str(error)) from None
        self.stdout.write(
            self.style.SUCCESS(
                f"Restauración verificada: {len(result['files'])} archivos. "
                "Mantener offline hasta revisar configuración."
            )
        )
