from __future__ import annotations

from .base import PredictionMarketProvider
from .kalshi import KalshiProvider
from .manifold import ManifoldProvider
from .metaculus import MetaculusProvider
from .polymarket import PolymarketProvider
from .predictit import PredictItProvider

_REGISTRY: dict[str, type[PredictionMarketProvider]] = {
    "polymarket": PolymarketProvider,
    "manifold": ManifoldProvider,
    "predictit": PredictItProvider,
    "kalshi": KalshiProvider,
    "metaculus": MetaculusProvider,
}


def list_provider_names() -> list[str]:
    return sorted(_REGISTRY)


def get_provider_class(name: str) -> type[PredictionMarketProvider]:
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise ValueError(
            f"Unknown provider '{name}'. Available: {', '.join(list_provider_names())}"
        ) from exc


def create_provider(name: str, timeout: float = 20.0) -> PredictionMarketProvider:
    cls = get_provider_class(name)
    try:
        return cls(timeout=timeout)
    except TypeError:
        # Providers with no constructor args (pure placeholders).
        return cls()
