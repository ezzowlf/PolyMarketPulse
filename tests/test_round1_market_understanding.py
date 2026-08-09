"""ROUND-1 tests — Part 1 (Market Understanding / MarketProposition
additive fields) and the 3 explicit disambiguation examples from the
85-section brief's section 3.
"""

from __future__ import annotations

from polymarketpulse.prediction.semantics import parse_market_proposition

# ---------------------------------------------------------------------------
# Disambiguation example 1: at-deadline vs by-deadline (deadline_semantics,
# already real from an earlier round — confirmed/tested here, not rebuilt).
# ---------------------------------------------------------------------------


def test_btc_above_on_date_is_terminal_at_deadline() -> None:
    prop = parse_market_proposition("Will BTC be above $100,000 on December 31?", None)
    assert prop.event_type == "price_above"
    assert prop.deadline_semantics == "at_deadline"
    assert prop.contract_type == "price_threshold_at_date"


def test_btc_hit_by_date_is_barrier_by_deadline() -> None:
    prop = parse_market_proposition("Will BTC hit $100,000 by December 31?", None)
    assert prop.event_type == "price_above"
    assert prop.deadline_semantics == "by_deadline"
    assert prop.contract_type == "price_threshold_by_date"


# ---------------------------------------------------------------------------
# Disambiguation example 2: office_departure vs election (different
# event_type, even though both name the same politician).
# ---------------------------------------------------------------------------


def test_leaves_office_is_office_departure_not_election() -> None:
    prop = parse_market_proposition("Will Trump leave office by August 31?", None)
    assert prop.event_type == "office_departure"
    assert prop.domain == "POLITICS"
    assert prop.resolution_mechanism == "official_announcement"


def test_loses_election_is_election_not_office_departure() -> None:
    prop = parse_market_proposition("Will Trump lose an election?", None)
    assert prop.event_type == "election"
    assert prop.event_type != "office_departure"
    assert prop.resolution_mechanism == "election_result"


# ---------------------------------------------------------------------------
# Disambiguation example 3: match-level vs tournament-level sports events.
# ---------------------------------------------------------------------------


def test_team_wins_next_match_is_match_level() -> None:
    prop = parse_market_proposition("Will Team Alpha win their next match?", None)
    assert prop.event_type == "sport_match"
    assert prop.domain == "SPORTS"


def test_team_wins_tournament_is_tournament_level() -> None:
    prop = parse_market_proposition("Will Team Alpha win the tournament?", None)
    assert prop.event_type == "sport_tournament"
    assert prop.event_type != "sport_match"


# ---------------------------------------------------------------------------
# New-field extraction tests (real/realistic question text).
# ---------------------------------------------------------------------------


def test_subject_type_and_domain_for_central_bank_market() -> None:
    prop = parse_market_proposition(
        "Will the Fed decrease interest rates by 25 bps after the September 2026 meeting?", None
    )
    assert prop.event_type == "rate_cut"
    assert prop.domain == "MACRO"
    assert prop.subject_type == "INSTITUTION"
    assert prop.contract_type == "binary_event"
    assert prop.resolution_mechanism == "official_announcement"


def test_asset_market_has_asset_subject_type() -> None:
    prop = parse_market_proposition("Will BTC be above $80,000 on August 7?", None)
    assert prop.subject_type == "ASSET"
    assert prop.domain == "CRYPTO"


def test_geopolitics_strategic_waterway_domain() -> None:
    prop = parse_market_proposition(
        "Will the Strait of Hormuz traffic return to normal by August 31?", None
    )
    assert prop.event_type == "strategic_waterway"
    assert prop.domain == "GEOPOLITICS"
    assert prop.subject_type == "EVENT"


def test_unrecognized_event_type_leaves_new_fields_none_not_guessed() -> None:
    prop = parse_market_proposition("Will something unusual happen next week?", None)
    assert prop.event_type is None
    assert prop.domain is None
    assert prop.subject_type is None
    assert prop.contract_type is None
    assert prop.resolution_mechanism is None


def test_semantic_confidence_lower_for_ambiguous_than_clear() -> None:
    # Subject extraction is a naive first-capitalized-run heuristic that
    # excludes sentence-initial "Will" — phrase the question so the real
    # subject leads instead, to get a genuine CLEAR proposition_status to
    # compare against.
    clear = parse_market_proposition(
        "Federal Reserve to decrease interest rates by 25 bps after the September 2026 meeting?", None
    )
    ambiguous = parse_market_proposition("Will something unusual happen next week?", None)
    assert clear.proposition_status == "CLEAR"
    assert ambiguous.proposition_status == "AMBIGUOUS"
    assert clear.semantic_confidence is not None
    assert ambiguous.semantic_confidence is not None
    assert clear.semantic_confidence > ambiguous.semantic_confidence


def test_resolution_source_mirrors_resolution_authority() -> None:
    resolution_text = "Resolves YES if the Fed cuts rates. Resolves NO if not, as determined by Federal Reserve."
    prop = parse_market_proposition(
        "Will the Fed decrease interest rates by 25 bps after the September 2026 meeting?", resolution_text
    )
    assert prop.resolution_authority is not None
    assert prop.resolution_source == prop.resolution_authority


def test_as_dict_includes_all_round1_fields() -> None:
    prop = parse_market_proposition("Will BTC be above $80,000 on August 7?", None)
    d = prop.as_dict()
    for key in (
        "subject_type", "actor", "domain", "contract_type", "resolution_mechanism",
        "resolution_source", "semantic_confidence",
    ):
        assert key in d
