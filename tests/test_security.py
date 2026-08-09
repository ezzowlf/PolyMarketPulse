import ssl
from pathlib import Path

import certifi
import pytest

from polymarketpulse import security
from polymarketpulse.security import (
    SSRFError,
    assert_safe_url,
    get_ca_bundle,
    get_ssl_context,
    mask_secret,
    redact_secrets,
)


def test_public_ip_literal_url_is_safe() -> None:
    # A literal public IP avoids a real DNS lookup, keeping this a pure
    # unit test with no network access (per project test policy).
    assert_safe_url("https://8.8.8.8/feeds/press_all.xml")


def test_localhost_is_blocked() -> None:
    with pytest.raises(SSRFError):
        assert_safe_url("http://localhost:8000/admin")


def test_loopback_ip_is_blocked() -> None:
    with pytest.raises(SSRFError):
        assert_safe_url("http://127.0.0.1:8000/admin")


def test_private_ip_is_blocked() -> None:
    with pytest.raises(SSRFError):
        assert_safe_url("http://192.168.1.1/config")


def test_non_http_scheme_is_blocked() -> None:
    with pytest.raises(SSRFError):
        assert_safe_url("file:///etc/passwd")


def test_mask_secret_never_returns_full_value() -> None:
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz"
    masked = mask_secret(secret)
    assert secret not in masked
    assert masked.startswith("sk-p")


def test_mask_secret_handles_none_and_short() -> None:
    assert mask_secret(None) == "(nicht gesetzt)"
    assert mask_secret("abc") == "***"


def test_redact_secrets_removes_bearer_token() -> None:
    text = "Request failed with header Authorization: Bearer abcdefghij1234567890"
    redacted = redact_secrets(text)
    assert "abcdefghij1234567890" not in redacted


def _reset_ca_cache() -> None:
    security._ca_bundle_cache = None
    security._ca_bundle_cache_key = None


def test_get_ca_bundle_defaults_to_plain_certifi_with_no_env_vars(monkeypatch) -> None:
    # No behavior change from today when none of the extra-CA env vars are set.
    for var in security._EXTRA_CA_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    _reset_ca_cache()
    assert get_ca_bundle() == certifi.where()


def test_get_ca_bundle_ignores_env_var_pointing_at_missing_file(monkeypatch, tmp_path) -> None:
    missing = tmp_path / "does-not-exist.pem"
    monkeypatch.setenv("NODE_EXTRA_CA_CERTS", str(missing))
    for var in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        monkeypatch.delenv(var, raising=False)
    _reset_ca_cache()
    assert get_ca_bundle() == certifi.where()


def test_get_ca_bundle_combines_certifi_with_extra_ca_when_env_var_set(monkeypatch, tmp_path) -> None:
    extra_ca = tmp_path / "extra-ca.pem"
    extra_ca.write_bytes(b"-----BEGIN CERTIFICATE-----\nFAKE-TEST-CA-ONLY\n-----END CERTIFICATE-----\n")
    monkeypatch.setenv("NODE_EXTRA_CA_CERTS", str(extra_ca))
    for var in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        monkeypatch.delenv(var, raising=False)
    _reset_ca_cache()

    combined = get_ca_bundle()
    assert combined != certifi.where()

    combined_bytes = Path(combined).read_bytes()
    certifi_bytes = Path(certifi.where()).read_bytes()
    assert certifi_bytes in combined_bytes
    assert b"FAKE-TEST-CA-ONLY" in combined_bytes
    _reset_ca_cache()


def test_get_ca_bundle_respects_ssl_cert_file_and_requests_ca_bundle(monkeypatch, tmp_path) -> None:
    for var_name in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        extra_ca = tmp_path / f"{var_name}.pem"
        extra_ca.write_bytes(b"-----BEGIN CERTIFICATE-----\nFAKE-TEST-CA-ONLY\n-----END CERTIFICATE-----\n")
        for other in security._EXTRA_CA_ENV_VARS:
            monkeypatch.delenv(other, raising=False)
        monkeypatch.setenv(var_name, str(extra_ca))
        _reset_ca_cache()
        combined = get_ca_bundle()
        assert combined != certifi.where()
        _reset_ca_cache()


def test_get_ssl_context_returns_ssl_context(monkeypatch) -> None:
    for var in security._EXTRA_CA_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    _reset_ca_cache()
    ctx = get_ssl_context()
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    _reset_ca_cache()
