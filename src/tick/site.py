"""The page. Plain HTML, no scripts, rebuilt from data/ on every run.

The page is the shop window; the archive is the repository. If the page
goes down the record is untouched, and everything on the page links to
the file it came from.
"""

from __future__ import annotations

import html
from datetime import date, timedelta

from . import config, store, watchdog
from .score import revision_drift

CSS = """
:root{color-scheme:light}
body{margin:0;background:#fff;color:#111;font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
main{max-width:760px;margin:0 auto;padding:56px 20px 80px}
h1{font-size:22px;font-weight:600;letter-spacing:-.01em;margin:0 0 6px}
.sub{color:#666;margin:0 0 44px;font-size:15px}
h2{font-size:12px;text-transform:uppercase;letter-spacing:.1em;color:#777;margin:44px 0 12px;font-weight:600}
p{margin:0 0 12px}
.headline{font-size:21px;line-height:1.45;margin:0 0 10px;font-weight:500}
.muted{color:#666;font-size:14px}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;font-size:14px;margin:0 0 8px}
th,td{padding:7px 10px;border-bottom:1px solid #e6e6e6;text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left}
th{font-weight:500;color:#666}
.wrap{overflow-x:auto}
code{font:13px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:#f4f4f4;padding:1px 5px;border-radius:3px}
pre{font:13px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:#f4f4f4;padding:12px 14px;border-radius:4px;overflow-x:auto}
a{color:#111;text-decoration-color:#bbb;text-underline-offset:3px}
a:hover{text-decoration-color:#111}
svg{display:block;max-width:100%;height:auto}
ul{padding-left:20px;margin:0 0 12px}
li{margin:2px 0}
footer{margin-top:60px;color:#888;font-size:13px}
"""


def _esc(s) -> str:
    return html.escape(str(s))


def _f(x, digits=1, suffix="") -> str:
    if x is None:
        return "·"
    return f"{x:,.{digits}f}{suffix}"


def _pct(x) -> str:
    return "·" if x is None else f"{100 * x:+.1f}%"


def _file_link(rel: str, text: str | None = None) -> str:
    return f'<a href="{config.REPO_URL}/blob/main/{rel}">{_esc(text or rel)}</a>'


def load_scores() -> dict[str, dict]:
    """delivery_day -> score document, final preferred over preliminary."""
    docs: dict[str, dict] = {}
    if not config.SCORES.exists():
        return docs
    for p in sorted(config.SCORES.glob("*.json")):
        d = store.read(p)
        if not d:
            continue
        day = d["delivery_day"]
        if day not in docs or d["stage"] == "final":
            docs[day] = d
    return docs


def lead_rows(docs: dict[str, dict], zone: str, target: str, lead: str) -> list[dict]:
    rows = []
    for day, d in sorted(docs.items()):
        entry = d["zones"].get(zone, {}).get(target, {}).get("leads", {}).get(lead)
        if entry and entry.get("clock", {}).get("n"):
            rows.append({"day": day, "stage": d["stage"], **entry})
    return rows


def _metric_key(target: str) -> tuple[str, str, int]:
    """Which number to show per target: (key, label, digits)."""
    if target in ("wind", "solar"):
        return "nmae_pct", "nMAE, % of installed capacity", 2
    if target == "load":
        return "mape_pct", "MAPE, %", 2
    return "mae", "MAE, EUR/MWh", 2


def _mean(rows: list[dict], source: str, key: str) -> float | None:
    xs = [r[source][key] for r in rows if r.get(source) and r[source].get(key) is not None]
    return sum(xs) / len(xs) if xs else None


