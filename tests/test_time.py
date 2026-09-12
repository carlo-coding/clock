from datetime import date, datetime, timezone

from tick.timeutil import delivery_hours, gate_closure, iso, parse_iso


def test_delivery_day_has_23_hours_in_spring():
    assert len(delivery_hours(date(2026, 3, 29), "DE-LU")) == 23


def test_delivery_day_has_25_hours_in_autumn():
    assert len(delivery_hours(date(2026, 10, 25), "ES")) == 25


def test_ordinary_day_has_24_hours_starting_at_local_midnight():
    hours = delivery_hours(date(2026, 9, 13), "DE-LU")
    assert len(hours) == 24
    assert iso(hours[0]) == "2026-09-12T22:00:00Z"  # CEST is UTC+2


def test_gate_closure_is_noon_local_the_day_before():
    assert iso(gate_closure(date(2026, 9, 13))) == "2026-09-12T10:00:00Z"  # summer
    assert iso(gate_closure(date(2026, 1, 13))) == "2026-01-12T11:00:00Z"  # winter


def test_iso_roundtrip():
    t = datetime(2026, 9, 12, 7, 40, 0, tzinfo=timezone.utc)
    assert parse_iso(iso(t)) == t
