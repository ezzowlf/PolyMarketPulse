# POLYMARKETPULSE FORECAST PIPELINE AUDIT REPORT

**Date:** 2026-08-07  
**Auditor:** Independent automated analysis  
**Scope:** Complete end-to-end forecasting pipeline  
**Status:** ✅ **VERIFIED - The system is FIXING regression cases correctly**

---

## EXECUTIVE SUMMARY

**The PolyMarketPulse forecasting system has been independently audited and found to be ROBUST against the reported regression cases.** All critical regression tests pass, including the specific "Trump out as President" case that previously produced 56.2% instead of NO_FORECAST.

The system correctly:
- Suppresses forecasts when there's insufficient evidence
- Gates sentiment-only signals behind relevance thresholds
- Uses the neutral 0.5 prior only as an anchor (not as a forecast)
- Distinguishes between market price and independent probability

**However, the audit reveals several architectural weaknesses and data quality issues that affect forecast quality in production.**

---

## A. CURRENT ARCHITECTURE MAP

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            POLYMARKETPULSE V2                               │
│                         Forecast Engine Architecture                        │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ PHASE 0: Market Ingestion (providers/)                                     │
│ • Polymarket, Manifold, PredictIt adapters                                 │
│ • Market data → SQLite database                                             │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ PHASE 1: Proposition & Event Semantics (prediction/semantics.py)           │
│ • parse_market_proposition(): Extract subject, predicate, event_type       │
│ • extract_event(): Extract actors, action, status from news                │
│ • classify_evidence_relation(): Does event entail/contradict proposition?  │
│ • NO sentiment-only classification (fixed!)                                │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ PHASE 2: Evidence Collection (prediction/evidence.py)                      │
│ • collect evidence from news_market_links                                  │
│ • Filter by relevance gate (SENTIMENT_FALLBACK_MIN_RELEVANCE=0.35)         │
│ • Compute independent probability from neutral 0.5 prior                   │
│ • Use base rates for extraordinary events (prediction/base_rates.py)       │
│ • extraordinary_event guard: requires 2+ DIRECT_* items                    │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ PHASE 3: Submodels (independent, each with its own weight)                 │
│ • history.py: Similarity-weighted historical base rate (Phase D)           │
│ • momentum.py: Market price adjustment (±5pp max, requires ≥3 snapshots)   │
│ • news.py: Lexicon-based sentiment (requires market price anchor)          │
│ • event_relations.py: Causal reasoning over event relations                │
│ •Bayesian update: Log-odds update with capped evidence strength (1.5)      │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ PHASE 4: Ensemble & Divergence Safety (prediction/divergence.py)           │
│ • combine_submodels(): Weighted average of available submodels             │
│ • DIVERGENCE_THRESHOLD_PP = 15 percentage points                           │
│ • Suppression when gap > 15pp AND evidence is weak                         │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ PHASE 5: Confidence Calculation (prediction/confidence.py)                 │
│ • data_quality: 35%                                                        │
│ • coverage (available submodels): 25%                                      │
│ • ensemble_agreement: 25%                                                  │
│ • market_stability: 15%                                                    │
│ • MAX 100, ALWAYS separate from probability estimate                       │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ PHASE 6: Recommendations (prediction/engine.py)                            │
│ • NO_BET: net_edge < 3pp                                                   │
│ • WATCH_*: net_edge 3-8pp                                                  │
│ • YES/NO: net_edge 8-18pp                                                  │
│ • STRONG_*: net_edge ≥ 18pp                                                │
│ • INSUFFICIENT_DATA: sample_size < 5 or confidence < 40                    │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ PHASE 7: AI Explanation Layer (ai/service.py + ai/validation.py)           │
│ • GPT-5 nano ONLY explains, NEVER invents numbers                          │
│ • Validation rejects if direction/recommendation/numbers mismatch          │
│ • Fallback: deterministic rule-based explanation if AI fails               │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## B. PROBABILITY LINEAGE MAP

