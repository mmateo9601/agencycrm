from django.core.exceptions import ValidationError

from .models import Job

SCENARIOS = [
    ("success", "Éxito"),
    ("temporary_once", "Fallo temporal y recuperación"),
    ("permanent", "Fallo permanente controlado"),
]


def validate_payload(kind: str, payload: dict) -> dict:
    """Only diagnostic fixtures execute in phase 2; arbitrary IDs or targets are rejected."""
    if kind != Job.Kind.CHECK_MOCK_PROVIDERS:
        raise ValidationError("Este tipo de trabajo pertenece a una fase posterior.")
    if not isinstance(payload, dict) or set(payload) - {"scenario"}:
        raise ValidationError("El diagnóstico solo admite un escenario de simulación.")
    scenario = payload.get("scenario", "success")
    if not isinstance(scenario, str) or scenario not in dict(SCENARIOS):
        raise ValidationError("Escenario no válido.")
    return {"scenario": scenario}
