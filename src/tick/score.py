"""Scoring a delivery day.

Every source is scored over the same hours, so the numbers are comparable:
an hour counts only if the actual and every source being compared have a
value for it. Solar is scored over daylight hours only (actual strictly
positive), decided once in config. The result is one file per delivery day
and stage, never rewritten.

Metrics: MAE and bias in the target's unit; nMAE as a share of installed
capacity for wind and solar, which is how the sector compares across zones
and years; MAPE for load. Skill against a reference is 1 - MAE/MAE_ref, so
positive means the clock beat it.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta

from . import config, store
from .timeutil import delivery_hours, iso, parse_iso, utc_now


def _metrics(pairs: list[tuple[float, float]], target: str, cap: float | None) -> dict:
    """pairs of (forecast, actual)."""
    n = len(pairs)
    if n == 0:
        return {"n": 0}
    errs = [f - a for f, a in pairs]
    mae = sum(abs(e) for e in errs) / n
    out = {
        "n": n,
        "mae": round(mae, 3),
        "bias": round(sum(errs) / n, 3),
        "rmse": round(math.sqrt(sum(e * e for e in errs) / n), 3),
    }
    if target in ("wind", "solar") and cap:
        out["nmae_pct"] = round(100 * mae / cap, 3)
    if target == "load":
        out["mape_pct"] = round(100 * sum(abs(e) / a for e, (_, a) in zip(errs, pairs) if a) / n, 3)
    return out


def _skill(a: dict, b: dict) -> float | None:
    if a.get("n") and b.get("n") and b["mae"] > 0:
        return round(1 - a["mae"] / b["mae"], 4)
    return None


def forecasts_for(day: date) -> dict[str, dict[str, dict[str, dict]]]:
    """{zone: {target: {lead: hours}}} gathered from the issue files that targeted this day."""
    out: dict = {}
    for k in config.LEAD_DAYS:
        for back in (k, k + 1):  # the issue UTC date can lag the local base day by one
            doc = store.read(store.forecast_path((day - timedelta(days=back)).isoformat()))
            if not doc:
                continue
            for zone, targets in doc["zones"].items():
                for target, body in targets.items():
                    lead = body["leads"].get(str(k))
                    if lead and lead["delivery_day"] == day.isoformat():
                        out.setdefault(zone, {}).setdefault(target, {})[str(k)] = {
                            "hours": lead["hours"],
                            "issued_at": doc["issued_at"],
                            "before_gate_closure": doc["before_gate_closure"] if k == 1 else None,
                            "model": doc["model"],
                        }
    return out


def _usable_hours(actual_hours: dict, target: str) -> dict[str, float]:
    vals = {h: e["v"] for h, e in actual_hours.items()}
    if target == "solar" and config.SOLAR_DAYLIGHT_ONLY:
        vals = {h: v for h, v in vals.items() if v > 0}
    return vals


def score_intraday(day: date, zone: str, actuals: dict, cap_by_target: dict) -> dict:
    """Intraday persistence against the official intraday forecast, by lead hour."""
    lines = store.read_lines(store.intraday_path(zone, day.isoformat())) + store.read_lines(
        store.intraday_path(zone, (day - timedelta(days=1)).isoformat())
    )
    day_hours = {iso(h) for h in delivery_hours(day, zone)}
    out = {}
    for target in ("wind", "solar", "load"):
        if target not in actuals:
            continue
        usable = _usable_hours(actuals[target]["hours"], target)
        # lead -> list of (clock, official or None, actual)
        by_lead: dict[int, list[tuple[float, float | None, float]]] = {}
        for line in lines:
            fc = line.get("forecasts", {}).get(target, {})
            for h, entry in fc.items():
                if h not in day_hours or h not in usable or entry.get("clock") is None:
                    continue
                by_lead.setdefault(int(entry["lead_h"]), []).append((entry["clock"], entry.get("official_intraday"), usable[h]))
        if not by_lead:
            continue
        cap = cap_by_target.get(target)
        leads = {}
        for lead, rows in sorted(by_lead.items()):
            clock = _metrics([(c, a) for c, _, a in rows], target, cap)
            with_off = [(c, o, a) for c, o, a in rows if o is not None]
            entry = {"clock": clock}
            if with_off:
                # Compared over the same hours, which is the only fair comparison.
                clock_common = _metrics([(c, a) for c, _, a in with_off], target, cap)
                off = _metrics([(o, a) for _, o, a in with_off], target, cap)
                entry.update({"clock_on_official_hours": clock_common, "official_intraday": off, "skill_vs_official_intraday": _skill(clock_common, off)})
            leads[str(lead)] = entry
        out[target] = leads
    return out


def ready(day: date, final: bool) -> bool:
    """A day is scored for a stage only once every zone has its actuals for it.

    The score file is written once. Scoring as soon as one zone had actuals
    froze the other zone out of the preliminary score for good: from 19 to 21
    September 2026 the platform published DE-LU generation a day late, Spain
    was complete, and the DE-LU day-ahead went unscored until the final stage.
    Waiting costs at most a day, because actuals older than yesterday are
    stored whether or not the platform completed them."""
    if not forecasts_for(day):
        return False
    return all(store.actuals_path(z, day.isoformat(), final).exists() for z in config.ZONES)


def score_day(day: date, final: bool, now: datetime | None = None) -> tuple[str, bool]:
    """Write the score file for a delivery day and stage. Returns (path, written)."""
    now = now or utc_now()
    path = store.score_path(day.isoformat(), final)
    if path.exists():
        return str(path), False
    fcs = forecasts_for(day)
    zones: dict = {}
    for zone in config.ZONES:
        act = store.read(store.actuals_path(zone, day.isoformat(), final))
        if not act:
            zones[zone] = {"note": "no actuals stored for this stage"}
            continue
        cap_doc = store.read(store.capacity_path(zone, day.year)) or {}
        cap_by_target = cap_doc.get("targets", {})
        zones[zone] = {}
        for target in config.TARGETS[zone]:
            if target not in act["targets"]:
                continue
            usable = _usable_hours(act["targets"][target]["hours"], target)
            cap = cap_by_target.get(target)
            official = store.read(store.official_path(zone, day.isoformat(), target))
            leads_out = {}
            for lead, fc in sorted(fcs.get(zone, {}).get(target, {}).items(), key=lambda kv: int(kv[0])):
                sources = ["clock", "persistence", "weekly"]
                cols = {s: {h: e[s] for h, e in fc["hours"].items() if e.get(s) is not None} for s in sources}
                if lead == "1" and official:
                    cols["official"] = {h: e["v"] for h, e in official["hours"].items()}
                common = set(usable)
                for s, col in cols.items():
                    common &= set(col)
                common = sorted(common)
                res = {s: _metrics([(col[h], usable[h]) for h in common], target, cap) for s, col in cols.items()}
                entry = {
                    "hours_scored": len(common),
                    "hours_in_day": len(delivery_hours(day, zone)),
                    "issued_at": fc["issued_at"],
                    "model": fc["model"],
                    **res,
                    "skill_vs_persistence": _skill(res["clock"], res["persistence"]),
                    "skill_vs_weekly": _skill(res["clock"], res["weekly"]),
                }
                if lead == "1":
                    entry["before_gate_closure"] = fc["before_gate_closure"]
                    if official:
                        entry["skill_vs_official"] = _skill(res["clock"], res["official"])
                        entry["official_first_seen_at"] = official["first_seen_at"]
                        entry["official_complete"] = official["complete"]
                    else:
                        entry["official"] = None
                        entry["official_note"] = "not captured" if target != "price" else "no official forecast exists for price"
                leads_out[lead] = entry
            zones[zone][target] = {
                "unit": config.UNITS[target],
                "capacity_mw": cap,
                "actuals_complete": act["targets"][target]["complete"],
                "actuals_fetched_at": act["fetched_at"],
                "daylight_only": target == "solar" and config.SOLAR_DAYLIGHT_ONLY,
                "leads": leads_out,
            }
        intraday = score_intraday(day, zone, act["targets"], cap_by_target)
        if intraday:
            zones[zone]["intraday"] = intraday
    store.write_new(
        path,
        {
            "schema": 1,
            "delivery_day": day.isoformat(),
            "stage": "final" if final else "preliminary",
            "scored_at": iso(now),
            "code": store.git_sha(),
            "zones": zones,
        },
    )
    return str(path), True


def revision_drift(day: date) -> dict | None:
    """How much the actuals moved between the preliminary and the final fetch. Informational."""
    out = {}
    for zone in config.ZONES:
        a = store.read(store.actuals_path(zone, day.isoformat(), False))
        b = store.read(store.actuals_path(zone, day.isoformat(), True))
        if not a or not b:
            continue
        for t in config.TARGETS[zone]:
            ha, hb = a["targets"].get(t, {}).get("hours", {}), b["targets"].get(t, {}).get("hours", {})
            common = set(ha) & set(hb)
            if common:
                out[f"{zone}/{t}"] = round(sum(abs(ha[h]["v"] - hb[h]["v"]) for h in common) / len(common), 3)
    return out or None
