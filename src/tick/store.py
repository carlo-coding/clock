"""Reading and writing the record.

One rule: a file under data/ is written once. There is no update function
in this module on purpose, and the test suite checks that writing to an
existing path fails. What was said stays said; a correction is a new file
with a new name (the .final.json scores are the example).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import config


class AlreadyWritten(FileExistsError):
    pass


def write_new(path: Path, obj: dict) -> Path:
    if path.exists():
        raise AlreadyWritten(f"{path} already exists and the record is never rewritten")
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def append_line(path: Path, obj: dict) -> Path:
    """Append one JSON line. Adding is not editing: earlier lines never change."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(obj, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n")
    return path


def read(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def read_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def git_sha() -> str | None:
    """The commit of the code that ran, or None before the first commit."""
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=config.ROOT, capture_output=True, text=True, timeout=10)
        sha = out.stdout.strip()
        return sha if out.returncode == 0 and len(sha) == 40 else None
    except Exception:
        return None


# Paths, so that nobody spells them twice.


def forecast_path(issue_date: str) -> Path:
    return config.FORECASTS / f"{issue_date}.json"


def actuals_path(zone: str, day: str, final: bool) -> Path:
    return config.ACTUALS / zone / (f"{day}.final.json" if final else f"{day}.json")


def official_path(zone: str, day: str, target: str) -> Path:
    return config.OFFICIAL / zone / day / f"{target}.json"


def score_path(day: str, final: bool) -> Path:
    return config.SCORES / (f"{day}.final.json" if final else f"{day}.json")


def intraday_path(zone: str, utc_day: str) -> Path:
    return config.INTRADAY / zone / f"{utc_day}.jsonl"


def capacity_path(zone: str, year: int) -> Path:
    return config.CAPACITY / f"{zone}-{year}.json"
