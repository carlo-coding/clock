from datetime import date

from tick import config, store, watchdog
from tick.timeutil import parse_iso


def _issue(tmp_path, monkeypatch, days):
    monkeypatch.setattr(config, "FORECASTS", tmp_path)
    for d in days:
        store.write_new(store.forecast_path(d), {"issue_day": d})


def test_gaps_are_listed():
    days = [date(2026, 9, 12), date(2026, 9, 13), date(2026, 9, 15)]
    assert watchdog.gaps(days) == [date(2026, 9, 14)]


def test_fresh_series_passes(tmp_path, monkeypatch):
    _issue(tmp_path, monkeypatch, ["2026-09-12", "2026-09-13"])
    problems, notes = watchdog.check(parse_iso("2026-09-13T13:00:00Z"))
    assert problems == []


def test_missing_today_past_slot_fails(tmp_path, monkeypatch):
    _issue(tmp_path, monkeypatch, ["2026-09-12"])
    problems, _ = watchdog.check(parse_iso("2026-09-13T13:00:00Z"))
    assert problems and "missing" in problems[0]


def test_48_hours_of_silence_fails(tmp_path, monkeypatch):
    _issue(tmp_path, monkeypatch, ["2026-09-12"])
    problems, _ = watchdog.check(parse_iso("2026-09-14T08:00:00Z"))
    assert problems and "48" in problems[0] or "hours" in problems[0]


def test_simulated_gap_fires(tmp_path, monkeypatch):
    _issue(tmp_path, monkeypatch, ["2026-09-13"])
    problems, _ = watchdog.check(parse_iso("2026-09-13T13:00:00Z"), simulate=True)
    assert any("simulated" in p for p in problems)


def test_no_record_at_all_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FORECASTS", tmp_path / "none")
    problems, _ = watchdog.check(parse_iso("2026-09-13T13:00:00Z"))
    assert problems
