from rest_framework import serializers

from apps.proposals.models import Proposal


class ProposalSerializer(serializers.ModelSerializer):
    class Meta:
        model = Proposal
        fields = (
            "id",
            "workspace",
            "lead",
            "version",
            "title",
            "status",
            "revision",
            "approved_by",
            "approved_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields
