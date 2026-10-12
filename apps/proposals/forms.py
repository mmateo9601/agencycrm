import uuid

from django import forms

from apps.crm.models import Lead
from common.selectors import scoped_records

from .models import SalesAsset
from .selectors import assets_for_user


class GenerateForm(forms.Form):
    lead = forms.ModelChoiceField(queryset=Lead.objects.none())
    provider = forms.ChoiceField(
        choices=[("template", "Plantilla determinista"), ("mock", "IA simulada sin red")]
    )
    assets = forms.ModelMultipleChoiceField(queryset=SalesAsset.objects.none(), required=False)
    idempotency_key = forms.UUIDField(initial=uuid.uuid4, widget=forms.HiddenInput)

    def __init__(self, *args, user, workspace_id, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["lead"].queryset = (
            scoped_records(model=Lead, user=user, workspace_id=workspace_id)
            .filter(is_archived=False, company__is_active=True)
            .exclude(campaign__status="ARCHIVED")
            .select_related("company", "campaign")
        )
        self.fields["assets"].queryset = assets_for_user(
            user=user, workspace_id=workspace_id
        ).filter(is_active=True)
        self.initial.setdefault(
            "assets", list(self.fields["assets"].queryset.filter(is_default=True))
        )


class ProposalActionForm(forms.Form):
    action = forms.ChoiceField(choices=[("REVIEWED", "Revisar"), ("APPROVED", "Aprobar")])
    expected_revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)


class AssetForm(forms.Form):
    name = forms.CharField(max_length=200)
    asset_type = forms.ChoiceField(choices=SalesAsset.Type.choices)
    is_default = forms.BooleanField(required=False)
    file = forms.FileField()
