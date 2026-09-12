"""Time is where forecasting records go wrong, so it gets its own module.

Three conventions, applied everywhere:

- Every timestamp stored on disk is UTC, ISO 8601, with a trailing Z.
- A "delivery day" is midnight to midnight in the zone's local calendar
  (CET/CEST for both zones). It has 23, 24 or 25 hours on the two daylight
  saving days of the year, and the code never assumes 24.
- The day-ahead gate closure is 12:00 local on the day before delivery.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config

UTC = timezone.utc


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def iso(dt: datetime) -> str:
    """UTC ISO string with a Z, seconds precision."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def entsoe_param(dt: datetime) -> str:
    """The YYYYMMDDHHMM form the ENTSO-E API wants, in UTC."""
    return dt.astimezone(UTC).strftime("%Y%m%d%H%M")


def zone_tz(zone: str) -> ZoneInfo:
    return ZoneInfo(config.ZONES[zone]["tz"])


def local_date(dt: datetime, zone: str) -> date:
    return dt.astimezone(zone_tz(zone)).date()


def delivery_bounds(day: date, zone: str) -> tuple[datetime, datetime]:
    """UTC start (inclusive) and end (exclusive) of a local delivery day."""
    tz = zone_tz(zone)
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    nxt = day + timedelta(days=1)
    end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=tz)
    return start.astimezone(UTC), end.astimezone(UTC)


def delivery_hours(day: date, zone: str) -> list[datetime]:
    """The UTC hour stamps of a local delivery day: 23, 24 or 25 of them."""
    start, end = delivery_bounds(day, zone)
    hours = []
    t = start
    while t < end:
        hours.append(t)
        t += timedelta(hours=1)
    return hours


def gate_closure(delivery_day: date) -> datetime:
    """Day-ahead market gate closure for a delivery day: 12:00 CET/CEST the day before."""
    tz = ZoneInfo("Europe/Berlin")
    d = delivery_day - timedelta(days=1)
    return datetime(d.year, d.month, d.day, config.GATE_CLOSURE_LOCAL_HOUR, tzinfo=tz).astimezone(UTC)


def floor_hour(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
