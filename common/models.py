import json

from django.core.exceptions import ValidationError
from django.db import models

from apps.accounts.models import TimestampedModel


def limited_metadata(value):
    if not isinstance(value, dict) or len(json.dumps(value).encode()) > 16000:
        raise ValidationError("Los metadatos deben ser un objeto JSON de hasta 16 KB.")


class WorkspaceModel(TimestampedModel):
    """Tenant identity is immutable; related tenant records must share its workspace."""

    workspace = models.ForeignKey("accounts.Workspace", on_delete=models.PROTECT)

    class Meta:
        abstract = True

    def clean(self):
        super().clean()
        errors = {}
        if not self._state.adding:
            previous = (
                type(self).objects.filter(pk=self.pk).values_list("workspace_id", flat=True).first()
            )
            if previous != self.workspace_id:
                errors["workspace"] = "No se puede cambiar el espacio de trabajo."
        for field in self._meta.fields:
            if (
                not field.is_relation
                or field.name == "workspace"
                or not getattr(self, field.attname)
            ):
                continue
            related = field.related_model
            if issubclass(related, WorkspaceModel):
                if not related.objects.filter(
                    pk=getattr(self, field.attname), workspace_id=self.workspace_id
                ).exists():
                    errors[field.name] = "La relación no pertenece a este espacio de trabajo."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("El historial es inmutable.")

    def delete(self):
        raise ValidationError("El historial es inmutable.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError("El historial es inmutable.")


class AppendOnlyModel(models.Model):
    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("El historial es inmutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("El historial es inmutable.")
