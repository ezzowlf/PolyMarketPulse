"""ROUND-1 tests — Part 3 (Path-to-Resolution hardening, sections 9-10).

Audit finding, confirmed by these tests: PRE-round-1, `required_transitions`
on `PathToResolution` was real (not always empty) but shallow — a single
plain string, populated only for waterway-flavoured markets whose current
state is neither NORMAL nor UNKNOWN. This round adds a richer, structured
`required_transition_steps` shape (`TransitionStep`) additively, derived
from the exact same real, already-computed evidence (never fabricated).

The critical invariant this file locks in: `probability_status` is ALWAYS
"UNKNOWN" — there is no code path anywhere in this codebase that can
currently produce a fabricated transition probability, because no
empirical/historical basis for one exists yet.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from polymarketpulse.news.base import NewsEvent
from polymarketpulse.news.linker import NewsMarketLink
from polymarketpulse.prediction.evidence import compute_independent_evidence
from polymarketpulse.prediction.semantics import parse_market_proposition
from polymarketpulse.prediction.world_state import assemble_world_state
from polymarketpulse.storage import Storage


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    s = Storage(tmp_path / "test.db")
    yield s
    s.close()


class _FakeMarket:
    def __init__(self, question: str) -> None:
        self.question = question


def _link_evidence(
    storage: Storage, provider: str, provider_market_id: str, question: str,
    items: list[tuple[str, str, str, float]], now: datetime | None = None,
) -> None:
    now = now or datetime.now(UTC)
    market = _FakeMarket(question)
    for i, (title, source, source_url, confidence) in enumerate(items):
        event = NewsEvent(
            source=source, source_url=source_url, title=title,
            published_at=now - timedelta(hours=i), fetched_at=now,
        )
        row_id = storage.save_news_event(event)
        link = NewsMarketLink(
            news_event=event, market=market, match_reason="shared_terms",
            matched_terms=(), confidence=confidence,
        )
        storage.connection.execute(
            "INSERT INTO news_market_links (news_event_id, provider, provider_market_id, "
            "match_reason, matched_terms, confidence, confirmed, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'automatic', ?)",
            (row_id, provider, provider_market_id, link.match_reason, "", confidence, now.isoformat()),
        )
    storage.connection.commit()


def test_required_transition_steps_empty_when_no_evidence() -> None:
    proposition = parse_market_proposition("Will the President resign this year?", None)
    ws = assemble_world_state(
        proposition=proposition, resolution_date=None, now=datetime.now(UTC),
        independent_evidence=None, classified_category="POLITICS",
    )
    assert ws.path_to_resolution is not None
    assert ws.path_to_resolution.required_transition_steps == ()
    # Legacy string form stays consistent (empty together).
    assert ws.path_to_resolution.required_transitions == ()


def test_required_transition_steps_structured_for_real_waterway_state(storage: Storage) -> None:
    now = datetime.now(UTC)
    question = "Will the ceasefire hold through year end?"
    _link_evidence(
        storage, "polymarket", "m1", question,
        [
            ("Military offensive intensifies, waterway blockade imposed", "bbc", "https://bbc.com/a", 0.9),
            ("Ceasefire confirmed, shipping returns to normal", "reuters", "https://reuters.com/b", 0.9),
        ],
        now=now,
    )
    ev = compute_independent_evidence(
        storage.connection, "polymarket", "m1", question, None, market_yes_price=0.5, now=now,
    )
    proposition = parse_market_proposition(question, None)
    ws = assemble_world_state(
        proposition=proposition, resolution_date=None, now=now,
        independent_evidence=ev, classified_category="GEOPOLITICS",
    )
    assert ws.path_to_resolution is not None
    steps = ws.path_to_resolution.required_transition_steps
    assert len(steps) == 1
    step = steps[0]
    assert step.state_from == "CLOSED"
    assert step.state_to == "NORMAL"
    assert step.required_event
    # Real evidence titles, not invented text.
    all_titles = {f.title for f in (*ev.evidence_for_yes, *ev.evidence_for_no)}
    for t in step.supporting_evidence:
        assert t in all_titles
    for t in step.counter_evidence:
        assert t in all_titles
    assert step.estimated_duration is None
    assert step.probability_status == "UNKNOWN"
    assert step.confidence is None


def test_probability_status_is_always_unknown_no_code_path_fabricates_a_number(tmp_path: Path) -> None:
    """The explicit regression test the round-1 brief asks for: across
    several distinct real scenarios (empty, waterway-deteriorating,
    waterway-normal), no TransitionStep produced anywhere ever carries a
    populated confidence or any probability_status other than UNKNOWN."""
    now = datetime.now(UTC)
    scenarios: list[tuple[str, list[tuple[str, str, str, float]], str]] = [
        (
            "Will the ceasefire hold through year end?",
            [
                ("Military offensive intensifies, waterway blockade imposed", "bbc", "https://bbc.com/a", 0.9),
                ("Ceasefire confirmed, shipping returns to normal", "reuters", "https://reuters.com/b", 0.9),
            ],
            "GEOPOLITICS",
        ),
        (
            "Will the ceasefire hold through year end?",
            [
                ("Ceasefire confirmed, both sides agree, shipping resumes normal levels", "reuters", "https://reuters.com/a", 0.9),
                ("Officials confirm ceasefire holding steady", "apnews", "https://apnews.com/b", 0.9),
            ],
            "GEOPOLITICS",
        ),
        ("Will the President resign this year?", [], "POLITICS"),
    ]
    all_steps = []
    for i, (question, items, category) in enumerate(scenarios):
        s = Storage(tmp_path / f"scenario-{i}.db")
        if items:
            _link_evidence(s, "polymarket", f"m{i}", question, items, now=now)
        ev = compute_independent_evidence(
            s.connection, "polymarket", f"m{i}", question, None, market_yes_price=0.5, now=now,
        ) if items else None
        proposition = parse_market_proposition(question, None)
        ws = assemble_world_state(
            proposition=proposition, resolution_date=None, now=now,
            independent_evidence=ev, classified_category=category,
        )
        if ws.path_to_resolution is not None:
            all_steps.extend(ws.path_to_resolution.required_transition_steps)
        s.close()

    assert len(all_steps) >= 1  # sanity: at least one real step was produced across scenarios
    for step in all_steps:
        assert step.probability_status == "UNKNOWN"
        assert step.confidence is None


def test_as_dict_includes_required_transition_steps() -> None:
    proposition = parse_market_proposition(
        "Will the Strait of Hormuz traffic return to normal by August 31?", None
    )
    ws = assemble_world_state(
        proposition=proposition, resolution_date=None, now=datetime.now(UTC),
        independent_evidence=None, classified_category="GEOPOLITICS",
    )
    d = ws.path_to_resolution.as_dict()
    assert "required_transition_steps" in d
    assert d["required_transition_steps"] == []
