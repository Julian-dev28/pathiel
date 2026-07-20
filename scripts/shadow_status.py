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


_BAR_MS = {"1h": 3_600_000, "1d": 86_400_000}


def _make_fetch_fwd():
    """Real forward-candle fetch (lazy import so --inventory needs no network).
    Lookback is sized from the signal's AGE, not a fixed pad — a fixed +45 pad
    silently mis-windowed any signal older than ~50d (grader window rot)."""
    from hermes_trader.client.hl_client import fetch_hl_candles

    def fetch_fwd(coin: str, signal_bar_t: int, n_bars: int, interval: str = "1d") -> List[Any]:
        bar_ms = _BAR_MS.get(interval, 86_400_000)
        age_bars = max(0, int((time.time() * 1000 - int(signal_bar_t)) // bar_ms))
        bars = fetch_hl_candles(coin, interval, n_bars + age_bars + 3)
        return [b for b in bars if int(getattr(b, "t", 0)) > int(signal_bar_t)]

    return fetch_fwd


def _make_fetch_funding():
    """Real funding-history fetch so graded returns are NET of funding (a
    funding-gated short book pays every hour held; price-only grading
    overstated neg_funding_fade by ~1%/sig — audit 2026-07-09)."""
    from hermes_trader.client.hl_client import fetch_funding_history

    def fetch_funding(coin: str, start_ms: int, end_ms: int) -> List[Any]:
        return fetch_funding_history(coin, int(start_ms), int(end_ms))

    return fetch_funding


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", default=None, help="grade just one book")
    ap.add_argument("--inventory", action="store_true", help="counts only, skip candle fetch + grade")
    ap.add_argument("--min-n", type=int, default=8, help="min resolved signals before a verdict")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--meta", action="append", default=[], metavar="KEY=VALUE",
                    help="grade only records whose meta matches, e.g. --meta armed=true "
                         "(repeatable; a skew-armed book's live policy is the armed=true "
                         "subset, NOT the unconditional ledger)")
    args = ap.parse_args()
    meta_filters = SL.parse_meta_filters(args.meta)

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
            recs = SL.filter_by_meta(SL.load(book), meta_filters)
            grade = SL.grade_records(recs, _make_fetch_fwd(), now_ms=now_ms,
                                     fetch_funding=_make_fetch_funding()) if recs else {"n": 0}
            grade.pop("detail", None) if not args.json else None
            entry["grade"] = grade
            entry["verdict"] = grade.get("verdict") or SL.classify(grade, min_n=args.min_n)
        report.append(entry)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return 0

    flt = f" [meta filter: {meta_filters}]" if meta_filters else ""
    print(f"# shadow survey — {len(books)} book(s) @ {time.strftime('%Y-%m-%d %H:%M')}{flt}")
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
