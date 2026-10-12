from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.accounts.models import Membership
from common.models import WorkspaceModel


class FollowUpRule(WorkspaceModel):
    """An explicitly enabled plan for internal reviews, never a transport instruction."""

    name = models.CharField(max_length=150)
    campaign = models.ForeignKey(
        "campaigns.Campaign", on_delete=models.PROTECT, null=True, blank=True
    )
    enabled = models.BooleanField(default=False)
    delay_days = models.PositiveSmallIntegerField(default=14, validators=[MaxValueValidator(365)])
    interval_days = models.PositiveSmallIntegerField(
        default=14, validators=[MinValueValidator(1), MaxValueValidator(365)]
    )
    max_steps = models.PositiveSmallIntegerField(
        default=2, validators=[MinValueValidator(1), MaxValueValidator(10)]
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    revision = models.PositiveIntegerField(default=0, editable=False)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(fields=["workspace", "name"], name="unique_followup_rule_name"),
            models.CheckConstraint(
                condition=models.Q(
                    delay_days__lte=365,
                    interval_days__gte=1,
                    interval_days__lte=365,
                    max_steps__gte=1,
                    max_steps__lte=10,
                ),
                name="followup_rule_ranges",
            ),
        ]

    def clean(self):
        super().clean()
        if not Membership.objects.filter(
            workspace_id=self.workspace_id,
            user_id=self.created_by_id,
            is_active=True,
            user__is_active=True,
            role__in=["OWNER", "ADMIN"],
        ).exists():
            raise ValidationError("La regla requiere un propietario o administrador activo.")

    def __str__(self):
        return self.name