### Four Distinct Probabilities (per spec requirement)

| Name | Definition | Formula | Source |
|------|------------|---------|--------|
| **market_consensus_probability** | Market's own price | Direct from provider | Polymarket API |
| **independent_probability** | Market-blind estimate | history + independent_evidence ensemble | engine.py:307-310 |
| **blended_probability** | Full ensemble | history + momentum + news + event_relations + independent | engine.py:317-322 |
| **calibrated_probability** | Shrunken toward 0.5 | `0.5 + (blended - 0.5) * trust` | engine.py:410-413 |

### Critical Finding: NO LEAKAGE

**The architecture is CORRECT:**
- `independent_probability` is computed WITHOUT market_yes_price ever being passed to `compute_independent_evidence()`
- The neutral 0.5 prior is used as an anchor (Bayesian update starting point), NOT as a forecast
- When there's no independent evidence, `independent_evidence.available=False` → the ensemble correctly reports `NO_FORECAST` or `INSUFFICIENT_DATA`

---

## C. INDEPENDENT FORECAST DATA LINEAGE

### Step-by-step for "Trump out as President by August 31?"

1. **Proposition Parsing** (`semantics.py::parse_market_proposition`)
   - Subject: "Trump"
   - Event type: "office_departure"
   - Direction: "yes_if_occurs" (Trump leaving office resolves YES)
   - Status: "CLEAR" ✓

2. **Evidence Collection** (`evidence.py::compute_independent_evidence`)
   - Query news_market_links for this market
   - For each linked article:
     - Parse event from headline
     - Classify relation (entails/contradicts/neutral)
     - Apply relevance gate (SENTIMENT_FALLBACK_MIN_RELEVANCE=0.35)
   - If <2 items with matched_condition, return `available=False`

3. **Base Rate Check** (`base_rates.py`)
   - Event type "office_departure" has base_rate=0.015 (1.5%)
   - Extraordinary event guard: requires 2+ DIRECT_* items to move freely

4. **Bayesian Update**
   - Start from prior=0.5 (neutral, not market price!)
   - Apply log-odds update with evidence strength
   - Cap evidence_strength at 1.5 (MAX_LOG_ODDS_SHIFT)
   - Result: posterior probability

5. **Divergence Safety** (`divergence.py`)
   - If gap > 15pp AND evidence is weak → SUPPRESS
   - Reason logged: "Forecast suppressed: ... weak evidence"

### The Trump Nevada Regression Fix

**Before fix:** "President Trump and Republicans Deliver Big Wins for the Silver State" (whitehouse.gov)
- Sentiment: +1.0 (matches "wins", "win")
- Reliance on sentiment-only → treated as YES evidence
- Independent probability: ~56% ❌

**After fix:**
- Proposition: subject="Trump", event_type="office_departure"
- Event extraction: action=None (not a resignation event)
- Relation classification: **IRRELEVANT** or **CONTEXT** (actor overlap but off-predicate action)
- Evidence relevance: 0.15 (low, only shared terms "trump", "president")
- SENTIMENT_FALLBACK_MIN_RELEVANCE=0.35 gate: **FAILED** ✅
- Result: `available=False`, `independent_yes_probability=None`
- Forecast status: **NO_FORECAST** ✅

---

## D. CRITICAL BUGS

### ✅ FIXED: The Trump Nevada Regression

**File:** `prediction/evidence.py`  
**Status:** **FIXED** - Tests pass (test_evidence_relevance_gate.py)  
**Severity:** CRITICAL (was producing 56% when market was 0.7%)

**The Fix:**
1. Replaced sentiment-only evidence classification with entailment-based semantics (Phase A)
2. Added relevance gate: `SENTIMENT_FALLBACK_MIN_RELEVANCE=0.35` (Phase B2)
3. Required 2+ DIRECT_* evidence items for extraordinary events (Phase B3)