def headline_block(docs) -> str:
    z, t = config.HEADLINE["zone"], config.HEADLINE["target"]
    rows = lead_rows(docs, z, t, "1")
    with_off = [r for r in rows if r.get("official") and r["official"].get("n")]
    n = len(with_off)
    key, label, digits = _metric_key(t)
    if n < config.MIN_DAYS_FOR_HEADLINE:
        head = f"{n} of {config.MIN_DAYS_FOR_HEADLINE} delivery days scored against the operator's own forecast."
        note = (
            f"The headline number, {z} {t} day-ahead against the TSO's forecast, is withheld until "
            f"{config.MIN_DAYS_FOR_HEADLINE} days are on record. A score over a handful of days measures nothing and looks like it does."
        )
        return f'<p class="headline">{_esc(head)}</p><p class="muted">{_esc(note)}</p>'
    clock = _mean(with_off, "clock", key)
    off = _mean(with_off, "official", key)
    skill = 1 - clock / off if clock is not None and off else None
    head = f"Against the operator's own day-ahead forecast, over {n} days: clock {_f(clock, digits)}% {label.split(',')[0]}, operator {_f(off, digits)}%."
    note = f"Skill {_pct(skill)}. Positive means the clock beat the TSO's published forecast on the same hours. Model {with_off[-1]['model']}."
    return f'<p class="headline">{_esc(head)}</p><p class="muted">{_esc(note)}</p>'


def chart(docs) -> str:
    """Daily headline metric, clock vs official, last 120 days. Inline SVG."""
    z, t = config.HEADLINE["zone"], config.HEADLINE["target"]
    key, label, _ = _metric_key(t)
    rows = [r for r in lead_rows(docs, z, t, "1") if r["clock"].get(key) is not None][-120:]
    if len(rows) < 2:
        return ""
    W, H, L, B = 760, 200, 44, 24
    vals = [r["clock"][key] for r in rows] + [r["official"][key] for r in rows if r.get("official") and r["official"].get(key) is not None]
    top = max(vals) * 1.1 or 1
    def x(i):
        return L + i * (W - L - 8) / max(1, len(rows) - 1)
    def y(v):
        return H - B - v / top * (H - B - 10)
    def path(series):
        pts = [(x(i), y(v)) for i, v in enumerate(series) if v is not None]
        return " ".join(f"{'M' if j == 0 else 'L'}{px:.1f},{py:.1f}" for j, (px, py) in enumerate(pts))
    clock_p = path([r["clock"][key] for r in rows])
    off_p = path([(r["official"][key] if r.get("official") and r["official"].get(key) is not None else None) for r in rows])
    ticks = "".join(
        f'<text x="{L-6}" y="{y(v)+4:.1f}" font-size="11" fill="#888" text-anchor="end">{v:g}</text>'
        f'<line x1="{L}" x2="{W-8}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="#eee"/>'
        for v in (0, top / 2, top)
    )
    first, last = rows[0]["day"], rows[-1]["day"]
    return (
        f'<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-label="{_esc(label)} by day">'
        f"{ticks}"
        f'<path d="{off_p}" fill="none" stroke="#bbb" stroke-width="1.5"/>'
        f'<path d="{clock_p}" fill="none" stroke="#111" stroke-width="1.5"/>'
        f'<text x="{L}" y="{H-6}" font-size="11" fill="#888">{first}</text>'
        f'<text x="{W-8}" y="{H-6}" font-size="11" fill="#888" text-anchor="end">{last}</text>'
        f"</svg>"
        f'<p class="muted">{_esc(label)}, day-ahead, {z} {t}. Black: clock. Grey: the operator\'s forecast.</p>'
    )


def targets_table(docs) -> str:
    out = ['<div class="wrap"><table><tr><th>Zone · target</th><th>Days</th><th>Metric</th><th>Clock</th><th>Persistence</th><th>Weekly</th><th>Operator</th><th>Skill vs operator</th></tr>']
    for zone in config.ZONES:
        for target in config.TARGETS[zone]:
            rows = lead_rows(docs, zone, target, "1")
            key, label, digits = _metric_key(target)
            off_rows = [r for r in rows if r.get("official") and r["official"].get("n")]
            clock = _mean(rows, "clock", key)
            off = _mean(off_rows, "official", key)
            clock_off = _mean(off_rows, "clock", key)
            skill = (1 - clock_off / off) if clock_off is not None and off else None
            out.append(
                f"<tr><td>{zone} · {target}</td><td>{len(rows)}</td><td>{_esc(label.split(',')[0])}</td>"
                f"<td>{_f(clock, digits)}</td><td>{_f(_mean(rows, 'persistence', key), digits)}</td><td>{_f(_mean(rows, 'weekly', key), digits)}</td>"
                f"<td>{_f(off, digits) if target != 'price' else 'none exists'}</td><td>{_pct(skill)}</td></tr>"
            )
    out.append("</table></div>")
    out.append('<p class="muted">Day-ahead (lead 1), mean of daily scores, every source over the same hours. Solar over daylight hours only. Operator columns count only days where the TSO forecast was captured before delivery.</p>')
    return "".join(out)


