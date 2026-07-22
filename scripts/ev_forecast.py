#!/usr/bin/env python3
"""Expected 1-week PnL across every LIVE book, net of fees, at current equity.

Deterministic: sizing comes from the live .agent-config.json + real dex equity,
EV comes from each book's own forward shadow-ledger grade where one exists, and
fire rates come from observed ledger history where the span supports it.

THE HONESTY RULE this script enforces: evidence has tiers, and they are never
summed into one number as if equal.

  FORWARD  — the book's own live ledger, n >= the grader's min-n. Trustworthy.
  PRIOR    — a backtest / research finding. Survivorship-biased UPPER BOUND.
             Reported separately; never added to the forward total.
  UNKNOWN  — carries capital, has no EV estimate at all. Reported as risk.

Usage:
    python3 scripts/ev_forecast.py [--slippage 25] [--json]
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
_ENV = _REPO / ".env.local"
if _ENV.is_file():
    for _line in _ENV.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

from hermes_trader.agents import shadow_ledger as SL  # noqa: E402

_WEEK_MS = 7 * 86_400_000
_MIN_N = 8            # the grader's own verdict bar
_MIN_SPAN_DAYS = 2.0  # below this a ledger cannot imply a weekly rate

# Research priors: backtest/audit numbers for books with no forward verdict yet.
# (ev_pct_per_event, events_per_week, provenance). SURVIVORSHIP UPPER BOUNDS.
_PRIORS = {
    "xs_momentum": (2.24, 0.7, "W-X5 phase-mean +2.24%/rebal, H10 -> 0.7 rebal/wk"),
    "xs_xyz_equities": (0.65, 1.4, "W-X2 cell A +0.65%/rebal net, H5 -> 1.4 rebal/wk"),
    "news_surge_short": (10.59, 1.5, "reverse-refuted audit re-graded at live 6% stop; "
                                     "rate from 9 distinct breaking-equity coins/3.9d"),
    "mover_pass_short": (6.09, 1.2, "reverse-refuted audit re-graded at live 6% stop; "
                                    "rate from mover_pass ledger cadence"),
}


def _dex_equity() -> Dict[str, float]:
    """Real per-dex equity — sizing is a function of the FUNDING account."""
    from hermes_trader.client.hl_client import _http_post, resolve_user_address
    user = (resolve_user_address()
            or os.environ.get("HYPERLIQUID_MASTER_ADDRESS")
            or os.environ.get("HYPERLIQUID_WALLET_ADDRESS", ""))
    out: Dict[str, float] = {}
    for label, dex in (("main", ""), ("xyz", "xyz")):
        try:
            r = _http_post("/info", {"type": "clearinghouseState", "user": user, "dex": dex})
            val = float(((r or {}).get("marginSummary") or {}).get("accountValue") or 0)
        except Exception:
            val = 0.0
        if val:
            out[label] = val
    return out


def _live_books(cfg: Dict[str, Any], eq: Dict[str, float]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    def fixed(name: str, block: Dict[str, Any], ledger: Optional[str] = None):
        if not block or not block.get("enabled") or block.get("shadow_only"):
            return
        lev = float(block.get("leverage") or 1)
        out.append({"book": name, "ledger": ledger or name,
                    "notional_usd": float(block.get("notional_usd") or 0) * lev,
                    "size": f"${float(block.get('notional_usd') or 0):g}x{lev:g}"})

    for key in ("rally_exhaustion", "crash_continue_div_short", "engulf_short",
                "funding_spike_short", "unlock_short", "news_surge_short"):
        fixed(key, cfg.get(key) or {},
              ledger="unlock_short_runin" if key == "unlock_short" else None)
    mr = cfg.get("mover_recorders") or {}
    fixed("mover_pass_short", mr.get("pass_short_live") or {})

    # Basket books: leg notional = funding-dex equity * frac * lev, capped.
    frac = float(cfg.get("strategy_book_equity_frac") or 0)
    cap = float(cfg.get("strategy_book_notional_usd") or 0)
    lev = float(cfg.get("leverage") or 1)
    for name, dex, legs_key, ledger in (
            ("xs_momentum", "main", "k_per_leg", "v2_xs_momentum"),
            ("xs_xyz_equities", "xyz", "k_per_leg", "xs_xyz_equities")):
        blk = cfg.get(name) or {}
        if not blk.get("enabled") or blk.get("shadow_only"):
            continue
        legs = 2 * int(blk.get(legs_key) or 0)
        leg_notional = (eq.get(dex, 0.0) * frac * lev) if frac else 0.0
        if cap > 0:
            leg_notional = min(leg_notional, cap)
        out.append({"book": name, "ledger": ledger,
                    "notional_usd": leg_notional * legs,
                    "size": f"{legs} legs x ${leg_notional:.0f}"})
    return out


def _rate_per_week(recs: List[Dict[str, Any]]) -> tuple[Optional[float], float]:
    if not recs:
        return None, 0.0
    kept, _ = SL.dedup_episodes(recs)
    ts = sorted(t for t in (int(r.get("ts") or r.get("signal_bar_t") or 0) for r in kept) if t > 0)
    if len(ts) < 2:
        return None, 0.0
    span_d = (ts[-1] - ts[0]) / 86_400_000
    if span_d < _MIN_SPAN_DAYS:
        return None, span_d      # too short to imply a weekly rate — don't guess
    return len(ts) * 7.0 / span_d, span_d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slippage", type=int, default=25, choices=[0, 12, 25, 50])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    from hermes_trader.agents.config_store import read_agent_config
    from hermes_trader.client.hl_client import fetch_hl_candles, fetch_funding_history

    cfg = read_agent_config()
    eq = _dex_equity()
    now_ms = int(time.time() * 1000)

    def fetch_fwd(coin, sig_t, n_bars, interval="1d"):
        bar_ms = {"1h": 3_600_000, "1d": 86_400_000}.get(interval, 86_400_000)
        age = max(0, int((now_ms - int(sig_t)) // bar_ms))
        bars = fetch_hl_candles(coin, interval, n_bars + age + 3)
        return [b for b in bars if int(getattr(b, "t", 0)) > int(sig_t)]

    rows: List[Dict[str, Any]] = []
    for b in _live_books(cfg, eq):
        recs = SL.load(b["ledger"])
        rate, span_d = _rate_per_week(recs)
        grade = SL.grade_records(
            recs, fetch_fwd, now_ms=now_ms,
            fetch_funding=lambda c, s, e: fetch_funding_history(c, int(s), int(e))
        ) if recs else {"n": 0}
        n = int(grade.get("n", 0))
        ev = grade.get(f"slip{args.slippage}", {}).get("mean_pct")

        tier, weekly, note = "UNKNOWN", None, ""
        if n >= _MIN_N and ev is not None and rate:
            tier = "FORWARD"
            weekly = b["notional_usd"] * (ev / 100.0) * rate
            note = f"own ledger, n={n}"
        elif b["book"] in _PRIORS:
            tier = "PRIOR"
            p_ev, p_rate, note = _PRIORS[b["book"]]
            ev, rate = p_ev, p_rate
            weekly = b["notional_usd"] * (p_ev / 100.0) * p_rate
        else:
            note = (f"n={n} < {_MIN_N}" if n < _MIN_N else "no usable rate")
            if span_d and span_d < _MIN_SPAN_DAYS:
                note += f", span {span_d:.1f}d"
        rows.append({**b, "tier": tier, "n": n, "ev_pct": ev,
                     "eps_per_week": rate, "weekly_usd": weekly, "note": note})

    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return 0

    total_eq = sum(eq.values())
    print(f"# expected 1-week PnL by LIVE book — net of {args.slippage}bps + funding")
    print(f"# equity ${total_eq:.2f} (" + ", ".join(f"{k} ${v:.2f}" for k, v in sorted(eq.items()) if v) + ")\n")
    print(f"{'book':<26} {'size':>16} {'notional':>9} {'ev/ep':>8} {'eps/wk':>7} {'$/week':>8}  tier")
    print("-" * 92)
    totals: Dict[str, float] = {"FORWARD": 0.0, "PRIOR": 0.0}
    for r in sorted(rows, key=lambda x: (x["tier"] != "FORWARD", -(x["weekly_usd"] or -1e9))):
        if r["weekly_usd"] is not None:
            totals[r["tier"]] += r["weekly_usd"]
            ev_s = f"{r['ev_pct']:+.2f}%"
            wk_s = f"${r['weekly_usd']:+.2f}"
            rt_s = f"{r['eps_per_week']:.2f}"
        else:
            ev_s, wk_s, rt_s = "—", "—", "—"
        print(f"{r['book']:<26} {r['size']:>16} ${r['notional_usd']:>8.0f} {ev_s:>8} "
              f"{rt_s:>7} {wk_s:>8}  {r['tier']}")
    print("-" * 92)
    print(f"{'FORWARD-MEASURED total':<26} {'':>16} {'':>9} {'':>8} {'':>7} ${totals['FORWARD']:+.2f}")
    print(f"{'RESEARCH-PRIOR total':<26} {'':>16} {'':>9} {'':>8} {'':>7} ${totals['PRIOR']:+.2f}   <- upper bound")
    print(f"{'COMBINED (if priors hold)':<26} {'':>16} {'':>9} {'':>8} {'':>7} "
          f"${totals['FORWARD'] + totals['PRIOR']:+.2f}")
    unknown = [r["book"] for r in rows if r["tier"] == "UNKNOWN"]
    if unknown:
        print(f"\n# CAPITAL WITH NO EV ESTIMATE (unknown, not zero): {', '.join(unknown)}")
        for r in rows:
            if r["tier"] == "UNKNOWN":
                print(f"#   {r['book']:<24} ${r['notional_usd']:.0f} notional — {r['note']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
