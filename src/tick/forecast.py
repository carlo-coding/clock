"""Issuing the daily forecast file.

One file per issue day, named by the UTC date it was issued, containing the
forecasts for every lead from day-ahead to seven days out, every zone and
every target. It is written once; if it exists, nothing happens. That is
the property the whole record rests on, and it is tested.

Phase 1 model (naive-v1). Deliberately trivial, because the scarce thing is
the dated series and the method improves forward. Three columns per hour:

- persistence  same UTC hour of the last complete delivery day
- weekly       same UTC hour seven days before the target (fourteen if missing)
- clock        the published forecast: one of the two above, or a 3-day mean,
               chosen per target in config.MODEL_RULE

"Same UTC hour" rather than "same local hour": across a daylight saving
switch this shifts by an hour, which is negligible for a naive baseline and
keeps the arithmetic free of calendar special cases.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from . import config, store
from .actuals import Hourly, fetch_history, last_complete_day
from .entsoe import Client
from .timeutil import delivery_hours, gate_closure, iso, local_date, utc_now


def _at(series: Hourly, t: datetime) -> float | None:
    e = series.get(t)
    return None if e is None else e["v"]


def _mean(vals: list[float | None]) -> float | None:
    xs = [v for v in vals if v is not None]
    return sum(xs) / len(xs) if xs else None


def baselines(series: Hourly, t: datetime, last_day: date | None, target_day: date, rule: str) -> dict[str, float | None]:
    gap = (target_day - last_day).days if last_day else None
    persistence = _at(series, t - timedelta(days=gap)) if gap else None
    weekly = _at(series, t - timedelta(days=7))
    if weekly is None:
        weekly = _at(series, t - timedelta(days=14))
    if rule == "persistence":
        clock = persistence
    elif rule == "weekly":
        clock = weekly
    elif rule == "mean3":
        clock = _mean([_at(series, t - timedelta(days=gap + k)) for k in range(3)]) if gap else None
    else:
        raise ValueError(rule)
    return {"clock": clock, "persistence": persistence, "weekly": weekly}


def build(history: dict[str, dict[str, Hourly]], now: datetime) -> dict:
    """The forecast document for an issue time, from fetched history."""
    issue_day = now.date()
    zones: dict = {}
    inputs: dict = {}
    for zone, by_target in history.items():
        base_day = local_date(now, zone)
        zones[zone] = {}
        inputs[zone] = {}
        for target, series in by_target.items():
            last_day = last_complete_day(series, zone, base_day + timedelta(days=1))
            through = max(series) if series else None
            inputs[zone][target] = {
                "last_complete_day": last_day.isoformat() if last_day else None,
                "actuals_through": iso(through) if through else None,
                "hours_in_history": len(series),
            }
            leads = {}
            for k in config.LEAD_DAYS:
                target_day = base_day + timedelta(days=k)
                hours = {}
                for t in delivery_hours(target_day, zone):
                    hours[iso(t)] = baselines(series, t, last_day, target_day, config.MODEL_RULE[target])
                leads[str(k)] = {"delivery_day": target_day.isoformat(), "hours": hours}
            zones[zone][target] = {"unit": config.UNITS[target], "leads": leads}
    day_ahead = min(local_date(now, z) for z in history) + timedelta(days=1)
    gc = gate_closure(day_ahead)
    return {
        "schema": 1,
        "issue_day": issue_day.isoformat(),
        "issued_at": iso(now),
        "model": config.MODEL_VERSION,
        "model_rule": config.MODEL_RULE,
        "code": store.git_sha(),
        "day_ahead": day_ahead.isoformat(),
        "gate_closure": iso(gc),
        "before_gate_closure": now < gc,
        "inputs": inputs,
        "zones": zones,
    }


def issue(client: Client | None = None, now: datetime | None = None, history: dict[str, dict[str, Hourly]] | None = None) -> tuple[str, bool]:
    """Write today's forecast file. Returns (path, written)."""
    now = now or utc_now()
    path = store.forecast_path(now.date().isoformat())
    if path.exists():
        return str(path), False
    # Between 22:00 and 24:00 UTC the local day in both zones is already the
    # next one, and a file issued then would carry the previous UTC date with
    # the wrong delivery day. The window for issuing is the UTC day itself.
    if any(local_date(now, z) != now.date() for z in config.ZONES):
        return str(path), False
    if history is None:
        client = client or Client()
        history = {zone: fetch_history(client, zone, config.HISTORY_DAYS, now) for zone in config.ZONES}
    doc = build(history, now)
    store.write_new(path, doc)
    return str(path), True
