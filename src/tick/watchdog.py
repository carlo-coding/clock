"""The dead man's switch.

An automated series does not fail loudly: it stops, and nobody notices for
months. This check runs on its own schedule, independent of the jobs that
write, and fails (non-zero, which GitHub turns into an email and an issue)
if the newest forecast is older than 48 hours or today's is missing past
its slot. Past gaps are listed but do not fail: they are permanent and
already on record.

`simulate=True` forces a failure. The exit criterion for this project is a
provoked interruption that fires the alert: if it does not fire, the clock
is not finished.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from . import config
from .timeutil import utc_now

GRACE_HOURS = 4  # after the issue slot before today's absence counts


def issue_days() -> list[date]:
    if not config.FORECASTS.exists():
        return []
    return sorted(date.fromisoformat(p.stem) for p in config.FORECASTS.glob("????-??-??.json"))


def gaps(days: list[date]) -> list[date]:
    if len(days) < 2:
        return []
    have = set(days)
    d = days[0]
    out = []
    while d <= days[-1]:
        if d not in have:
            out.append(d)
        d += timedelta(days=1)
    return out


def check(now: datetime | None = None, simulate: bool = False) -> tuple[list[str], list[str]]:
    """Returns (problems, notes). Any problem means the alert fires."""
    now = now or utc_now()
    problems, notes = [], []
    days = issue_days()
    if not days:
        problems.append("no forecast has ever been issued")
        return problems, notes
    latest = days[-1]
    notes.append(f"latest forecast issued {latest.isoformat()}, {len(days)} issue days on record since {days[0].isoformat()}")
    age_h = (now - datetime(latest.year, latest.month, latest.day, config.ISSUE_HOUR_UTC, tzinfo=now.tzinfo)).total_seconds() / 3600
    if age_h >= 48:
        problems.append(f"no forecast for {age_h:.0f} hours: last issue day was {latest.isoformat()}")
    elif latest < now.date() and now.hour >= config.ISSUE_HOUR_UTC + GRACE_HOURS:
        problems.append(f"today's forecast ({now.date().isoformat()}) is missing past its slot")
    g = gaps(days)
    if g:
        notes.append("permanent gaps on record: " + ", ".join(d.isoformat() for d in g))
    if simulate:
        problems.append("simulated gap (requested by hand to test that the alert fires)")
    return problems, notes
