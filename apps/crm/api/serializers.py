from rest_framework import serializers

from apps.crm.models import Lead


class LeadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Lead
        fields = (
            "id",
            "workspace",
            "company",
            "campaign",
            "status",
            "status_revision",
            "score",
            "score_version",
            "recommended_service",
            "assigned_to",
            "next_action_at",
            "last_contact_at",
            "is_archived",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields
