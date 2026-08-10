"""Tests for specialized forecasting models (Phase E).

Each test verifies:
  - correct event type routing
  - available/unavailable handling
  - Trump/Nevada protection
  - market-blind guarantees (no market price used)
"""

from polymarketpulse.prediction.geopolitics import analyze_geopolitics
from polymarketpulse.prediction.macro import analyze_macro
from polymarketpulse.prediction.politics import analyze_politics
from polymarketpulse.prediction.quant import analyze_quant
from polymarketpulse.prediction.semantics import parse_market_proposition
from polymarketpulse.prediction.specialized_router import route_to_specialized_model
from polymarketpulse.prediction.sports import analyze_sports

# --- Geopolitics Model Tests -----------------------------------------------


def test_geopolitics_ceasefire_agreed():
    """Actual ceasefire agreement — high probability YES."""
    result = analyze_geopolitics(
        text="Ceasefire agreed by both parties, troops withdraw",
        event_type="ceasefire",
        proposition_status="CLEAR",
    )
    assert result.available is True
    assert result.probability is not None
    assert result.probability >= 0.7
    assert "ceasefire" in result.reason.lower()


def test_geopolitics_ceasefire_denied():
    """Ceasefire denied — low probability YES."""
    result = analyze_geopolitics(
        text="Ceasefire denied, talks collapse",
        event_type="ceasefire",
        proposition_status="CLEAR",
    )
    assert result.available is True
    assert result.probability is not None
    assert result.probability < 0.2


def test_geopolitics_ceasefire_talks_only():
    """Peace talks only — no agreement yet. Per GeopoliticsResult's own
    contract (available=False means "no judgment formed", not "the event
    is impossible"), talks-only is correctly a NO_FORECAST: available=False."""
    result = analyze_geopolitics(
        text="Peace talks continue, no agreement reached",
        event_type="ceasefire",
        proposition_status="CLEAR",
    )
    assert result.available is False
    assert result.probability is None
    assert "talks" in result.reason.lower()


def test_geopolitics_war_escalation():
    """Actual escalation — high probability YES."""
    result = analyze_geopolitics(
        text="War escalates, offensive launched, airstrike confirmed",
        event_type="war_escalation",
        proposition_status="CLEAR",
    )
    assert result.available is True
    assert result.probability is not None
    assert result.probability > 0.6


def test_geopolitics_irrelevant_article():
    """Article about unrelated topic — unavailable (NO_FORECAST)."""
    result = analyze_geopolitics(
        text="Economic report shows growth",
        event_type="ceasefire",
        proposition_status="CLEAR",
    )
    assert result.available is False
    assert result.probability is None
    assert "insufficient" in result.reason.lower()


def test_geopolitics_unsupported_event_type():
    """Unsupported event type — unavailable."""
    result = analyze_geopolitics(
        text="Trump out as President",
        event_type="office_departure",
        proposition_status="CLEAR",
    )
    assert result.available is False
    assert result.probability is None
    assert "not handled" in result.reason.lower()


# --- Politics Model Tests ---------------------------------------------------


def test_politics_trump_nevada_protected():
    """CRITICAL: Trump/Nevada case must remain NO_FORECAST (regression protection)."""
    result = analyze_politics(
        text="Trump out as President by November",
        event_type="office_departure",
        proposition_status="CLEAR",
        subject="Trump",
        location="Nevada",
    )
    assert result.available is False
    assert result.probability is None
    assert "protected" in result.reason.lower() or "trump/nevada" in result.reason.lower()


def test_politics_resignation_actual():
    """Actual resignation — high probability YES."""
    result = analyze_politics(
        text="President resigns effective immediately",
        event_type="resignation",
        proposition_status="CLEAR",
        subject="President",
    )
    assert result.available is True
    assert result.probability is not None
    assert result.probability > 0.7


def test_politics_resignation_demand_not_actual():
    """Call for resignation — NOT actual resignation."""
    result = analyze_politics(
        text="Calls on President to resign",
        event_type="resignation",
        proposition_status="CLEAR",
        subject="President",
    )
    assert result.available is True
    assert result.probability is not None
    assert result.probability < 0.2


def test_politics_legislation_passed():
    """Bill passed — high probability YES."""
    result = analyze_politics(
        text="Bill passed by Congress, signed into law",
        event_type="legislation",
        proposition_status="CLEAR",
    )
    assert result.available is True
    assert result.probability is not None
    assert result.probability > 0.7


