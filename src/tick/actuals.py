"""Fetching what actually happened, and keeping it twice.

The platform revises published values. Whoever scores only against the
revised figure is using information that did not exist the day after
delivery, and whoever scores only against the preliminary one never sees
the revision. So each delivery day is stored the day after (preliminary)
and again FINAL_LAG_DAYS later (final), in two files, and the difference
between them is part of the record.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from . import config, store
from .entsoe import Client
from .timeutil import delivery_hours, iso, utc_now

Hourly = dict[datetime, dict]  # {hour_utc: {"v": value, "c": coverage}}


def fetch_target(client: Client, zone: str, target: str, start: datetime, end: datetime) -> Hourly:
    """Actual hourly series of one target over a UTC window.

    Wind is the sum of onshore and offshore where both exist. An hour is
    kept only if every production type that reports at all in the window
    reports that hour; otherwise the sum would be silently short."""
    if target in ("wind", "solar"):
        parts = []
        for psr in config.PSR_TYPES[target]:
            s = client.actual_generation(zone, psr, start, end)
            if s:
                parts.append(s)
        if not parts:
            return {}
        hours = set(parts[0])
        for p in parts[1:]:
            hours &= set(p)
        return {
            h: {"v": sum(p[h]["v"] for p in parts), "c": min(p[h]["c"] for p in parts)}
            for h in sorted(hours)
        }
    if target == "load":
        s, _ = client.load(zone, start, end, process="A16")
        return s
    if target == "price":
        return client.day_ahead_prices(zone, start, end)
    raise ValueError(target)


def fetch_history(client: Client, zone: str, days: int, now: datetime | None = None) -> dict[str, Hourly]:
    now = now or utc_now()
    start = (now - timedelta(days=days)).replace(minute=0, second=0, microsecond=0)
    end = now + timedelta(hours=1)
    return {t: fetch_target(client, zone, t, start, end) for t in config.TARGETS[zone]}


def is_complete(series: Hourly, day: date, zone: str) -> bool:
    return all(h in series for h in delivery_hours(day, zone))


def last_complete_day(series: Hourly, zone: str, before: date) -> date | None:
    """The most recent local delivery day before `before` with every hour present."""
    d = before - timedelta(days=1)
    for _ in range(config.HISTORY_DAYS):
        if is_complete(series, d, zone):
            return d
        d -= timedelta(days=1)
    return None


def day_slice(series: Hourly, day: date, zone: str) -> dict[str, dict]:
    return {iso(h): series[h] for h in delivery_hours(day, zone) if h in series}


def store_day(zone: str, day: date, history: dict[str, Hourly], final: bool, fetched_at: datetime) -> bool:
    """Write the actuals of one delivery day if not already written. Returns whether it wrote."""
    path = store.actuals_path(zone, day.isoformat(), final)
    if path.exists():
        return False
    targets = {}
    for t, series in history.items():
        hours = day_slice(series, day, zone)
        targets[t] = {
            "unit": config.UNITS[t],
            "hours": hours,
            "complete": len(hours) == len(delivery_hours(day, zone)),
        }
    store.write_new(
        path,
        {
            "schema": 1,
            "zone": zone,
            "delivery_day": day.isoformat(),
            "stage": "final" if final else "preliminary",
            "fetched_at": iso(fetched_at),
            "targets": targets,
        },
    )
    return True


def capacity(client: Client, zone: str, year: int) -> dict[str, float | None]:
    """Installed capacity per target for nMAE, cached once per year.

    If the platform does not answer, the value is null and nMAE is not
    reported: an empty column beats a number nobody measured."""
    path = store.capacity_path(zone, year)
    cached = store.read(path)
    if cached:
        return cached["targets"]
    try:
        by_psr = client.installed_capacity(zone, year)
    except Exception:
        by_psr = {}
    targets = {}
    for t, psrs in config.PSR_TYPES.items():
        vals = [by_psr[p] for p in psrs if p in by_psr]
        targets[t] = sum(vals) if vals else None
    if any(v is not None for v in targets.values()):
        store.write_new(
            path,
            {"schema": 1, "zone": zone, "year": year, "fetched_at": iso(utc_now()), "unit": "MW", "by_psr": by_psr, "targets": targets},
        )
    return targets
