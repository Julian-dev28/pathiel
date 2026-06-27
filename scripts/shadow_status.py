#!/usr/bin/env python3
"""Shadow-mode survey — the single handler for all shadow books.

Inventories every book that records to the unified shadow ledger
(`<state_dir>/shadow_ledger/<book>.jsonl`) and forward-grades the resolved
signals into a per-strategy VERDICT: REFUTED / VALIDATED / MARGINAL / PENDING.

This does NOT trade. It reads the ledger + public candles only. It is how a
shadow strategy gets surveyed and refuted-or-validated before any operator flip
to live — the PIT forward read, no survivorship upper-bound bias.

Usage:
    python3 scripts/shadow_status.py                 # inventory + verdict, all books
    python3 scripts/shadow_status.py --book crash_continue_div_short
    python3 scripts/shadow_status.py --inventory     # counts only, no candle fetch
    python3 scripts/shadow_status.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, List

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
_env = _REPO / ".env.local"
if _env.is_file():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

from hermes_trader.agents import shadow_ledger as SL  # noqa: E402


def _make_fetch_fwd():
    """Real forward-candle fetch (lazy import so --inventory needs no network)."""
    from hermes_trader.client.hl_client import fetch_hl_candles

    def fetch_fwd(coin: str, signal_bar_t: int, n_bars: int) -> List[Any]:
        bars = fetch_hl_candles(coin, "1d", n_bars + 45)
        return [b for b in bars if int(getattr(b, "t", 0)) > int(signal_bar_t)]

    return fetch_fwd


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", default=None, help="grade just one book")
    ap.add_argument("--inventory", action="store_true", help="counts only, skip candle fetch + grade")
    ap.add_argument("--min-n", type=int, default=8, help="min resolved signals before a verdict")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    now_ms = int(time.time() * 1000)
    inv = {r["book"]: r for r in SL.summary(now_ms)}
    books = [args.book] if args.book else SL.list_books()

    if not books:
        print("# no shadow books have recorded yet — let the loop run a few cycles.")
        return 0

    report: List[dict] = []
    for book in books:
        row = inv.get(book, {"book": book, "n": 0})
        entry: dict = {"book": book, "inventory": row}
        if not args.inventory:
            recs = SL.load(book)
            grade = SL.grade_records(recs, _make_fetch_fwd(), now_ms=now_ms) if recs else {"n": 0}
            grade.pop("detail", None) if not args.json else None
            entry["grade"] = grade
            entry["verdict"] = grade.get("verdict") or SL.classify(grade, min_n=args.min_n)
        report.append(entry)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return 0

    print(f"# shadow survey — {len(books)} book(s) @ {time.strftime('%Y-%m-%d %H:%M')}")
    print(f"{'book':<28} {'sig':>4} {'grd':>4} {'res':>4} {'pend':>4} {'last_age_h':>10}   verdict")
    print("-" * 96)
    for e in report:
        iv = e["inventory"]
        n = iv.get("n", 0)
        grd = iv.get("gradeable", "-")
        res = iv.get("resolved", "-")
        pend = iv.get("pending", "-")
        age = iv.get("last_age_h")
        age_s = f"{age:.1f}" if isinstance(age, (int, float)) else "-"
        if args.inventory:
            print(f"{e['book']:<28} {n:>4} {grd:>4} {res:>4} {pend:>4} {age_s:>10}   (inventory only)")
            continue
        v = e.get("verdict", {})
        print(f"{e['book']:<28} {n:>4} {grd:>4} {res:>4} {pend:>4} {age_s:>10}   {v.get('label','?')}: {v.get('why','')}")
    if not args.inventory:
        print("\n# VALIDATED = forward +EV both OOS halves, survives 25bps (eligible for operator live-flip review).")
        print("# verdicts are the PIT forward read — stronger evidence than the backtest. No auto-flip.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
