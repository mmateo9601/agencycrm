"""Deterministic rules over explicit snapshots; no ORM, network or state changes."""

from django.core.exceptions import ValidationError

ENGINE_VERSION = "scoring-v1"
DEFAULTS = [
    ("WEBSITE", "Web disponible", 10, {}),
    ("EMAIL", "Email empresarial disponible", 12, {}),
    ("PHONE", "Teléfono comercial normalizado", 8, {}),
    ("SOCIAL", "Perfiles sociales encontrados", 5, {}),
    ("HIGH_OPPORTUNITY", "Oportunidad de prioridad alta", 15, {}),
    ("MEDIUM_OPPORTUNITY", "Oportunidad de prioridad media", 8, {}),
    ("LOW_REVIEWS", "Pocas reseñas registradas", 10, {"max_reviews": 20}),
    ("WEAK_CTA", "Conversión no observada", 10, {}),
    ("MISSING_CHANNEL", "Canal de campaña no disponible", 7, {"channel": "EMAIL"}),
]
CODES = {row[0] for row in DEFAULTS}


def validate_configuration(code: str, configuration: dict) -> None:
    """Accept a closed declarative schema, never executable expressions."""
    if code not in CODES or not isinstance(configuration, dict):
        raise ValidationError("Regla o configuración desconocida.")
    allowed = (
        {"max_reviews"}
        if code == "LOW_REVIEWS"
        else ({"channel"} if code == "MISSING_CHANNEL" else set())
    )
    if set(configuration) - allowed:
        raise ValidationError("La configuración incluye campos no permitidos.")
    if code == "LOW_REVIEWS":
        value = configuration.get("max_reviews", 20)
        if type(value) is not int or not 0 <= value <= 100000:
            raise ValidationError("El umbral de reseñas debe estar entre 0 y 100000.")
    if code == "MISSING_CHANNEL" and configuration.get("channel", "EMAIL") not in {
        "EMAIL",
        "PHONE",
        "LINKEDIN",
        "INSTAGRAM",
    }:
        raise ValidationError("Canal no soportado por el detector.")


def evaluate(snapshot: dict, rules: list[dict]) -> tuple[int, list[dict], list[dict]]:
    """Return clamped score, complete enabled-rule breakdown and observed opportunities."""
    detectors = []
    matches = {
        "WEBSITE": snapshot["website"],
        "EMAIL": snapshot["email"],
        "PHONE": snapshot["phone"],
        "SOCIAL": snapshot["social"],
        "HIGH_OPPORTUNITY": snapshot["high_opportunity"],
        "MEDIUM_OPPORTUNITY": snapshot["medium_opportunity"],
    }
    for rule in rules:
        validate_configuration(rule["code"], rule["configuration"])
        code, config = rule["code"], rule["configuration"]
        if code == "LOW_REVIEWS":
            matches[code] = snapshot["review_count"] is not None and snapshot[
                "review_count"
            ] < config.get("max_reviews", 20)
            if matches[code]:
                detectors.append(
                    {
                        "code": code,
                        "type": "REVIEWS",
                        "severity": "MEDIUM",
                        "title": "Pocas reseñas registradas",
                        "evidence": f"Reseñas: {snapshot['review_count']}; "
                        f"umbral configurado: {config.get('max_reviews', 20)}.",
                        "confidence": "0.800",
                        "service_code": "REPUTATION",
                    }
                )
        elif code == "WEAK_CTA":
            obs = snapshot["observation"]
            matches[code] = bool(
                obs and not obs["contact"] and not obs["booking"] and obs["contact_form"] is False
            )
            if matches[code]:
                detectors.append(
                    {
                        "code": code,
                        "type": "CONVERSION",
                        "severity": "MEDIUM",
                        "title": "Conversión no observada en las páginas analizadas",
                        "evidence": f"Observación {obs['id']}: {obs['pages']} páginas; "
                        "sin contacto, reserva o formulario observado. Requiere revisión.",
                        "confidence": "0.600",
                        "service_code": "WEB_DESIGN",
                    }
                )
        elif code == "MISSING_CHANNEL":
            channel = config.get("channel", "EMAIL")
            present = snapshot["available_channels"][channel]
            matches[code] = channel in snapshot["campaign_channels"] and not present
            if matches[code]:
                detectors.append(
                    {
                        "code": code,
                        "type": "CONVERSION",
                        "severity": "MEDIUM",
                        "title": "Canal objetivo sin datos registrados",
                        "evidence": f"Canal {channel} habilitado en campaña; "
                        "no constan datos empresariales para ese canal.",
                        "confidence": "0.500",
                        "service_code": "WEB_DESIGN",
                    }
                )
    matches["MEDIUM_OPPORTUNITY"] = matches["MEDIUM_OPPORTUNITY"] or bool(detectors)
    evidence = {
        "WEBSITE": f"Web registrada: {snapshot['website']}.",
        "EMAIL": f"Email empresarial registrado: {snapshot['email']}.",
        "PHONE": f"Teléfono empresarial E.164 registrado: {snapshot['phone']}.",
        "SOCIAL": f"Perfil social registrado: {snapshot['social']}.",
        "HIGH_OPPORTUNITY": f"Oportunidad manual HIGH activa: {snapshot['high_opportunity']}.",
        "MEDIUM_OPPORTUNITY": f"Oportunidad manual MEDIUM: {snapshot['medium_opportunity']}; "
        f"detectores coincidentes: {len(detectors)}.",
        "LOW_REVIEWS": f"Reseñas registradas: {snapshot['review_count']}; "
        "comparar con max_reviews.",
        "WEAK_CTA": f"Observación válida utilizada: "
        f"{snapshot['observation']['id'] if snapshot['observation'] else 'ninguna'}.",
        "MISSING_CHANNEL": "Comparación del canal configurado con los canales habilitados "
        "y datos disponibles del snapshot.",
    }
    items = [
        {
            "rule_code": rule["code"],
            "points": rule["points"] if matches[rule["code"]] else 0,
            "explanation": rule["name"],
            "evidence": evidence[rule["code"]]
            + (" Coincide." if matches[rule["code"]] else " No coincide o datos insuficientes."),
            "matched": bool(matches[rule["code"]]),
            "rule_version": rule["version"],
        }
        for rule in rules
    ]
    return max(0, min(100, sum(item["points"] for item in items))), items, detectors
