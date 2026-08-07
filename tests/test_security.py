import pytest

from polymarketpulse.security import SSRFError, assert_safe_url, mask_secret, redact_secrets


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
