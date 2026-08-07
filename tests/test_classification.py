from polymarketpulse.prediction.classification import classify_market
from polymarketpulse.prediction.semantics import parse_market_proposition

# ---------------------------------------------------------------------------
# One realistic Polymarket-style question per category.
# ---------------------------------------------------------------------------


def test_elections_question() -> None:
    result = classify_market("Will the Republican nominee win the 2028 general election?")
    assert result.category == "ELECTIONS"


def test_politics_question() -> None:
    # Boundary rule: an office-departure proposition about a sitting US
    # president is POLITICS, not GEOPOLITICS — GEOPOLITICS is reserved for
    # explicitly foreign-state contexts (see test_geopolitics_question and
    # the module docstring in classification.py).
    result = classify_market("Trump out as President by August 31?")
    assert result.category == "POLITICS"


def test_geopolitics_question() -> None:
    result = classify_market(
        "Will the Prime Minister of a NATO member state resign amid sanctions dispute by year end?"
    )
    assert result.category == "GEOPOLITICS"


def test_war_peace_question() -> None:
    result = classify_market("Will Russia and Ukraine agree to a ceasefire by the end of the year?")
    assert result.category == "WAR_PEACE"


def test_legislation_question() -> None:
    result = classify_market("Will the immigration reform bill be signed into law before the Senate vote deadline?")
    assert result.category == "LEGISLATION"


def test_macroeconomics_question() -> None:
    result = classify_market("Will US inflation (CPI) exceed 4% year-over-year in the next jobs report?")
    assert result.category == "MACROECONOMICS"


def test_central_banks_question() -> None:
    # Documented boundary: rate-decision questions naming the Fed/FOMC
    # explicitly resolve to CENTRAL_BANKS, not the broader MACROECONOMICS
    # bucket, because the entity signal (Federal Reserve / FOMC / rate
    # decision) is more specific than the generic macro keyword group.
    result = classify_market("Will the Fed cut rates by September?")
    assert result.category == "CENTRAL_BANKS"


def test_crypto_question() -> None:
    result = classify_market("Will Bitcoin reach $200k before the end of the year?")
    assert result.category == "CRYPTO"


def test_financial_markets_question() -> None:
    result = classify_market("Will the S&P 500 close above 6000 by December 31?")
    assert result.category == "FINANCIAL_MARKETS"


def test_energy_question() -> None:
    result = classify_market("Will WTI crude oil price exceed $100 per barrel this quarter?")
    assert result.category == "ENERGY"


def test_technology_question() -> None:
    result = classify_market("Will OpenAI release a new ChatGPT model before Q3?")
    assert result.category == "TECHNOLOGY"


def test_sport_football_question() -> None:
    result = classify_market("Champions League winner?")
    assert result.category == "SPORT_FOOTBALL"


def test_sport_basketball_question() -> None:
    result = classify_market("Will the Boston Celtics win the NBA Finals this season?")
    assert result.category == "SPORT_BASKETBALL"


def test_sport_tennis_question() -> None:
    result = classify_market("Will Novak Djokovic win Wimbledon this year?")
    assert result.category == "SPORT_TENNIS"


def test_sport_other_question() -> None:
    result = classify_market("Will the USA win the most gold medals at the next Olympics?")
    assert result.category == "SPORT_OTHER"


def test_entertainment_question() -> None:
    result = classify_market("Will 'Dune: Part Three' win the Oscar for Best Picture?")
    assert result.category == "ENTERTAINMENT"


def test_social_question() -> None:
    result = classify_market("Will this tweet go viral and hit 1 million likes on X by Friday?")
    assert result.category == "SOCIAL"


# ---------------------------------------------------------------------------
# Ambiguity should lower confidence, not produce false certainty.
# ---------------------------------------------------------------------------


def test_ambiguous_question_has_lower_confidence_than_clear_ones() -> None:
    clear = classify_market("Champions League winner?")
    # Genuinely ambiguous between POLITICS (the "president" entity signal)
    # and LEGISLATION (the "bill" / "senate vote" process signal) — a
    # single-keyword classifier would have to silently pick one; this one
    # reports the close margin instead.
    ambiguous = classify_market("Will the President sign the bill after the Senate vote?")

    assert clear.confidence > ambiguous.confidence
    assert any("ambiguous_with" in s for s in ambiguous.signals) or ambiguous.confidence < 0.6


def test_unclassifiable_question_falls_back_to_other_with_low_confidence() -> None:
    result = classify_market("Will it happen sometime maybe?")
    assert result.category == "OTHER"
    assert result.confidence < 0.5


# ---------------------------------------------------------------------------
# event_type from semantics.py should be a real, measurable signal — not
# just consumed cosmetically.
# ---------------------------------------------------------------------------


def test_event_type_corrects_a_keyword_only_misclassification() -> None:
    # This question has no domain keyword classification.py's own
    # POLITICS group recognizes ("ousted" isn't one of its listed phrases,
    # deliberately — the phrase list isn't exhaustive of every resignation
    # synonym). semantics.py's resignation-term detector *does* recognize
    # "ousted" and produces event_type="office_departure", which should
    # push this to POLITICS where keyword-only classification could not.
    question = "Will Milei be ousted by December 1?"

    title_only = classify_market(question)
    proposition = parse_market_proposition(question, None)
    event_type_aware = classify_market(question, None, proposition)

    assert proposition.event_type == "office_departure"
    assert event_type_aware.category == "POLITICS"
    assert any("event_type" in s for s in event_type_aware.signals)
    # Demonstrate the measurable difference: without event_type this
    # question does not confidently land on POLITICS.
    assert title_only.category != event_type_aware.category or title_only.confidence < event_type_aware.confidence


def test_event_type_prefers_geopolitics_for_foreign_head_of_state_context() -> None:
    question = "Will the Chancellor resign amid the coalition crisis?"
    proposition = parse_market_proposition(question, None)
    result = classify_market(question, None, proposition)
    # "resign" alone -> POLITICS keyword group would fire; the event_type
    # override plus "chancellor" foreign-head-of-state cue pushes this to
    # GEOPOLITICS. Documented boundary call, see classification.py.
    assert result.category == "GEOPOLITICS"


def test_classification_reports_event_type_field() -> None:
    question = "Will there be a major escalation in the conflict this month?"
    proposition = parse_market_proposition(question, None)
    result = classify_market(question, None, proposition)
    assert result.event_type == "conflict_escalation"
    assert result.category == "WAR_PEACE"
