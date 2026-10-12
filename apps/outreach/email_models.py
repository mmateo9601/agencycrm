from django.db import models

from common.models import WorkspaceModel


class EmailConnection(WorkspaceModel):
    """Workspace sender authorization; authentication secrets remain in the environment."""

    workspace = models.OneToOneField(
        "accounts.Workspace", on_delete=models.PROTECT, related_name="email_connection"
    )
    sender_email = models.EmailField(max_length=254)
    sender_name = models.CharField(max_length=150, blank=True)
    configuration_fingerprint = models.CharField(max_length=64, editable=False)
    is_enabled = models.BooleanField(default=False)
    revision = models.PositiveIntegerField(default=0, editable=False)

    def __str__(self):
        return self.sender_email
