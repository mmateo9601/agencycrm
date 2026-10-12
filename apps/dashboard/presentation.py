from urllib.parse import urlencode
from uuid import UUID

from django.urls import reverse

from apps.accounts.models import User
from common.access import workspace_permissions
from common.resources import RESOURCES

from .selectors import dashboard_for_user


def dashboard_context(*, user: User, workspace_id: UUID) -> dict:
    """Present authorized counters with links to the existing workspace lists."""
    snapshot = dashboard_for_user(user=user, workspace_id=workspace_id)
    companies_url = reverse("workspace:list", args=[workspace_id, "companies"])
    leads_url = reverse("workspace:list", args=[workspace_id, "leads"])
    cards = [
        {"label": "Empresas totales", "value": snapshot.companies_total, "url": companies_url},
        {"label": "Leads activos totales", "value": snapshot.leads_total, "url": leads_url},
        *[
            {
                "label": f"Leads: {count.label}",
                "value": count.value,
                "url": f"{leads_url}?{urlencode({'status': count.status})}",
            }
            for count in snapshot.lead_counts
        ],
        {
            "label": "Trabajos fallidos",
            "value": snapshot.jobs_failed,
            "url": reverse("jobs:list", args=[workspace_id]) + "?status=FAILED",
        },
        {
            "label": "Mensajes pendientes de aprobación",
            "value": snapshot.messages_pending,
            "url": reverse("outreach:outbox", args=[workspace_id]),
        },
    ]
    return {
        "workspace": snapshot.workspace,
        "snapshot": snapshot,
        "cards": cards,
        "resources": RESOURCES.values(),
        "dashboard_active": True,
        **workspace_permissions(user=user, workspace=snapshot.workspace),
    }
