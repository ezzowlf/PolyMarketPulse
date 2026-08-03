from __future__ import annotations

import hashlib
import json
from typing import Any

from ..storage import Storage


def hash_payload(*parts: Any) -> str:
    """Stable hash over arbitrary JSON-serializable parts — used to build
    cache keys for requests that combine more than one MarketContext (e.g.
    `ask` with a question string, or `compare` with several market IDs)."""
    canonical = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def lookup(
    storage: Storage, analysis_type: str, model: str, prompt_version: str, key_hash: str, ttl_seconds: int
) -> dict | None:
    if ttl_seconds <= 0:
        return None
    return storage.find_cached_ai_run(analysis_type, model, prompt_version, key_hash, ttl_seconds)
