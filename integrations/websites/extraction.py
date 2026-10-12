"""Extract bounded business facts, never HTML or executable markup."""

import re
from urllib.parse import unquote, urljoin, urlsplit

import phonenumbers
from bs4 import BeautifulSoup
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from common.security import crawl_url

from .errors import CrawlError

SOCIAL = {
    "instagram.com": "INSTAGRAM",
    "facebook.com": "FACEBOOK",
    "linkedin.com": "LINKEDIN",
    "tiktok.com": "TIKTOK",
    "youtube.com": "YOUTUBE",
    "x.com": "X",
    "twitter.com": "X",
}
RELEVANT = re.compile(r"contact|about|nosotros|servicio|service|reserva|booking|cita", re.I)
PRIVATE = re.compile(r"login|logout|signin|admin|account|checkout|cart|delete|wp-json", re.I)


def private_path(url: str) -> bool:
    """Check decoded paths too, so escaped login/admin routes are never crawled."""
    path = urlsplit(url).path
    for _ in range(10):
        decoded = unquote(path)
        if decoded == path:
            return bool(PRIVATE.search(path))
        path = decoded
    return True


def extract(html: str, url: str, region: str) -> dict:
    """Observe visible contacts and links; cap facts and retain each source URL."""
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True)[:300] if soup.title else ""
    meta = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    description = str(meta.get("content", ""))[:1000] if meta else ""
    analytics = any(
        marker in html.lower()
        for marker in ("googletagmanager.com/gtag", "google-analytics.com", "connect.facebook.net")
    )
    for element in soup.select("script,style,noscript,template,[hidden],[aria-hidden='true']"):
        element.decompose()
    text = soup.get_text(" ", strip=True)
    emails = set(re.findall(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", text))
    phones = {
        phonenumbers.format_number(match.number, phonenumbers.PhoneNumberFormat.E164)
        for match in phonenumbers.PhoneNumberMatcher(text, region)
    }
    socials, links = {}, []
    flags = dict(
        has_contact_page=False, has_privacy_page=False, has_booking=False, has_ecommerce=False
    )
    contact_form = bool(soup.find("form"))
    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"])
        if href.lower().startswith("mailto:"):
            emails.add(unquote(href[7:].split("?", 1)[0]).lower())
            continue
        if href.lower().startswith("tel:"):
            try:
                number = phonenumbers.parse(unquote(href[4:]), region)
                if phonenumbers.is_valid_number(number):
                    phones.add(
                        phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
                    )
            except phonenumbers.NumberParseException:
                continue
            continue
        try:
            target = crawl_url(urljoin(url, href))
        except CrawlError:
            continue
        host = urlsplit(target).hostname
        for domain, platform in SOCIAL.items():
            if host == domain or host.endswith("." + domain):
                if len(socials) < 20:
                    socials[target] = platform
        if host != urlsplit(url).hostname:
            continue
        label = urlsplit(target).path + " " + anchor.get_text(" ", strip=True)
        flags["has_contact_page"] |= bool(re.search(r"contact", label, re.I))
        flags["has_privacy_page"] |= bool(re.search(r"privacy|privacidad", label, re.I))
        flags["has_booking"] |= bool(re.search(r"booking|reserva|cita", label, re.I))
        flags["has_ecommerce"] |= bool(re.search(r"cart|checkout|carrito|tienda|shop", label, re.I))
        if RELEVANT.search(label) and not private_path(target) and len(links) < 40:
            links.append(target)
    valid_emails = []
    for email in sorted(emails):
        try:
            validate_email(email)
        except ValidationError:
            continue
        valid_emails.append(email.lower())
    return {
        "title": title,
        "meta_description": description,
        **flags,
        "emails": valid_emails[:20],
        "phones": sorted(phones)[:20],
        "socials": socials,
        "links": list(dict.fromkeys(links)),
        "analytics_observed": analytics,
        "contact_form_observed": contact_form,
    }
