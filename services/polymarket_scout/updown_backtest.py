"""Backtest the AI's OWN up/down verdict on historical 5-min BTC windows.

Not a mechanical rule — this replays what the model would actually say. For each
past window it rebuilds the SAME multi-timeframe bar context the live reader
feeds the model (position vs the window-open, 1m/5m/15m momentum + 3-bar trends,
the random-walk anchor), asks the real brain for a verdict with NO web search and
NO dates (so it reasons purely from the tape — no lookahead, no leakage), then
grades the verdict against the window's actual close.

Reports the AI's hit rate and the paper EV of buying its side at a coin-flip 0.50
fill, fee-aware. A hit rate at 50% = the model adds nothing over the coin; above,
with EV clearing fees, is the first real evidence it can read 5-minute BTC.

    python -m services.polymarket_scout.updown_backtest --n 30 --decision 0.7
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional

from services.polymarket_scout.scout import FEE_PER_FILL
from services.polymarket_scout.updown import (
    BINANCE, _chg, _stdev, _trend3, build_prompt, call_label, randomwalk_up_prob,
    _parse, _SYS,
)

WINDOW_MIN = 5


def fetch_1m(limit: int = 1000, end_ms: Optional[int] = None,
             runner: Optional[Callable] = None) -> List[List[float]]:
    """BTC 1-minute klines, newest at the end. Paginate backward with end_ms."""
    url = f"{BINANCE}?symbol=BTCUSDT&interval=1m&limit={limit}"
    if end_ms:
        url += f"&endTime={end_ms}"
    try:
        if runner is not None:
            raw = runner(url)
        else:
            out = subprocess.run(["curl", "-s", "--max-time", "20", url],
                                 capture_output=True, text=True, timeout=25)
            raw = json.loads(out.stdout)
        return raw if isinstance(raw, list) else []
    except Exception:
        return []


def load_history(minutes: int, runner: Optional[Callable] = None) -> List[List[float]]:
    """Contiguous 1m klines covering ~`minutes`, oldest first."""
    out: List[List[float]] = []
    end = None
    while len(out) < minutes:
        batch = fetch_1m(1000, end_ms=end, runner=runner)
        if not batch:
            break
        out = batch + out
        end = int(batch[0][0]) - 1                 # step back before the oldest
        if len(batch) < 1000:
            break
    return out[-minutes:] if minutes < len(out) else out


def context_from_1m(bars: List[List[float]], decision_i: int, seconds_left: float) -> Optional[Dict[str, Any]]:
    """Rebuild the reader's context from 1m bars only, as of `decision_i` (the
    last bar VISIBLE at decision time). 5m/15m layers are resampled from the 1m
    closes; the 1s layer is absent in history and simply omitted.

    `bars` must span the current 5-min window's start through decision_i plus at
    least 15 bars of prior history for the slow trends."""
    if decision_i < 16:
        return None
    closes = [float(b[4]) for b in bars[:decision_i + 1]]
    price = closes[-1]
    # window open = open of the first 1m bar of the current 5-min window
    win_start = (decision_i // WINDOW_MIN) * WINDOW_MIN
    btc_open = float(bars[win_start][1])
    move_abs = price - btc_open
    c5 = closes[::5][-8:]                           # ~5m resample
    c15 = closes[::15][-6:]                         # ~15m resample
    sigma = _stdev([closes[i] - closes[i - 1] for i in range(max(1, len(closes) - 30), len(closes))]) / 7.75
    # 1m-diff stdev -> approx per-second by /sqrt(60)~7.75, so drift math is on 1s units
    ctx: Dict[str, Any] = {
        "asset": "BTC", "price": round(price, 2),
        "chg_5m_pct": round(_chg(closes, 5), 3), "chg_15m_pct": round(_chg(closes, 15), 3),
        "range_pos_15m": 0.5,
        "m1": {"chg_1m": round(_chg(closes, 1), 3), "chg_5m": round(_chg(closes, 5), 3),
               "chg_15m": round(_chg(closes, 15), 3), "trend3": _trend3(closes),
               "last3": [round(c, 2) for c in closes[-3:]]},
        "window_open": round(btc_open, 2),
        "vs_open_pct": round(move_abs / btc_open * 100, 4) if btc_open else 0.0,
        "seconds_left": int(seconds_left),
        "sigma_1s": round(sigma, 3),
        "drift_prob_up": randomwalk_up_prob(move_abs, sigma, seconds_left),
    }
    hi15 = max(float(b[2]) for b in bars[decision_i - 14:decision_i + 1])
    lo15 = min(float(b[3]) for b in bars[decision_i - 14:decision_i + 1])
    ctx["range_pos_15m"] = round((price - lo15) / (hi15 - lo15), 3) if hi15 > lo15 else 0.5
    if len(c5) >= 4:
        ctx["m5"] = {"chg_5m": round(_chg(c5, 1), 3), "chg_15m": round(_chg(c5, 3), 3),
                     "chg_30m": round(_chg(c5, 6), 3), "trend3_15m": _trend3(c5),
                     "last3": [round(c, 2) for c in c5[-3:]]}
    ctx["resolution"] = "1m+5m (historical; no 1s)"
    return ctx


def window_outcome(bars: List[List[float]], win_start: int) -> Optional[bool]:
    """Did the 5-min window close UP vs its open? None if the window is incomplete."""
    end_i = win_start + WINDOW_MIN - 1
    if end_i >= len(bars):
        return None
    return float(bars[end_i][4]) > float(bars[win_start][1])


def backtest(n: int = 30, decision_frac: float = 0.7, minutes: int = 4000,
             brain: Any = None, runner: Optional[Callable] = None,
             progress: bool = True) -> Dict[str, Any]:
    """Sample `n` complete windows spread across ~`minutes` of history, ask the
    brain for each, grade on the close. `decision_frac` = how far into the window
    the read is taken (0.7 = ~3.5min in, ~90s left)."""
    bars = load_history(minutes, runner=runner)
    if len(bars) < 60:
        return {"n": 0, "error": "not enough history"}
    if brain is None:
        from hermes_trader.agents.ai_brain import get_brain
        brain = get_brain()
    # complete windows with >=15 bars of prior history
    starts = [i for i in range(15, len(bars) - WINDOW_MIN, WINDOW_MIN)]
    step = max(1, len(starts) // n)
    picked = starts[::step][:n]
    dec_offset = max(1, min(WINDOW_MIN - 1, round(WINDOW_MIN * decision_frac)))
    secs_left = (WINDOW_MIN - dec_offset) * 60

    results: List[Dict[str, Any]] = []
    for k, ws in enumerate(picked):
        outcome = window_outcome(bars, ws)
        if outcome is None:
            continue
        ctx = context_from_1m(bars, ws + dec_offset, secs_left)
        if ctx is None:
            continue
        try:
            body = brain.complete(_SYS, build_prompt(ctx, None), web_search=False)
        except Exception:
            continue
        v = _parse(str(body or ""))
        if v is None:
            continue
        up = v["verdict"] == "UP"
        won = (up == outcome)
        # paper EV: buy the side at a 0.50 fill, fee-aware
        pnl = (1.0 - 0.50) - 2 * FEE_PER_FILL if won else -0.50 - FEE_PER_FILL
        results.append({"verdict": v["verdict"], "up_prob": v["up_prob"],
                        "call": call_label(v["up_prob"], v["verdict"]),
                        "vs_open": ctx["vs_open_pct"], "rw": ctx["drift_prob_up"],
                        "outcome_up": outcome, "won": won, "pnl": round(pnl, 4)})
        if progress and (k + 1) % 5 == 0:
            hr = sum(1 for r in results if r["won"]) / len(results)
            print(f"[backtest] {len(results)} graded, hit {hr:.1%}", flush=True)

    n_g = len(results)
    if n_g == 0:
        return {"n": 0, "error": "no gradable windows"}
    hits = sum(1 for r in results if r["won"])
    pnl = sum(r["pnl"] for r in results)
    # how often the AI just followed position-vs-open (the momentum tell)
    followed = sum(1 for r in results if (r["vs_open"] > 0) == (r["verdict"] == "UP"))
    return {
        "n": n_g, "decision_frac": decision_frac, "seconds_left": secs_left,
        "hit_rate": round(hits / n_g, 3),
        "ev_per_bet_at_0.50": round(pnl / n_g, 4),
        "total_pnl_per_$": round(pnl, 3),
        "followed_position_vs_open_pct": round(followed / n_g, 3),
        "sample": results[:12],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30, help="windows to sample")
    ap.add_argument("--decision", type=float, default=0.7, help="fraction into the window for the read")
    ap.add_argument("--minutes", type=int, default=4000, help="history to span")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    r = backtest(n=args.n, decision_frac=args.decision, minutes=args.minutes)
    if args.json:
        print(json.dumps(r, indent=1)); return 0
    if r.get("n", 0) == 0:
        print("backtest:", r.get("error")); return 1
    print(f"# AI up/down backtest — {r['n']} historical windows, read at "
          f"{r['decision_frac']:.0%} in ({r['seconds_left']}s left)")
    print(f"#   hit rate         {r['hit_rate']:.1%}   (50% = no edge over the coin)")
    print(f"#   EV / bet @0.50   {r['ev_per_bet_at_0.50']:+.4f}   (fee-aware, optimistic fill)")
    print(f"#   followed vs-open {r['followed_position_vs_open_pct']:.0%}   (how often it just rode momentum)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
