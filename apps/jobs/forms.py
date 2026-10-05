import uuid

from django import forms

from .models import Job
from .payloads import SCENARIOS


class DiagnosticJobForm(forms.Form):
    scenario = forms.ChoiceField(label="Escenario", choices=SCENARIOS)
    priority = forms.IntegerField(
        label="Prioridad (mayor número, antes)", min_value=0, max_value=100, initial=0
    )
    max_attempts = forms.IntegerField(
        label="Máximo de intentos", min_value=1, max_value=10, initial=3
    )
    available_at = forms.DateTimeField(
        label="Ejecutar a partir de",
        required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )
    idempotency_key = forms.UUIDField(initial=uuid.uuid4, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            )

    def clean_idempotency_key(self):
        return str(self.cleaned_data["idempotency_key"])


class JobFilterForm(forms.Form):
    status = forms.ChoiceField(
        label="Estado", required=False, choices=[("", "Todos"), *Job.Status.choices]
    )
    kind = forms.ChoiceField(
        label="Tipo", required=False, choices=[("", "Todos"), *Job.Kind.choices]
    )