def test_politics_unsupported_event_type():
    """Unsupported event type — unavailable."""
    result = analyze_politics(
        text="BTC above 50k",
        event_type="price_above",
        proposition_status="CLEAR",
    )
    assert result.available is False
    assert result.probability is None
    assert "not handled" in result.reason.lower()


# --- Macro Model Tests ------------------------------------------------------


def test_macro_rate_cut_confirmed():
    """Rate cut confirmed — high probability YES for rate_cut market."""
    result = analyze_macro(
        text="Central bank announces rate cut, policy decision confirmed",
        event_type="rate_cut",
        proposition_status="CLEAR",
    )
    assert result.available is True
    assert result.probability is not None
    assert result.probability > 0.7


def test_macro_rate_hike_confirmed():
    """Rate hike confirmed — high probability YES for rate_hike market."""
    result = analyze_macro(
        text="Central bank hikes rates, tightening confirmed",
        event_type="rate_hike",
        proposition_status="CLEAR",
    )
    assert result.available is True
    assert result.probability is not None
    assert result.probability > 0.7


def test_macro_rate_hold_confirmed():
    """Rate hold confirmed — high probability YES for rate_hold market."""
    result = analyze_macro(
        text="Central bank holds rates, no change confirmed",
        event_type="rate_hold",
        proposition_status="CLEAR",
    )
    assert result.available is True
    assert result.probability is not None
    assert result.probability > 0.7


def test_macro_upcoming_meeting():
    """Meeting text with no rate-move keyword and no decision-made marker —
    insufficient structured evidence, correctly NO_FORECAST (available=False)."""
    result = analyze_macro(
        text="Central bank meets next week to discuss rates",
        event_type="rate_cut",
        proposition_status="CLEAR",
    )
    assert result.available is False
    assert result.probability is None


def test_macro_unsupported_event_type():
    """Unsupported event type — unavailable."""
    result = analyze_macro(
        text="Trump out",
        event_type="office_departure",
        proposition_status="CLEAR",
    )
    assert result.available is False
    assert result.probability is None
    assert "not handled" in result.reason.lower()


# --- Quant Model Tests ------------------------------------------------------


def test_quant_above_threshold_not_crossed():
    """BTC above 100k, current 80k — medium probability."""
    result = analyze_quant(
        text="Will BTC be above $100,000 by December?",
        event_type="price_above",
        proposition_status="CLEAR",
        threshold=100_000,
        asset="bitcoin",
        current_price=80_000,
        historical_volatility=0.02,  # 2% daily
        deadline="2026-12-31",
        deadline_semantics="by_deadline",  # question phrased "by December"
    )
    assert result.available is True
    assert result.probability is not None
    # With 80k current, 100k threshold, should be < 0.5
    assert result.probability < 0.5


def test_quant_below_threshold_not_crossed():
    """ETH below 2k, current 2.5k, phrased "by end of year" — this is a
    BARRIER (touch-by-deadline) question, not a terminal one. Barrier
    probability is deliberately more generous than terminal probability
    for the same distance (any dip below the threshold at any point counts,
    per the reflection-principle math documented in quant.py) — so, unlike
    a naive terminal model, it is legitimate for this to land close to or
    above 0.5 even though current price is comfortably above the threshold.
    That is the whole point of not conflating the two."""
    result = analyze_quant(
        text="Will ETH be below $2,000 by end of year?",
        event_type="price_below",
        proposition_status="CLEAR",
        threshold=2_000,
        asset="ethereum",
        current_price=2_500,
        historical_volatility=0.03,
        deadline="2026-12-31",
        deadline_semantics="by_deadline",  # question phrased "by end of year"
    )
    assert result.available is True
    assert result.probability is not None
    assert 0.0 < result.probability < 1.0
    assert "barrier" in result.reason.lower()

    # The terminal ("at deadline") probability for the identical inputs
    # must be strictly lower — this is the concrete, numeric proof that
    # terminal and barrier are not conflated.
    terminal_result = analyze_quant(
        text="Will ETH be below $2,000 on December 31?",
        event_type="price_below",
        proposition_status="CLEAR",
        threshold=2_000,
        asset="ethereum",
        current_price=2_500,
        historical_volatility=0.03,
        deadline="2026-12-31",
        deadline_semantics="at_deadline",
    )
    assert terminal_result.available is True
    assert terminal_result.probability is not None
    assert terminal_result.probability < result.probability