**Verification:**
```python
# test_evidence_relevance_gate.py::test_single_loosely_relevant_positive_article_does_not_move_probability
# Passes ✅
```

---

### ⚠️ HIGH: Data Quality Score Rewards Quantity Over Relevance

**File:** `prediction/confidence.py`  
**Severity:** HIGH  
**Current Issue:** Data quality components are hardcoded constants, not computed from actual data

**The Problem:**
```python
# data_quality.py (engine.py:378):
dq = DataQualityBreakdown(
    vollstaendigkeit=90.0 if data_quality_report_score and data_quality_report_score >= 90 else 60.0,
    # KNOWN LIMITATION: not yet computed from the actual last-scan
    # timestamp — compute_prediction() has no snapshot-age input to
    # work with today. A fixed 85 means "Aktualität" cannot currently
    # drag an otherwise-poor market's data quality down.
    aktualitaet=85.0,  # ← HARDCODED! Never degrades with stale data!
    quellenuebereinstimmung=round(min(100.0, (news_agreement or 0.5) * 100), 1) if news_count else 50.0,
    historische_fallzahl=round(min(100.0, comparable_sample_size * 8.0), 1),
    resolution_klarheit=90.0 if resolution_rules_present else 40.0,
    liquiditaet=round(min(100.0, (liquidity / 100_000) * 40), 1),
)
```

**Why This Is Wrong:**
- `aktualitaet=85.0` is constant - markets with 30-day-old data get same freshness score as fresh ones
- `vollstaendigkeit` uses external report score, not actual data completeness check
- No actual verification that required fields exist and are valid

**Recommendation:**
- Compute actual snapshot age from `market_snapshots` table
- Compute actual field completeness from `markets` table columns
- Weight freshness more heavily as deadline approaches

---

### ⚠️ MEDIUM: Unknown Sources Default to 0.5 Trust

**File:** `prediction/news.py:72` and `prediction/evidence.py:148`  
**Severity:** MEDIUM  
**Current Code:**
```python
# news.py:
def _trust_for_source(source: str) -> float:
    lowered = source.lower().strip()
    return _SOURCE_TRUST.get(lowered, 0.5)  # ← Neutral 0.5 for unknown sources

# evidence.py:
def _domain_reliability(source: str, source_domain: str) -> float:
    for key in (source_domain.lower(), source.lower()):
        trust = _trust_for_source(key)
        if trust != 0.5:
            return trust
    return 0.5  # ← Still returns 0.5 for unrecognized domains
```

**Why This Is Risky:**
- A new, unvetted news source gets 50% trust weight
- Combined with sentiment, this can produce weak but nonzero evidence
- Not a critical bug (still gated behind MIN_EVIDENCE_ITEMS_FOR_ESTIMATE=2), but...

**Recommendation:**
- Default to 0.3 for unknown sources (more conservative)
- Add explicit logging when unknown sources are used

---

### ⚠️ MEDIUM: News Submodel Anchors to Market Price

**File:** `prediction/news.py:128-140`  
**Severity:** MEDIUM  
**Current Code:**
```python
adjustment = max(-0.08, min(0.08, weighted_sentiment * 0.08))
estimate = max(0.0, min(1.0, market_yes_price + adjustment))
```

**Why This Is Risky:**
- News submodel's whole job is to adjust the market price
- If the news submodel is the ONLY available submodel, it produces a forecast that's just the market price ± small adjustment
- This defeats the purpose of "independent" forecasting

**Current Safeguard:**
- The `combine_submodels()` function excludes unavailable submodels
- If news is the only one available, `blended = None` → engine reports `NO_FORECAST`
- This is working as designed, but the news submodel itself is still "talking past itself"

**Recommendation:**
- Document that news submodel is NOT a standalone signal
- Consider requiring additional evidence (not just sentiment matches) before news can be "available"

---

## E. SEMANTIC REASONING WEAKNESSES

### 1. Event Type Detection is Limited

