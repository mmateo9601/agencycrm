from rest_framework import serializers

from apps.outreach.models import OutreachMessage


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = OutreachMessage
        fields = (
            "id",
            "workspace",
            "lead",
            "proposal",
            "contact",
            "channel",
            "provider",
            "status",
            "revision",
            "approved_at",
            "approved_by",
            "job",
            "sent_at",
            "error_code",
            "error_message",
        )
        read_only_fields = fields
