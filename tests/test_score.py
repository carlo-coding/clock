from datetime import date

from tick import config, score, store
from tick.timeutil import delivery_hours, iso, parse_iso


def test_metrics_and_skill():
    pairs = [(10.0, 12.0), (14.0, 12.0)]  # errors -2, +2
    m = score._metrics(pairs, "wind", cap=100.0)
    assert m["n"] == 2 and m["mae"] == 2.0 and m["bias"] == 0.0 and m["nmae_pct"] == 2.0
    worse = score._metrics([(8.0, 12.0), (16.0, 12.0)], "wind", cap=100.0)
    assert score._skill(m, worse) == 0.5
    assert score._metrics([], "wind", None) == {"n": 0}
    load = score._metrics([(110.0, 100.0)], "load", None)
    assert load["mape_pct"] == 10.0


def test_solar_is_scored_over_daylight_only():
    hours = {"a": {"v": 0.0, "c": 1}, "b": {"v": 5.0, "c": 1}}
    assert score._usable_hours(hours, "solar") == {"b": 5.0}
    assert score._usable_hours(hours, "wind") == {"a": 0.0, "b": 5.0}


def test_score_day_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FORECASTS", tmp_path / "forecasts")
    monkeypatch.setattr(config, "ACTUALS", tmp_path / "actuals")
    monkeypatch.setattr(config, "OFFICIAL", tmp_path / "official")
    monkeypatch.setattr(config, "SCORES", tmp_path / "scores")
    monkeypatch.setattr(config, "INTRADAY", tmp_path / "intraday")
    monkeypatch.setattr(config, "CAPACITY", tmp_path / "capacity")
    monkeypatch.setattr(config, "ZONES", {"DE-LU": config.ZONES["DE-LU"]})
    monkeypatch.setattr(config, "TARGETS", {"DE-LU": ["wind"]})

    day = date(2026, 9, 13)
    hours = delivery_hours(day, "DE-LU")
    # Forecast issued the day before: clock is 10 MW high everywhere, persistence 20 MW low, weekly 30 MW high.
    fc_hours = {iso(h): {"clock": 110.0, "persistence": 80.0, "weekly": 130.0} for h in hours}
    store.write_new(
        store.forecast_path("2026-09-12"),
        {
            "issued_at": "2026-09-12T07:40:00Z",
            "before_gate_closure": True,
            "model": "naive-v1",
            "zones": {"DE-LU": {"wind": {"leads": {"1": {"delivery_day": day.isoformat(), "hours": fc_hours}}}}},
        },
    )
    store.write_new(
        store.actuals_path("DE-LU", day.isoformat(), final=False),
        {"fetched_at": "2026-09-14T07:40:00Z", "targets": {"wind": {"complete": True, "hours": {iso(h): {"v": 100.0, "c": 1.0} for h in hours}}}},
    )
    store.write_new(
        store.official_path("DE-LU", day.isoformat(), "wind"),
        {"first_seen_at": "2026-09-12T15:00:00Z", "complete": True, "hours": {iso(h): {"v": 105.0, "c": 1.0} for h in hours}},
    )
    store.write_new(store.capacity_path("DE-LU", 2026), {"targets": {"wind": 1000.0}})

    path, written = score.score_day(day, final=False, now=parse_iso("2026-09-14T07:41:00Z"))
    assert written
    doc = store.read(store.score_path(day.isoformat(), False))
    lead = doc["zones"]["DE-LU"]["wind"]["leads"]["1"]
    assert lead["hours_scored"] == 24
    assert lead["clock"]["mae"] == 10.0 and lead["clock"]["nmae_pct"] == 1.0
    assert lead["official"]["mae"] == 5.0
    assert lead["skill_vs_official"] == -1.0  # the naive clock was twice as bad as the operator
    assert lead["skill_vs_persistence"] == 0.5
    assert lead["official_first_seen_at"] == "2026-09-12T15:00:00Z"

    # Scoring again does nothing: the file is never rewritten.
    path2, written2 = score.score_day(day, final=False)
    assert not written2


def test_a_day_is_not_scored_until_every_zone_has_actuals(tmp_path, monkeypatch):
    """19-21 Sep 2026: Spain complete, DE-LU a day late, and the preliminary score went out without DE-LU."""
    monkeypatch.setattr(config, "FORECASTS", tmp_path / "forecasts")
    monkeypatch.setattr(config, "ACTUALS", tmp_path / "actuals")
    day = date(2026, 9, 20)
    store.write_new(
        store.forecast_path("2026-09-19"),
        {"issued_at": "2026-09-19T00:08:00Z", "before_gate_closure": True, "model": "naive-v1",
         "zones": {"ES": {"wind": {"leads": {"1": {"delivery_day": day.isoformat(), "hours": {}}}}}}},
    )
    store.write_new(store.actuals_path("ES", day.isoformat(), final=False), {"targets": {}})
    assert not score.ready(day, final=False)
    store.write_new(store.actuals_path("DE-LU", day.isoformat(), final=False), {"targets": {}})
    assert score.ready(day, final=False)
    assert not score.ready(day, final=True)