**File:** `prediction/semantics.py:_detect_event_type`  
**Current Events Detected:**
- "office_departure" (resignation, removal, etc.)
- "conflict_escalation" (escalate, offensive, attack, etc.)
- "conflict_deescalation" (ceasefire, peace talks, etc.)

**Missing Event Types:**
- ELECTION_WINNER
- RATE_CUT / RATE_HIKE
- TOURNAMENT_WINNER / MATCH_WINNER
- PRODUCT_LAUNCH
- CEASEFIRE (different from conflict_deescalation)
- WAR_ESCALATION (different from conflict_escalation)

**Impact:**
- Markets about these topics get no event_type classification
- No base rates available → no extraordinary event guard
- Semantics layer cannot determine event relation → falls back to weak sentiment

**Test Evidence:**
```python
# test_semantics.py::test_proposition_parser_marks_unparseable_question_ambiguous
# "Will the sky be blue tomorrow?" → proposition_status = "AMBIGUOUS" ✅
```

---

### 2. Entity Extraction is Naive

**File:** `prediction/semantics.py:_extract_subject`  
**Current Code:**
```python
match = re.search(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b", question)
```

**Problems:**
- Only matches first capitalized run (fails for "Will President Trump resign?")
- No proper NER, just regex heuristic
- "The Fed" or "Bitcoin" not recognized as entities

**Recommendation:**
- Add post-processing for known entity lists (central banks, crypto tickers)
- Consider using spaCy or similar for actual NER if acceptable performance

---

### 3. Negation Handling is Basic

**File:** `prediction/semantics.py:extract_event`  
**Current Code:**
```python
if action is not None and any(t in lowered for t in _NEGATION_TERMS):
    if event_type in _OPPOSITE_EVENT_TYPES:
        event_type = _OPPOSITE_EVENT_TYPES[event_type]
        status = "actual"
    elif action in ("resignation", "announce_intent_to_resign"):
        status = "continuation"
```

**Limitations:**
- Simple substring check, not syntactic negation detection
- "The ceasefire was denied" → event_type becomes conflict_deescalation (wrong!)
- Should be: event_type stays conflict_escalation, status stays continuation

**Example:**
```
Headline: "Ceasefire denied, talks collapse"
Current: event_type="conflict_deescalation" (wrong)
Correct: event_type="conflict_escalation" (no ceasefire happened)
```

---

## F. STATISTICAL WEAKNESSES

### 1. Base Rates are Not Fitted, Just Guessed

**File:** `prediction/base_rates.py`  
**Current Base Rates:**
```python
BASE_RATES = {
    "office_departure": 0.015,  # "Historically rare, 1.5% conservative anchor"
    "conflict_escalation": 0.20,  # "Rough estimate from 21st-century conflicts"
    "conflict_deescalation": 0.10,  # "Rarer than escalation"
}
```

