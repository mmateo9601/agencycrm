import uuid

from django.core.exceptions import ValidationError
from django.core.validators import MaxLengthValidator
from django.db import models

from common.models import WorkspaceModel, limited_metadata


def asset_path(instance, filename):
    return f"sales_assets/{instance.workspace_id}/{instance.pk}/{uuid.uuid4()}.pdf"


class SalesAsset(WorkspaceModel):
    class Type(models.TextChoices):
        AGENCY_DECK = "AGENCY_DECK", "Presentación de agencia"
        CASE_STUDY = "CASE_STUDY", "Caso de estudio"
        RATE_CARD = "RATE_CARD", "Tarifas"
        BROCHURE = "BROCHURE", "Folleto"
        OTHER = "OTHER", "Otro"

    name = models.CharField(max_length=200)
    asset_type = models.CharField(max_length=15, choices=Type.choices)
    file = models.FileField(upload_to=asset_path, max_length=400, editable=False)
    sha256 = models.CharField(max_length=64, editable=False)
    size_bytes = models.PositiveIntegerField(editable=False)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(size_bytes__lte=1500000), name="sales_asset_size_limit"
            )
        ]

    def clean(self):
        super().clean()
        if not self._state.adding:
            old = type(self).objects.get(pk=self.pk)
            if (old.file.name, old.sha256, old.size_bytes) != (
                self.file.name,
                self.sha256,
                self.size_bytes,
            ):
                raise ValidationError("Los archivos se versionan subiendo un nuevo activo.")

    def __str__(self):
        return self.name


class Proposal(WorkspaceModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Borrador"
        GENERATED = "GENERATED", "Generada"
        REVIEWED = "REVIEWED", "Revisada"
        APPROVED = "APPROVED", "Aprobada"
        SENT = "SENT", "Enviada"
        SUPERSEDED = "SUPERSEDED", "Sustituida"

    lead = models.ForeignKey("crm.Lead", on_delete=models.PROTECT, related_name="proposals")
    version = models.PositiveIntegerField()
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.DRAFT, editable=False
    )
    title = models.CharField(max_length=250)
    executive_summary = models.TextField(validators=[MaxLengthValidator(5000)])
    problem_statement = models.TextField(validators=[MaxLengthValidator(5000)])
    recommended_solution = models.TextField(validators=[MaxLengthValidator(5000)])
    services_json = models.JSONField(default=dict, validators=[limited_metadata])
    next_steps = models.TextField(validators=[MaxLengthValidator(5000)])
    internal_notes = models.TextField(blank=True, validators=[MaxLengthValidator(5000)])
    generated_by = models.CharField(
        max_length=10,
        choices=[("TEMPLATE", "Plantilla"), ("AI", "IA simulada"), ("HUMAN", "Manual")],
        default="TEMPLATE",
    )
    generated_model = models.CharField(max_length=100, blank=True)
    approved_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="approved_proposals",
    )
    approved_at = models.DateTimeField(null=True, blank=True, editable=False)
    reviewed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="reviewed_proposals",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True, editable=False)
    revision = models.PositiveIntegerField(default=0, editable=False)
    idempotency_key = models.CharField(max_length=100, editable=False)
    snapshot = models.JSONField(validators=[limited_metadata], editable=False)
    pdf = models.FileField(max_length=400, editable=False)
    pdf_sha256 = models.CharField(max_length=64, editable=False)
    pdf_size = models.PositiveIntegerField(editable=False)

    class Meta:
        ordering = ["-version", "id"]
        constraints = [
            models.UniqueConstraint(fields=["lead", "version"], name="unique_proposal_version"),
            models.UniqueConstraint(
                fields=["workspace", "idempotency_key"], name="unique_proposal_key"
            ),
            models.CheckConstraint(
                condition=models.Q(version__gte=1), name="proposal_version_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=["DRAFT", "GENERATED", "REVIEWED", "APPROVED", "SENT", "SUPERSEDED"]
                ),
                name="proposal_valid_status",
            ),
            models.CheckConstraint(
                condition=~models.Q(status__in=["APPROVED", "SENT"])
                | models.Q(approved_at__isnull=False),
                name="proposal_approval_required",
            ),
        ]

    def clean(self):
        super().clean()
        if not self._state.adding:
            raise ValidationError("Las propuestas se conservan; usa ProposalService para estados.")

    def __str__(self):
        return f"{self.title} · v{self.version}"

    def delete(self, *args, **kwargs):
        raise ValidationError("Las versiones de propuesta se conservan como histórico.")


class ProposalAttachment(WorkspaceModel):
    proposal = models.ForeignKey(Proposal, on_delete=models.PROTECT, related_name="attachments")
    asset = models.ForeignKey(
        SalesAsset, on_delete=models.PROTECT, related_name="proposal_attachments"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["proposal", "asset"], name="unique_proposal_asset")
        ]

    def clean(self):
        super().clean()
        if not self._state.adding:
            raise ValidationError("Los adjuntos se conservan con la versión original.")

    def delete(self, *args, **kwargs):
        raise ValidationError("Los adjuntos originales de una propuesta se conservan.")
