from django import forms

from apps.campaigns.models import Campaign
from common.selectors import scoped_records

from .models import FollowUpRule, OutreachMessage


class FollowUpRuleForm(forms.ModelForm):
    expected_revision = forms.IntegerField(min_value=0, initial=0, widget=forms.HiddenInput)

    class Meta:
        model = FollowUpRule
        fields = ["name", "campaign", "enabled", "delay_days", "interval_days", "max_steps"]

    def __init__(self, *args, user, workspace_id, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["campaign"].queryset = scoped_records(
            model=Campaign, user=user, workspace_id=workspace_id
        )
        self.instance.workspace_id = workspace_id
        self.instance.created_by = user


class ScheduleFollowUpsForm(forms.Form):
    source = forms.ModelChoiceField(
        queryset=OutreachMessage.objects.none(), label="Contacto confirmado"
    )

    def __init__(self, *args, user, workspace_id, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["source"].queryset = scoped_records(
            model=OutreachMessage, user=user, workspace_id=workspace_id
        ).filter(status__in=["SENT", "DELIVERED", "READ"], sent_at__isnull=False)


class FollowUpTaskForm(forms.Form):
    task_id = forms.UUIDField()
    expected_revision = forms.IntegerField(min_value=0)
    action = forms.ChoiceField(
        choices=[
            ("complete", "Completar revisión"),
            ("cancel", "Cancelar"),
            ("reschedule", "Reprogramar"),
        ]
    )
    outcome = forms.CharField(max_length=2000, required=False)
    due_at = forms.DateTimeField(required=False)
