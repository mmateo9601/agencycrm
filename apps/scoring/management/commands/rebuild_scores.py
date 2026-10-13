from uuid import UUID

from apps.jobs.models import Job
from common.management import ScopedJobCommand


class Command(ScopedJobCommand):
    help = "Encolar un lote acotado de recálculo de scores; no ejecutar providers ni enviar."
    kind = Job.Kind.REBUILD_SCORES

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument("--campaign", type=UUID, required=True)
        parser.add_argument("--after", type=UUID)
        parser.add_argument("--limit", type=int, default=100, help="Tamaño del lote, 1–200.")

    def payload(self, options):
        return {
            "campaign_id": str(options["campaign"]),
            "after": str(options["after"]) if options["after"] else None,
            "limit": options["limit"],
        }
