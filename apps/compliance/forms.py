from django import forms

from apps.companies.models import Company
from common.selectors import scoped_records

from .models import SuppressionEntry


class SuppressionForm(forms.Form):
    scope = forms.ChoiceField(choices=SuppressionEntry.Scope.choices)
    value = forms.CharField(max_length=254, required=False)
    company = forms.ModelChoiceField(queryset=Company.objects.none(), required=False)
    reason = forms.ChoiceField(choices=SuppressionEntry._meta.get_field("reason").choices)
    source = forms.CharField(max_length=250)
    expires_at = forms.DateTimeField(required=False)

    def __init__(self, *args, user, workspace_id, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["company"].queryset = scoped_records(
            model=Company, user=user, workspace_id=workspace_id
        )
