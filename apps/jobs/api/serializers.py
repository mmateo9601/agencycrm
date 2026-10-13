from rest_framework import serializers

from apps.jobs.models import Job


class JobSerializer(serializers.ModelSerializer):
    class Meta:
        model = Job
        fields = (
            "id",
            "workspace",
            "kind",
            "status",
            "progress_current",
            "progress_total",
            "attempts",
            "max_attempts",
            "available_at",
            "started_at",
            "finished_at",
            "last_error",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields
