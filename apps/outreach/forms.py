import uuid

from django import forms

from apps.companies.models import Contact
from apps.proposals.models import Proposal
from common.selectors import scoped_records

from .models import OutreachSettings, WhatsAppTemplate


class PrepareForm(forms.Form):
    proposal = forms.ModelChoiceField(queryset=Proposal.objects.none())
    contact = forms.ModelChoiceField(queryset=Contact.objects.none(), required=False)
    channel = forms.ChoiceField(
        choices=[
            ("EMAIL", "Email"),
            ("WHATSAPP", "WhatsApp simulado"),
            ("PHONE", "Llamada manual"),
            ("CONTACT_FORM", "Formulario manual"),
            ("LINKEDIN", "LinkedIn manual"),
            ("INSTAGRAM", "Instagram manual"),
            ("OTHER", "Otra acción manual"),
        ]
    )
    subject = forms.CharField(max_length=250, required=False)
    body_text = forms.CharField(max_length=12000, required=False, widget=forms.Textarea)
    idempotency_key = forms.UUIDField(initial=uuid.uuid4, widget=forms.HiddenInput)
    real_email = forms.BooleanField(
        required=False,
        label="Preparar email real con el remitente autorizado; requiere doble activación",
    )
    real_whatsapp = forms.BooleanField(
        required=False, label="Preparar WhatsApp real con plantilla aprobada"
    )
    whatsapp_template = forms.ModelChoiceField(
        queryset=WhatsAppTemplate.objects.none(), required=False
    )
    template_parameters = forms.JSONField(
        required=False,
        initial=list,
        label='Parámetros de WhatsApp: lista JSON de textos, como ["Empresa", "Servicio"]',
    )

    def __init__(self, *args, user, workspace_id, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["proposal"].queryset = (
            scoped_records(model=Proposal, user=user, workspace_id=workspace_id)
            .exclude(status="SUPERSEDED")
            .select_related("lead__company")
        )
        self.fields["contact"].queryset = scoped_records(
            model=Contact, user=user, workspace_id=workspace_id
        )
        self.fields["whatsapp_template"].queryset = scoped_records(
            model=WhatsAppTemplate, user=user, workspace_id=workspace_id
        ).filter(is_active=True, approved_in_meta=True)


class ApproveForm(forms.Form):
    expected_revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    policy_review_confirmed = forms.BooleanField(
        label="He revisado la política y elegibilidad aplicables al contacto"
    )


class SettingsForm(forms.ModelForm):
    expected_revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)

    class Meta:
        model = OutreachSettings
        fields = [
            "daily_email_limit",
            "daily_whatsapp_limit",
            "default_cooldown_days",
            "max_attempts_per_company",
        ]


class EmailConnectionForm(forms.Form):
    sender_name = forms.CharField(max_length=150, required=False, label="Nombre del remitente")
    enabled = forms.BooleanField(
        required=False, label="Autorizar la conexión SMTP para este workspace"
    )
    expected_revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)


class LiveModeForm(forms.Form):
    enabled = forms.BooleanField(
        required=False, label="Permitir canales reales autorizados en este workspace"
    )
    expected_revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)


class RecoveryForm(forms.Form):
    outcome = forms.ChoiceField(
        choices=[
            ("sync", "Actualizar CRM de una aceptación ya registrada"),
            ("retry", "Reintentar fallo confirmado"),
            ("accepted", "Servidor confirma aceptación"),
            ("not_accepted", "Servidor confirma ausencia de aceptación"),
        ]
    )
    evidence = forms.CharField(
        max_length=500, label="Referencia de la comprobación en el servidor", widget=forms.Textarea
    )
    confirmed = forms.BooleanField(label="He comprobado el resultado en el proveedor")
    provider_message_id = forms.CharField(
        required=False, max_length=400, label="WAMID confirmado (solo para WhatsApp incierto)"
    )


class WhatsAppConnectionForm(forms.Form):
    enabled = forms.BooleanField(required=False, label="Autorizar WhatsApp de este workspace")
    expected_revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)


class WhatsAppTemplateForm(forms.ModelForm):
    approved_in_meta = forms.BooleanField(
        label="He comprobado aprobación y contenido exacto en Meta"
    )

    class Meta:
        model = WhatsAppTemplate
        fields = [
            "name",
            "language",
            "category",
            "body_template",
            "document_header",
            "approved_in_meta",
        ]
