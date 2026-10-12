from django import forms

from apps.accounts.models import User
from apps.campaigns.models import Campaign
from apps.companies.models import Company
from apps.crm.models import Lead
from common.selectors import with_related


class RecordFilterForm(forms.Form):
    q = forms.CharField(label="Buscar", max_length=200, required=False)

    def __init__(self, *args, model, workspace, **kwargs):
        super().__init__(*args, **kwargs)
        name = model._meta.model_name
        if name in {"company", "lead"}:
            self.fields["city"] = forms.CharField(label="Ciudad", max_length=200, required=False)
            self.fields["sector"] = forms.CharField(label="Sector", max_length=200, required=False)
        if name in {"lead", "campaign"}:
            self.fields["status"] = forms.ChoiceField(
                label="Estado", choices=[("", "Todos"), *model.Status.choices], required=False
            )
        for field, related, label in (
            ("company", Company, "Empresa"),
            ("campaign", Campaign, "Campaña"),
            ("lead", Lead, "Lead"),
        ):
            if any(f.name == field for f in model._meta.fields):
                self.fields[field] = forms.ModelChoiceField(
                    queryset=with_related(related.objects.filter(workspace=workspace)),
                    label=label,
                    required=False,
                )
        if name == "lead":
            self.fields["assigned_to"] = forms.ModelChoiceField(
                label="Responsable",
                required=False,
                queryset=User.objects.filter(
                    memberships__workspace=workspace, memberships__is_active=True, is_active=True
                ),
            )
            self.fields["min_score"] = forms.IntegerField(
                label="Score mínimo", required=False, min_value=0, max_value=100
            )
            self.fields["max_score"] = forms.IntegerField(
                label="Score máximo", required=False, min_value=0, max_value=100
            )
            for field, label in (
                ("has_email", "Tiene email"),
                ("has_phone", "Tiene teléfono"),
                ("has_opportunity", "Tiene oportunidad activa"),
            ):
                self.fields[field] = forms.ChoiceField(
                    label=label,
                    required=False,
                    choices=[("", "Todos"), ("yes", "Sí"), ("no", "No")],
                )
            self.fields["archived"] = forms.ChoiceField(
                label="Archivo",
                required=False,
                choices=[("", "Activos"), ("yes", "Archivados"), ("all", "Todos")],
            )
        if name in {"company", "opportunity"}:
            self.fields["active"] = forms.ChoiceField(
                label="Actividad",
                required=False,
                choices=[("", "Todos"), ("yes", "Activos"), ("no", "Inactivos")],
            )
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            )

    def clean(self):
        data = super().clean()
        if (
            data.get("min_score") is not None
            and data.get("max_score") is not None
            and data["min_score"] > data["max_score"]
        ):
            raise forms.ValidationError("El score mínimo no puede superar el máximo.")
        return data
