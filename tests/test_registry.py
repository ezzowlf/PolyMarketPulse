import pytest

from polymarketpulse.providers.registry import (
    create_provider,
    get_provider_class,
    list_provider_names,
)


def test_list_provider_names_includes_expected_providers() -> None:
    names = list_provider_names()
    assert {"polymarket", "manifold", "predictit", "kalshi", "metaculus"} <= set(names)


def test_get_provider_class_unknown_raises() -> None:
    with pytest.raises(ValueError):
        get_provider_class("nonexistent")


def test_create_provider_returns_instance_with_capabilities() -> None:
    provider = create_provider("polymarket")
    assert provider.name == "polymarket"
    assert provider.capabilities.market_lists is True
    provider.close()


def test_placeholder_providers_declare_no_auth_free_capabilities() -> None:
    kalshi = create_provider("kalshi")
    assert kalshi.capabilities.requires_auth is True
    assert kalshi.capabilities.market_lists is False
    with pytest.raises(NotImplementedError):
        kalshi.fetch_markets()

    metaculus = create_provider("metaculus")
    with pytest.raises(NotImplementedError):
        metaculus.fetch_markets()
