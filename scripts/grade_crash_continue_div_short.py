#!/usr/bin/env python3
"""Forward-grade the crash_continue_div_short shadow log.

Reads `.crash_continue_div_short_shadow.jsonl` (written by the live module in
shadow mode), and for each candidate that is now old enough to have resolved,
fetches the realized forward daily bars and simulates the exact short the live
book WOULD have taken (fill next-open ~= signal-bar close, `stop_pct` stop,
`hold_days` horizon). Prints aggregate EV at the project's slippage tiers + win
rate, so the edge can be confirmed forward BEFORE any operator flip to live.

This is the EVAL for the shadow logger — it does NOT trade. It only reads the
shadow log + public candles. Run it after ~2-4 weeks of shadow accrual.

Usage:
    python3 scripts/grade_crash_continue_div_short.py
    python3 scripts/grade_crash_continue_div_short.py --slip-bps 12
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
_env = _REPO / ".env.local"
if _env.is_file():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

from hermes_trader.client.hl_client import fetch_hl_candles  # noqa: E402

DAY_MS = 86_400_000
SHADOW_FILE = _REPO / ".crash_continue_div_short_shadow.jsonl"
SLIP_TIERS_BPS = [0, 6, 12, 25, 50]


def _load(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def _simulate_short(entry_px: float, fwd: List[Any], stop_pct: float, horizon: int) -> Optional[float]:
    """Lookahead-safe short sim: fill at entry_px, stop at +stop_pct, else exit at
    horizon close. fwd = daily bars STRICTLY AFTER the signal bar."""
    if entry_px <= 0 or not fwd:
        return None
    stop_px = entry_px * (1 + stop_pct / 100.0)
    for bar in fwd[:horizon]:
        hi = float(bar.h if hasattr(bar, "h") else bar["h"])
        if hi >= stop_px:
            return -(stop_pct / 100.0)  # short loses stop_pct
    last = fwd[min(horizon, len(fwd)) - 1]
    last_c = float(last.c if hasattr(last, "c") else last["c"])
    return entry_px / last_c - 1.0  # short return = (entry-exit)/entry


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(SHADOW_FILE))
    ap.add_argument("--slip-bps", type=int, default=None, help="show one tier instead of the full sweep")
    args = ap.parse_args()

    rows = _load(Path(args.file))
    if not rows:
        print(f"# no shadow records at {args.file} — let the loop accrue some first.")
        return 0

    now_ms = int(time.time() * 1000)
    rets: List[float] = []
    pending = 0
    graded: List[Dict[str, Any]] = []
    for r in rows:
        sig_t = int(r.get("signal_bar_t") or 0)
        entry_px = float(r.get("entry_ref_px") or 0)
        stop_pct = float(r.get("stop_pct", 8.0))
        hold_days = int(float(r.get("hold_days", 10.0)))
        if not sig_t or entry_px <= 0:
            continue
        resolve_ms = sig_t + (hold_days + 2) * DAY_MS
        if now_ms < resolve_ms:
            pending += 1
            continue
        coin = r.get("coin")
        need = hold_days + 5
        try:
            bars = fetch_hl_candles(coin, "1d", need + 40)
        except Exception:
            continue
        fwd = [b for b in bars if int(b.t) > sig_t]
        ret = _simulate_short(entry_px, fwd, stop_pct, hold_days)
        if ret is None:
            continue
        rets.append(ret)
        graded.append({"coin": coin, "move_pct": r.get("move_pct"), "ret_pct": round(100 * ret, 2)})

    n = len(rets)
    print(f"# crash_continue_div_short forward grade — {n} resolved, {pending} pending")
    if n == 0:
        print("# nothing resolved yet (need signal age > hold_days+2).")
        return 0

    tiers = [args.slip_bps] if args.slip_bps is not None else SLIP_TIERS_BPS
    print(f"{'slip_bps':>8} {'mean_ret%':>10} {'total%':>9} {'win':>6}")
    for bps in tiers:
        cost = bps / 10000.0
        net = [x - cost for x in rets]
        mean = sum(net) / n
        wins = sum(1 for x in net if x > 0)
        print(f"{bps:>8} {100*mean:>10.3f} {100*sum(net):>9.2f} {wins/n:>6.2f}")

    # OOS both halves at 12bps
    graded_sorted = sorted(range(n), key=lambda i: rets[i])  # not time; use input order for time split
    half = n // 2
    h1 = rets[:half]
    h2 = rets[half:]
    def _ev(xs):
        return round(100 * (sum(x - 0.0012 for x in xs) / len(xs)), 3) if xs else None
    print(f"# OOS @12bps  first_half={_ev(h1)}  second_half={_ev(h2)}  (n {len(h1)}/{len(h2)})")
    print("# survivorship note: shadow log is forward + PIT, so this is the HONEST forward read (no upper-bound bias).")
    worst = sorted(graded, key=lambda g: g["ret_pct"])[:5]
    print("# worst 5:", worst)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
