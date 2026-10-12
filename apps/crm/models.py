import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.accounts.models import Membership
from common.models import AppendOnlyModel, WorkspaceModel


class Lead(WorkspaceModel):
    class Status(models.TextChoices):
        NEW = "NEW", "Nuevo"
        ENRICHING = "ENRICHING", "En enriquecimiento"
        QUALIFIED = "QUALIFIED", "Cualificado"
        READY = "READY", "Preparado"
        CONTACTED = "CONTACTED", "Contactado"
        REPLIED = "REPLIED", "Respuesta recibida"
        MEETING = "MEETING", "Reunión"
        PROPOSAL = "PROPOSAL", "Propuesta"
        WON = "WON", "Ganado"
        LOST = "LOST", "Perdido"
        DISQUALIFIED = "DISQUALIFIED", "Descartado"

    campaign = models.ForeignKey(
        "campaigns.Campaign", on_delete=models.PROTECT, related_name="leads"
    )
    company = models.ForeignKey("companies.Company", on_delete=models.PROTECT, related_name="leads")
    status = models.CharField(
        max_length=15, choices=Status.choices, default=Status.NEW, editable=False
    )
    status_revision = models.PositiveIntegerField(default=0, editable=False)
    score = models.PositiveSmallIntegerField(
        default=0, validators=[MaxValueValidator(100)], editable=False
    )
    score_version = models.CharField(max_length=50, blank=True, editable=False)
    current_score_run = models.ForeignKey(
        "scoring.ScoringRun",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        editable=False,
        related_name="current_leads",
    )
    recommended_service = models.ForeignKey(
        "accounts.ServiceOffering", on_delete=models.PROTECT, null=True, blank=True
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    next_action_at = models.DateTimeField(null=True, blank=True)
    next_action_managed = models.BooleanField(default=False, editable=False)
    last_contact_at = models.DateTimeField(null=True, blank=True, editable=False)
    lost_reason = models.TextField(blank=True, editable=False)
    notes = models.TextField(blank=True)
    is_archived = models.BooleanField(default=False, editable=False)

    class Meta:
        ordering = ["-created_at", "id"]
        indexes = [
            models.Index(fields=["workspace", "status"]),
            models.Index(fields=["workspace", "assigned_to"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["campaign", "company"], name="unique_campaign_company_lead"
            ),
            models.CheckConstraint(condition=models.Q(score__lte=100), name="lead_score_range"),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=[
                        "NEW",
                        "ENRICHING",
                        "QUALIFIED",
                        "READY",
                        "CONTACTED",
                        "REPLIED",
                        "MEETING",
                        "PROPOSAL",
                        "WON",
                        "LOST",
                        "DISQUALIFIED",
                    ]
                ),
                name="lead_valid_status",
            ),
        ]

    def clean(self):
        super().clean()
        if self.current_score_run_id and self.current_score_run.lead_id != self.pk:
            raise ValidationError("El score activo corresponde a otro lead.")
        if (
            self.recommended_service_id
            and self.recommended_service.workspace_id != self.workspace_id
        ):
            raise ValidationError({"recommended_service": "Servicio de otro espacio de trabajo."})
        if (
            self.assigned_to_id
            and not Membership.objects.filter(
                workspace_id=self.workspace_id,
                user_id=self.assigned_to_id,
                is_active=True,
                user__is_active=True,
            ).exists()
        ):
            raise ValidationError({"assigned_to": "El responsable debe ser un miembro activo."})
        if not self._state.adding:
            old = Lead.objects.get(pk=self.pk)
            if (self.status, self.status_revision) != (old.status, old.status_revision):
                raise ValidationError("Usa LeadStateService para cambiar el estado.")
            if (self.campaign_id, self.company_id) != (old.campaign_id, old.company_id):
                raise ValidationError("La campaña y empresa de un lead no pueden cambiarse.")

    def __str__(self):
        return f"{self.company} · {self.campaign}"


class LeadStatusHistory(AppendOnlyModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lead = models.ForeignKey(Lead, on_delete=models.PROTECT, related_name="status_history")
    from_status = models.CharField(max_length=15, blank=True)
    to_status = models.CharField(max_length=15, choices=Lead.Status.choices)
    revision = models.PositiveIntegerField()
    reason = models.TextField(blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-revision", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["lead", "revision"], name="unique_lead_history_revision"
            )
        ]


class Opportunity(WorkspaceModel):
    scoring_run = models.ForeignKey(
        "scoring.ScoringRun",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        editable=False,
        related_name="opportunities",
    )

    class Type(models.TextChoices):
        SEO_LOCAL = "SEO_LOCAL", "SEO local"
        SEO = "SEO", "SEO"
        WEB = "WEB", "Web"
        CONVERSION = "CONVERSION", "Conversión"
        GOOGLE_ADS = "GOOGLE_ADS", "Google Ads"
        META_ADS = "META_ADS", "Meta Ads"
        SOCIAL_MEDIA = "SOCIAL_MEDIA", "Redes sociales"
        REVIEWS = "REVIEWS", "Reseñas"
        BRANDING = "BRANDING", "Marca"
        CONTENT = "CONTENT", "Contenido"
        AUTOMATION = "AUTOMATION", "Automatización"
        OTHER = "OTHER", "Otra"

    class Severity(models.TextChoices):
        LOW = "LOW", "Baja"
        MEDIUM = "MEDIUM", "Media"
        HIGH = "HIGH", "Alta"

    lead = models.ForeignKey(Lead, on_delete=models.PROTECT, related_name="opportunities")
    type = models.CharField(max_length=15, choices=Type.choices)
    severity = models.CharField(max_length=6, choices=Severity.choices, default=Severity.LOW)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    evidence = models.TextField(max_length=2000)
    recommended_service = models.ForeignKey(
        "accounts.ServiceOffering", on_delete=models.PROTECT, null=True, blank=True
    )
    confidence = models.DecimalField(
        max_digits=4,
        decimal_places=3,
        default=1,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
    )
    detected_by = models.CharField(max_length=100, default="HUMAN", editable=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(confidence__gte=0, confidence__lte=1),
                name="opportunity_confidence_range",
            )
        ]

    def clean(self):
        super().clean()
        if self.scoring_run_id and self.scoring_run.lead_id != self.lead_id:
            raise ValidationError("La oportunidad corresponde a otra ejecución/lead.")
        if (
            self.recommended_service_id
            and self.recommended_service.workspace_id != self.workspace_id
        ):
            raise ValidationError({"recommended_service": "Servicio de otro espacio de trabajo."})

    def __str__(self):
        return self.title
