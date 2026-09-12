"""A thin client for the ENTSO-E transparency platform REST API.

Written from scratch rather than taken from a library for two reasons. The
record has to be reproducible for years, and a dependency that changes how
it fills gaps or handles the daylight saving switch changes the record
silently. And the parts that matter fit in one file: fetch, parse the
document, forward-fill the curve, aggregate to hours, and say what is
missing instead of filling it.

Document types used (Regulation (EU) 543/2013 article in brackets):

- A69  wind and solar forecast, day-ahead (A01) and intraday (A40)   [14.1.D]
- A75  actual generation per production type (A16)                  [16.1.B/C]
- A65  total load, day-ahead forecast (A01) and actual (A16)          [6.1.B/A]
- A44  day-ahead prices                                               [12.1.D]
- A68  installed generation capacity per type, yearly (A33)           [14.1.A]
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import requests

from . import config
from .timeutil import UTC, entsoe_param, floor_hour

BASE_URL = "https://web-api.tp.entsoe.eu/api"
TIMEOUT = 120
RETRIES = 4

# The platform publishes at 15 minute resolution for these zones. Everything
# is aggregated to hourly means; an hour is kept only if at least this share
# of its sub-periods is present, and the share is stored next to the value.
MIN_COVERAGE = 0.5

_RESOLUTION = {"PT15M": 15, "PT30M": 30, "PT60M": 60, "P1D": 24 * 60, "P1Y": None}


class EntsoeError(RuntimeError):
    pass


@dataclass
class Series:
    psr_type: str | None
    business_type: str | None
    classification: int | None
    in_domain: str | None
    out_domain: str | None
    resolution_minutes: int | None
    points: dict[datetime, float] = field(default_factory=dict)  # native resolution, forward-filled


@dataclass
class Document:
    revision: int | None
    series: list[Series]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(el: ET.Element, name: str) -> str | None:
    for c in el:
        if _local(c.tag) == name:
            return (c.text or "").strip()
    return None


def _find_text(el: ET.Element, name: str) -> str | None:
    for c in el.iter():
        if _local(c.tag) == name:
            return (c.text or "").strip()
    return None


def _parse_time(s: str) -> datetime:
    # The platform writes 2026-09-11T22:00Z; be tolerant of seconds.
    s = s.replace("Z", "")
    fmt = "%Y-%m-%dT%H:%M:%S" if s.count(":") == 2 else "%Y-%m-%dT%H:%M"
    return datetime.strptime(s, fmt).replace(tzinfo=UTC)


def parse_document(text: str) -> Document:
    """Parse a GL_MarketDocument or Publication_MarketDocument.

    An Acknowledgement_MarketDocument (the platform's "no data" answer)
    yields an empty document rather than an error: no data is a fact worth
    recording, not a failure."""
    root = ET.fromstring(text)
    if _local(root.tag) == "Acknowledgement_MarketDocument":
        return Document(revision=None, series=[])

    rev = _child_text(root, "revisionNumber")
    doc = Document(revision=int(rev) if rev and rev.isdigit() else None, series=[])

    for ts in root:
        if _local(ts.tag) != "TimeSeries":
            continue
        cls = _find_text(ts, "classificationSequence_AttributeInstanceComponent.position")
        s = Series(
            psr_type=_find_text(ts, "psrType"),
            business_type=_child_text(ts, "businessType"),
            classification=int(cls) if cls and cls.isdigit() else None,
            in_domain=_child_text(ts, "inBiddingZone_Domain.mRID") or _child_text(ts, "in_Domain.mRID"),
            out_domain=_child_text(ts, "outBiddingZone_Domain.mRID") or _child_text(ts, "out_Domain.mRID"),
            resolution_minutes=None,
        )
        curve = _child_text(ts, "curveType") or "A01"
        for period in ts:
            if _local(period.tag) != "Period":
                continue
            res = _child_text(period, "resolution") or "PT60M"
            minutes = _RESOLUTION.get(res)
            interval = next(c for c in period if _local(c.tag) == "timeInterval")
            start = _parse_time(_child_text(interval, "start"))
            end = _parse_time(_child_text(interval, "end"))
            raw: dict[int, float] = {}
            for p in period:
                if _local(p.tag) != "Point":
                    continue
                pos = int(_child_text(p, "position"))
                val = _child_text(p, "quantity")
                if val is None:
                    val = _child_text(p, "price.amount")
                if val is None or val == "":
                    continue
                raw[pos] = float(val)
            if minutes is None:
                # Yearly figures (installed capacity): a single value per period.
                if raw:
                    s.points[start] = raw[min(raw)]
                continue
            s.resolution_minutes = minutes
            n = int((end - start).total_seconds() // 60 // minutes)
            last: float | None = None
            for pos in range(1, n + 1):
                if pos in raw:
                    last = raw[pos]
                elif curve != "A03":
                    # Curve A01 lists every point; a missing one is a real gap.
                    last = None
                if last is not None:
                    s.points[start + timedelta(minutes=minutes * (pos - 1))] = last
        doc.series.append(s)
    return doc


def hourly(points: dict[datetime, float], resolution_minutes: int | None) -> dict[datetime, dict]:
    """Aggregate native points to hourly means with a coverage share.

    Output: {hour_utc: {"v": mean, "c": share_of_hour_present}}. Hours below
    MIN_COVERAGE are dropped: a gap is a gap."""
    if not points:
        return {}
    res = resolution_minutes or 60
    per_hour = 60 // res
    buckets: dict[datetime, list[float]] = {}
    for t, v in points.items():
        buckets.setdefault(floor_hour(t), []).append(v)
    out = {}
    for h, vals in sorted(buckets.items()):
        c = min(1.0, len(vals) / per_hour)
        if c >= MIN_COVERAGE:
            out[h] = {"v": sum(vals) / len(vals), "c": round(c, 4)}
    return out


class Client:
    def __init__(self, token: str | None = None):
        self.token = token or config.entsoe_token()
        self.session = requests.Session()

    def get(self, **params) -> Document:
        q = {"securityToken": self.token, **params}
        last_err: Exception | None = None
        for attempt in range(RETRIES):
            try:
                r = self.session.get(BASE_URL, params=q, timeout=TIMEOUT)
            except requests.RequestException as e:
                last_err = e
                time.sleep(5 * (attempt + 1))
                continue
            if r.status_code == 401:
                raise EntsoeError("ENTSO-E rejected the token (401)")
            if r.status_code == 400:
                # Bad request or no data; the body says which.
                body = re.sub(r"\s+", " ", r.text)[:300]
                if "No matching data" in body:
                    return Document(revision=None, series=[])
                raise EntsoeError(f"ENTSO-E 400: {body}")
            if r.status_code >= 500 or r.status_code == 429:
                last_err = EntsoeError(f"ENTSO-E {r.status_code}")
                time.sleep(10 * (attempt + 1))
                continue
            r.raise_for_status()
            return parse_document(r.text)
        raise EntsoeError(f"ENTSO-E unreachable after {RETRIES} attempts: {last_err}")

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    def generation_forecast(self, zone: str, start: datetime, end: datetime, process: str = "A01") -> tuple[dict[str, dict], int | None]:
        """Wind and solar forecast per production type. process A01 = day-ahead, A40 = intraday."""
        doc = self.get(
            documentType="A69",
            processType=process,
            in_Domain=config.ZONES[zone]["eic"],
            periodStart=entsoe_param(start),
            periodEnd=entsoe_param(end),
        )
        out: dict[str, dict] = {}
        for s in doc.series:
            if s.psr_type:
                out.setdefault(s.psr_type, {}).update(hourly(s.points, s.resolution_minutes))
        return out, doc.revision

    def actual_generation(self, zone: str, psr: str, start: datetime, end: datetime) -> dict[datetime, dict]:
        doc = self.get(
            documentType="A75",
            processType="A16",
            psrType=psr,
            in_Domain=config.ZONES[zone]["eic"],
            periodStart=entsoe_param(start),
            periodEnd=entsoe_param(end),
        )
        pts: dict[datetime, float] = {}
        res = None
        for s in doc.series:
            # Series with an out-domain are consumption (pumping); generation has an in-domain.
            if s.out_domain and not s.in_domain:
                continue
            pts.update(s.points)
            res = s.resolution_minutes or res
        return hourly(pts, res)

    def installed_capacity(self, zone: str, year: int) -> dict[str, float]:
        doc = self.get(
            documentType="A68",
            processType="A33",
            in_Domain=config.ZONES[zone]["eic"],
            periodStart=f"{year}01010000",
            periodEnd=f"{year}12310000",
        )
        out = {}
        for s in doc.series:
            if s.psr_type and s.points:
                out[s.psr_type] = list(s.points.values())[0]
        return out

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def load(self, zone: str, start: datetime, end: datetime, process: str) -> tuple[dict[datetime, dict], int | None]:
        """Total load. process A01 = day-ahead forecast, A16 = actual."""
        doc = self.get(
            documentType="A65",
            processType=process,
            outBiddingZone_Domain=config.ZONES[zone]["eic"],
            periodStart=entsoe_param(start),
            periodEnd=entsoe_param(end),
        )
        pts: dict[datetime, float] = {}
        res = None
        for s in doc.series:
            pts.update(s.points)
            res = s.resolution_minutes or res
        return hourly(pts, res), doc.revision

    # ------------------------------------------------------------------
    # Prices
    # ------------------------------------------------------------------

    def day_ahead_prices(self, zone: str, start: datetime, end: datetime) -> dict[datetime, dict]:
        """Day-ahead price, hourly mean of the quarter-hour results.

        The platform returns more than one series for some zones (the
        classificationSequence attribute tells them apart). Sequence 1 is
        taken when present, the lowest sequence otherwise. Decided once."""
        eic = config.ZONES[zone]["eic"]
        doc = self.get(
            documentType="A44",
            in_Domain=eic,
            out_Domain=eic,
            periodStart=entsoe_param(start),
            periodEnd=entsoe_param(end),
        )
        if not doc.series:
            return {}
        by_cls: dict[int, list[Series]] = {}
        for s in doc.series:
            by_cls.setdefault(s.classification or 1, []).append(s)
        chosen = by_cls[min(by_cls)]
        pts: dict[datetime, float] = {}
        res = None
        for s in chosen:
            pts.update(s.points)
            res = s.resolution_minutes or res
        return hourly(pts, res)
