from apps.jobs.models import Job
from common.management import ScopedJobCommand


class Command(ScopedJobCommand):
    help = "Encolar una instantánea de métricas de un workspace; el dashboard sigue siendo live."
    kind = Job.Kind.REFRESH_METRICS

    def payload(self, options):
        return {}
