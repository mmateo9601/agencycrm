"""Bounded, explicit CSV exports. Formula-like cells are always treated as text."""

import csv
import hashlib
import io
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.models import User, Workspace
from apps.audit.models import AuditEvent
from common.access import require_capability
from common.resources import RESOURCES

FIELDS = {
    "companies": (
        "id",
        "name",
        "primary_sector",
        "city",
        "website",
        "phone_e164",
        "do_not_contact",
    ),
    "leads": ("id", "company__name", "campaign__name", "status", "score", "next_action_at"),
    "contacts": (
        "id",
        "company__name",
        "full_name",
        "normalized_email",
        "phone_e164",
        "do_not_contact",
    ),
}
MAX_EXPORT_ROWS = 5000


def csv_cell(value: object) -> str:
    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


@transaction.atomic
def export_csv(*, user: User, workspace_id: UUID, resource: str, filters: dict) -> str:
    """Authorize before reading, limit memory, and record an export without its contents."""
    require_capability(user=user, workspace_id=workspace_id, capability="export")
    if resource not in FIELDS:
        raise ValidationError("Recurso no exportable.")
    definition = RESOURCES[resource]
    records = definition.select(
        model=definition.model, user=user, workspace_id=workspace_id, filters=filters
    ).order_by("id")
    rows = list(records.values_list(*FIELDS[resource])[: MAX_EXPORT_ROWS + 1])
    if len(rows) > MAX_EXPORT_ROWS:
        raise ValidationError("Máximo 5000 filas por exportación. Aplica filtros más restrictivos.")
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(FIELDS[resource])
    writer.writerows([csv_cell(value) for value in row] for row in rows)
    data = "\ufeff" + output.getvalue()
    AuditEvent.objects.create(
        workspace_id=workspace_id,
        actor=user,
        action="CSV_EXPORTED",
        object_type=Workspace._meta.label,
        object_id=workspace_id,
        metadata={
            "resource": resource,
            "rows": len(rows),
            "sha256": hashlib.sha256(data.encode("utf-8")).hexdigest(),
        },
    )
    return data
