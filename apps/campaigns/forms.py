from common.forms import ScopedModelForm

from .models import Campaign, CampaignChannel
from .schema import CAMPAIGN_FIELDS, CHANNEL_FIELDS


class CampaignForm(ScopedModelForm):
    class Meta:
        model = Campaign
        fields = (*CAMPAIGN_FIELDS, "services")


class ChannelForm(ScopedModelForm):
    class Meta:
        model = CampaignChannel
        fields = CHANNEL_FIELDS
