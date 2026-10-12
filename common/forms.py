from django import forms
from django.core.exceptions import ValidationError

from apps.accounts.models import User
from common.access import workspace_access
from common.selectors import with_related


class ScopedModelForm(forms.ModelForm):
    """Model forms never accept workspace IDs or unscoped foreign keys from POST."""

    def __init__(self, *args, user, workspace_id, **kwargs):
        super().__init__(*args, **kwargs)
        workspace = workspace_access(user=user, workspace_id=workspace_id)
        self.instance.workspace = workspace
        for field in self.fields.values():
            if isinstance(field, forms.ModelChoiceField):
                if field.queryset.model is User:
                    field.queryset = User.objects.filter(
                        is_active=True,
                        memberships__workspace=workspace,
                        memberships__is_active=True,
                    ).order_by("username")
                else:
                    field.queryset = with_related(field.queryset.filter(workspace=workspace))
            css = (
                "form-check-input"
                if isinstance(field.widget, forms.CheckboxInput)
                else "form-select"
                if isinstance(field.widget, forms.Select)
                else "form-control"
            )
            field.widget.attrs["class"] = css
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs["rows"] = 3
            if isinstance(field, forms.DateTimeField):
                field.widget = forms.DateTimeInput(
                    attrs={"type": "datetime-local", "class": "form-control"},
                    format="%Y-%m-%dT%H:%M",
                )
        if (
            self.instance.pk
            and not self.instance._state.adding
            and self.instance._meta.model_name == "lead"
        ):
            for name in ("campaign", "company"):
                self.fields[name].disabled = True


def add_service_errors(form: forms.Form, error: ValidationError) -> None:
    """Safely display service/model errors, including fields excluded from the UI."""
    if hasattr(error, "error_dict"):
        for field, errors in error.error_dict.items():
            for item in errors:
                form.add_error(field if field in form.fields else None, item)
    else:
        form.add_error(None, error)
