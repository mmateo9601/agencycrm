from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.core.management.base import BaseCommand, CommandError
from django.http import Http404

from apps.accounts.models import User
from apps.outreach.followups import reconcile
from common.access import workspace_access


class Command(BaseCommand):
    help = "Reconcile existing internal reminders. Never sends or creates messages."

    def add_arguments(self, parser):
        parser.add_argument("--workspace", type=UUID, required=True)
        parser.add_argument("--actor", type=UUID, required=True)

    def handle(self, *args, **options):
        try:
            actor = User.objects.get(pk=options["actor"])
            workspace_access(user=actor, workspace_id=options["workspace"], write=True)
        except User.DoesNotExist, PermissionDenied, Http404:
            raise CommandError("Actor o workspace no autorizado.") from None
        reconcile(workspace_id=options["workspace"], actor=actor)
        self.stdout.write(
            self.style.SUCCESS("Recordatorios reconciliados; ningún contacto enviado.")
        )
