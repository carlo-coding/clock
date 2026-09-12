from datetime import timedelta

from tick import config, forecast
from tick.timeutil import delivery_hours, iso, parse_iso


def synthetic_history(now, days=16):
    """Hourly actuals for both zones: value = hour of day + 100 * day offset, so we can see which day a baseline came from."""
    history = {}
    for zone in config.ZONES:
        history[zone] = {}
        for target in config.TARGETS[zone]:
            series = {}
            d0 = now.date() - timedelta(days=days)
            for k in range(days + 1):
                day = d0 + timedelta(days=k)
                for h in delivery_hours(day, zone):
                    if h < now:
                        series[h] = {"v": float(h.hour + 100 * k), "c": 1.0}
            history[zone][target] = series
    return history


def test_build_uses_the_rules_and_records_inputs():
    now = parse_iso("2026-09-12T07:40:00Z")
    doc = forecast.build(synthetic_history(now), now)
    assert doc["model"] == config.MODEL_VERSION
    assert doc["day_ahead"] == "2026-09-13"
    assert doc["before_gate_closure"] is True
    assert doc["gate_closure"] == "2026-09-12T10:00:00Z"
    de = doc["zones"]["DE-LU"]
    assert set(de) == set(config.TARGETS["DE-LU"])
    lead1 = de["wind"]["leads"]["1"]
    assert lead1["delivery_day"] == "2026-09-13"
    assert len(lead1["hours"]) == 24
    first_hour = lead1["hours"]["2026-09-12T22:00:00Z"]
    # Persistence for wind: same UTC hour of the last complete day (Sep 11, offset 15) -> 22 + 1500
    assert first_hour["clock"] == first_hour["persistence"] == 22 + 1500
    # Weekly: seven days before the target (Sep 6, offset 10)
    assert first_hour["weekly"] == 22 + 1000
    load1 = de["load"]["leads"]["1"]["hours"]["2026-09-12T22:00:00Z"]
    assert load1["clock"] == load1["weekly"]
    assert doc["inputs"]["DE-LU"]["wind"]["last_complete_day"] == "2026-09-11"
    assert "7" in de["wind"]["leads"] and "8" not in de["wind"]["leads"]


def test_late_issue_is_flagged_not_refused():
    now = parse_iso("2026-09-12T13:00:00Z")
    doc = forecast.build(synthetic_history(now), now)
    assert doc["before_gate_closure"] is False
    assert doc["day_ahead"] == "2026-09-13"


def test_missing_history_yields_nulls_not_numbers():
    now = parse_iso("2026-09-12T07:40:00Z")
    doc = forecast.build({"DE-LU": {"wind": {}}, "ES": {"wind": {}}}, now)
    h = doc["zones"]["DE-LU"]["wind"]["leads"]["1"]["hours"]
    assert all(v["clock"] is None and v["persistence"] is None and v["weekly"] is None for v in h.values())
