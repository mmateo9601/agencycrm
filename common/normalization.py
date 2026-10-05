"""Deterministic normalization without network access."""

import unicodedata
from urllib.parse import urlsplit, urlunsplit

import phonenumbers
from django.core.exceptions import ValidationError


def normalize_company_name(value: str) -> str:
    """Normalize comparison text without treating it as a unique identity."""
    text = unicodedata.normalize("NFKD", value.casefold())
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).split())


def normalize_email(value: str) -> str:
    """Return a trimmed, case-insensitive comparison value."""
    return value.strip().casefold()


def canonicalize_url(value: str) -> str:
    """Accept HTTP(S) URLs without credentials; never resolve or request the host."""
    if not value.strip():
        return ""
    try:
        parsed = urlsplit(value.strip())
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise ValueError
        if parsed.username or parsed.password:
            raise ValueError
        host = parsed.hostname.encode("idna").decode("ascii").lower()
        if ":" in host:
            host = f"[{host}]"
        port = parsed.port
        netloc = (
            host if port in (None, 80 if parsed.scheme == "http" else 443) else f"{host}:{port}"
        )
        return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", parsed.query, ""))
    except (ValueError, UnicodeError) as exc:
        raise ValidationError("Introduce una URL HTTP(S) válida y sin credenciales.") from exc


def normalize_domain(value: str) -> str:
    """Return an IDNA hostname, ignoring a leading www and preserving subdomains."""
    return (urlsplit(canonicalize_url(value)).hostname or "").removeprefix("www.")


def normalize_phone(value: str, default_region: str = "ES") -> str:
    """Validate a phone number and return E.164; an empty input stays empty."""
    if not value.strip():
        return ""
    try:
        number = phonenumbers.parse(value, default_region)
    except phonenumbers.NumberParseException as exc:
        raise ValidationError("Introduce un teléfono válido con prefijo de país.") from exc
    if not phonenumbers.is_valid_number(number):
        raise ValidationError("El teléfono no es válido.")
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
