from polymarketpulse.research_status import classify_research_status


def test_published_is_distinct_from_a_model_hypothesis() -> None:
    assert classify_research_status(
        published_forecast_probability=0.217,
        model_hypothesis_probability=0.217,
        forecast_status="BLENDED_FORECAST",
        has_research_run=True,
    ) == "PUBLISHED"
    assert classify_research_status(
        published_forecast_probability=None,
        model_hypothesis_probability=0.217,
        forecast_status="LOW_DATA",
        has_research_run=True,
    ) == "MODEL_ONLY"


def test_blocked_and_missing_research_are_not_collapsed() -> None:
    assert classify_research_status(
        published_forecast_probability=None,
        model_hypothesis_probability=0.217,
        forecast_status="FORECAST_SUPPRESSED",
        has_research_run=True,
    ) == "FORECAST_BLOCKED"
    assert classify_research_status(
        published_forecast_probability=None,
        model_hypothesis_probability=None,
        forecast_status=None,
        has_research_run=False,
    ) == "NOT_RESEARCHED"
