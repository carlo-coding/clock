"""External timestamping with OpenTimestamps.

A git commit date is set by whoever commits. A GPG signature proves who,
not when. OpenTimestamps puts the hash of a file into the Bitcoin
blockchain through public calendar servers, for free, and anyone can verify
it forever without trusting this repository or its author.

Two steps, because Bitcoin takes hours: `stamp` gets a calendar receipt
right away and writes <file>.ots next to the file; `upgrade`, run on later
days, completes the receipts whose block has been mined. Both proofs are
committed. Verify with `ots verify <file>.ots`, or at opentimestamps.org.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from . import config

STAMPED_DIRS = (config.FORECASTS, config.SCORES, config.OFFICIAL, config.ACTUALS, config.CAPACITY)


def ots_bin() -> str:
    found = shutil.which("ots")
    if found:
        return found
    for name in ("ots", "ots.exe"):
        cand = Path(sys.executable).parent / name
        if cand.exists():
            return str(cand)
    raise RuntimeError("the ots client is not installed (pip install opentimestamps-client)")


def unstamped() -> list[Path]:
    out = []
    for d in STAMPED_DIRS:
        if d.exists():
            out += [p for p in d.rglob("*.json") if not p.with_name(p.name + ".ots").exists()]
    return sorted(out)


def stamp(paths: list[Path] | None = None) -> list[Path]:
    """Stamp every record file that has no proof yet, in one calendar submission."""
    paths = paths if paths is not None else unstamped()
    if not paths:
        return []
    subprocess.run([ots_bin(), "stamp", *[str(p) for p in paths]], check=True, timeout=300)
    return paths


def upgrade() -> tuple[int, int]:
    """Try to complete pending proofs. Returns (upgraded, still_pending)."""
    done = pending = 0
    for d in STAMPED_DIRS:
        if not d.exists():
            continue
        for p in sorted(d.rglob("*.ots")):
            r = subprocess.run([ots_bin(), "upgrade", str(p)], capture_output=True, text=True, timeout=120)
            text = (r.stdout + r.stderr).lower()
            if r.returncode == 0 and "success" in text:
                done += 1
            elif "pending" in text or r.returncode != 0:
                pending += 1
            bak = p.with_name(p.name + ".bak")
            if bak.exists():
                bak.unlink()
    return done, pending
