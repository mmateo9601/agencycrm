from common.forms import ScopedModelForm

from .models import Campaign, CampaignChannel
from .schema import CAMPAIGN_FIELDS, CHANNEL_FIELDS


class CampaignForm(ScopedModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Older clients can omit the new limit without resetting an existing budget.
        self.fields["places_request_limit"].required = False

    def clean_places_request_limit(self):
        value = self.cleaned_data["places_request_limit"]
        return self.instance.places_request_limit if value is None else value

    class Meta:
        model = Campaign
        fields = (*CAMPAIGN_FIELDS, "services")


class ChannelForm(ScopedModelForm):
    class Meta:
        model = CampaignChannel
        fields = CHANNEL_FIELDS
