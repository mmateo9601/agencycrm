from collections.abc import Callable, Sequence
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.shortcuts import get_object_or_404

from apps.accounts.models import User
from apps.audit.services import record_event
from common.access import workspace_access
from common.selectors import scoped_record


def apply_data(*, obj: models.Model, data: dict, fields: Sequence[str]) -> None:
    """Whitelist fields and re-resolve related IDs inside the object's workspace."""
    if set(data) - set(fields):
        raise ValidationError("Se han enviado campos no permitidos.")
    for name, value in data.items():
        field = obj._meta.get_field(name)
        if field.is_relation and value is not None:
            related = field.related_model
            pk = getattr(value, "pk", value)
            filters = {"pk": pk}
            if related is User:
                filters.update(
                    is_active=True,
                    memberships__workspace_id=obj.workspace_id,
                    memberships__is_active=True,
                )
            else:
                filters["workspace_id"] = obj.workspace_id
            value = get_object_or_404(related.objects.filter(**filters))
        setattr(obj, name, value)


def save_record(
    *,
    model: type[models.Model],
    user: User,
    workspace_id: UUID,
    data: dict,
    fields: Sequence[str],
    pk: UUID | None = None,
    creation_id: UUID | None = None,
    after_save: Callable | None = None,
) -> models.Model:
    """Validate, persist and audit one scoped mutation atomically."""
    workspace = workspace_access(user=user, workspace_id=workspace_id, write=True)
    try:
        with transaction.atomic():
            obj = (
                scoped_record(model=model, user=user, workspace_id=workspace_id, pk=pk)
                if pk
                else model(workspace=workspace, **({"pk": creation_id} if creation_id else {}))
            )
            apply_data(obj=obj, data=data, fields=fields)
            if not pk and hasattr(obj, "created_by_id"):
                obj.created_by = user
            if pk:
                derived_fields = {
                    name
                    for name in (
                        "normalized_name",
                        "normalized_domain",
                        "normalized_email",
                        "phone_e164",
                    )
                    if hasattr(obj, name)
                }
                # Editing a note must never overwrite a concurrently changed status or archive flag.
                obj.save(update_fields=set(data) | derived_fields | {"updated_at"})
            else:
                obj.save(force_insert=True)
            if after_save:
                after_save(obj)
            record_event(
                actor=user,
                obj=obj,
                action=f"{model.__name__.upper()}_{'UPDATED' if pk else 'CREATED'}",
            )
            return obj
    except IntegrityError as exc:
        raise ValidationError("Ya existe un registro con esa combinación de datos.") from exc


@transaction.atomic
def delete_record(*, model: type[models.Model], user: User, workspace_id: UUID, pk: UUID) -> None:
    """Delete a non-historical child record with an audit event."""
    workspace_access(user=user, workspace_id=workspace_id, delete=True)
    obj = scoped_record(model=model, user=user, workspace_id=workspace_id, pk=pk)
    record_event(actor=user, obj=obj, action=f"{model.__name__.upper()}_DELETED")
    obj.delete()
