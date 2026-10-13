from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.demo import seed_demo
from apps.jobs.locking import WorkerAlreadyRunning


class Command(BaseCommand):
    help = "Crear Demo Agency con datos ficticios, sin aprobar ni enviar mensajes."

    def add_arguments(self, parser):
        parser.add_argument(
            "--create-user",
            metavar="USERNAME",
            help="Crear un usuario OWNER sin contraseña; configurar después con changepassword.",
        )

    def handle(self, *args, **options):
        try:
            result = seed_demo(username=options["create_user"])
        except (ValidationError, WorkerAlreadyRunning) as error:
            raise CommandError(str(error)) from error
        status = "creado" if result.created else "existente; datos conservados"
        self.stdout.write(
            self.style.SUCCESS(f"Demo Agency: {status}. Workspace: {result.workspace_id}")
        )
        if result.user_created:
            self.stdout.write(
                "Usuario creado sin contraseña utilizable. Usa manage.py changepassword."
            )
