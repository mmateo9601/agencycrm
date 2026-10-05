from django import forms

from common.forms import ScopedModelForm

from .models import Lead, Opportunity
from .policies import allowed_transitions
from .schema import LEAD_FIELDS, OPPORTUNITY_FIELDS


class LeadForm(ScopedModelForm):
    class Meta:
        model = Lead
        fields = LEAD_FIELDS


class OpportunityForm(ScopedModelForm):
    class Meta:
        model = Opportunity
        fields = OPPORTUNITY_FIELDS


class LeadTransitionForm(forms.Form):
    target = forms.ChoiceField(
        label="Nuevo estado", widget=forms.Select(attrs={"class": "form-select"})
    )
    reason = forms.CharField(
        label="Motivo",
        required=False,
        max_length=2000,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )
    expected_revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)

    def __init__(self, *args, lead, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["target"].choices = allowed_transitions(lead)
        self.fields["expected_revision"].initial = lead.status_revision