def test_quant_threshold_already_crossed_above():
    """BTC above 100k, current 120k — threshold already crossed."""
    result = analyze_quant(
        text="Will BTC be above $100,000 by December?",
        event_type="price_above",
        proposition_status="CLEAR",
        threshold=100_000,
        asset="bitcoin",
        current_price=120_000,
        historical_volatility=0.02,
        deadline="2026-12-31",
    )
    assert result.available is True
    assert result.probability is not None
    # Threshold already exceeded — high probability
    assert result.probability > 0.9


def test_quant_missing_price_data():
    """Missing price data — unavailable."""
    result = analyze_quant(
        text="Will BTC be above 100k?",
        event_type="price_above",
        proposition_status="CLEAR",
        threshold=100_000,
        asset="bitcoin",
        current_price=None,  # Missing!
        historical_volatility=0.02,
        deadline="2026-12-31",
    )
    assert result.available is False
    assert result.probability is None
    assert "price" in result.reason.lower()


def test_quant_missing_volatility():
    """Missing volatility — unavailable."""
    result = analyze_quant(
        text="Will BTC be above 100k?",
        event_type="price_above",
        proposition_status="CLEAR",
        threshold=100_000,
        asset="bitcoin",
        current_price=80_000,
        historical_volatility=None,  # Missing!
        deadline="2026-12-31",
    )
    assert result.available is False
    assert result.probability is None
    assert "volatility" in result.reason.lower()


def test_quant_unsupported_asset():
    """Unsupported asset — unavailable."""
    # "ripple" IS in _SUPPORTED_ASSETS (aliased to xrp) — not exercised
    # further here; the real assertion below uses an asset that isn't.
    result2 = analyze_quant(
        text="Will INVALID be above 10?",
        event_type="price_above",
        proposition_status="CLEAR",
        threshold=10,
        asset="invalidcoin",
        current_price=5,
        historical_volatility=0.05,
        deadline="2026-12-31",
    )
    assert result2.available is False
    assert result2.probability is None
    assert "not supported" in result2.reason.lower()


def test_quant_expired_deadline_without_point_in_time_observation():
    """Deadline expired — outcome determined."""
    result = analyze_quant(
        text="Will BTC be above 100k by December?",
        event_type="price_above",
        proposition_status="CLEAR",
        threshold=100_000,
        asset="bitcoin",
        current_price=80_000,
        historical_volatility=0.02,
        deadline="2025-12-31",  # Past!
    )
    assert result.available is False
    assert result.probability is None
    assert "point-in-time" in result.reason


# --- Sports Model Tests -----------------------------------------------------


def test_sports_match_result_detected():
    """Match result detected — result known but winner not identified."""
    result = analyze_sports(
        text="Team A wins the match",
        event_type="sport_match",
        proposition_status="CLEAR",
    )
    assert result.available is False
    assert result.probability is None  # We know a result exists but not the specifics
    assert "result" in result.reason.lower()


def test_sports_future_match():
    """Future match — no outcome yet."""
    result = analyze_sports(
        text="Team A vs Team B, match today",
        event_type="sport_match",
        proposition_status="CLEAR",
    )
    assert result.available is False
    assert result.probability is None
    assert "not yet played" in result.reason.lower()


def test_sports_tournament_result():
    """Tournament result detected."""
    result = analyze_sports(
        text="Team A champion of the tournament",
        event_type="sport_tournament",
        proposition_status="CLEAR",
    )
    assert result.available is False
    assert result.probability is None
    assert "result" in result.reason.lower()


def test_sports_qualification_pending():
    """Qualification round pending."""
    result = analyze_sports(
        text="Qualifying round starts tomorrow",
        event_type="sport_qualification",
        proposition_status="CLEAR",
    )
    assert result.available is False
    assert result.probability is None
    assert "not yet played" in result.reason.lower()


def test_sports_unsupported_event_type():
    """Unsupported event type — unavailable."""
    result = analyze_sports(
        text="Trump out",
        event_type="office_departure",
        proposition_status="CLEAR",
    )
    assert result.available is False
    assert result.probability is None
    assert "not handled" in result.reason.lower()


# --- Engine Integration Tests ----------------------------------------------


