from django import forms


class AuditFilterForm(forms.Form):
    action = forms.CharField(max_length=100, required=False, label="Acción")
    object_id = forms.UUIDField(required=False, label="ID del registro")
    correlation_id = forms.UUIDField(required=False, label="Correlación")


class ExportForm(forms.Form):
    resource = forms.ChoiceField(
        choices=[("companies", "Empresas"), ("leads", "Leads"), ("contacts", "Contactos")],
        label="Datos",
    )


class RetentionForm(forms.Form):
    inbound_days = forms.IntegerField(
        min_value=7, max_value=3650, initial=365, label="Conservar texto entrante durante (días)"
    )
    confirmed = forms.BooleanField(label="Confirmo la política y la eliminación del texto antiguo")
    request_key = forms.UUIDField(widget=forms.HiddenInput)
