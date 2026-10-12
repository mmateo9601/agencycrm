from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from common.models import AppendOnlyModel, WorkspaceModel, limited_metadata

from .engine import CODES, validate_configuration


class ScoringRule(WorkspaceModel):
    code = models.CharField(max_length=30, choices=[(code, code) for code in sorted(CODES)])
    name = models.CharField(max_length=200)
    points = models.SmallIntegerField(validators=[MinValueValidator(-100), MaxValueValidator(100)])
    enabled = models.BooleanField(default=True)
    configuration = models.JSONField(default=dict, blank=True, validators=[limited_metadata])
    version = models.PositiveIntegerField(default=1, editable=False)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "code"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "code"], name="unique_workspace_score_rule"
            ),
            models.CheckConstraint(
                condition=models.Q(points__gte=-100, points__lte=100),
                name="score_rule_points_range",
            ),
            models.CheckConstraint(condition=models.Q(version__gte=1), name="score_rule_version"),
            models.CheckConstraint(
                condition=models.Q(code__in=sorted(CODES)), name="score_rule_code"
            ),
        ]

    def clean(self):
        super().clean()
        validate_configuration(self.code, self.configuration)

    def __str__(self):
        return self.name


class ScoringRun(AppendOnlyModel, WorkspaceModel):
    lead = models.ForeignKey("crm.Lead", on_delete=models.PROTECT, related_name="scoring_runs")
    idempotency_key = models.CharField(max_length=100)
    engine_version = models.CharField(max_length=50)
    fingerprint = models.CharField(max_length=64)
    inputs = models.JSONField(validators=[limited_metadata])
    rules = models.JSONField(validators=[limited_metadata])
    score = models.PositiveSmallIntegerField(validators=[MaxValueValidator(100)])
    raw_score = models.IntegerField()
    actor = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True)

    class Meta:
        ordering = ["-created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "idempotency_key"], name="unique_score_key"
            ),
            models.CheckConstraint(condition=models.Q(score__lte=100), name="score_run_range"),
        ]
        indexes = [models.Index(fields=["workspace", "lead", "-created_at"])]


class LeadScoreItem(AppendOnlyModel, WorkspaceModel):
    lead = models.ForeignKey("crm.Lead", on_delete=models.PROTECT, related_name="score_items")
    scoring_run = models.ForeignKey(ScoringRun, on_delete=models.PROTECT, related_name="items")
    rule_code = models.CharField(max_length=30)
    points = models.SmallIntegerField(validators=[MinValueValidator(-100), MaxValueValidator(100)])
    explanation = models.CharField(max_length=200)
    evidence = models.CharField(max_length=2000)
    rule_version = models.PositiveIntegerField()
    matched = models.BooleanField()

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["scoring_run", "rule_code"], name="unique_run_rule_item"
            ),
            models.CheckConstraint(
                condition=models.Q(points__gte=-100, points__lte=100),
                name="score_item_points_range",
            ),
        ]

    def clean(self):
        super().clean()
        if self.scoring_run_id and self.scoring_run.lead_id != self.lead_id:
            raise ValidationError("El desglose corresponde a otro lead.")
