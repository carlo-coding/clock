"""Everything that is a decision rather than a computation lives here.

The rule of the series is that decisions are made once and written down.
Changing a value in this file changes what gets published from that day on,
and never what was already published: the files under data/ are immutable.
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
FORECASTS = DATA / "forecasts"
OFFICIAL = DATA / "official"
ACTUALS = DATA / "actuals"
SCORES = DATA / "scores"
INTRADAY = DATA / "intraday"
CAPACITY = DATA / "capacity"
SITE = ROOT / "site"

REPO_URL = "https://github.com/carlo-coding/clock"
SITE_URL = "https://clock.krafta.pro"
SITE_DOMAIN = "clock.krafta.pro"

# ---------------------------------------------------------------------------
# What is forecast
# ---------------------------------------------------------------------------

# Bidding zones, by their ENTSO-E EIC code. Both share the CET/CEST calendar,
# so a "delivery day" is midnight to midnight in that zone's local time and
# has 23, 24 or 25 hours depending on the daylight saving switch.
ZONES: dict[str, dict] = {
    "DE-LU": {"eic": "10Y1001A1001A82H", "tz": "Europe/Berlin", "name": "Germany and Luxembourg"},
    "ES": {"eic": "10YES-REE------0", "tz": "Europe/Madrid", "name": "Spain"},
}

# Targets per zone. Only targets for which the TSO publishes a day-ahead
# forecast on the ENTSO-E transparency platform have a citable rival; price
# has none and is published as a dated series only.
TARGETS: dict[str, list[str]] = {
    "DE-LU": ["wind", "solar", "load", "price"],
    "ES": ["wind", "solar"],
}

# The one number on the front page. It never moves: a headline that moves
# resets the day counter, which is the only property the series cannot lose.
HEADLINE = {"zone": "DE-LU", "target": "wind"}

# ENTSO-E production types that make up each generation target.
PSR_TYPES: dict[str, list[str]] = {
    "wind": ["B19", "B18"],  # onshore, offshore (offshore absent in ES: that is fine)
    "solar": ["B16"],
}

UNITS = {"wind": "MW", "solar": "MW", "load": "MW", "price": "EUR/MWh"}

# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------

# Forecasts are issued once a day at a fixed UTC time, well before the
# day-ahead market gate closure (12:00 CET/CEST, i.e. 11:00 UTC in winter
# and 10:00 UTC in summer). Cron runs in UTC and cannot follow daylight
# saving, so the cutoff is fixed in UTC and the file records whether it was
# met. A late issue is still published: a flagged late file beats a hole.
#
# GitHub does not honour cron times under load: on the first day the 07:40
# slot ran at 11:42, after gate closure; on the second, every slot ran five
# hours late. So the slots run hourly from 00:17 to 07:17, every scheduled
# workflow issues the day's file if it is missing, and all of that is a
# no-op once the file exists. Ten hours of slack before the summer gate.
ISSUE_HOUR_UTC = 0
GATE_CLOSURE_LOCAL_HOUR = 12  # in Europe/Berlin, converted per day

# Lead times in days. Lead 1 is day-ahead, the headline. Leads 2 to 7 are
# stored from day one because a horizon added later starts its series later.
LEAD_DAYS = list(range(1, 8))

# Intraday leads, in hours ahead of the last actual available at run time.
INTRADAY_LEAD_HOURS = [1, 2, 4, 8]

# The actual value is scored twice and neither is overwritten: once the day
# after delivery, with the preliminary figure, and once at this lag, with the
# revised one. The difference between the two is itself data.
FINAL_LAG_DAYS = 7

# Days of history fetched on every run. Enough for the weekly baseline of the
# lead-7 forecast plus a margin for gaps.
HISTORY_DAYS = 16

# ---------------------------------------------------------------------------
# Models and metrics
# ---------------------------------------------------------------------------

# Phase 1 model. Deliberately trivial: the scarce thing is the dated series,
# and the method improves forward. The name is written into every file so a
# change of method is visible in the record.
MODEL_VERSION = "naive-v1"

# What "clock" means per target under naive-v1. Baselines "persistence"
# (same hour of the last complete delivery day) and "weekly" (same hour,
# seven days before the target) are always published alongside.
MODEL_RULE = {
    "wind": "persistence",
    "solar": "mean3",  # mean of the same hour over the last three complete days
    "load": "weekly",
    "price": "weekly",
}

# Solar is scored over daylight hours only, defined as hours where the actual
# generation is strictly positive. Night hours are trivially zero and inflate
# any metric. Decided once, here.
SOLAR_DAYLIGHT_ONLY = True

# The front page reports the headline number only once at least this many
# delivery days have been scored. A score over four days measures nothing
# and looks like it does.
MIN_DAYS_FOR_HEADLINE = 30

# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------


def entsoe_token() -> str:
    """The API token, from the environment or from a local .env file.

    In GitHub Actions it comes from the ENTSOE_API repository secret. Locally
    a .env with ENTSOE_API=... is enough; the file is git-ignored."""
    token = os.environ.get("ENTSOE_API")
    if token:
        return token.strip()
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("ENTSOE_API="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("ENTSOE_API is not set and no .env file was found")
