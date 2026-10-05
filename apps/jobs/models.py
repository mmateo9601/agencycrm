import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from apps.accounts.models import TimestampedModel
from common.models import limited_metadata


class Job(TimestampedModel):
    class Kind(models.TextChoices):
        CHECK_MOCK_PROVIDERS = "CHECK_MOCK_PROVIDERS", "Comprobar proveedores simulados"
        BUILD_SEARCH_PLAN = "BUILD_SEARCH_PLAN", "Crear plan de búsqueda"
        RUN_SEARCH_ITEM = "RUN_SEARCH_ITEM", "Ejecutar búsqueda"
        ENRICH_COMPANY = "ENRICH_COMPANY", "Enriquecer empresa"
        SCORE_LEAD = "SCORE_LEAD", "Calcular score"
        GENERATE_PROPOSAL = "GENERATE_PROPOSAL", "Generar propuesta"
        PREPARE_OUTREACH = "PREPARE_OUTREACH", "Preparar contacto"
        SEND_OUTREACH = "SEND_OUTREACH", "Enviar contacto"
        REFRESH_METRICS = "REFRESH_METRICS", "Actualizar métricas"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pendiente"
        RUNNING = "RUNNING", "En ejecución"
        SUCCEEDED = "SUCCEEDED", "Completado"
        FAILED = "FAILED", "Fallido"
        CANCELLED = "CANCELLED", "Cancelado"

    workspace = models.ForeignKey("accounts.Workspace", on_delete=models.PROTECT)
    kind = models.CharField(max_length=30, choices=Kind.choices)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING, editable=False
    )
    priority = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(100)])
    payload = models.JSONField(default=dict, blank=True, validators=[limited_metadata])
    result = models.JSONField(default=dict, blank=True, validators=[limited_metadata])
    progress_current = models.PositiveIntegerField(default=0)
    progress_total = models.PositiveIntegerField(default=0)
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(
        default=3, validators=[MinValueValidator(1), MaxValueValidator(10)]
    )
    idempotency_key = models.CharField(max_length=100, null=True, blank=True)
    available_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    run_token = models.UUIDField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-created_at", "id"]
        indexes = [
            models.Index(fields=["status", "available_at", "priority"]),
            models.Index(fields=["workspace", "status", "created_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "idempotency_key"],
                condition=models.Q(idempotency_key__isnull=False),
                name="unique_workspace_job_key",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    max_attempts__gte=1,
                    max_attempts__lte=10,
                    attempts__lte=models.F("max_attempts"),
                ),
                name="job_attempt_budget",
            ),
            models.CheckConstraint(
                condition=models.Q(priority__lte=100), name="job_priority_range"
            ),
            models.CheckConstraint(
                condition=models.Q(progress_current__lte=models.F("progress_total")),
                name="job_progress_range",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=["PENDING", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]
                ),
                name="job_valid_status",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="RUNNING", run_token__isnull=False, started_at__isnull=False)
                    | (~models.Q(status="RUNNING") & models.Q(run_token__isnull=True))
                ),
                name="job_running_token",
            ),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} · {self.pk}"


class JobAttempt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(Job, on_delete=models.PROTECT, related_name="attempt_history")
    attempt_number = models.PositiveSmallIntegerField()
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    succeeded = models.BooleanField(null=True)
    error_type = models.CharField(max_length=100, blank=True)
    error_message = models.TextField(blank=True)
    traceback_excerpt = models.TextField(blank=True)

    class Meta:
        ordering = ["-attempt_number", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["job", "attempt_number"], name="unique_job_attempt_number"
            ),
            models.CheckConstraint(
                condition=models.Q(attempt_number__gte=1), name="job_attempt_positive"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(finished_at__isnull=True, succeeded__isnull=True)
                    | models.Q(finished_at__isnull=False, succeeded__isnull=False)
                ),
                name="job_attempt_completion",
            ),
        ]
