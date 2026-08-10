"""Shared security helpers for anything that fetches external content:
SSRF guarding, response-size capping, and secret masking for logs. Kept
dependency-free and small enough to audit at a glance — this is the one
place every outbound fetcher in the project should route its URL through
before making a request.
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
import ssl
import tempfile
import threading
from urllib.parse import urlparse

import certifi
import truststore

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


_ca_bundle_lock = threading.Lock()
_ca_bundle_cache: str | None = None
_ca_bundle_cache_key: tuple[str | None, str | None, str | None] | None = None

# Well-known environment variables used by many tools/languages (Node.js's
# NODE_EXTRA_CA_CERTS, OpenSSL's SSL_CERT_FILE, and requests/urllib3's
# REQUESTS_CA_BUNDLE) plus one explicit project override. The default path
# uses the native OS trust store; these variables are only needed when an
# operator intentionally supplies a separate PEM file.
_EXTRA_CA_ENV_VARS = (
    "POLYMARKETPULSE_CA_BUNDLE",
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS",
)


def _configured_extra_ca() -> str | None:
    """Return the first readable explicitly configured CA file.

    A project-specific path is an operator instruction, so a missing file is
    reported instead of silently falling back. Missing conventional variables
    are ignored because other installed tools may own them independently.
    """
    for var in _EXTRA_CA_ENV_VARS:
        candidate = os.environ.get(var)
        if not candidate:
            continue
        if os.path.isfile(candidate):
            return candidate
        if var == "POLYMARKETPULSE_CA_BUNDLE":
            raise FileNotFoundError(f"{var} points to a missing CA file")
    return None


def get_ca_bundle() -> str:
    """Returns a filesystem path to use as the `verify=` CA bundle for all
    outbound HTTPS requests in this project.

    Why this exists: corporate/AV software (e.g. antivirus products that do
    local TLS interception for malware scanning) install their own root CA
    into the OS trust store and re-sign outbound HTTPS traffic with it. The
    OS and browsers trust that root CA, but Python's `certifi` bundle is a
    fixed snapshot of public CAs and knows nothing about it, so `httpx`/
    `requests` calls fail with SSL: CERTIFICATE_VERIFY_FAILED even though
    the connection is genuinely fine. This is a very common environment,
    not specific to any one vendor or machine.

    This helper NEVER weakens verification and NEVER hardcodes a vendor or
    path. It only *adds* trust for one extra CA when the operator explicitly
    points to it. `get_ssl_context()` normally uses the native OS trust store;
    this bundle is the explicit-file path and certifi fallback.

    Certificate verification (`verify=...`) is always performed; this
    function only chooses *which* trusted bundle to verify against.
    """
    global _ca_bundle_cache, _ca_bundle_cache_key

    extra_path = _configured_extra_ca()

    certifi_path = certifi.where()
    if extra_path is None:
        return certifi_path

    cache_key = (certifi_path, extra_path, str(os.path.getmtime(extra_path)))
    with _ca_bundle_lock:
        if _ca_bundle_cache is not None and _ca_bundle_cache_key == cache_key:
            return _ca_bundle_cache
        try:
            with open(certifi_path, "rb") as f:
                base = f.read()
            with open(extra_path, "rb") as f:
                extra = f.read()
        except OSError:
            # Extra CA file became unreadable between the check above and
            # now — fail safe to plain certifi rather than erroring out.
            return certifi_path

        fd, combined_path = tempfile.mkstemp(prefix="polymarketpulse_cabundle_", suffix=".pem")
        with os.fdopen(fd, "wb") as f:
            f.write(base)
            f.write(b"\n")
            f.write(extra)

        _ca_bundle_cache = combined_path
        _ca_bundle_cache_key = cache_key
        return combined_path


def get_ssl_context() -> ssl.SSLContext:
    """Return a verification-enforcing context shared by every HTTP client.

    An explicitly configured extra CA is combined with certifi for portable,
    deterministic behavior. Otherwise truststore uses the native OS trust
    store (Windows Certificate Store on Windows), which includes locally
    administered enterprise/AV interception roots. If native trust cannot be
    initialized, certifi remains the safe cross-platform fallback.
    """
    if _configured_extra_ca() is not None:
        return ssl.create_default_context(cafile=get_ca_bundle())
    try:
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except (OSError, RuntimeError):
        return ssl.create_default_context(cafile=certifi.where())


def get_tls_trust_source() -> str:
    """Non-sensitive diagnostic label for smoke tests and health reporting."""
    if _configured_extra_ca() is not None:
        return "configured_ca_plus_certifi"
    return "system_trust_store"


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
