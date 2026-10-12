from django.core.paginator import Paginator
from django.db import models

from apps.campaigns.models import Campaign
from apps.companies.models import Company
from apps.crm.forms import LeadTransitionForm
from apps.crm.models import Lead
from common.pagination import section_page


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
            score_items=Paginator(
                obj.score_items.filter(
                    workspace_id=obj.workspace_id, scoring_run_id=obj.current_score_run_id
                ),
                25,
            ).get_page(params.get("score_page")),
            transition_form=LeadTransitionForm(lead=obj),
            history_page=Paginator(obj.status_history.select_related("actor"), 20).get_page(
                params.get("history_page")
            ),
        )
        context["related_sections"] = [
            ("Contactos", obj.company.contacts.filter(workspace_id=obj.workspace_id)),
            ("Fuentes", obj.company.external_refs.filter(workspace_id=obj.workspace_id)),
            ("Oportunidades", obj.opportunities.filter(workspace_id=obj.workspace_id)),
            ("Propuestas", obj.proposals.filter(workspace_id=obj.workspace_id)),
            ("Mensajes", obj.outreach_messages.filter(workspace_id=obj.workspace_id)),
            ("Tareas y recordatorios", obj.sales_tasks.filter(workspace_id=obj.workspace_id)),
        ]
    elif isinstance(obj, Company):
        context["related_sections"] = [
            (label, manager.filter(workspace_id=obj.workspace_id))
            for label, manager in (
                ("Contactos", obj.contacts),
                ("Fuentes", obj.external_refs),
                ("Perfiles sociales", obj.social_profiles),
            )
        ]
    elif isinstance(obj, Campaign):
        context["related_sections"] = [
            ("Servicios", obj.services.filter(workspace_id=obj.workspace_id)),
            ("Canales", obj.channels.filter(workspace_id=obj.workspace_id)),
        ]
        context["campaign_target"] = (
            "READY" if obj.status == "DRAFT" else "DRAFT" if obj.status == "READY" else ""
        )
    sections = []
    for index, (label, records) in enumerate(context["related_sections"]):
        relations = [
            field.name
            for field in records.model._meta.fields
            if field.is_relation and (field.many_to_one or field.one_to_one)
        ]
        sections.append(
            {
                "label": label,
                **section_page(
                    records.select_related(*relations).order_by("id"),
                    params,
                    f"related_{index + 1}",
                ),
            }
        )
    context["related_sections"] = sections
    return context
