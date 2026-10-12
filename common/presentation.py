from django.core.paginator import Paginator
from django.db import models

from apps.campaigns.models import Campaign
from apps.companies.models import Company
from apps.crm.forms import LeadTransitionForm
from apps.crm.models import Lead


def detail_context(*, obj: models.Model, params) -> dict:
    """Prepare scoped related data and display values, without applying mutations."""
    fields = []
    for field in obj._meta.fields:
        if field.name in {"workspace", "id", "status_revision"}:
            continue
        value = getattr(obj, f"get_{field.name}_display", lambda f=field: getattr(obj, f.name))()
        if isinstance(value, bool):
            value = "Sí" if value else "No"
        fields.append((field.verbose_name, value if value not in (None, "") else "—"))
    context = {"detail_fields": fields, "related_sections": []}
    if isinstance(obj, Lead):
        context.update(
            score_items=obj.score_items.filter(
                workspace_id=obj.workspace_id, scoring_run_id=obj.current_score_run_id
            ),
            transition_form=LeadTransitionForm(lead=obj),
            history_page=Paginator(obj.status_history.select_related("actor"), 20).get_page(
                params.get("history_page")
            ),
        )
        context["related_sections"] = [
            ("Contactos", obj.company.contacts.filter(workspace_id=obj.workspace_id)[:25]),
            ("Fuentes", obj.company.external_refs.filter(workspace_id=obj.workspace_id)[:25]),
            ("Oportunidades", obj.opportunities.filter(workspace_id=obj.workspace_id)[:25]),
            ("Propuestas", obj.proposals.filter(workspace_id=obj.workspace_id)[:25]),
            ("Mensajes", obj.outreach_messages.filter(workspace_id=obj.workspace_id)[:25]),
            ("Tareas y recordatorios", obj.sales_tasks.filter(workspace_id=obj.workspace_id)[:25]),
        ]
    elif isinstance(obj, Company):
        context["related_sections"] = [
            (label, manager.filter(workspace_id=obj.workspace_id)[:25])
            for label, manager in (
                ("Contactos", obj.contacts),
                ("Fuentes", obj.external_refs),
                ("Perfiles sociales", obj.social_profiles),
            )
        ]
    elif isinstance(obj, Campaign):
        context["related_sections"] = [
            ("Servicios", obj.services.filter(workspace_id=obj.workspace_id)[:25]),
            ("Canales", obj.channels.filter(workspace_id=obj.workspace_id)[:25]),
        ]
        context["campaign_target"] = (
            "READY" if obj.status == "DRAFT" else "DRAFT" if obj.status == "READY" else ""
        )
    return context
