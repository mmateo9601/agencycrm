"""Bounded, deterministic planning and CSV parsing; no database or network I/O."""

import csv
import hashlib
import io
import json

from django.core.exceptions import ValidationError

CSV_FIELDS = ["name", "website", "email", "phone", "address", "city", "province", "country"]
MAX_CSV_BYTES = 200_000


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def text_items(query: str, locations: list[str], target: int) -> list[dict]:
    """Allocate a finite result budget across explicit locations, without geocoding."""
    if not isinstance(query, str) or not query.strip() or len(query) > 250:
        raise ValidationError("Indica una consulta de hasta 250 caracteres.")
    if type(target) is not int or not 1 <= target <= 500:
        raise ValidationError("Objetivo permitido: 1–500 resultados.")
    if not isinstance(locations, list) or not 1 <= len(locations) <= 20:
        raise ValidationError("Indica entre una y veinte localidades.")
    if any(
        not isinstance(value, str) or not value.strip() or len(value) > 250 for value in locations
    ):
        raise ValidationError("Cada localidad debe tener entre 1 y 250 caracteres.")
    places = list(dict.fromkeys(value.strip() for value in locations))
    count = min(len(places), target)
    return [
        {
            "query_text": query.strip(),
            "location_text": location,
            "max_results": min(100, target // count + (index < target % count)),
        }
        for index, location in enumerate(places[:count])
    ]


def parse_csv(content: bytes) -> list[dict]:
    """Parse UTF-8 CSV within strict size/row limits; field validation happens per row."""
    if not isinstance(content, bytes) or len(content) > MAX_CSV_BYTES or b"\x00" in content:
        raise ValidationError("CSV no válido o superior a 200 KB.")
    try:
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")), strict=True)
        if reader.fieldnames != CSV_FIELDS:
            raise ValidationError("Cabecera requerida: " + ",".join(CSV_FIELDS))
        rows = []
        for row in reader:
            line = reader.line_num
            malformed = None in row or any(value is None for value in row.values())
            rows.append(
                {
                    "line": line,
                    "data": {} if malformed else row,
                    "error": "Número de columnas incorrecto." if malformed else "",
                }
            )
            if len(rows) > 200:
                raise ValidationError("Máximo 200 filas por importación.")
    except (UnicodeDecodeError, csv.Error) as exc:
        raise ValidationError("CSV mal formado; utiliza UTF-8 y comas.") from exc
    if not rows:
        raise ValidationError("El CSV no contiene filas.")
    return rows
