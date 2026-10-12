from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxLengthValidator, MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from apps.accounts.models import Membership
from apps.campaigns.models import CampaignChannel
from common.models import AppendOnlyModel, WorkspaceModel, limited_metadata


class OutreachSettings(WorkspaceModel):
    workspace = models.OneToOneField(
        "accounts.Workspace", on_delete=models.PROTECT, related_name="outreach_settings"
    )
    daily_email_limit = models.PositiveIntegerField(
        default=25, validators=[MaxValueValidator(10000)]
    )
    daily_whatsapp_limit = models.PositiveIntegerField(
        default=10, validators=[MaxValueValidator(10000)]
    )
    default_cooldown_days = models.PositiveSmallIntegerField(
        default=14, validators=[MaxValueValidator(365)]
    )
    max_attempts_per_company = models.PositiveSmallIntegerField(
        default=3, validators=[MinValueValidator(1), MaxValueValidator(100)]
    )
    manual_approval_required = models.BooleanField(default=True, editable=False)
    live_mode_enabled = models.BooleanField(default=False, editable=False)
    is_paused = models.BooleanField(default=False)
    revision = models.PositiveIntegerField(default=0, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(manual_approval_required=True), name="outreach_human_approval"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    max_attempts_per_company__gte=1, max_attempts_per_company__lte=100
                ),
                name="outreach_attempt_limit",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    daily_email_limit__lte=10000,
                    daily_whatsapp_limit__lte=10000,
                    default_cooldown_days__lte=365,
                ),
                name="outreach_limits_range",
            ),
        ]


class OutreachMessage(WorkspaceModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Borrador"
        PENDING_APPROVAL = "PENDING_APPROVAL", "Pendiente de aprobación"
        APPROVED = "APPROVED", "Aprobado"
        QUEUED = "QUEUED", "Encolado"
        SENDING = "SENDING", "En proceso"
        SENT = "SENT", "Enviado (simulado)"
        DELIVERED = "DELIVERED", "Entregado"
        READ = "READ", "Leído"
        FAILED = "FAILED", "Fallido"
        REPLIED = "REPLIED", "Respuesta"
        CANCELLED = "CANCELLED", "Cancelado"
        SUPPRESSED = "SUPPRESSED", "Bloqueado por política"

    lead = models.ForeignKey("crm.Lead", on_delete=models.PROTECT, related_name="outreach_messages")
    contact = models.ForeignKey(
        "companies.Contact", on_delete=models.PROTECT, null=True, blank=True
    )
    proposal = models.ForeignKey(
        "proposals.Proposal", on_delete=models.PROTECT, related_name="messages"
    )
    channel = models.CharField(max_length=15, choices=CampaignChannel.Channel.choices)
    direction = models.CharField(max_length=10, default="OUTBOUND", editable=False)
    recipient = models.CharField(max_length=500, blank=True, editable=False)
    subject = models.CharField(max_length=250, blank=True)
    body_text = models.TextField(validators=[MaxLengthValidator(12000)])
    body_html = models.TextField(blank=True, editable=False)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT, editable=False
    )
    provider = models.CharField(max_length=10, default="mock", editable=False)
    provider_message_id = models.CharField(max_length=100, blank=True, editable=False)
    idempotency_key = models.CharField(max_length=100, editable=False)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_messages",
    )
    approved_at = models.DateTimeField(null=True, blank=True, editable=False)
    approval_hash = models.CharField(max_length=64, blank=True, editable=False)
    policy_review_confirmed = models.BooleanField(default=False, editable=False)
    revision = models.PositiveIntegerField(default=0, editable=False)
    scheduled_at = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(null=True, blank=True, editable=False)
    delivered_at = models.DateTimeField(null=True, blank=True, editable=False)
    failed_at = models.DateTimeField(null=True, blank=True, editable=False)
    error_code = models.CharField(max_length=100, blank=True, editable=False)
    error_message = models.CharField(max_length=250, blank=True, editable=False)
    reply_to_message = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, editable=False
    )
    send_attempts = models.PositiveSmallIntegerField(default=0, editable=False)
    attempt_started_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_outreach_messages",
    )
    job = models.OneToOneField("jobs.Job", on_delete=models.PROTECT, null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "id"]
        indexes = [models.Index(fields=["workspace", "channel", "sent_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "idempotency_key"], name="unique_message_key"
            ),
            models.CheckConstraint(
                condition=models.Q(provider__in=["mock", "manual"]), name="message_local_provider"
            ),
            models.CheckConstraint(
                condition=models.Q(direction="OUTBOUND"), name="message_outbound_v1"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=[
                        value
                        for value in (
                            "DRAFT",
                            "PENDING_APPROVAL",
                            "APPROVED",
                            "QUEUED",
                            "SENDING",
                            "SENT",
                            "DELIVERED",
                            "READ",
                            "FAILED",
                            "REPLIED",
                            "CANCELLED",
                            "SUPPRESSED",
                        )
                    ]
                ),
                name="message_valid_status",
            ),
            models.CheckConstraint(
                condition=~models.Q(
                    status__in=["APPROVED", "QUEUED", "SENDING", "SENT", "DELIVERED", "READ"]
                )
                | models.Q(approved_at__isnull=False, policy_review_confirmed=True),
                name="message_approval_required",
            ),
        ]

    def clean(self):
        super().clean()
        if self.proposal_id and self.proposal.lead_id != self.lead_id:
            raise ValidationError("La propuesta pertenece a otro lead.")
        if self.contact_id and self.contact.company_id != self.lead.company_id:
            raise ValidationError("El contacto pertenece a otra empresa.")
        if self.job_id and (
            self.job.workspace_id != self.workspace_id or self.job.kind != "SEND_OUTREACH"
        ):
            raise ValidationError("Trabajo ajeno o no válido.")
        if self.body_html:
            raise ValidationError("FASE 8 solo admite texto plano.")
        if timezone.is_naive(self.scheduled_at):
            raise ValidationError("La programación debe incluir zona horaria.")
        if not self._state.adding:
            raise ValidationError(
                "Usa los servicios de outreach; el contenido de un mensaje es inmutable."
            )

    def delete(self, *args, **kwargs):
        raise ValidationError("Los mensajes y sus evidencias se conservan.")

    def __str__(self):
        return f"{self.get_channel_display()} · {self.subject or self.lead.company.name}"


