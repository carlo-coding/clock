"""Command line entry point. `tick daily` is what the scheduled job runs."""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from . import actuals, config, forecast, intraday, official, score, site, stamp, store, watchdog
from .entsoe import Client
from .timeutil import local_date, utc_now


def _rel(p: str | Path) -> str:
    try:
        return str(Path(p).relative_to(config.ROOT)).replace("\\", "/")
    except ValueError:
        return str(p)


def cmd_daily(args) -> int:
    now = utc_now()
    client = Client()
    print(f"tick daily at {now.isoformat()}")

    # 1. Actuals: fetch the history once, store every past day that lacks its file
    #    for its stage (preliminary for recent days, final once FINAL_LAG_DAYS old).
    history = {}
    for zone in config.ZONES:
        history[zone] = actuals.fetch_history(client, zone, config.HISTORY_DAYS, now)
        today = local_date(now, zone)
        for back in range(1, config.HISTORY_DAYS):
            day = today - timedelta(days=back)
            final = back >= config.FINAL_LAG_DAYS
            if actuals.store_day(zone, day, history[zone], final=final, fetched_at=now):
                print(f"  actuals {zone} {day} {'final' if final else 'preliminary'}")
        cap = actuals.capacity(client, zone, now.year)
        print(f"  capacity {zone} {now.year}: {cap}")

    # 2. Today's forecast, once.
    path, written = forecast.issue(client, now, history)
    print(f"  forecast {_rel(path)} {'issued' if written else 'already issued, untouched'}")

    # 3. Scores for every day that has forecasts and actuals for a stage.
    for back in range(1, config.HISTORY_DAYS):
        day = now.date() - timedelta(days=back)
        for final in (False, True):
            if any(store.actuals_path(z, day.isoformat(), final).exists() for z in config.ZONES) and score.forecasts_for(day):
                p, w = score.score_day(day, final, now)
                if w:
                    print(f"  scored {_rel(p)}")

    # 4. Official forecast for tomorrow, if it is already out.
    for p in official.capture(client, now):
        print(f"  official {_rel(p)}")

    # 5. Timestamps: stamp the new files, complete the pending proofs.
    stamped = stamp.stamp()
    print(f"  stamped {len(stamped)} file(s)")
    done, pending = stamp.upgrade()
    print(f"  proofs upgraded {done}, still pending {pending}")

    # 6. The page.
    out = site.build()
    print(f"  site {_rel(out)}")
    return 0


def cmd_issue(args) -> int:
    path, written = forecast.issue()
    print(f"{_rel(path)} {'issued' if written else 'already issued'}")
    return 0


def cmd_official(args) -> int:
    paths = official.capture()
    for p in paths:
        print(_rel(p))
    print(f"{len(paths)} captured")
    return 0


def cmd_intraday(args) -> int:
    for p in intraday.run():
        print(_rel(p))
    return 0


def cmd_score(args) -> int:
    day = date.fromisoformat(args.day)
    p, w = score.score_day(day, final=args.final)
    print(f"{_rel(p)} {'written' if w else 'already written'}")
    return 0


def cmd_stamp(args) -> int:
    paths = stamp.stamp()
    for p in paths:
        print(f"stamped {_rel(p)}")
    done, pending = stamp.upgrade()
    print(f"proofs upgraded {done}, pending {pending}")
    return 0


def cmd_site(args) -> int:
    print(_rel(site.build()))
    return 0


def cmd_watchdog(args) -> int:
    problems, notes = watchdog.check(simulate=args.simulate)
    for n in notes:
        print(f"note: {n}")
    for p in problems:
        print(f"PROBLEM: {p}")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tick", description="the program behind clock")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("daily", help="actuals, forecast, scores, official, stamps, site").set_defaults(fn=cmd_daily)
    sub.add_parser("issue", help="issue today's forecast file only").set_defaults(fn=cmd_issue)
    sub.add_parser("official", help="capture tomorrow's official forecast if published").set_defaults(fn=cmd_official)
    sub.add_parser("intraday", help="append the hourly intraday record").set_defaults(fn=cmd_intraday)
    s = sub.add_parser("score", help="score one delivery day")
    s.add_argument("day")
    s.add_argument("--final", action="store_true")
    s.set_defaults(fn=cmd_score)
    sub.add_parser("stamp", help="stamp new files and upgrade pending proofs").set_defaults(fn=cmd_stamp)
    sub.add_parser("site", help="rebuild the static page").set_defaults(fn=cmd_site)
    w = sub.add_parser("watchdog", help="fail if the series has stopped")
    w.add_argument("--simulate", action="store_true", help="force a failure to test the alert")
    w.set_defaults(fn=cmd_watchdog)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
