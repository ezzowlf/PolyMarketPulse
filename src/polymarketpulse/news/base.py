from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class NewsEvent:
    source: str
    source_url: str
    title: str
    published_at: datetime | None
    fetched_at: datetime
    summary: str = ""
    entities: tuple[str, ...] = field(default_factory=tuple)

    @property
    def content_hash(self) -> str:
        """Stable fingerprint used for deduplication, independent of when the
        item was fetched (so re-fetching the same feed doesn't duplicate)."""
        payload = f"{self.source}|{self.source_url}|{self.title}".encode()
        return hashlib.sha256(payload).hexdigest()