class MessageAttachment(AppendOnlyModel, WorkspaceModel):
    message = models.ForeignKey(
        OutreachMessage, on_delete=models.PROTECT, related_name="attachments"
    )
    proposal = models.ForeignKey(
        "proposals.Proposal", on_delete=models.PROTECT, null=True, blank=True
    )
    asset = models.ForeignKey(
        "proposals.SalesAsset", on_delete=models.PROTECT, null=True, blank=True
    )
    file = models.FileField(max_length=400, editable=False)
    filename = models.CharField(max_length=150)
    mime_type = models.CharField(max_length=100, default="application/pdf")
    size_bytes = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(proposal__isnull=False, asset__isnull=True)
                | models.Q(proposal__isnull=True, asset__isnull=False),
                name="message_attachment_source",
            ),
            models.CheckConstraint(
                condition=models.Q(size_bytes__lte=2000000), name="message_attachment_size"
            ),
            models.UniqueConstraint(
                fields=["message", "proposal"], name="unique_message_proposal_file"
            ),
            models.UniqueConstraint(fields=["message", "asset"], name="unique_message_asset_file"),
        ]


class MessageEvent(AppendOnlyModel, WorkspaceModel):
    message = models.ForeignKey(OutreachMessage, on_delete=models.PROTECT, related_name="events")
    event_type = models.CharField(max_length=30)
    provider_event_id = models.CharField(max_length=100)
    occurred_at = models.DateTimeField(default=timezone.now)
    payload = models.JSONField(default=dict, blank=True, validators=[limited_metadata])

    class Meta:
        ordering = ["-occurred_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["message", "event_type", "provider_event_id"], name="unique_message_event"
            )
        ]


class SalesTask(WorkspaceModel):
    lead = models.ForeignKey("crm.Lead", on_delete=models.PROTECT, related_name="sales_tasks")
    message = models.OneToOneField(
        OutreachMessage, on_delete=models.PROTECT, related_name="manual_task"
    )
    type = models.CharField(
        max_length=15,
        choices=[
            ("CALL", "Llamar"),
            ("CONTACT_FORM", "Formulario"),
            ("LINKEDIN", "LinkedIn"),
            ("INSTAGRAM", "Instagram"),
            ("OTHER", "Otro"),
        ],
    )
    due_at = models.DateTimeField(default=timezone.now)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    status = models.CharField(
        max_length=10,
        default="PENDING",
        choices=[("PENDING", "Pendiente"), ("COMPLETED", "Completada"), ("CANCELLED", "Cancelada")],
        editable=False,
    )
    notes = models.TextField(blank=True, validators=[MaxLengthValidator(2000)])
    outcome = models.TextField(blank=True, validators=[MaxLengthValidator(2000)])

    def clean(self):
        super().clean()
        if self.message_id and self.message.lead_id != self.lead_id:
            raise ValidationError("La tarea pertenece a otro lead.")
        if (
            self.assigned_to_id
            and not Membership.objects.filter(
                workspace_id=self.workspace_id,
                user_id=self.assigned_to_id,
                is_active=True,
                user__is_active=True,
            ).exists()
        ):
            raise ValidationError("Responsable ajeno o inactivo.")

    def __str__(self):
        return f"{self.get_type_display()} · {self.get_status_display()}"
