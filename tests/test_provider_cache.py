from polymarketpulse.prediction import engine


def test_provider_cache_reuses_value_and_expires_at_ttl(monkeypatch) -> None:
    engine._provider_cache.clear()
    times = iter((0.0, 0.0, 299.0, 301.0, 301.0))
    monkeypatch.setattr(engine, "monotonic", lambda: next(times))
    calls = []

    def fetch():
        calls.append(True)
        return len(calls)

    assert engine._cached_provider_call(("test",), fetch) == 1
    assert engine._cached_provider_call(("test",), fetch) == 1
    assert engine._cached_provider_call(("test",), fetch) == 2
    assert len(calls) == 2


def test_negative_cache_allows_provider_recovery_after_ttl(monkeypatch) -> None:
    engine._provider_cache.clear()
    times = iter((0.0, 0.0, 1.0, 301.0, 301.0))
    monkeypatch.setattr(engine, "monotonic", lambda: next(times))
    responses = iter((None, "recovered"))
    calls = []

    def fetch():
        calls.append(True)
        return next(responses)

    assert engine._cached_provider_call(("offline",), fetch) is None
    assert engine._cached_provider_call(("offline",), fetch) is None
    assert engine._cached_provider_call(("offline",), fetch) == "recovered"
    assert len(calls) == 2
