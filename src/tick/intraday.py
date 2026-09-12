"""Hourly intraday record.

Every hour, for wind, solar and load: the last actual hour the platform
has, a persistence forecast for the next 1, 2, 4 and 8 hours from it, and
a snapshot of the TSO's intraday forecast for those same hours. Appended as
one line to the day's file; earlier lines are never touched.

This is not the headline and it is silent on the front page until it has
something to say. It exists because a horizon added later starts later.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from . import config, store
from .actuals import fetch_target
from .entsoe import Client
from .timeutil import iso, utc_now

INTRADAY_TARGETS = ("wind", "solar", "load")


def run(client: Client | None = None, now: datetime | None = None) -> list[str]:
    now = now or utc_now()
    client = client or Client()
    written = []
    horizon = max(config.INTRADAY_LEAD_HOURS)
    for zone in config.ZONES:
        targets = [t for t in INTRADAY_TARGETS if t in config.TARGETS[zone]]
        official, rev = client.generation_forecast(zone, now - timedelta(hours=2), now + timedelta(hours=horizon + 2), process="A40")
        line = {"run_at": iso(now), "zone": zone, "entsoe_intraday_revision": rev, "last_actual": {}, "forecasts": {}}
        for t in targets:
            series = fetch_target(client, zone, t, now - timedelta(hours=8), now + timedelta(hours=1))
            if not series:
                line["last_actual"][t] = None
                continue
            t0 = max(series)
            v0 = series[t0]["v"]
            line["last_actual"][t] = iso(t0)
            off_hours: dict = {}
            if t in config.PSR_TYPES:
                parts = [official[p] for p in config.PSR_TYPES[t] if p in official]
                if parts:
                    hours = set(parts[0])
                    for p in parts[1:]:
                        hours &= set(p)
                    off_hours = {h: sum(p[h]["v"] for p in parts) for h in hours}
            fc = {}
            for lead in config.INTRADAY_LEAD_HOURS:
                h = t0 + timedelta(hours=lead)
                fc[iso(h)] = {"lead_h": lead, "clock": v0, "official_intraday": off_hours.get(h)}
            line["forecasts"][t] = fc
        written.append(str(store.append_line(store.intraday_path(zone, now.date().isoformat()), line)))
    return written
