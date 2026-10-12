from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import models
from django.utils import timezone

from common.models import AppendOnlyModel, WorkspaceModel, limited_metadata
from common.normalization import normalize_domain, normalize_email, normalize_phone


class SuppressionEntry(WorkspaceModel):
    class Scope(models.TextChoices):
        EMAIL = "EMAIL", "Email"
        PHONE = "PHONE", "Teléfono"
        DOMAIN = "DOMAIN", "Dominio"
        COMPANY = "COMPANY", "Empresa"

    scope = models.CharField(max_length=10, choices=Scope.choices)
    normalized_value = models.CharField(max_length=254)
    company = models.ForeignKey(
        "companies.Company", on_delete=models.PROTECT, null=True, blank=True
    )
    reason = models.CharField(
        max_length=20,
        choices=[
            (code, code)
            for code in (
                "DO_NOT_CONTACT",
                "UNSUBSCRIBED",
                "COMPLAINT",
                "BOUNCED",
                "MANUAL_BLOCK",
                "POLICY",
                "OTHER",
            )
        ],
    )
    source = models.CharField(max_length=250)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "scope", "normalized_value"], name="unique_suppression_value"
            )
        ]

    def clean(self):
        super().clean()
        if self.scope == "EMAIL":
            self.normalized_value = normalize_email(self.normalized_value)
            validate_email(self.normalized_value)
        elif self.scope == "PHONE":
            self.normalized_value = normalize_phone(self.normalized_value, "ES")
        elif self.scope == "DOMAIN":
            value = self.normalized_value.strip()
            self.normalized_value = normalize_domain(
                value if "://" in value else f"https://{value}"
            )
        elif self.scope == "COMPANY":
            if not self.company_id:
                raise ValidationError({"company": "Selecciona la empresa bloqueada."})
            self.normalized_value = str(self.company_id)
        if not self.normalized_value:
            raise ValidationError("Valor de supresión obligatorio.")
        if self.expires_at and timezone.is_naive(self.expires_at):
            raise ValidationError("La caducidad debe incluir zona horaria.")


class ContactEligibilityDecision(AppendOnlyModel, WorkspaceModel):
    lead = models.ForeignKey("crm.Lead", on_delete=models.PROTECT)
    contact = models.ForeignKey(
        "companies.Contact", on_delete=models.PROTECT, null=True, blank=True
    )
    message = models.ForeignKey(
        "outreach.OutreachMessage", on_delete=models.PROTECT, related_name="eligibility_decisions"
    )
    channel = models.CharField(max_length=15)
    decision = models.CharField(
        max_length=20,
        choices=[
            ("ELIGIBLE", "Elegible"),
            ("INELIGIBLE", "No elegible"),
            ("REVIEW_REQUIRED", "Revisión requerida"),
        ],
    )
    reasons = models.JSONField(default=dict, validators=[limited_metadata])
    policy_version = models.CharField(max_length=50, default="outreach-v1")
    evaluated_at = models.DateTimeField(default=timezone.now)
    evaluated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)

    class Meta:
        ordering = ["-evaluated_at", "id"]

    def clean(self):
        super().clean()
        if self.message_id and self.message.lead_id != self.lead_id:
            raise ValidationError("La decisión pertenece a otro lead.")
        if self.contact_id and self.contact.company_id != self.lead.company_id:
            raise ValidationError("El contacto pertenece a otra empresa.")

    @property
    def reason_labels(self):
        from .policies import describe_reasons

        return describe_reasons(self.reasons.get("codes", []))
