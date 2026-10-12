from uuid import UUID

from django.core.exceptions import ValidationError

from .models import Job

SCENARIOS = [
    ("success", "Éxito"),
    ("temporary_once", "Fallo temporal y recuperación"),
    ("permanent", "Fallo permanente controlado"),
]


def validate_payload(kind: str, payload: dict) -> dict:
    """Accept explicit schemas only; resource IDs are separately authorized on enqueue."""
    if kind == Job.Kind.GENERATE_PROPOSAL:
        try:
            if (
                not isinstance(payload, dict)
                or set(payload) != {"lead_id", "provider", "asset_ids"}
                or payload["provider"] not in {"template", "mock"}
                or not isinstance(payload["asset_ids"], list)
                or len(payload["asset_ids"]) > 5
            ):
                raise ValueError
            return {
                "lead_id": str(UUID(str(payload["lead_id"]))),
                "provider": payload["provider"],
                "asset_ids": sorted({str(UUID(str(pk))) for pk in payload["asset_ids"]}),
            }
        except (ValueError, TypeError) as error:
            raise ValidationError("Parámetros de generación de propuesta no válidos.") from error
    if kind == Job.Kind.SCORE_LEAD:
        if not isinstance(payload, dict) or set(payload) != {"lead_id"}:
            raise ValidationError("El scoring requiere únicamente lead_id.")
        try:
            return {"lead_id": str(UUID(str(payload["lead_id"])))}
        except (ValueError, TypeError) as exc:
            raise ValidationError("Identificador de lead no válido.") from exc
    if kind == Job.Kind.ENRICH_COMPANY:
        from apps.companies.enrichment import validate_enrichment_payload

        return validate_enrichment_payload(payload)
    if kind == Job.Kind.RUN_SEARCH_ITEM:
        if not isinstance(payload, dict) or set(payload) != {"item_id"}:
            raise ValidationError("La búsqueda requiere únicamente item_id.")
        try:
            return {"item_id": str(UUID(str(payload["item_id"])))}
        except (ValueError, TypeError) as exc:
            raise ValidationError("Identificador de búsqueda no válido.") from exc
    if kind != Job.Kind.CHECK_MOCK_PROVIDERS:
        raise ValidationError("Este tipo de trabajo pertenece a una fase posterior.")
    if not isinstance(payload, dict) or set(payload) - {"scenario"}:
        raise ValidationError("El diagnóstico solo admite un escenario de simulación.")
    scenario = payload.get("scenario", "success")
    if not isinstance(scenario, str) or scenario not in dict(SCENARIOS):
        raise ValidationError("Escenario no válido.")
    return {"scenario": scenario}
