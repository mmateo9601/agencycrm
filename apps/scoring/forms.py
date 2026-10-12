import uuid

from django import forms

from .models import ScoringRule


class ScoreForm(forms.Form):
    idempotency_key = forms.UUIDField(initial=uuid.uuid4, widget=forms.HiddenInput)


class RuleForm(forms.ModelForm):
    expected_version = forms.IntegerField(widget=forms.HiddenInput)

    class Meta:
        model = ScoringRule
        fields = ["name", "points", "enabled", "configuration", "sort_order"]
