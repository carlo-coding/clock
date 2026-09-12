# clock

A public forecasting record for European power. Every day a forecast, the next day the score, every file timestamped in the Bitcoin blockchain and never edited.

Live page: [clock.krafta.pro](https://clock.krafta.pro). The page is the shop window; this repository is the record.

## What it does

Once a day, before the day-ahead market closes, a scheduled job:

1. downloads the latest actuals from the [ENTSO-E transparency platform](https://transparency.entsoe.eu),
2. writes a forecast for every hour of the next seven delivery days, for every zone and target,
3. commits it here and anchors its hash with [OpenTimestamps](https://opentimestamps.org),
4. downloads what actually happened yesterday and scores every source over the same hours,
5. rebuilds the page.

Seven days later it scores the day again against the revised actuals, in a second file. Nothing is overwritten: what was said stays said.

Through the day, another job polls the platform for the TSO's own day-ahead forecast and stores it with the time it was first seen complete. The regulation sets a deadline for publishing it (18:00 CET the day before), not a time; a comparison against a number of unknown availability is not a comparison, so the availability is measured.

Every hour, a third job appends the intraday record: the last actual on the platform, a persistence forecast for 1, 2, 4 and 8 hours ahead, and the operator's intraday forecast for those hours.

## Why

A forecasting track record cannot be built backwards. Anyone can show a good backtest; nobody can show three years of forecasts that were published before the fact unless they published them. Two defects make most such records worthless, and this one is designed against both:

- **A commit date proves nothing.** It is set by whoever commits. A GPG signature proves who, not when. So every file's hash goes into the Bitcoin blockchain through OpenTimestamps, and anyone can verify it forever without trusting this repository or its author.
- **"Up or down" proves nothing either.** Skill is only meaningful against a public, verifiable baseline. The one that matters is the forecast the grid operator itself publishes on ENTSO-E, and the headline is the clock against it, on the same hours, with the persistence and weekly baselines beside them.

## What is forecast

| Zone | Targets | Official forecast to compare against |
|---|---|---|
| DE-LU (Germany and Luxembourg) | wind, solar, load, day-ahead price | wind, solar, load |
| ES (Spain) | wind, solar | wind, solar |

The headline is **DE-LU wind, day-ahead, against the TSO's own forecast**, as nMAE (share of installed capacity). It never moves: a headline that moves resets the day counter. Price has no official forecast anywhere and is a dated series only.

Leads 2 to 7 days are recorded from day one with no rival to compare against, because a horizon added later starts its series later.

## Decisions, made once

- **Issue time.** 07:40 UTC, which is before the 12:00 CET/CEST gate closure all year. Cron cannot follow daylight saving, so the cutoff is fixed in UTC and every file records `before_gate_closure`. A late file is published and flagged; a flagged file beats a hole.
- **Phase 1 model is naive** (`naive-v1`): wind is persistence (same UTC hour of the last complete day), solar the mean of the last three days, load and price the same hour a week earlier. Persistence and weekly baselines are always published beside the clock. The scarce thing is the dated series; the method improves forward, and the model name is written into every file so a change is visible.
- **Hourly, UTC, from quarter-hours.** The platform publishes 15-minute values; they are averaged to hours. An hour is kept only if at least half of it is present, and the coverage is stored next to the value. A gap is a gap; nothing is interpolated.
- **Delivery days follow the CET/CEST calendar** and have 23, 24 or 25 hours on the two switch days. The code never assumes 24.
- **Solar is scored over daylight hours only** (actual strictly positive). Night hours are trivially zero and inflate any metric.
- **Two scores per day.** Preliminary the day after, final seven days later, in separate files. The drift between them is published.
- **Same hours for every source.** An hour counts only if the actual and every source being compared have it.
- **Wind is onshore plus offshore** where both exist. **Price** takes the platform's classification sequence 1 when it publishes more than one series.
- **The front page withholds the headline number until 30 days** have been scored against the operator's forecast. A score over a handful of days measures nothing and looks like it does.

## The record

```
data/
  forecasts/YYYY-MM-DD.json        issued that UTC day: leads 1..7, all zones and targets, model, inputs, gate closure flag
  official/ZONE/YYYY-MM-DD/T.json  the TSO's day-ahead forecast for that delivery day, with first_seen_at
  actuals/ZONE/YYYY-MM-DD.json     preliminary actuals, fetched the day after
  actuals/ZONE/YYYY-MM-DD.final.json  revised actuals, fetched seven days later
  scores/YYYY-MM-DD.json           preliminary scores of that delivery day, every lead and source
  scores/YYYY-MM-DD.final.json     the same against the final actuals
  intraday/ZONE/YYYY-MM-DD.jsonl   one line per hourly run, append only
  capacity/ZONE-YYYY.json          installed capacity used for nMAE
```

Every `.json` has a `.ots` proof beside it. Files are written once; the code has no update path and the test suite checks that writing to an existing file fails.

## Verify a timestamp

```
pip install opentimestamps-client
ots verify data/forecasts/2026-09-12.json.ots
```

Or drop the file and its proof at [opentimestamps.org](https://opentimestamps.org). A fresh proof reads *pending* until its Bitcoin block is mined, usually within a day; the daily job upgrades pending proofs as blocks arrive and commits the completed ones. The commit history is a second, independent witness.

## Failure modes

An automated series does not fail loudly; it stops. So:

- a **watchdog** runs on its own schedule and fails, opening an issue, if the newest forecast is older than 48 hours or today's is missing past its slot. It can be run by hand with `simulate=true` to prove the alert fires;
- **gaps are permanent and listed on the page**. A missed day is never backfilled: a forecast written after the fact is not a forecast;
- the platform's token, the Actions schedule and the domain are checked by hand every quarter.

## Running it

```
uv sync
echo ENTSOE_API=your-token > .env     # https://transparencyplatform.zendesk.com/hc/en-us/articles/12845911031188
uv run tick daily                     # actuals, forecast, scores, official, stamps, site
uv run tick official                  # capture tomorrow's official forecast if published
uv run tick intraday                  # append the hourly intraday record
uv run tick watchdog [--simulate]
uv run pytest
```

The API client is written from scratch (`src/tick/entsoe.py`) rather than taken from a library: how gaps are filled and how the daylight saving switch is handled must not change under the record.

## Licence

Code under MIT. The records under `data/` under CC BY 4.0. Market data from ENTSO-E is subject to the platform's terms of use.