def leads_table(docs) -> str:
    z, t = config.HEADLINE["zone"], config.HEADLINE["target"]
    key, label, digits = _metric_key(t)
    out = [f'<div class="wrap"><table><tr><th>Lead, days</th><th>Days</th><th>Clock</th><th>Persistence</th><th>Weekly</th></tr>']
    for k in config.LEAD_DAYS:
        rows = lead_rows(docs, z, t, str(k))
        out.append(f"<tr><td>{k}</td><td>{len(rows)}</td><td>{_f(_mean(rows, 'clock', key), digits)}</td><td>{_f(_mean(rows, 'persistence', key), digits)}</td><td>{_f(_mean(rows, 'weekly', key), digits)}</td></tr>")
    out.append("</table></div>")
    out.append(f'<p class="muted">{z} {t}, {_esc(label)}. Leads 2 to 7 have no official forecast to compare against; they are on record so that the series exists when they do.</p>')
    return "".join(out)


def intraday_table(docs) -> str:
    z, t = config.HEADLINE["zone"], config.HEADLINE["target"]
    key, label, digits = _metric_key(t)
    by_lead: dict[str, list] = {}
    for d in docs.values():
        entry = d["zones"].get(z, {}).get("intraday", {}).get(t, {})
        for lead, e in entry.items():
            by_lead.setdefault(lead, []).append(e)
    if not by_lead:
        return '<p class="muted">Nothing scored yet.</p>'
    out = ['<div class="wrap"><table><tr><th>Lead, hours</th><th>Days</th><th>Clock (persistence)</th><th>Operator intraday</th><th>Skill</th></tr>']
    for lead in sorted(by_lead, key=int):
        es = by_lead[lead]
        c = _mean([{"clock": e["clock"]} for e in es], "clock", key)
        withoff = [e for e in es if e.get("official_intraday")]
        cc = _mean([{"c": e["clock_on_official_hours"]} for e in withoff], "c", key)
        o = _mean([{"o": e["official_intraday"]} for e in withoff], "o", key)
        skill = (1 - cc / o) if cc is not None and o else None
        out.append(f"<tr><td>{lead}</td><td>{len(es)}</td><td>{_f(c, digits)}</td><td>{_f(o, digits)}</td><td>{_pct(skill)}</td></tr>")
    out.append("</table></div>")
    out.append(f'<p class="muted">{z} {t}, {_esc(label)}, hourly runs. Recorded from day one, not a headline.</p>')
    return "".join(out)


def latest_block() -> str:
    days = watchdog.issue_days()
    if not days:
        return "<p>No forecast issued yet.</p>"
    last = days[-1]
    doc = store.read(store.forecast_path(last.isoformat())) or {}
    rel = f"data/forecasts/{last.isoformat()}.json"
    gate = "before" if doc.get("before_gate_closure") else "after"
    lines = [
        f"<p>Issued {_esc(doc.get('issued_at'))}, {gate} the day-ahead gate closure ({_esc(doc.get('gate_closure'))}), for delivery day {_esc(doc.get('day_ahead'))} and six days beyond. Model <code>{_esc(doc.get('model'))}</code>.</p>",
        f"<p>{_file_link(rel)} · {_file_link(rel + '.ots', 'proof')}</p>",
    ]
    g = watchdog.gaps(days)
    if g:
        lines.append("<p>Gaps on record, permanent: " + ", ".join(_esc(x.isoformat()) for x in g) + ".</p>")
    else:
        lines.append(f"<p>{len(days)} issue days since {days[0].isoformat()}, no gaps.</p>")
    drift = None
    for back in range(config.FINAL_LAG_DAYS, config.FINAL_LAG_DAYS + 5):
        drift = revision_drift(date.today() - timedelta(days=back))
        if drift:
            break
    if drift:
        lines.append('<p class="muted">Latest revision drift (mean absolute change between the preliminary and the final actuals, per hour): ' + ", ".join(f"{_esc(k)} {_f(v, 1)}" for k, v in drift.items()) + ".</p>")
    return "".join(lines)


