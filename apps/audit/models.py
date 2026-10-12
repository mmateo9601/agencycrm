import uuid

from django.conf import settings
from django.db import models

from common.models import AppendOnlyModel, limited_metadata


class AuditEvent(AppendOnlyModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey("accounts.Workspace", on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    action = models.CharField(max_length=100)
    object_type = models.CharField(max_length=100)
    object_id = models.UUIDField()
    correlation_id = models.UUIDField(default=uuid.uuid4)
    metadata = models.JSONField(default=dict, validators=[limited_metadata])
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "id"]
        indexes = [
            models.Index(fields=["workspace", "object_id"]),
            models.Index(fields=["workspace", "-created_at"]),
            models.Index(fields=["workspace", "action", "-created_at"]),
        ]
