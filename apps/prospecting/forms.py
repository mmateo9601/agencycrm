import uuid
from pathlib import Path

from django import forms

from apps.campaigns.models import Campaign
from common.selectors import scoped_records

from .planner import MAX_CSV_BYTES


class PlanForm(forms.Form):
    campaign = forms.ModelChoiceField(queryset=Campaign.objects.none(), label="Campaña preparada")
    locations = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        max_length=5020,
        label="Localidades (una por línea; vacío usa la campaña)",
    )
    csv_file = forms.FileField(
        required=False, label="CSV opcional (UTF-8, hasta 200 filas y 200 KB)"
    )
    request_key = forms.UUIDField(initial=uuid.uuid4, widget=forms.HiddenInput)

    def __init__(self, *args, user, workspace_id, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["campaign"].queryset = scoped_records(
            model=Campaign, user=user, workspace_id=workspace_id
        ).filter(status__in=["READY", "RUNNING", "COMPLETED"])
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"

    def clean_locations(self):
        return [
            line.strip() for line in self.cleaned_data["locations"].splitlines() if line.strip()
        ] or None

    def clean_csv_file(self):
        upload = self.cleaned_data["csv_file"]
        if upload:
            if (
                Path(upload.name).suffix.lower() != ".csv"
                or upload.size > MAX_CSV_BYTES
                or upload.content_type
                not in {
                    "text/csv",
                    "text/plain",
                    "application/csv",
                    "application/vnd.ms-excel",
                    "application/octet-stream",
                }
            ):
                raise forms.ValidationError("Utiliza un fichero .csv de texto de hasta 200 KB.")
            return upload.read(MAX_CSV_BYTES + 1)
        return None

    def clean(self):
        data = super().clean()
        if data.get("csv_file") is not None and data.get("locations"):
            raise forms.ValidationError("Selecciona CSV o localidades, no ambos a la vez.")
        return data


class RerunForm(forms.Form):
    request_key = forms.UUIDField(initial=uuid.uuid4, widget=forms.HiddenInput)
