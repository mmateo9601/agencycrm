import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)


class TimestampedModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Workspace(TimestampedModel):
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Membership(TimestampedModel):
    class Role(models.TextChoices):
        OWNER = "OWNER", "Propietario"
        ADMIN = "ADMIN", "Administrador"
        SALES = "SALES", "Comercial"
        MEMBER = "MEMBER", "Miembro"
        VIEWER = "VIEWER", "Lector"

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.MEMBER)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["workspace", "user"], name="unique_workspace_user"),
            models.CheckConstraint(
                condition=models.Q(
                    role__in=[
                        "OWNER",
                        "ADMIN",
                        "SALES",
                        "MEMBER",
                        "VIEWER",
                    ]
                ),
                name="valid_membership_role",
            ),
        ]


class AgencyProfile(TimestampedModel):
    workspace = models.OneToOneField(
        Workspace, on_delete=models.CASCADE, related_name="agency_profile"
    )
    trading_name = models.CharField(max_length=200)
    legal_name = models.CharField(max_length=250, blank=True)
    website = models.URLField(blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    address = models.TextField(blank=True)
    # Uploads are deferred; no unauthenticated media route is exposed in Bootstrap.
    logo = models.FileField(upload_to="agency_logos/", blank=True, editable=False)
    value_proposition = models.TextField(blank=True)
    default_sender_name = models.CharField(max_length=200, blank=True)
    meeting_url = models.URLField(blank=True)
    proposal_footer = models.TextField(blank=True)
    brand_primary_text = models.CharField(max_length=200, blank=True)

    def __str__(self):
        return self.trading_name


class ServiceOffering(TimestampedModel):
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, related_name="service_offerings"
    )
    code = models.SlugField(max_length=50)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    default_pitch = models.TextField(blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "code"], name="unique_workspace_service_code"
            )
        ]

    def __str__(self):
        return self.name
