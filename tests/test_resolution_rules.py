from polymarketpulse.prediction.resolution_rules import parse_resolution_conditions


def test_parses_explicit_yes_and_no_clauses() -> None:
    question = "Will Candidate X win the election?"
    resolution_text = (
        'This market resolves to "Yes" if Candidate X wins the general election. '
        'This market resolves to "No" if Candidate X loses or withdraws before election day.'
    )
    yes_terms, no_terms, subject_terms = parse_resolution_conditions(question, resolution_text)
    assert "wins" in yes_terms or "election" in yes_terms
    assert "loses" in no_terms or "withdraws" in no_terms
    assert "candidate" in subject_terms


def test_no_resolution_text_returns_empty_yes_no_terms() -> None:
    yes_terms, no_terms, subject_terms = parse_resolution_conditions("Will X happen?", None)
    assert yes_terms == ()
    assert no_terms == ()
    assert subject_terms  # subject terms still derived from the question


def test_resolution_text_without_explicit_clause_stays_empty() -> None:
    yes_terms, no_terms, _ = parse_resolution_conditions("Will X happen?", "Resolution source: polymarket")
    assert yes_terms == ()
    assert no_terms == ()
