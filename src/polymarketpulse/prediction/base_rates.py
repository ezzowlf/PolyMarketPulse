"""Event-type base rate table — Phase B of the "no universal 50% prior" fix.

The engine's independent-evidence submodel needs *some* anchor to start a
Bayesian update from. A flat 50% ("we have no idea") is fine as a
deliberately uninformative prior for run-of-the-mill propositions where real
evidence then does the actual work (see evidence.py's Bayesian update) — but
for genuinely rare/extraordinary event types, a base rate grounded in real
historical frequency is a much better anchor than "coin flip", and is what
lets the extraordinary-event guard (see below / evidence.py) dampen a weak
signal back toward something defensible instead of letting one loosely-toned
headline produce a market-moving probability.

Every entry here is a rough, deliberately conservative, SOURCED estimate —
the comment next to each number is the citation/reasoning, not decoration.
Where no defensible number exists for an event_type, it is simply **omitted**
— `get_base_rate()` returns `None` for anything not in the table, and no
caller may substitute 0.5 (or any other number) for that None. "We don't have
a good base rate for this category" must stay "we don't know", not become a
fabricated 50/50.

event_type keys reuse/extend the vocabulary produced by
`semantics.parse_market_proposition` / `semantics._detect_event_type`
(currently: "office_departure", "conflict_escalation",
"conflict_deescalation"). A few additional, not-yet-detected event types from
the product spec's broader vocabulary (elections, rate decisions, sports,
legislation, product launches, etc.) are listed further below purely as
documented placeholders for when semantics.py's detector is extended to
recognize them — they are deliberately NOT populated with invented numbers
today, precisely because "we don't have real evidence-backed data for this
type yet" is exactly the case this file exists to keep honest.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Populated base rates — one YES-probability per event_type, understood as
# "probability the described event occurs within a short (~1 month) window
# for a market that is already asking about it", NOT an unconditional
# lifetime probability. Rough, deliberately conservative single numbers —
# not fitted, not market-specific; a real base rate a human forecaster could
# defend out loud.
# ---------------------------------------------------------------------------

BASE_RATES: dict[str, float] = {
    # An incumbent US-style president/head-of-state leaving office outside a
    # scheduled election, death, or a resignation crisis already underway is
    # historically very rare. Since 1789 the US has had exactly one forced
    # resignation (Nixon, 1974) and a small number of in-office deaths, out
    # of ~230 years / ~59 presidential terms. Scoped down to a ~1-month
    # resolution window (as most "X out as President by <date>" markets
    # are), the base rate is very low — low single digits at most, and
    # usually far lower absent an active, already-known crisis. Using 1.5%
    # as a deliberately conservative "no specific crisis known" anchor;
    # real evidence should move it, not replace it.
    "office_departure": 0.015,
    # Base rate that an already-active armed conflict sees a further,
    # meaningfully escalatory event (new offensive, strikes crossing a
    # prior red line, mobilization, etc.) within a ~1 month window. Rough
    # estimate from observed patterns in ongoing 21st-century conflicts
    # (Russia-Ukraine, Israel-Gaza, etc.), where escalatory incidents are
    # common but a *major* escalation in any given month is still the
    # minority case. Treated as a starting anchor only.
    "conflict_escalation": 0.20,
    # Base rate that an active conflict reaches a ceasefire/de-escalation
    # milestone within a ~1 month window. Historically, confirmed
    # ceasefires/peace agreements are rarer than escalatory incidents for
    # any given still-active conflict in any given month — set lower than
    # conflict_escalation for that reason.
    "conflict_deescalation": 0.10,
}

# ---------------------------------------------------------------------------
# Documented-but-omitted event types (spec's broader vocabulary). No number
# is defensible as a single, generic base rate for these without knowing the
# specific market (how many candidates, current policy-rate trajectory,
# field size, bill's committee status, etc.) — get_base_rate() intentionally
# returns None for all of these, and always will unless a real, defensible,
# sourced number is added above.
#   ELECTION_WINNER, RATE_CUT, RATE_HIKE, PRICE_ABOVE, PRICE_BELOW,
#   TOURNAMENT_WINNER, MATCH_WINNER, QUALIFICATION, LEGISLATION_PASS,
#   PRODUCT_LAUNCH, CEASEFIRE (see conflict_deescalation above for the
#   closest populated proxy), WAR_ESCALATION (see conflict_escalation above)
# ---------------------------------------------------------------------------

# Extraordinary event types: rare, high-consequence events where a single
# weak/ambiguous piece of evidence must never be allowed to swing the
# forecast far from the base rate. Currently limited to what
# semantics.py's detector can actually produce ("office_departure"); the
# remaining entries are documented for when semantics.py is extended to
# detect them (government collapse, invasion, emergency rate decision, CEO
# resignation, sovereign default) — they are inert today (never emitted by
# extract_event/parse_market_proposition) but listed so the guard doesn't
# need to be revisited when that detection lands.
EXTRAORDINARY_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "office_departure",  # president/head-of-state leaves office early
        "government_collapse",  # not yet detected by semantics.py — placeholder
        "invasion",  # not yet detected by semantics.py — placeholder
        "emergency_rate_decision",  # not yet detected by semantics.py — placeholder
        "ceo_resignation",  # not yet detected by semantics.py — placeholder
        "sovereign_default",  # not yet detected by semantics.py — placeholder
    }
)


def get_base_rate(event_type: str | None) -> float | None:
    """Returns the defensible historical base rate for `event_type`, or
    `None` if this table has no real, sourced number for it. Callers must
    treat `None` as "no base rate available" and must NOT substitute 0.5 (or
    any other value) in its place."""
    if event_type is None:
        return None
    return BASE_RATES.get(event_type)
