from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models

from common.models import WorkspaceModel


class Campaign(WorkspaceModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Borrador"
        READY = "READY", "Preparada"
        RUNNING = "RUNNING", "En curso"
        PAUSED = "PAUSED", "Pausada"
        COMPLETED = "COMPLETED", "Completada"
        ARCHIVED = "ARCHIVED", "Archivada"

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.DRAFT, editable=False
    )
    target_query = models.CharField(max_length=250, blank=True)
    target_sector = models.CharField(max_length=150, blank=True)
    target_location_text = models.CharField(max_length=250, blank=True)
    target_country_code = models.CharField(
        max_length=2, default="ES", validators=[RegexValidator(r"^[A-Z]{2}$")]
    )
    desired_leads = models.PositiveIntegerField(default=25, validators=[MinValueValidator(1)])
    minimum_score = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(100)])
    require_manual_approval = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, editable=False
    )
    started_at = models.DateTimeField(null=True, blank=True, editable=False)
    completed_at = models.DateTimeField(null=True, blank=True, editable=False)
    services = models.ManyToManyField(
        "accounts.ServiceOffering", through="CampaignService", blank=True
    )

    class Meta:
        ordering = ["-created_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(minimum_score__lte=100), name="campaign_score_range"
            ),
            models.CheckConstraint(
                condition=models.Q(desired_leads__gte=1), name="campaign_positive_target"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=["DRAFT", "READY", "RUNNING", "PAUSED", "COMPLETED", "ARCHIVED"]
                ),
                name="campaign_valid_status",
            ),
        ]

    def clean(self):
        super().clean()
        if (
            not self._state.adding
            and Campaign.objects.filter(pk=self.pk).exclude(status=self.status).exists()
        ):
            raise ValidationError("El estado debe cambiarse mediante el servicio de campañas.")

    def __str__(self):
        return self.name


class CampaignService(WorkspaceModel):
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE)
    service = models.ForeignKey("accounts.ServiceOffering", on_delete=models.PROTECT)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["campaign", "service"], name="unique_campaign_service")
        ]

    def clean(self):
        super().clean()
        if self.service_id and self.service.workspace_id != self.workspace_id:
            raise ValidationError({"service": "Servicio de otro espacio de trabajo."})


class CampaignChannel(WorkspaceModel):
    class Channel(models.TextChoices):
        EMAIL = "EMAIL", "Email"
        WHATSAPP = "WHATSAPP", "WhatsApp"
        PHONE = "PHONE", "Teléfono"
        CONTACT_FORM = "CONTACT_FORM", "Formulario web"
        LINKEDIN = "LINKEDIN", "LinkedIn"
        INSTAGRAM = "INSTAGRAM", "Instagram"
        OTHER = "OTHER", "Otro"

    campaign = models.ForeignKey(Campaign, on_delete=models.PROTECT, related_name="channels")
    channel = models.CharField(max_length=15, choices=Channel.choices)
    enabled = models.BooleanField(default=True)
    priority = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1)])
    cooldown_days = models.PositiveSmallIntegerField(default=14)

    class Meta:
        ordering = ["priority", "channel", "id"]
        constraints = [
            models.UniqueConstraint(fields=["campaign", "channel"], name="unique_campaign_channel"),
            models.CheckConstraint(
                condition=models.Q(max_attempts__gte=1), name="channel_positive_attempts"
            ),
        ]

    def __str__(self):
        return self.get_channel_display()
