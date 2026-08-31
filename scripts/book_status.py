#!/usr/bin/env python3
"""Where each live book stands against the promotion bar. No network.

The full grader (scripts/autonomous_cycle.py) needs forward candles from
Hyperliquid to produce a verdict, so it cannot answer anything while the
exchange is down — which is exactly when someone wants to know where things
stand. This answers the part that needs no network: how many signals each book
has recorded, whether it clears the evidence floor, and whether the evidence
loop can actually switch it.

    python scripts/book_status.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Load .env.local BEFORE importing anything that resolves state paths.
# PATHIA_STATE_DIR lives there, and without it the ledgers resolve to the repo
# root and every book reads as zero signals — silently.
_env = ROOT / ".env.local"
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

from pathia.agents import shadow_ledger as SL  # noqa: E402
import pathia.dashboard as db                  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "autonomous_cycle", ROOT / "scripts" / "autonomous_cycle.py")
AC = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(AC)


def signal_count(book: str) -> int:
    path = SL._book_path(book)
    if not os.path.exists(path):
        return 0
    with open(path) as fh:
        return sum(1 for line in fh if line.strip())


def main(argv=None) -> int:
    books = sorted(db._KNOWN_BOOK_NAMES)
    print(f"ledger dir : {SL._ledger_dir()}")
    print(f"evidence floor (MIN_N) : {AC.MIN_N}")
    print(f"promotion also needs: EV>0 at {AC.REAL_FEE_TIER}, both OOS halves "
          f"positive, survives {AC.STRICT_FEE_TIER}, mc_p < {AC.PROMOTE_MAX_P}\n")
    print(f"{'book':<24}{'signals':>9}{'gradeable':>11}{'switch':>9}")
    ungoverned = []
    for b in books:
        n = signal_count(b)
        has_switch = b in AC._SWITCHES
        if not has_switch and b not in AC._NEVER_PROMOTE:
            ungoverned.append(b)
        print(f"{b:<24}{n:>9}{('yes' if n >= AC.MIN_N else 'no'):>11}"
              f"{('yes' if has_switch else 'NO'):>9}")
    if ungoverned:
        print(f"\nWARNING: {ungoverned} can place orders but the evidence loop "
              f"has no switch for them — they can never be auto-demoted.")
        return 1
    print("\nA verdict (VALIDATED / REFUTED) needs forward candles and therefore "
          "a working exchange.\nRun scripts/autonomous_cycle.py --dry-run when "
          "Hyperliquid is healthy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