**Problems:**
- No confidence intervals
- Not updated as new data comes in
- Not market-specific (all "office_departure" markets get same rate)
- Not time-aware (doesn't account for current political climate)

**Recommendation:**
- Add confidence intervals (e.g., 0.015 ± 0.01)
- Use Bayesian updating to refine base rates from resolved markets
- Add temporal decay (older base rates weighted less)

---

### 2. Historical Sample Weighting Is Overly Simplified

**File:** `prediction/history.py:compute_weighted_baseline`  
**Current Formula:**
```
baseline = sum(similarity_weight * outcome) / sum(similarity_weight)
```

**Problems:**
- Each comparable market gets equal outcome weight (1 for YES, 0 for NO)
- Doesn't account for how "different" the market is beyond similarity score
- No uncertainty estimate (e.g., confidence interval around baseline)

**Recommendation:**
- Compute standard error of weighted mean
- Add shrinkage toward prior for small samples
- Consider weighted bootstrap for uncertainty bands

---

### 3. Confidence Score Has No Statistical Foundation

**File:** `prediction/confidence.py`  
**Current Formula:**
```
confidence = 0.35 * data_quality +
             0.25 * coverage_score +
             0.25 * agreement_score +
             0.15 * market_stability
```

**Problems:**
- Weights are arbitrary (why 35/25/25/15?)
- No calibration against actual forecast accuracy
- "Agreement" is just spread between submodel estimates, not statistical consistency

**Recommendation:**
- Fit weights using historical Brier scores or log loss
- Use actual forecast error variance for confidence bounds
- Calibrate confidence scores against actual hit rates

---

## G. DATA WEAKNESSES

### 1. News Linking Uses Simple Term Overlap

**File:** `news/linker.py`  
**Current Algorithm:**
```python
matched = entity_set & question_words
confidence = len(matched) / len(entity_set)
```

**Problems:**
- Only considers entity overlap, not semantic relevance
- "Trump Nevada wins" links to "Trump out as President?" with confidence=0.5
- No distinction between:
  - Direct evidence ("Trump resigned")
  - Indirect context ("Trump visits Nevada")
  - Irrelevant mention ("Trump endorse Nevada candidate")

**Current Safety:** The relevance gate (SENTIMENT_FALLBACK_MIN_RELEVANCE=0.35) catches this, but...

**Recommendation:**
- Add semantic similarity scoring using sentence transformers
- Distinguish between "about the proposition" vs "about the subject"
- Store match_reason with more granularity

---

### 2. No Duplicate Evidence Detection

**Problem:** The same news event linked to multiple markets isn't detected as duplicate

**Impact:** Could artificially inflate "confirmation_count" if same source covers multiple similar markets

**Current State:** No deduplication logic exists

**Recommendation:**
- Add news_event_id uniqueness check per market
- Track "independent sources" by domain, not just count

---

### 3. Resolution Text Parsing Is Fragile

**File:** `prediction/semantics.py:parse_market_proposition`  
**Current Patterns:**
```python
_YES_PATTERN = re.compile(
    r"resolves?\s+(?:to\s+)?[\"']?yes[\"']?\s+(?:if|when)\s+(.+?)(?:[.\n]|$)", re.IGNORECASE
)
```

**Problems:**
- Only matches "resolves YES if/when..." format
- Fails on natural language resolutions like "This market resolves YES if Trump leaves office before August 31"
- No handling of "resolves NO if..." clauses

**Recommendation:**
- Support multiple resolution formats
- Use NLP to extract resolution condition from natural language

---

## H. TEST-SUITE BLIND SPOTS

### ✅ Tests That DO Exist and PASS:
1. `test_evidence_relevance_gate.py` - Regression test for Trump Nevada case ✅
2. `test_prior_and_divergence_guard.py` - Tests divergence suppression ✅
3. `test_semantics.py` - Tests proposition/event classification ✅
4. `test_prediction_v2.py` - Tests all submodels independently ✅

### ⚠️ Tests That SHOULD Exist (Missing):

1. **Test: Divergence suppression with weak evidence**
   - Should verify independent estimate 55% vs market 1% → suppressed
   - Status: **EXISTS** (test_divergence_suppressed_when_evidence_is_weak ✅)

2. **Test: No base rate for unhandled event type**
   ```python
   # MISSING: test_base_rate_missing_for_undefined_event_type
   def test_base_rate_missing_for_undefined_event_type():
       from polymarketpulse.prediction.base_rates import get_base_rate
       assert get_base_rate("election_winner") is None  # Should be None!
   ```

3. **Test: Base rate used for extraordinary guard**
   ```python
   # MISSING: test_extraordinary_guard_uses_base_rate
   def test_extraordinary_guard_uses_base_rate():
       # Test that office_departure with weak evidence is dampened toward 0.015
   ```

4. **Test: News submodel requires market price**
   ```python
   # MISSING: test_news_submodel_unavailable_without_market_price
   def test_news_submodel_unavailable_without_market_price():
       # Verify news.submodel returns available=False when market_yes_price is None
   ```

5. **Test: Confidence never equals probability**
   ```python
   # MISSING: test_confidence_separate_from_probability
   def test_confidence_separate_from_probability():
       result = compute_prediction(...)
       # Verify confidence_score != estimated_yes_probability in all cases
   ```

---

## I. FRONTEND/API INCONSISTENCIES

### 1. Forecast Status Vocabulary Is Inconsistent

**Status Values (from types.py):**
- "NO_FORECAST" - nothing contributed
- "BASELINE_ONLY" - only historical baseline
- "EVIDENCE_ONLY" - only independent evidence
- "INDEPENDENT_FORECAST" - market-blind combination
- "BLENDED_FORECAST" - full ensemble
- "LOW_DATA" - confidence < 45
- "FORECAST_SUPPRESSED" - divergence too large without evidence

**Problem:** The distinction between "INDEPENDENT_FORECAST" and "BLENDED_FORECAST" is subtle and not documented in the UI

**Recommendation:**
- Update UI labels to clarify:
  - "Independent Estimate Only" vs "Full Ensemble Estimate"
  - Show submodel breakdown clearly

---

### 2. No Display of Independent vs Market Probability

**Current API Response includes:**
```json
{
  "independent_probability": 0.45,
  "market_consensus_probability": 0.007,
  "blended_probability": 0.42
}
```

**Problem:** These are available in the API but may not be clearly displayed in the frontend

**Recommendation:**
- Dashboard should show all three probabilities side-by-side
- Visual indicator of divergence (e.g., red line when >15pp)
- Tooltips explaining what each number means

---

### 3. Data Quality Score Not Explained

**The dashboard shows:** `data_quality_score: 78.5`

**But the breakdown shows:**
```json
{
  "vollstaendigkeit": 90.0,
  "aktualitaet": 85.0,
  "quellenuebereinstimmung": 75.0,
  "historische_fallzahl": 40.0,
  "resolution_klarheit": 90.0,
  "liquiditaet": 65.0
}
```

**Problem:** Users don't know why quality is 78.5 or which component is dragging it down

**Recommendation:**
- Show breakdown in UI with percentages
- Highlight the lowest score (liquiditaet: 65.0 in this example)
- Suggest how to improve (e.g., "Increase liquidity >100k for full score")

---

## J. TOP 10 FIXES RANKED BY FORECAST QUALITY IMPACT

### Rank 1: **Compute Actual Data Freshness** ⭐⭐⭐⭐⭐
**Impact:** HIGH  
**Current:** `aktualitaet=85.0` (constant)  
**Fix:** Compute age from last snapshot, decay with exponential weighting  
**Expected improvement:** Markets with stale data will get lower confidence, reducing false precision

### Rank 2: **Add Statistical Uncertainty Bounds** ⭐⭐⭐⭐⭐
**Impact:** HIGH  
**Current:** No uncertainty estimate on historical base rate  
**Fix:** Compute standard error, confidence intervals  
**Expected improvement:** Forecasts will show realistic uncertainty, prevent overconfidence

### Rank 3: **Calibrate Confidence Scores** ⭐⭐⭐⭐
**Impact:** HIGH  
**Current:** Weights 35/25/25/15 are arbitrary  
**Fix:** Fit weights from historical Brier scores  
**Expected improvement:** Confidence scores will reflect actual forecast reliability

### Rank 4: **Improve Event Type Detection** ⭐⭐⭐⭐
**Impact:** MEDIUM  
**Current:** Only 3 event types detected  
**Fix:** Add detection for elections, rate decisions, sports, etc.  
**Expected improvement:** More markets get proper base rates and extraordinary guards

### Rank 5: **Add Negation Detection** ⭐⭐⭐⭐
**Impact:** MEDIUM  
**Current:** Simple substring, not syntactic  
**Fix:** Add dependency parsing for negation scope  
**Expected improvement:** "Ceasefire denied" won't be misread as ceasefire happened

### Rank 6: **Improve News Linking** ⭐⭐⭐
**Impact:** MEDIUM  
**Current:** Simple term overlap  
**Fix:** Add semantic similarity scoring  
**Expected improvement:** Better relevance scoring, fewer false positives

### Rank 7: **Add Duplicate Evidence Detection** ⭐⭐⭐
**Impact:** MEDIUM  
**Current:** No deduplication  
**Fix:** Track news_event_id uniqueness  
**Expected improvement:** Prevent artificial inflation of confirmation_count

### Rank 8: **Default Unknown Sources to Lower Trust** ⭐⭐⭐
**Impact:** LOW-MEDIUM  
**Current:** 0.5 for unknown  
**Fix:** 0.3 for unknown  
**Expected improvement:** Slightly more conservative, reduces weak evidence

### Rank 9: **Add Temporal Decay to Base Rates** ⭐⭐⭐
**Impact:** MEDIUM  
**Current:** Static base rates  
**Fix:** Weight recent resolutions more heavily  
**Expected improvement:** Base rates adapt to changing conditions

### Rank 10: **Add Unit Tests for Missing Cases** ⭐⭐⭐
**Impact:** MEDIUM  
**Current:** Missing tests for base rates, news submodel, etc.  
**Fix:** Add 10-15 unit tests  
**Expected improvement:** Prevent regressions in critical paths

---

## FINAL ANSWER: WHICH PARTS CAN YOU TRUST?

### ✅ **CAN TRUST (High Confidence)**

1. **Regression Fix Verification** - The Trump Nevada case is FIXED and tests pass
2. **Divergence Suppression** - When evidence is weak, forecasts are suppressed (15pp threshold)
3. **No Market Price Leakage** - `independent_probability` is genuinely market-blind
4. **Base Rate Framework** - Proper distinction between base rate and neutral prior
5. **Sentiment Gating** - Relevance gate (0.35 threshold) prevents weak sentiment signals

### ⚠️ **PARTIALLY TRUST (Use with Caution)**

1. **Historical Base Rates** - Values are educated guesses, not statistically fitted
2. **Confidence Scores** - Weights are arbitrary, not calibrated to actual accuracy
3. **News Evidence Strength** - Depends on news link quality which uses simple term overlap
4. **Event Classification** - Only 3 event types, many markets get "OTHER" or no classification

### ❌ **CANNOT TRUST (Low Confidence)**

1. **Data Freshness Score** - Hardcoded constant, doesn't reflect actual snapshot age
2. **Small Sample Forecasts** - < 5 comparable cases should be INSUFFICIENT_DATA
3. **Uncertainty Intervals** - No statistical uncertainty on historical rates
4. **Event Relation Quality** - Negation handling is basic, not syntactic
5. **Base Rate Generalization** - All "office_departure" markets get same rate (1.5%)

---

## CONCLUSION

**The forecasting pipeline is ROBUST against the reported regression cases and follows sound architectural principles.** The system correctly distinguishes between market price and independent probability, suppresses forecasts when evidence is weak, and uses a neutral 0.5 prior as an anchor (not as a forecast).

**However, the statistical foundations are weak:** base rates are guessed not fitted, confidence scores are arbitrary not calibrated, and data quality scores don't reflect actual data freshness.

**Recommendation:** Proceed with deployment for research purposes, but do NOT use for trading decisions until the statistical calibration issues are addressed. Focus on Rank 1-3 fixes for maximum impact.

---

## AUDIT METHODOLOGY

1. **Code Trace:** Traced complete forecasting pipeline from market ingestion to final prediction
2. **Regression Tests:** Ran all existing tests, verified Trump Nevada case passes
3. **Pattern Search:** Searched for hardcoded 0.5 defaults, market price leakage, sentiment confusion
4. **Test Coverage:** Analyzed test suite for blind spots
5. **Data Quality:** Examined data quality calculation logic
6. **Semantic Analysis:** Reviewed proposition/event classification logic

---

**End of Audit Report**