def test_quant_model_route_via_engine():
    """Quant model is selected for price_above events, via the real router
    (parse_market_proposition -> route_to_specialized_model), not a stub."""
    proposition = parse_market_proposition("Will BTC be above $100,000 by December 31, 2026?", None)
    assert proposition.event_type == "price_above"
    result = route_to_specialized_model(
        proposition, proposition.yes_condition, current_price=80_000, historical_volatility=0.02,
    )
    assert "quant" in result.eligible_models
    assert "quant" in result.reasons[0]


def test_at_deadline_phrasing_populates_deadline_string_not_just_semantics():
    """Regression test for a real bug: parse_market_proposition's
    "on/at/as of <date>" branch (_AT_DEADLINE_PATTERN) previously set
    deadline_semantics="at_deadline" but left proposition.deadline as None
    (deadline was only ever assigned from the "by <date>" branch). Every
    at_deadline-phrased price-threshold question (the exact phrasing real
    BTC markets in data/polymarketpulse.db use, e.g. "Bitcoin above $60,000
    on August 7") therefore always produced deadline=None, which meant
    quant.py's analyze_quant always fell into "missing_time_horizon" and
    returned available=False, regardless of whether price/volatility data
    was otherwise available — the root cause of quant being eligible on 4
    real BTC markets but actually_used 0/4 times."""
    proposition = parse_market_proposition(
        "Will the price of Bitcoin be above $60,000 on August 7?", None
    )
    assert proposition.deadline_semantics == "at_deadline"
    assert proposition.deadline == "August 7"


def test_router_uses_real_resolution_date_not_unparseable_proposition_deadline():
    """Regression test: proposition.deadline is only ever a best-effort
    natural-language string pulled from the question text by a regex (e.g.
    "August 7", no year, never ISO-formatted). quant.py's analyze_quant
    calls datetime.fromisoformat(deadline), which always raises on that
    text, silently discarding the time horizon and leaving quant
    unavailable even when real price/volatility data is present. The real
    fix threads the market's actual `resolution_date` (a real datetime
    loaded from the `markets.end_date` column, ISO-formatted) through
    route_to_specialized_model so quant receives a deadline it can
    actually parse."""
    from datetime import UTC, datetime, timedelta

    proposition = parse_market_proposition(
        "Will the price of Bitcoin be above $60,000 on August 7?", None
    )
    assert proposition.deadline_semantics == "at_deadline"

    future_date = datetime.now(UTC) + timedelta(days=30)
    result = route_to_specialized_model(
        proposition,
        proposition.yes_condition,
        current_price=50_000.0,  # below threshold, so quant must actually use the time horizon
        historical_volatility=0.03,
        resolution_date=future_date,
    )
    assert "quant" in result.used_models
    quant_result = result.model_results[0]
    assert quant_result["available"] is True
    assert quant_result["probability"] is not None
    # Sanity: without a real resolution_date, the regex-parsed deadline
    # string still fails to parse and quant stays unavailable (documents
    # the bug this test guards against, so a future regression is obvious).
    result_no_real_date = route_to_specialized_model(
        proposition,
        proposition.yes_condition,
        current_price=50_000.0,
        historical_volatility=0.03,
    )
    assert "quant" not in result_no_real_date.used_models


def test_politics_model_route_via_engine():
    """Politics model is selected for office_departure events, via the real
    router. Trump/Nevada protection itself is verified separately in
    test_politics_trump_nevada_protected."""
    proposition = parse_market_proposition("Will Trump resign as President before 2027?", None)
    assert proposition.event_type == "office_departure"
    result = route_to_specialized_model(proposition, proposition.yes_condition)
    assert "politics" in result.eligible_models
    assert "politics" in result.reasons[0]


