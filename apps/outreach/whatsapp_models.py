import re

from django.core.exceptions import ValidationError
from django.core.validators import MaxLengthValidator, RegexValidator
from django.db import models

from common.models import AppendOnlyModel, WorkspaceModel


class WhatsAppConnection(WorkspaceModel):
    """One business phone is assigned to exactly one tenant; tokens are never persisted."""

    workspace = models.OneToOneField(
        "accounts.Workspace", on_delete=models.PROTECT, related_name="whatsapp_connection"
    )
    phone_number_id = models.CharField(
        max_length=30, unique=True, validators=[RegexValidator(r"^[0-9]+$")]
    )
    waba_id = models.CharField(max_length=30, validators=[RegexValidator(r"^[0-9]+$")])
    api_version = models.CharField(max_length=10, validators=[RegexValidator(r"^v[0-9]+\.0$")])
    configuration_fingerprint = models.CharField(max_length=64, editable=False)
    is_enabled = models.BooleanField(default=False)
    revision = models.PositiveIntegerField(default=0, editable=False)


class WhatsAppTemplate(WorkspaceModel):
    connection = models.ForeignKey(WhatsAppConnection, on_delete=models.PROTECT)
    name = models.CharField(max_length=100, validators=[RegexValidator(r"^[a-z0-9_]+$")])
    language = models.CharField(
        max_length=10, validators=[RegexValidator(r"^[a-z]{2,3}(_[A-Z]{2})?$")]
    )
    category = models.CharField(
        max_length=10, choices=[("MARKETING", "Marketing"), ("UTILITY", "Utilidad")]
    )
    body_template = models.TextField(validators=[MaxLengthValidator(4000)])
    document_header = models.BooleanField(default=False)
    approved_in_meta = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=1, editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "name", "language"], name="unique_whatsapp_template"
            ),
            models.CheckConstraint(
                condition=models.Q(category__in=["MARKETING", "UTILITY"]),
                name="whatsapp_template_category",
            ),
        ]

    def clean(self):
        super().clean()
        positions = sorted(
            {int(value) for value in re.findall(r"{{([0-9]+)}}", self.body_template)}
        )
        remaining = re.sub(r"{{[0-9]+}}", "", self.body_template)
        if (
            positions != list(range(1, len(positions) + 1))
            or len(positions) > 10
            or "{{" in remaining
            or "}}" in remaining
        ):
            raise ValidationError("Usa parámetros consecutivos {{1}} a {{10}}, sin expresiones.")

    def render_parameters(self, values: list[str]) -> str:
        count = len({int(value) for value in re.findall(r"{{([0-9]+)}}", self.body_template)})
        if (
            not isinstance(values, list)
            or len(values) != count
            or any(not isinstance(value, str) or not 1 <= len(value) <= 1000 for value in values)
        ):
            raise ValidationError("Parámetros de plantilla incompletos o no válidos.")
        return re.sub(r"{{([0-9]+)}}", lambda match: values[int(match[1]) - 1], self.body_template)

    def __str__(self):
        return f"{self.name} · {self.language}"


class InboundWhatsAppMessage(AppendOnlyModel, WorkspaceModel):
    connection = models.ForeignKey(WhatsAppConnection, on_delete=models.PROTECT)
    provider_message_id = models.CharField(max_length=400)
    sender_e164 = models.CharField(max_length=20)
    received_at = models.DateTimeField()
    message_type = models.CharField(max_length=20)
    body_text = models.TextField(blank=True, validators=[MaxLengthValidator(4000)])
    reply_to = models.ForeignKey(
        "outreach.OutreachMessage",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="whatsapp_replies",
    )
    contact = models.ForeignKey(
        "companies.Contact", on_delete=models.PROTECT, null=True, blank=True
    )

    class Meta:
        ordering = ["-received_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "provider_message_id"], name="unique_inbound_whatsapp"
            )
        ]
