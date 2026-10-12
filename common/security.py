"""URL and address validation independent of the ORM and network transport."""

import ipaddress
from urllib.parse import urlsplit, urlunsplit

from integrations.websites.errors import CrawlError


def public_address(value: str) -> str:
    """Reject non-global destinations, including IPv6 transition mechanisms."""
    try:
        ip = ipaddress.ip_address(value)
    except ValueError as exc:
        raise CrawlError("UNSAFE_ADDRESS") from exc
    if (
        not ip.is_global
        or ip.is_multicast
        or ip.is_reserved
        or (
            ip.version == 6
            and (
                ip.ipv4_mapped
                or ip.sixtofour
                or ip.teredo
                or ip in ipaddress.ip_network("64:ff9b::/96")
                or ip in ipaddress.ip_network("64:ff9b:1::/48")
            )
        )
    ):
        raise CrawlError("UNSAFE_ADDRESS")
    return str(ip)


def crawl_url(value: str) -> str:
    """Accept public-page URLs only, without credentials, queries or custom ports."""
    try:
        if not isinstance(value, str) or len(value) > 500:
            raise ValueError
        if any(ord(char) <= 32 or ord(char) == 127 for char in value) or "\\" in value:
            raise ValueError
        parsed = urlsplit(value)
        host = (parsed.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
        if (
            parsed.scheme not in {"http", "https"}
            or not host
            or "%" in host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.port not in {None, 80 if parsed.scheme == "http" else 443}
        ):
            raise ValueError
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if "." not in host or host.endswith((".localhost", ".local", ".internal")):
                raise ValueError from None
        else:
            public_address(host)
        netloc = f"[{host}]" if ":" in host else host
        return urlunsplit((parsed.scheme, netloc, parsed.path or "/", "", ""))
    except (ValueError, UnicodeError) as exc:
        raise CrawlError("UNSAFE_URL") from exc
