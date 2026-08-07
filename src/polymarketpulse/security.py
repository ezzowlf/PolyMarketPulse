"""Shared security helpers for anything that fetches external content:
SSRF guarding, response-size capping, and secret masking for logs. Kept
dependency-free and small enough to audit at a glance — this is the one
place every outbound fetcher in the project should route its URL through
before making a request.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse

ALLOWED_SCHEMES = {"http", "https"}
MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5 MB — generous for RSS/JSON, blocks runaway downloads

_SECRET_PATTERNS = [
    re.compile(r"(sk-[a-zA-Z0-9_-]{10,})"),
    re.compile(r"(Bearer\s+[a-zA-Z0-9._-]{10,})", re.IGNORECASE),
    re.compile(r"([a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{10,})"),  # JWT-shaped
]


class SSRFError(ValueError):
    """Raised when a URL fails the outbound-request safety check."""


def _is_private_or_reserved(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        # Can't resolve — treat as unsafe rather than silently proceeding
        # (httpx would fail anyway, but we want a clear, auditable reason).
        return True
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_multicast or ip.is_reserved or ip.is_unspecified
        ):
            return True
    return False


def assert_safe_url(url: str) -> None:
    """Raises SSRFError if the URL is not a safe, public http(s) target.
    Every fetcher that follows a URL derived from external/user-influenced
    input (an RSS feed's <link>, a GDELT article URL, ...) must call this
    before making the request."""
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise SSRFError(f"Unerlaubtes URL-Schema: {parsed.scheme!r}")
    if not parsed.hostname:
        raise SSRFError("URL ohne Host.")
    if parsed.hostname.lower() in ("localhost",):
        raise SSRFError("Zugriff auf localhost blockiert.")
    if _is_private_or_reserved(parsed.hostname):
        raise SSRFError(f"Zugriff auf private/reservierte Adresse blockiert: {parsed.hostname}")


def mask_secret(value: str | None) -> str:
    """Never logs a usable secret. Shows only a short prefix/suffix so an
    operator can distinguish two different keys without either one being
    reconstructable from the log line."""
    if not value:
        return "(nicht gesetzt)"
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def redact_secrets(text: str) -> str:
    """Best-effort redaction of secret-shaped substrings from arbitrary
    text before it's logged (e.g. an error message that echoed a header)."""
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("***REDACTED***", redacted)
    return redacted
