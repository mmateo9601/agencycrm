from rest_framework import serializers

from apps.campaigns.models import Campaign
from apps.campaigns.schema import CAMPAIGN_FIELDS
from common.api import StrictInputMixin


class CampaignSerializer(serializers.ModelSerializer):
    class Meta:
        model = Campaign
        fields = (
            "id",
            "workspace",
            "name",
            "description",
            "status",
            "target_query",
            "target_sector",
            "target_location_text",
            "target_country_code",
            "desired_leads",
            "minimum_score",
            "require_manual_approval",
            "places_request_limit",
            "places_requests_used",
            "services",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class CampaignCreateSerializer(StrictInputMixin, serializers.ModelSerializer):
    idempotency_key = serializers.UUIDField()
    services = serializers.ListField(child=serializers.UUIDField(), max_length=100, default=list)

    class Meta:
        model = Campaign
        fields = ("idempotency_key", *CAMPAIGN_FIELDS, "services")
        extra_kwargs = {
            field: {"default": Campaign._meta.get_field(field).get_default()}
            for field in CAMPAIGN_FIELDS
            if field != "name"
        }
