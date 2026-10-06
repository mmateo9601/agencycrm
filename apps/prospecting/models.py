from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from common.models import WorkspaceModel, limited_metadata

SEARCH_STATES = ["PENDING", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]


def bounded_rows(value):
    import json

    if not isinstance(value, list) or len(value) > 200 or len(json.dumps(value).encode()) > 300_000:
        raise ValidationError("Máximo 200 filas y 300 KB de datos normalizados.")


class SearchPlan(WorkspaceModel):
    class Strategy(models.TextChoices):
        TEXT_QUERIES = "TEXT_QUERIES", "Consultas de texto"
        MANUAL_IMPORT = "MANUAL_IMPORT", "Importación CSV"

    campaign = models.ForeignKey(
        "campaigns.Campaign", on_delete=models.PROTECT, related_name="search_plans"
    )
    provider = models.CharField(max_length=30)
    status = models.CharField(max_length=12, default="PENDING", editable=False)
    strategy = models.CharField(max_length=20, choices=Strategy.choices)
    target_count = models.PositiveIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(500)]
    )
    generated_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    request_key = models.UUIDField()
    request_fingerprint = models.CharField(max_length=64)
    country_code = models.CharField(max_length=2, default="ES")

    class Meta:
        ordering = ["-created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "request_key"], name="unique_search_plan_request"
            ),
            models.CheckConstraint(
                condition=models.Q(target_count__gte=1, target_count__lte=500),
                name="search_plan_target_range",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=SEARCH_STATES), name="search_plan_status"
            ),
        ]


class SearchPlanItem(WorkspaceModel):
    plan = models.ForeignKey(SearchPlan, on_delete=models.PROTECT, related_name="items")
    sequence = models.PositiveSmallIntegerField()
    query_text = models.CharField(max_length=250, blank=True)
    location_text = models.CharField(max_length=250, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    radius_m = models.PositiveIntegerField(null=True, blank=True)
    place_type = models.CharField(max_length=100, blank=True)
    max_results = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(200)]
    )
    status = models.CharField(max_length=12, default="PENDING", editable=False)
    job = models.ForeignKey(
        "jobs.Job", on_delete=models.PROTECT, null=True, blank=True, related_name="search_items"
    )
    import_rows = models.JSONField(default=list, blank=True, validators=[bounded_rows])

    class Meta:
        ordering = ["sequence", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["plan", "sequence"], name="unique_search_item_sequence"
            ),
            models.CheckConstraint(
                condition=models.Q(sequence__gte=1, max_results__gte=1, max_results__lte=200),
                name="search_item_limits",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=SEARCH_STATES), name="search_item_status"
            ),
        ]

    def clean(self):
        super().clean()
        if self.job_id and self.job.workspace_id != self.workspace_id:
            raise ValidationError("El job pertenece a otro workspace.")


class SearchRun(WorkspaceModel):
    plan_item = models.ForeignKey(SearchPlanItem, on_delete=models.PROTECT, related_name="runs")
    job = models.OneToOneField("jobs.Job", on_delete=models.PROTECT, related_name="search_run")
    provider = models.CharField(max_length=30)
    request_fingerprint = models.CharField(max_length=64)
    status = models.CharField(max_length=12, default="RUNNING", editable=False)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    results_seen = models.PositiveIntegerField(default=0)
    requests_count = models.PositiveIntegerField(default=0)
    cache_hit = models.BooleanField(default=False)
    companies_created = models.PositiveIntegerField(default=0)
    companies_updated = models.PositiveIntegerField(default=0)
    leads_created = models.PositiveIntegerField(default=0)
    duplicates = models.PositiveIntegerField(default=0)
    errors = models.PositiveIntegerField(default=0)
    error_code = models.CharField(max_length=100, blank=True)
    error_message = models.TextField(blank=True)
    provider_metadata = models.JSONField(default=dict, blank=True, validators=[limited_metadata])
    snapshot = models.JSONField(default=list, blank=True, validators=[bounded_rows])
    snapshot_ready = models.BooleanField(default=False)

    class Meta:
        ordering = ["-started_at", "id"]
        indexes = [models.Index(fields=["workspace", "request_fingerprint"])]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=SEARCH_STATES), name="search_run_status"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    results_seen=models.F("companies_created")
                    + models.F("duplicates")
                    + models.F("errors"),
                    companies_updated__lte=models.F("duplicates"),
                    leads_created__lte=models.F("results_seen"),
                ),
                name="search_run_counts",
            ),
        ]

    def clean(self):
        super().clean()
        if self.job_id and self.job.workspace_id != self.workspace_id:
            raise ValidationError("El job pertenece a otro workspace.")


class SearchResult(WorkspaceModel):
    """One durable disposition per observed row; retries cannot ingest it twice."""

    run = models.ForeignKey(SearchRun, on_delete=models.PROTECT, related_name="rows")
    line = models.PositiveIntegerField()
    external_id = models.CharField(max_length=255, blank=True)
    company = models.ForeignKey(
        "companies.Company", on_delete=models.PROTECT, null=True, blank=True
    )
    lead = models.ForeignKey("crm.Lead", on_delete=models.PROTECT, null=True, blank=True)
    company_created = models.BooleanField(default=False)
    company_updated = models.BooleanField(default=False)
    lead_created = models.BooleanField(default=False)
    error = models.CharField(max_length=300, blank=True)
    observed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["line", "id"]
        constraints = [
            models.UniqueConstraint(fields=["run", "line"], name="unique_search_result_line")
        ]


class DuplicateCandidate(WorkspaceModel):
    """A suggestion only: ambiguous companies are never merged automatically."""

    company = models.ForeignKey(
        "companies.Company", on_delete=models.PROTECT, related_name="duplicate_suggestions"
    )
    candidate = models.ForeignKey(
        "companies.Company", on_delete=models.PROTECT, related_name="potential_duplicates"
    )
    reason = models.CharField(max_length=120)

    class Meta:
        ordering = ["-created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "candidate"], name="unique_duplicate_suggestion"
            ),
            models.CheckConstraint(
                condition=~models.Q(company=models.F("candidate")),
                name="different_duplicate_companies",
            ),
        ]


class PlacesCache(WorkspaceModel):
    """Short-lived normalized snapshots, isolated by workspace and complete request fingerprint."""

    fingerprint = models.CharField(max_length=64)
    rows = models.JSONField(default=list, blank=True, validators=[bounded_rows])
    truncated = models.BooleanField(default=False)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "fingerprint"], name="unique_workspace_places_cache"
            )
        ]
