"""Deterministic URL and domain normalization for company records."""
from __future__ import annotations
import re
from ipaddress import ip_address
from urllib.parse import urlsplit, urlunsplit

TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}
THIRD_PARTY_HOSTS = (
    "linkedin.com", "crunchbase.com", "welcometothejungle.com", "wttj.com",
    "societe.com", "pappers.fr", "verif.com", " wikipedia.org", "glassdoor.com",
)


def normalize_domain(value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    raw = value.strip()
    parts = urlsplit(raw if "://" in raw else "//" + raw)
    host = (parts.hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    if not host or any(ch.isspace() for ch in host):
        return None
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    return host


def normalize_url(value: str | None) -> str | None:
    if not value:
        return None
    raw = value.strip()
    if re.match(r"^[a-z][a-z0-9+.-]*:", raw, re.I) and "://" not in raw and not re.match(r"^[^/:]+:\d+(?:/|$)", raw):
        return None
    parts = urlsplit(raw if "://" in raw else "https://" + raw)
    if parts.scheme.lower() not in {"http", "https"} or parts.username or parts.password:
        return None
    try:
        parts.port
    except ValueError:
        return None
    domain = normalize_domain(parts.netloc or parts.path)
    if not domain:
        return None
    # Keep path because URL identity may be meaningful; domain-level dedupe uses normalize_domain.
    path = re.sub(r"/{2,}", "/", parts.path or "/").rstrip("/") or "/"
    query = "&".join(
        item for item in parts.query.split("&")
        if item and not item.split("=", 1)[0].lower().startswith("utm_")
        and item.split("=", 1)[0].lower() not in TRACKING_KEYS
    )
    return urlunsplit(("https", domain, path, query, ""))


def is_third_party_domain(domain: str | None) -> bool:
    normalized = normalize_domain(domain)
    return bool(normalized and any(normalized == host.strip() or normalized.endswith("." + host.strip()) for host in THIRD_PARTY_HOSTS))


def is_public_web_domain(domain: str | None) -> bool:
    normalized = normalize_domain(domain)
    if not normalized or normalized in {"localhost", "metadata.google.internal"} or normalized.endswith((".localhost", ".local", ".internal")):
        return False
    try:
        return ip_address(normalized).is_global
    except ValueError:
        return True
