import uuid

from django import forms

from apps.companies.models import Contact
from apps.proposals.models import Proposal
from common.selectors import scoped_records

from .models import OutreachSettings


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
    enabled = forms.BooleanField(required=False, label="Permitir email real en este workspace")
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
    confirmed = forms.BooleanField(label="He comprobado el resultado en el servidor SMTP")