def method_block() -> str:
    rules = ", ".join(f"{t}: {r}" for t, r in config.MODEL_RULE.items())
    return f"""
<p>Every day at about {config.ISSUE_HOUR_UTC:02d}:40 UTC, before the day-ahead market closes at 12:00 CET, a scheduled job downloads the latest actuals from the ENTSO-E transparency platform, writes a forecast for every hour of the next seven delivery days, commits it to the public repository and anchors its hash in the Bitcoin blockchain with OpenTimestamps. The next day it downloads what happened, scores every source over the same hours, and publishes the score next to the forecast. Seven days later it scores again with the revised actuals, in a second file; nothing is overwritten.</p>
<p>The operator's own day-ahead forecast is polled through the day and stored with the time it was first seen complete, because the regulation sets a deadline for publishing it, not a time, and a comparison against a number of unknown availability is not a comparison.</p>
<p>Zones: DE-LU and ES. Targets: wind, solar, load and day-ahead price for DE-LU; wind and solar for ES. Price has no official forecast and is a dated series only. Phase 1 model <code>{config.MODEL_VERSION}</code> is deliberately naive ({_esc(rules)}); persistence and weekly baselines are always published beside it. The scarce thing is the dated series, and the method improves forward.</p>
<p>Wind and solar are scored as nMAE, a share of installed capacity; solar over daylight hours only. Load as MAPE. Everything is hourly means of the platform's quarter-hour values, in UTC; delivery days follow the CET/CEST calendar and have 23, 24 or 25 hours when the clocks change.</p>
"""


def verify_block() -> str:
    return f"""
<p>Every file under <code>data/</code> has a <code>.ots</code> proof beside it. To check that a forecast existed before the day it forecast:</p>
<pre>pip install opentimestamps-client
ots verify data/forecasts/2026-09-12.json.ots</pre>
<p>Or drop the file and its proof at <a href="https://opentimestamps.org">opentimestamps.org</a>. A fresh proof says <em>pending</em> until its Bitcoin block is mined, typically within a day; the job upgrades proofs as blocks arrive. The commit history is a second, independent witness. The full record and code are at {_file_link('', config.REPO_URL)}.</p>
"""


def build() -> str:
    docs = load_scores()
    body = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>clock · a dated forecasting record for European power</title>
<meta name="description" content="Daily day-ahead forecasts for DE-LU and Spain, scored against the TSO's own forecast, timestamped with OpenTimestamps, never edited.">
<style>{CSS}</style>
</head>
<body>
<main>
<h1>clock</h1>
<p class="sub">A public forecasting record for European power. Every day a forecast, the next day the score, every file timestamped and never edited.</p>

{headline_block(docs)}
{chart(docs)}

<h2>Day-ahead, all targets</h2>
{targets_table(docs)}

<h2>By lead time</h2>
{leads_table(docs)}

<h2>Intraday</h2>
{intraday_table(docs)}

<h2>Latest issue</h2>
{latest_block()}

<h2>Method</h2>
{method_block()}

<h2>Verify</h2>
{verify_block()}

<footer>Data: ENTSO-E transparency platform. Series started {(watchdog.issue_days() or [date.today()])[0].isoformat()}. Rebuilt on every run from the files in the repository.</footer>
</main>
</body>
</html>
"""
    config.SITE.mkdir(parents=True, exist_ok=True)
    out = config.SITE / "index.html"
    out.write_text(body, encoding="utf-8", newline="\n")
    (config.SITE / "CNAME").write_text(config.SITE_DOMAIN + "\n", encoding="utf-8")
    (config.SITE / ".nojekyll").write_text("", encoding="utf-8")
    return str(out)
