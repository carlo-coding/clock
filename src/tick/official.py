"""Capturing the TSO's own day-ahead forecast, with the time it appeared.

The regulation sets 18:00 CET the day before delivery as the deadline for
publishing these forecasts, not the time they actually appear, and the
platform does not say when a value arrived. Comparing against a number
without knowing when it was available is the same mistake as signing with
GPG and calling it a timestamp. So the official forecast is polled through
the day and stored, once, together with the clock time at which the full
delivery day was first seen. That time cannot be reconstructed later.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from . import config, store
from .entsoe import Client
from .timeutil import delivery_bounds, delivery_hours, iso, local_date, utc_now

# Targets with an official day-ahead forecast on the platform. Price has none.
OFFICIAL_TARGETS = ("wind", "solar", "load")


def _complete(hours: dict, day, zone) -> bool:
    return all(h in hours for h in delivery_hours(day, zone))


def capture(client: Client | None = None, now: datetime | None = None) -> list[str]:
    """Store the official forecast for tomorrow's delivery day where it has appeared. Returns paths written."""
    now = now or utc_now()
    client = client or Client()
    written = []
    for zone in config.ZONES:
        day = local_date(now, zone) + timedelta(days=1)
        wanted = [t for t in OFFICIAL_TARGETS if t in config.TARGETS[zone] and not store.official_path(zone, day.isoformat(), t).exists()]
        if not wanted:
            continue
        start, end = delivery_bounds(day, zone)
        fetched: dict[str, tuple[dict, int | None]] = {}
        if "wind" in wanted or "solar" in wanted:
            by_psr, rev = client.generation_forecast(zone, start, end, process="A01")
            for t in ("wind", "solar"):
                parts = [by_psr[p] for p in config.PSR_TYPES[t] if p in by_psr]
                if not parts:
                    fetched[t] = ({}, rev)
                    continue
                hours = set(parts[0])
                for p in parts[1:]:
                    hours &= set(p)
                fetched[t] = ({h: {"v": sum(p[h]["v"] for p in parts), "c": min(p[h]["c"] for p in parts)} for h in sorted(hours)}, rev)
        if "load" in wanted:
            fetched["load"] = client.load(zone, start, end, process="A01")
        for t in wanted:
            hours, rev = fetched[t]
            complete = bool(hours) and _complete(hours, day, zone)
            # Delivery has started and the platform never completed the day:
            # store what there is, marked incomplete, so the gap is on record.
            delivery_started = now >= start
            if not complete and not delivery_started:
                continue
            path = store.write_new(
                store.official_path(zone, day.isoformat(), t),
                {
                    "schema": 1,
                    "zone": zone,
                    "target": t,
                    "delivery_day": day.isoformat(),
                    "process": "A01",
                    "unit": config.UNITS[t],
                    "first_seen_at": iso(now),
                    "complete": complete,
                    "entsoe_revision": rev,
                    "hours": {iso(h): v for h, v in sorted(hours.items())},
                },
            )
            written.append(str(path))
    return written
