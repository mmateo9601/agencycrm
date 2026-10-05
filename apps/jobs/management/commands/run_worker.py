import math
import time

from django.core.management.base import BaseCommand, CommandError

from apps.jobs.locking import WorkerAlreadyRunning, WorkerLock
from apps.jobs.models import Job
from apps.jobs.runner import JobRunner


class Command(BaseCommand):
    help = "Procesa jobs locales con un único worker; no inicia procesos desde runserver."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Procesar como máximo un job.")
        parser.add_argument(
            "--sleep", type=float, default=2, help="Espera entre consultas sin trabajo (1–60 s)."
        )
        parser.add_argument("--kinds", help="Tipos separados por comas.")

    def handle(self, *args, **options):
        interval = options["sleep"]
        if not math.isfinite(interval) or not 1 <= interval <= 60:
            raise CommandError("--sleep debe estar entre 1 y 60 segundos.")
        kinds = None
        if options["kinds"] is not None:
            kinds = [value.strip() for value in options["kinds"].split(",")]
            if not kinds or any(value not in Job.Kind.values for value in kinds):
                raise CommandError("--kinds contiene un tipo de trabajo desconocido.")
        runner = JobRunner()
        try:
            with WorkerLock() as lock:
                self.stdout.write("Worker local iniciado. Un único proceso por base de datos.")
                while True:
                    job = runner.run_locked(lock=lock, kinds=kinds)
                    if job:
                        self.stdout.write(f"{job.pk}: {job.status} (intento {job.attempts})")
                    if options["once"]:
                        if job is None:
                            self.stdout.write("No hay trabajos disponibles.")
                        return
                    if job is None:
                        time.sleep(interval)
        except WorkerAlreadyRunning as exc:
            raise CommandError(str(exc)) from exc
        except KeyboardInterrupt:
            self.stdout.write(
                "Worker detenido. Recupera jobs interrumpidos con requeue_stale_jobs."
            )