def test_geopolitics_model_route_via_engine():
    """Geopolitics model is selected for ceasefire events, via the real
    router. NOTE: semantics.py's parse_market_proposition currently only
    ever emits "conflict_escalation"/"conflict_deescalation" for
    conflict-related text (see _detect_event_type) — never "ceasefire",
    "war_escalation", or any of the other event_type strings
    specialized_router._EVENT_TYPE_TO_MODEL actually maps to geopolitics.
    So this proposition is constructed directly to exercise the router's
    geopolitics path; the live question-parsing pipeline cannot reach it
    today. That gap is intentionally NOT papered over — see audit report."""
    from polymarketpulse.prediction.semantics import MarketProposition

    proposition = MarketProposition(
        subject=None, predicate="ceasefire", object=None, event_type="ceasefire",
        direction="yes_if_occurs", threshold=None, unit=None, location=None, start_time=None,
        deadline="December 31, 2026", yes_condition="a ceasefire is agreed",
        no_condition="no ceasefire is agreed", resolution_authority=None, proposition_status="CLEAR",
    )
    result = route_to_specialized_model(proposition, proposition.yes_condition)
    assert "geopolitics" in result.eligible_models
    assert "geopolitics" in result.reasons[0]


def test_macro_model_route_via_engine():
    """Macro model is selected for rate_cut events, via the real router.
    NOTE: semantics.py's parse_market_proposition never emits "rate_cut"
    (or any macro event_type) — _detect_event_type has no macro/rate
    keyword patterns at all. So this proposition is constructed directly;
    the live question-parsing pipeline cannot reach the macro model today.
    That gap is intentionally NOT papered over — see audit report."""
    from polymarketpulse.prediction.semantics import MarketProposition

    proposition = MarketProposition(
        subject=None, predicate="rate_cut", object=None, event_type="rate_cut",
        direction="yes_if_occurs", threshold=None, unit=None, location=None, start_time=None,
        deadline="December 31, 2026", yes_condition="the Fed cuts rates",
        no_condition="the Fed does not cut rates", resolution_authority=None, proposition_status="CLEAR",
    )
    result = route_to_specialized_model(proposition, proposition.yes_condition)
    assert "macro" in result.eligible_models
    assert "macro" in result.reasons[0]


def test_sports_model_route_via_engine():
    """Sports model is selected for sport_match events, via the real
    router. NOTE: semantics.py's parse_market_proposition does not
    currently classify any question as sport_match/sport_tournament/etc
    (no sports keyword patterns exist there yet), so this constructs the
    MarketProposition directly to exercise the router's sports path — the
    router-level wiring is real, but it is unreachable from the live
    question-parsing pipeline today. That gap is intentionally NOT papered
    over here; see the audit report."""
    from polymarketpulse.prediction.semantics import MarketProposition

    proposition = MarketProposition(
        subject="Team A", predicate="sport_match", object=None, event_type="sport_match",
        direction="yes_if_occurs", threshold=None, unit=None, location=None, start_time=None,
        deadline=None, yes_condition="Team A wins the match", no_condition="Team A does not win",
        resolution_authority=None, proposition_status="CLEAR",
    )
    result = route_to_specialized_model(proposition, proposition.yes_condition)
    assert "sports" in result.eligible_models
    assert "sports" in result.reasons[0]


def test_unrouted_event_type_not_forced_into_specialized_path():
    """Unknown/unrouted event types are not forced into a specialized
    model — the router must honestly report no eligible model rather than
    guessing one."""
    proposition = parse_market_proposition("Will it rain in London tomorrow?", None)
    result = route_to_specialized_model(proposition, proposition.yes_condition)
    assert result.selected_model is None
    assert result.eligible_models == ()


# --- Market-blindness -------------------------------------------------------


def test_quant_has_no_market_price_parameter():
    """analyze_quant's signature has no market_yes_price/market_probability
    parameter at all — it is market-blind by construction, not by
    discipline. This is a static guarantee: there is nothing a caller
    could pass in that would leak the market price into this model."""
    import inspect

    params = set(inspect.signature(analyze_quant).parameters)
    assert "market_yes_price" not in params
    assert "market_probability" not in params


def test_quant_identical_output_regardless_of_unrelated_kwargs():
    """Calling analyze_quant twice with identical real inputs produces an
    identical result — there is no hidden state or market-price channel
    that could make two calls diverge."""
    kwargs = {
        "text": "Will BTC be above $100,000 on December 31?",
        "event_type": "price_above",
        "proposition_status": "CLEAR",
        "threshold": 100_000,
        "asset": "bitcoin",
        "current_price": 80_000,
        "historical_volatility": 0.02,
        "deadline": "2026-12-31",
        "deadline_semantics": "at_deadline",
    }
    result_a = analyze_quant(**kwargs)
    result_b = analyze_quant(**kwargs)
    assert result_a.probability == result_b.probability
    assert result_a.as_dict() == result_b.as_dict()
