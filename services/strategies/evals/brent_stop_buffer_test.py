"""Brent stop buffer, weekend-off and weekend-only profit lock backtest, 2026-11-02.

Correction (2026-11-03): this eval's original run (17:17 the same day) was
accurate at the time -- `Params.risk_cap_frac` (the live 15% risk cap)
wasn't added until `loss_fix_test.py`, about 40 minutes later (17:59,
`LOSS_AUDIT_2026-11-02.md`). Every number in
`docs/strategy/BRENT_STOP_BUFFER_TEST_2026-11-02.md` is full-port size, not
today's live rule. `bp`/`sp` below now build through
`live_params.live_brent_params`/`live_silver_params`, so a fresh run of
this file matches the real live rule. Unlike `stop_cap_test.py` (the cap
there can't change which trades fire), study A here sweeps the STOP BUFFER
itself, which the cap interacts with directly: `risk_capped_notional`
shrinks size only once a stop's own risk fraction clears 15% of equity, so
a WIDE buffer gets capped more often than a narrow one, cutting its
downside more too -- the cap can plausibly change which buffer wins. See
that doc's correction note for the re-run and whether the verdict held.

Operator, 2026-11-02 ("sure"): "backtest whether a wider Brent stop would
have survived this dip without losing more over the full history". The live
long of 42.36 Brent from 110.0099 (stop 109.25 = 0.75 under the 110.00 mid,
target 111.83) was stopped at 109.23 at 08:35:15Z for -$33.45; price then sat
around 109.1 to 109.2 and the thesis flipped short at 110.20.

Later the same day: "maybe we should restrict the trading to market hours
sunday night to friday eve", then "maybe dont run weekends off but maybe
stricter rules like dont round trip, but during the week let it ride".

Three studies on one set of datasets:

  A. buffer   Brent stop buffer: fixed 0.5 / 0.75 (live) / 0.9 / 1.0 / 1.25 /
              1.5; m x ATR14_1h (m in ATR_MULTS); max(0.75, p% of the mid)
              (p in PCTS; desk_tick_sim.Params.fixed_buffer_floor_frac).
  B. weekend  no new entries in the oil futures weekend (Fri 16:00 to Sun
              18:00 New York, `desk_tick_sim.weekend_closed`): "hold" keeps an
              open position, "flatten" also closes it at the window open. On
              Brent live, Brent with the best buffer from A, and silver live.
  C. lock     the profit lock (PROFIT_LOCK_TEST_2026-11-01.md's grid, plus
              target-share triggers and break-even plus fees once up 1R), but
              only inside the weekend window (`Params.lock_weekend_only`);
              weekdays run the live rules. On Brent live and silver live.

Datasets, live rules otherwise ($64, market entry off the 24h range, 2R
gate, target 0.5% inside the 24h edge, limit target, 20x, 15-minute ticks):
  brent_journal          the live oil journal (READ ONLY, --oil-theses-dir; skipped when not given)
  brent_replay30         the 30 replayed Brent theses (/tmp/thesis-replay-30b)
  brent_<ctrl>_replay    always_long / always_short / daily_sign on the replay window
  brent_<ctrl>_30d       the same on the last 30 days
  silver_theses          the 30 replayed silver theses, their own cache (B and C)
  silver_journal         the live metals journal's silver theses (READ ONLY, --metals-theses-dir)
  silver_<ctrl>[_recent] silver controls on the replay window / last 30 days

Regression rows: the 2026-11-02 long re-judged with every buffer
(`todays_long`), the Sat 10-31 Brent long under every lock
(`weekend_long`), and this weekend's closed trades against the window
(`WEEKEND_TRADES`).

    python -m services.strategies.evals.brent_stop_buffer_test \\
        [--oil-theses-dir <pathia>/.state/strategies/oil/theses] \\
        [--metals-theses-dir <pathia>/.state/strategies/metals/theses]

Writes /tmp/brent-buffer/report.json, summary.csv and entries.csv. Reads
candles through the eval disk cache and the venue's public /info; never
touches the live account, the live config or the journal. No LLM calls.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from pathiel.agents.bracket import check_leverage
from pathiel.models.types import Candle

from services.strategies.backtest import data as D
from services.strategies.backtest.desk_tick_sim import (
    Direction, Params, Run, _levels, _setup, profit_lock_stop, simulate, weekend_closed)
from services.strategies.evals.desk_tick_backtest import DAY_MS, HOUR_MS, journaled_direction
from services.strategies.evals.live_params import live_brent_params, live_silver_params
from services.strategies.evals.profit_lock_test import LIVE_TRADE as WEEKEND_LIVE_TRADE
from services.strategies.evals.profit_lock_test import score
from services.strategies.evals.silver_stop_floor_test import attribute, attribution_summary
from services.strategies.evals.silver_stop_floor_test import summarize as floor_summarize
from services.strategies.evals.stop_cap_test import FIFTEEN_MS, START, _iso, closed_only, same_market, walk_entry

BASELINE = "live"
FIXED = (0.5, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0, 1.1, 1.25, 1.5)   # the ask plus 0.8-0.95 / 1.1 for the plateau check
LIVE_BUFFER = 0.75
ATR_MULTS = (1.0, 1.3, 1.6, 2.0)
PCTS = (0.6, 0.7, 0.8, 0.85, 0.9, 0.95, 1.0, 1.1, 1.2)
CONTROLS = ("always_long", "always_short", "daily_sign")
LOCK_TRIGGERS = (1.0, 1.5, 2.0)
LOCK_LEVELS = (0.0, 0.5, 1.0)
TARGET_FRACS = (0.5, 0.75)
MAX_STOP_FRAC_OF_LIQ = 0.5      # the 20x line: a stop may use at most half the way to liquidation
SILVER_LIVE = {"stop_buffer_atr": 1.3, "stop_buffer_floor_frac": 0.008}   # SILVER_STOP_FLOOR_TEST_2026-11-02.md

# The live long of 2026-11-02 (operator's numbers, desk loop log). Opened
# 03:50Z inside the 03:45 bar on a 110.00 mid; the 24h range then was
# 110.14 to 112.39, so the stop sat 0.75 under the mid and the target 0.5%
# inside 112.39. Stopped 08:35:15Z; the thesis flipped short (entry 110.20)
# at 08:42Z. Invalidation of the long thesis: a 1h close under 109.55.
TODAYS_LONG = {"side": "long", "entry_bar_utc": (2026, 11, 2, 3, 45), "decision_mid": 110.00,
               "entry_px": 110.0099, "size": 42.36, "stop_px": 109.25, "tp_px": 111.83, "liq_px": 107.20,
               "invalidation_px": 109.55, "stop_fill_px": 109.23, "stop_utc": "2026-11-02 08:35Z",
               "net_usd": -33.45, "flip_utc": (2026, 11, 2, 8, 42)}

# This weekend's closed trades (operator, 2026-11-02), checked against the window.
WEEKEND_TRADES = (
    {"desk": "oil", "side": "long", "entry_utc": (2026, 10, 31, 16, 18), "exit_utc": (2026, 11, 1, 18, 20),
     "net_usd": 7.83},
    {"desk": "silver", "side": "short", "entry_utc": (2026, 11, 2, 0, 51), "exit_utc": (2026, 11, 2, 1, 5),
     "net_usd": -1.82},
    {"desk": "silver", "side": "short", "entry_utc": (2026, 11, 2, 1, 18), "exit_utc": (2026, 11, 2, 5, 30),
     "net_usd": -2.56},
    {"desk": "oil", "side": "long", "entry_utc": (2026, 11, 2, 3, 50), "exit_utc": (2026, 11, 2, 8, 35),
     "net_usd": -33.45},
)


def _ms(parts: Tuple[int, ...]) -> int:
    return int(datetime(*parts, tzinfo=timezone.utc).timestamp() * 1000)


# ── the grids (pure) ───────────────────────────────────────────────────────

def pct_name(p: float) -> str:
    """pct_0.8, pct_0.85, pct_1.0: two decimals only when the second is used."""
    return f"pct_{p:.2f}" if round(p * 100) % 10 else f"pct_{p:.1f}"


def buffer_variants() -> Dict[str, Dict[str, Any]]:
    """Study A: name -> Params overrides on Brent's live params (fixed 0.75)."""
    out: Dict[str, Dict[str, Any]] = {BASELINE: {}}
    for b in FIXED:
        if b != LIVE_BUFFER:
            out[f"fixed_{b:.2f}"] = {"stop_buffer": b}
    for m in ATR_MULTS:
        out[f"atr_{m:.1f}"] = {"stop_buffer_atr": m}
    for p in PCTS:
        out[pct_name(p)] = {"fixed_buffer_floor_frac": round(p / 100.0, 6)}
    return out


# Each family's grid in order, for the plateau check.
FAMILIES = {
    "fixed": [BASELINE if b == LIVE_BUFFER else f"fixed_{b:.2f}" for b in FIXED],
    "atr": [f"atr_{m:.1f}" for m in ATR_MULTS],
    "pct": [pct_name(p) for p in PCTS],
}


WEEKEND_ONLY = ("wk_hold", "wk_flat")


def weekend_variants(base_ov: Dict[str, Any], prefix: str = "") -> Dict[str, Dict[str, Any]]:
    """Study B: 24/7 (the given rule trading through the weekend), weekend
    hold, weekend flatten. Since 2026-11-05 the live Brent rule IS wk_flat
    (`live_params().weekend_off == "flatten"`, operator "weekend flat"), so
    24/7 sets `weekend_off=None` explicitly and the `live` baseline row ties
    `wk_flat` exactly; `test_brent_stop_buffer_eval.py` pins both."""
    p = f"{prefix}_" if prefix else ""
    return {f"{p}24/7": {**base_ov, "weekend_off": None}, f"{p}wk_hold": {**base_ov, "weekend_off": "hold"},
            f"{p}wk_flat": {**base_ov, "weekend_off": "flatten"}}


def lock_variants(base_ov: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
    """Study C: the live rule, every weekend-only lock, and the 24/7 version
    of break-even plus fees as a reference."""
    base = dict(base_ov or {})
    wk = {**base, "lock_weekend_only": True}
    out: Dict[str, Dict[str, Any]] = {BASELINE: dict(base)}
    for trail in (False, True):
        for trig in LOCK_TRIGGERS:
            for lock in LOCK_LEVELS:
                if lock < trig:
                    out[f"wk_{'trail' if trail else 'lock'}_t{trig:g}_l{lock:g}"] = {
                        **wk, "lock_trigger_r": trig, "lock_r": lock, "lock_trail": trail}
    for frac in TARGET_FRACS:
        for lock in (0.0, 0.5):
            out[f"wk_tgt{int(frac * 100)}_l{lock:g}"] = {**wk, "lock_trigger_target_frac": frac, "lock_r": lock}
    for trig in LOCK_TRIGGERS:     # break-even plus the taker round trip; t1 is the operator's simple version
        out[f"wk_befees_t{trig:g}"] = {**wk, "lock_trigger_r": trig, "lock_r": 0.0, "lock_plus_fees": True}
    out["all_befees_t1"] = {**base, "lock_trigger_r": 1.0, "lock_r": 0.0, "lock_plus_fees": True}
    return out


# ── per-run numbers (pure) ─────────────────────────────────────────────────

def equity_before(run: Run, t: int) -> float:
    """The marked equity on the last tick strictly before `t` (the start
    equity before the first tick)."""
    eq = run.params.starting_equity if run.params else START
    for ts, mark in run.equity_curve:
        if ts >= t:
            break
        eq = mark
    return eq


def stop_liq_frac(entry: float, stop: float, params: Params) -> Optional[float]:
    """The stop's share of the way to liquidation at the params' leverage
    (the 20x line refuses anything over 0.5)."""
    if entry <= 0 or stop <= 0 or entry == stop:
        return None
    return check_leverage(entry, stop, params.leverage, params.max_leverage).get("stop_frac_of_liq")


def risk_numbers(run: Run) -> Dict[str, Any]:
    """Worst single loss ($ and % of the equity before it opened), the
    closest any stop came to the liquidation line, and the trades opened
    inside the weekend window."""
    p = run.params or Params()
    worst, worst_pct, closest = 0.0, 0.0, 0.0
    weekend_opened = 0
    for tr in run.trades:
        if tr.net_pnl_usd < worst:
            worst = tr.net_pnl_usd
        eq = equity_before(run, tr.entry_time)
        if eq > 0:
            worst_pct = min(worst_pct, tr.net_pnl_usd / eq)
        f = stop_liq_frac(tr.entry_px, tr.orig_stop_px if tr.orig_stop_px else tr.stop_px, p)
        if f is not None:
            closest = max(closest, f)
        weekend_opened += 1 if weekend_closed(tr.entry_time) else 0
    return {"worst_loss_usd": round(worst, 2), "worst_loss_pct": round(worst_pct * 100, 1),
            "max_stop_liq_frac": round(closest, 3), "weekend_opened": weekend_opened}


def summarize(run: Run, start_ms: int, end_ms: int) -> Dict[str, Any]:
    s = floor_summarize(run, start_ms, end_ms)
    s.update(risk_numbers(run))
    return s


def score_vs_live(rows: Dict[str, Dict[str, Any]], baseline: str = BASELINE) -> Dict[str, Dict[str, Any]]:
    """profit_lock_test.score with `baseline` as the reference."""
    return score({ds: {("baseline" if v == baseline else v): s for v, s in by_v.items()}
                  for ds, by_v in rows.items()})


def _good(scores: Dict[str, Dict[str, Any]], name: str) -> bool:
    """Beats live on most datasets with a geo-mean ratio of 1 or more."""
    s = scores.get(name)
    return bool(s) and s["beats"] > s["of"] / 2.0 and s["geo_mean_ratio"] >= 1.0


def plateau(scores: Dict[str, Dict[str, Any]], name: str, families: Dict[str, List[str]] = FAMILIES) -> bool:
    """A plateau, not a spike: in every grid family that holds `name`, each
    neighbour that is not live itself also beats live on most datasets with
    a geo-mean ratio of 1 or more. A neighbour that IS live leaves that side
    open, so at least one non-live neighbour must exist. False for a name in
    no family."""
    found = False
    for grid in families.values():
        if name not in grid:
            continue
        found = True
        i = grid.index(name)
        nbrs = [grid[j] for j in (i - 1, i + 1) if 0 <= j < len(grid) and grid[j] != BASELINE]
        if not nbrs or not all(_good(scores, n) for n in nbrs):
            return False
    return found


def pick_best(scores: Dict[str, Dict[str, Any]]) -> Optional[str]:
    """The candidate with the most datasets beaten, ties to the higher geo-mean."""
    if not scores:
        return None
    return max(scores, key=lambda v: (scores[v]["beats"] - scores[v]["loses"], scores[v]["geo_mean_ratio"], v))


def verdict(scores: Dict[str, Dict[str, Any]], max_liq: Dict[str, float], name: Optional[str],
            families: Optional[Dict[str, List[str]]] = FAMILIES) -> Dict[str, Any]:
    """The operator's rule: adopt only if it beats live on most datasets, is a
    plateau (when it has a grid), and never puts a stop past half the way to
    liquidation at 20x."""
    if name is None or name not in scores:
        return {"adopt": False, "candidate": name, "why": "no candidate"}
    s = scores[name]
    most = s["beats"] > s["of"] / 2.0
    flat = plateau(scores, name, families) if families else True
    liq_ok = max_liq.get(name, 0.0) <= MAX_STOP_FRAC_OF_LIQ + 1e-9
    why = [w for w, ok in (("beats live on most datasets", most), ("plateau", flat),
                           ("stop inside half the way to liquidation", liq_ok)) if not ok]
    return {"adopt": most and flat and liq_ok, "candidate": name, "beats": s["beats"], "loses": s["loses"],
            "of": s["of"], "geo_mean_ratio": s["geo_mean_ratio"], "plateau": flat, "liq_ok": liq_ok,
            "fails": why}


def rule_pass(scores: Dict[str, Dict[str, Any]], max_liq: Dict[str, float],
              families: Optional[Dict[str, List[str]]] = FAMILIES) -> List[str]:
    """Every candidate that clears all three of the operator's rules."""
    return sorted(v for v in scores if verdict(scores, max_liq, v, families)["adopt"])


# ── regression rows (pure on their inputs) ─────────────────────────────────

def flip_direction(side: str, invalidation_px: Optional[float], flip_ms: Optional[int]) -> Callable[[int], Direction]:
    """`side` (with its invalidation) until `flip_ms`, the other side after."""
    other = "short" if side == "long" else "long"

    def at(t: int) -> Direction:
        if flip_ms is not None and t >= flip_ms:
            return Direction(side=other)
        return Direction(side=side, invalidation_px=invalidation_px)
    return at


def todays_long(c15: Sequence[Candle], c1h: Sequence[Candle], named: Dict[str, Dict[str, Any]], base: Params,
                trade: Dict[str, Any] = TODAYS_LONG) -> Dict[str, Any]:
    """The 2026-11-02 long re-judged with each variant's stop by the sim's own
    `_setup` (same levels, same 110.00 mid, the 2R and 20x gates), then walked
    on from the live fill by `stop_cap_test.walk_entry` (stop before target,
    the 1h invalidation close, the 08:42Z flip to short at the next bar's
    open). Dollars at the live size, price only (no fees, no slippage)."""
    t = _ms(trade["entry_bar_utc"])
    hours = sorted(c1h, key=lambda c: c.t)
    closed = [c for c in hours if c.t + HOUR_MS <= t]
    by_close = {c.t + HOUR_MS: c for c in hours}
    d_at = flip_direction(trade["side"], trade.get("invalidation_px"),
                          _ms(trade["flip_utc"]) if trade.get("flip_utc") else None)
    bars = [c for c in sorted(c15, key=lambda c: c.t) if c.t >= t]
    out: Dict[str, Any] = {"trade": {k: v for k, v in trade.items()}, "data_end": _iso(bars[-1].t + FIFTEEN_MS)
                           if bars else None, "low_after_entry": min((c.l for c in bars), default=None),
                           "variants": {}}
    for v, ov in named.items():
        p = dataclasses.replace(base, **ov)
        need = max(15, p.entry_lookback_h, p.target_lookback_h)
        lv = _levels(closed[-need:], p.entry_lookback_h, p.target_lookback_h)
        if lv is None:
            out["variants"][v] = {"result": "no levels"}
            continue
        s = _setup(d_at(t), trade["decision_mid"], lv, p, p.leverage)
        if not s:
            out["variants"][v] = {"result": "no setup"}
            continue
        stop = s.get("stop_px")
        if s.get("refused"):
            # the stop it would have asked for, for the table
            raw = dataclasses.replace(p, min_r=0.0, max_leverage=1e9)
            rs = _setup(d_at(t), trade["decision_mid"], lv, raw, raw.leverage) or {}
            stop = rs.get("stop_px")
            r_ = ((rs["target_px"] - rs["entry_px"]) / (rs["entry_px"] - rs["stop_px"])
                  if rs.get("stop_px") and rs["entry_px"] != rs["stop_px"] else None)
            out["variants"][v] = {"result": f"refused ({s['refused']})", "stop_px": round(stop, 4) if stop else None,
                                  "r_at_entry": round(r_, 2) if r_ else None,
                                  "stop_liq_frac": stop_liq_frac(trade["entry_px"], stop, p) if stop else None,
                                  "exit": None, "pnl_usd": 0.0}
            continue
        reason, px, t_exit = walk_entry(trade["side"], t, trade["entry_px"], stop, s["target_px"], c15, by_close,
                                        d_at, p)
        sign = 1.0 if trade["side"] == "long" else -1.0
        out["variants"][v] = {
            "result": reason, "stop_px": round(stop, 4), "target_px": round(s["target_px"], 4),
            "r_at_entry": round((s["target_px"] - s["entry_px"]) / (s["entry_px"] - stop) * sign, 2),
            "stop_liq_frac": stop_liq_frac(trade["entry_px"], stop, p), "exit": _iso(t_exit),
            "exit_px": round(px, 4), "pnl_usd": round(sign * (px - trade["entry_px"]) * trade["size"], 2)}
    return out


def replay_locked(side: str, entry: float, stop: float, target: float, bars: Sequence[Candle],
                  params: Params) -> Dict[str, Any]:
    """One open position walked over `bars` (the bars after its fill bar),
    `profit_lock_test.replay_position`'s per-bar order, plus the target-share
    trigger, the fee-covering lock and the weekend-only gate (a bar outside
    the window neither folds in its best price nor moves the stop). Prices
    only, no slippage. Parity with `simulate` is gate-tested. Pure."""
    is_long = side == "long"
    sign = 1.0 if is_long else -1.0
    risk = abs(entry - stop)
    cur, moved, best, best_r = stop, False, None, 0.0
    for bar in bars:
        if (bar.l <= cur) if is_long else (bar.h >= cur):
            px = (min(cur, bar.o) if is_long else max(cur, bar.o)) if moved else cur
            return {"reason": "lock" if moved else "stop", "exit_px": px, "exit_time": bar.t, "best_r": best_r}
        if params.target_mode == "limit":
            hit = (bar.h >= target + params.tick_size - 1e-9) if is_long else (bar.l <= target - params.tick_size + 1e-9)
        else:
            hit = (bar.h >= target) if is_long else (bar.l <= target)
        if hit:
            return {"reason": "target", "exit_px": target, "exit_time": bar.t, "best_r": best_r}
        if params.lock_weekend_only and not weekend_closed(bar.t):
            continue
        b = bar.h if is_long else bar.l
        best = b if best is None else (max(best, b) if is_long else min(best, b))
        best_r = max(best_r, sign * (best - entry) / risk)
        new = profit_lock_stop(side, entry, risk, best, params, target)
        if new is not None and ((new > cur + 1e-12) if is_long else (new < cur - 1e-12)):
            cur, moved = new, True
            if params.lock_confirm_close and ((bar.c <= cur) if is_long else (bar.c >= cur)):
                return {"reason": "lock", "exit_px": cur, "exit_time": bar.t, "best_r": best_r}
    last = bars[-1] if bars else None
    return {"reason": "open", "exit_px": last.c if last else entry, "exit_time": last.t if last else None,
            "best_r": best_r}


def weekend_long(c15: Sequence[Candle], named: Dict[str, Dict[str, Any]], base: Params,
                 trade: Dict[str, Any] = WEEKEND_LIVE_TRADE, closed_utc: Tuple[int, ...] = (2026, 11, 1, 18, 20)
                 ) -> Dict[str, Any]:
    """The Sat 10-31 long under each lock, walked from the bar after its fill
    up to the bar the operator closed it in (11-01 18:15). A variant that has
    not exited by then is closed by hand like the live trade (+$7.83).
    Net = price move at the live size less the sim's fees."""
    fill = _ms(trade["entry_bar_utc"])
    closed_ms = _ms(closed_utc)
    bars = [c for c in sorted(c15, key=lambda c: c.t) if fill < c.t and c.t + FIFTEEN_MS <= closed_ms]
    out: Dict[str, Any] = {"trade": trade, "bars": len(bars), "all_in_window": all(weekend_closed(c.t) for c in bars),
                           "variants": {}}
    size, entry = trade["size"], trade["entry_px"]
    for v, ov in named.items():
        p = dataclasses.replace(base, **ov)
        r = replay_locked(trade["side"], entry, trade["stop_px"], trade["target_px"], bars, p)
        if r["reason"] == "open":
            out["variants"][v] = {"reason": "closed by hand 11-01 18:20Z", "net_usd": trade["closed_net_usd"],
                                  "best_r": round(r["best_r"], 2), "exit_px": None, "exit_time": None}
            continue
        gross = (r["exit_px"] - entry) * size * (1.0 if trade["side"] == "long" else -1.0)
        fees = base.taker_fee * entry * size + (base.maker_fee if r["reason"] == "target" else base.taker_fee) \
            * r["exit_px"] * size
        out["variants"][v] = {"reason": r["reason"], "exit_px": round(r["exit_px"], 3), "exit_time": _iso(r["exit_time"]),
                              "best_r": round(r["best_r"], 2), "net_usd": round(gross - fees, 2)}
    return out


def live_ties_wk_flat(results: Dict[str, Any], baseline: str = BASELINE, candidate: str = "wk_flat") -> Dict[str, Any]:
    """The 2026-11-05 adoption check: on every Brent dataset the `live` row
    (the live rule as given) and `wk_flat` must summarize identically.
    `pass` is False the day live_params() drifts from the adopted variant."""
    per = {ds: r["variants"].get(baseline) == r["variants"].get(candidate) for ds, r in results.items()}
    return {"pass": bool(per) and all(per.values()), "per_dataset": per}


def weekend_trade_rows(trades: Sequence[Dict[str, Any]] = WEEKEND_TRADES) -> List[Dict[str, Any]]:
    """This weekend's closed trades: was the entry inside the window (so
    weekend-off would have skipped it), and was the position open at the
    window start (so flatten would have closed it)?"""
    rows = []
    for tr in trades:
        e, x = _ms(tr["entry_utc"]), _ms(tr["exit_utc"])
        rows.append({**{k: tr[k] for k in ("desk", "side", "net_usd")}, "entry": _iso(e), "exit": _iso(x),
                     "entry_in_window": weekend_closed(e),
                     "open_at_window_start": (not weekend_closed(e)) and any(
                         weekend_closed(t) for t in range(e - e % FIFTEEN_MS + FIFTEEN_MS, x, FIFTEEN_MS))})
    return rows


# ── data + runs ────────────────────────────────────────────────────────────

def _window(c15, c1h, start_ms, end_ms):
    return [c for c in c15 if start_ms <= c.t < end_ms], [c for c in c1h if c.t < end_ms]


def run_dataset(c15: Sequence[Candle], c1h: Sequence[Candle], direction_at, base: Params, start_ms: int,
                end_ms: int, named: Dict[str, Dict[str, Any]], baseline: str = BASELINE,
                attribute_entries: bool = False, funding=None) -> Dict[str, Any]:
    """Every variant on one dataset; with `attribute_entries`, each
    baseline entry re-judged with the variant's stop
    (`silver_stop_floor_test.attribute`)."""
    w15, w1h = _window(c15, c1h, start_ms, end_ms)
    out: Dict[str, Any] = {"window": [_iso(start_ms), _iso(end_ms)], "variants": {}, "entries": {}}
    base_p = dataclasses.replace(base, **named[baseline])
    base_run = simulate(w15, w1h, direction_at, base_p, funding)
    for v, ov in named.items():
        p = dataclasses.replace(base, **ov)
        run = base_run if v == baseline else simulate(w15, w1h, direction_at, p, funding)
        s = summarize(run, start_ms, end_ms)
        if v != baseline and attribute_entries:
            rows = attribute(base_run, w15, w1h, direction_at, base_p, p)
            s["vs_live"] = attribution_summary(rows)
            out["entries"][v] = rows
        out["variants"][v] = s
    return out


SUMMARY_FIELDS = ["study", "dataset", "variant", "final_equity", "return_pct", "max_dd_pct", "trades", "win_rate",
                  "avg_r", "worst_loss_usd", "worst_loss_pct", "max_stop_liq_frac", "weekend_opened",
                  "stop_to_win", "win_to_stop", "gate_blocked_entries"]
ENTRY_FIELDS = ["dataset", "variant", "time", "side", "entry_px", "target_px", "atr14", "class", "gate",
                "base_stop", "base_reason", "base_r", "base_eq_pct", "var_stop", "var_reason", "var_r", "var_eq_pct"]


def summary_csv_rows(study: str, results: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for ds, r in results.items():
        for v, s in r["variants"].items():
            a = s.get("vs_live") or {}
            out.append({"study": study, "dataset": ds, "variant": v,
                        **{k: s.get(k) for k in SUMMARY_FIELDS[3:13]},
                        "stop_to_win": a.get("stop_to_win", ""), "win_to_stop": a.get("win_to_stop", ""),
                        "gate_blocked_entries": sum(a.get("gate_blocked", {}).values()) if a else ""})
    return out


def entry_csv_rows(results: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for ds, r in results.items():
        for v, rows in r.get("entries", {}).items():
            for e in rows:
                b, x = e["base"], e["var"] or {}
                out.append({"dataset": ds, "variant": v, "time": e["time"], "side": e["side"],
                            "entry_px": e["entry_px"], "target_px": e["target_px"], "atr14": e["atr14"],
                            "class": e["class"], "gate": e["gate"] or "", "base_stop": b["stop_px"],
                            "base_reason": b["reason"], "base_r": b["r_net"], "base_eq_pct": b["eq_pct"],
                            "var_stop": x.get("stop_px", ""), "var_reason": x.get("reason", ""),
                            "var_r": x.get("r_net", ""), "var_eq_pct": x.get("eq_pct", "")})
    return out


def _scores(results: Dict[str, Any], groups: Dict[str, List[str]], baseline: str = BASELINE) -> Dict[str, Any]:
    return {g: score_vs_live({k: results[k]["variants"] for k in ks}, baseline) for g, ks in groups.items() if ks}


def main(argv: Optional[List[str]] = None) -> int:
    from services.strategies.evals.cross_market_backtest import mean_atr
    from services.strategies.evals.market_scout import direction_series, load_coin_candles

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--oil-theses-dir", default=os.environ.get("PATHIEL_OIL_THESES_DIR", ""),
                    help="READ ONLY: the live oil journal's theses (skipped when empty)")
    ap.add_argument("--metals-theses-dir", default=os.environ.get("PATHIEL_METALS_THESES_DIR", ""),
                    help="READ ONLY: the live metals journal's theses (skipped when empty)")
    ap.add_argument("--oil-replay-dir", default="/tmp/thesis-replay-30b/oil/theses")
    ap.add_argument("--silver-theses-dir", default="/tmp/thesis-replay-silver/theses")
    ap.add_argument("--silver-cache-dir", default="/tmp/thesis-backtest/cache")
    ap.add_argument("--silver-max-leverage", type=float, default=None)
    ap.add_argument("--cache-dir", default="/tmp/brent-buffer/cache", help="fresh Brent + silver candles")
    ap.add_argument("--out-dir", default="/tmp/brent-buffer")
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    os.makedirs(a.cache_dir, exist_ok=True)
    t0 = time.time()
    log_path = os.path.join(a.out_dir, "progress.log")

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} brent-buffer: {msg}"
        print(line, flush=True)
        with open(log_path, "a") as fh:
            fh.write(line + "\n")

    def fresh(coin: str) -> Tuple[List[Candle], List[Candle]]:
        got = load_coin_candles(coin, a.cache_dir)
        asof = int(os.path.getmtime(D._cache_path(a.cache_dir, coin, "candles_15m_4820")) * 1000)
        return closed_only(got["c15"], FIFTEEN_MS, asof), closed_only(got["c1h"], HOUR_MS, asof)

    # ── Brent data sets
    b15, b1h = fresh("xyz:BRENTOIL")
    b_end = b15[-1].t + FIFTEEN_MS
    funding = D.fetch_funding_cached("xyz:BRENTOIL", b15[0].t, b_end, a.cache_dir)
    bp = live_brent_params(size_slippage=True, starting_equity=START)
    brent_sets: Dict[str, Tuple[Callable[[int], Any], int, int]] = {}
    for label, tdir, end_rule in (("brent_journal", a.oil_theses_dir, "data"),
                                  ("brent_replay30", a.oil_replay_dir, "last+1d")):
        theses = D.load_journaled_theses(tdir) if tdir else []
        if not theses:
            log(f"{label}: no theses at {tdir!r}, skipped")
            continue
        start = min(th.created_at_ms for th in theses)
        end = b_end if end_rule == "data" else min(b_end, max(th.created_at_ms for th in theses) + DAY_MS)
        brent_sets[label] = (journaled_direction(theses), start, end)
    rp = brent_sets.get("brent_replay30")
    for s in CONTROLS:
        if rp:
            brent_sets[f"brent_{s}_replay"] = (direction_series(s, b1h), rp[1], rp[2])
        brent_sets[f"brent_{s}_30d"] = (direction_series(s, b1h), b_end - 30 * DAY_MS, b_end)

    # ── A: buffers
    named_a = buffer_variants()
    res_a: Dict[str, Any] = {}
    for ds, (d_at, st, en) in brent_sets.items():
        res_a[ds] = run_dataset(b15, b1h, d_at, bp, st, en, named_a, attribute_entries=True, funding=funding)
    log(f"A buffers: {len(res_a)} datasets x {len(named_a)} variants ({time.time() - t0:.0f}s)")
    groups_b = {"thesis": [k for k in ("brent_journal", "brent_replay30") if k in res_a],
                "controls_replay": [k for k in res_a if k.endswith("_replay")],
                "controls_30d": [k for k in res_a if k.endswith("_30d")], "all": list(res_a)}
    scores_a = _scores(res_a, groups_b)
    max_liq = {v: max(r["variants"][v]["max_stop_liq_frac"] for r in res_a.values()) for v in named_a}
    best = pick_best(scores_a["all"])
    verdict_a = verdict(scores_a["all"], max_liq, best)
    verdict_a["rule_pass"] = rule_pass(scores_a["all"], max_liq)
    today = todays_long(b15, b1h, named_a, bp)

    # ── B: weekend off on Brent (live + best buffer) and silver (live)
    # Since 2026-11-05 the live Brent rule is wk_flat, so the baseline is the
    # live rule as given ({}), 24/7 is its own explicit row, and the
    # `live_is_wk_flat` check below must hold on every dataset.
    best_ov = named_a[best] if best else {}
    named_b = {BASELINE: {}, **weekend_variants({}, ""), **weekend_variants(best_ov, best or "best")}
    res_b_brent: Dict[str, Any] = {}
    for ds, (d_at, st, en) in brent_sets.items():
        res_b_brent[ds] = run_dataset(b15, b1h, d_at, bp, st, en, named_b, funding=funding)
    ties = live_ties_wk_flat(res_b_brent)
    log(f"B weekend Brent done ({time.time() - t0:.0f}s); live == wk_flat on "
        f"{sum(ties['per_dataset'].values())} of {len(ties['per_dataset'])} datasets")

    # silver: the replay (its own cache), the live journal when given, the controls
    if a.silver_max_leverage is None:
        from services.strategies.evals.market_scout import fetch_market_meta
        a.silver_max_leverage = float((fetch_market_meta(["xyz:SILVER"]).get("xyz:SILVER") or {})
                                      .get("maxLeverage") or 20)
    silver = load_coin_candles("xyz:SILVER", a.silver_cache_dir)
    s15, s1h = silver["c15"], silver["c1h"]
    atr, _ = mean_atr(s1h)
    sp = live_silver_params(a.silver_max_leverage, atr, starting_equity=START)
    s_theses = D.load_journaled_theses(a.silver_theses_dir)
    silver_sets: Dict[str, Tuple[Sequence[Candle], Sequence[Candle], Callable[[int], Any], int, int]] = {}
    if s_theses:
        s_start = min(th.created_at_ms for th in s_theses)
        s_end = max(c.t for c in s15) + FIFTEEN_MS
        silver_sets["silver_theses"] = (s15, s1h, journaled_direction(s_theses), s_start, s_end)
        for s in CONTROLS:
            silver_sets[f"silver_{s}"] = (s15, s1h, direction_series(s, s1h), s_start, s_end)
    f15, f1h = fresh("xyz:SILVER")
    f_end = f15[-1].t + FIFTEEN_MS
    m_all = D.load_journaled_theses(a.metals_theses_dir) if a.metals_theses_dir else []
    m_theses = same_market(m_all, f1h) if m_all else []
    if m_theses:
        silver_sets["silver_journal"] = (f15, f1h, journaled_direction(m_theses),
                                         min(th.created_at_ms for th in m_theses), f_end)
    else:
        log(f"silver_journal: no theses at {a.metals_theses_dir!r}, skipped")
    for s in CONTROLS:
        silver_sets[f"silver_{s}_recent"] = (f15, f1h, direction_series(s, f1h), f_end - 30 * DAY_MS, f_end)
    named_b_silver = {(BASELINE if k == "24/7" else k): v for k, v in weekend_variants({}, "").items()}
    res_b_silver: Dict[str, Any] = {}
    for ds, (c15_, c1h_, d_at, st, en) in silver_sets.items():
        res_b_silver[ds] = run_dataset(c15_, c1h_, d_at, sp, st, en, named_b_silver)
    log(f"B weekend silver done ({time.time() - t0:.0f}s)")

    # ── C: weekend-only profit lock
    named_c = lock_variants()
    res_c_brent: Dict[str, Any] = {}
    for ds, (d_at, st, en) in brent_sets.items():
        res_c_brent[ds] = run_dataset(b15, b1h, d_at, bp, st, en, named_c, funding=funding)
    res_c_silver: Dict[str, Any] = {}
    for ds, (c15_, c1h_, d_at, st, en) in silver_sets.items():
        res_c_silver[ds] = run_dataset(c15_, c1h_, d_at, sp, st, en, named_c)
    log(f"C weekend lock done ({time.time() - t0:.0f}s)")
    weekend_trade = weekend_long(b15, named_c, bp)

    groups_s = {"thesis": [k for k in ("silver_theses", "silver_journal") if k in res_b_silver],
                "controls": [k for k in res_b_silver if k not in ("silver_theses", "silver_journal")],
                "all": list(res_b_silver)}
    scores_b = {"brent": _scores(res_b_brent, groups_b), "silver": _scores(res_b_silver, groups_s)}
    scores_c = {"brent": _scores(res_c_brent, groups_b), "silver": _scores(res_c_silver, groups_s)}
    lock_liq = {v: 0.0 for v in named_c}
    verdict_c = {desk: {**verdict(sc["all"], lock_liq, pick_best(sc["all"]), families=lock_families()),
                        "rule_pass": rule_pass(sc["all"], lock_liq, lock_families())}
                 for desk, sc in scores_c.items()}
    wk_liq = {v: 0.0 for v in named_b}
    # the weekend question is asked of the live stop; the best-buffer rows are reference only
    verdict_b = {desk: verdict(sc["all"], wk_liq, pick_best({k: x for k, x in sc["all"].items()
                                                             if k in WEEKEND_ONLY}), families=None)
                 for desk, sc in scores_b.items()}

    with open(os.path.join(a.out_dir, "summary.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SUMMARY_FIELDS)
        w.writeheader()
        for study, res in (("A_buffer", res_a), ("B_weekend_brent", res_b_brent), ("B_weekend_silver", res_b_silver),
                           ("C_lock_brent", res_c_brent), ("C_lock_silver", res_c_silver)):
            w.writerows(summary_csv_rows(study, res))
    with open(os.path.join(a.out_dir, "entries.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=ENTRY_FIELDS)
        w.writeheader()
        w.writerows(entry_csv_rows(res_a))
    strip = lambda res: {k: {kk: vv for kk, vv in r.items() if kk != "entries"} for k, r in res.items()}  # noqa: E731
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()), "seconds": round(time.time() - t0, 1),
        "brent_data_end": _iso(b_end), "silver_data_end": _iso(f_end), "silver_max_leverage": a.silver_max_leverage,
        "A": {"variants": named_a, "datasets": strip(res_a), "scores": scores_a, "max_stop_liq_frac": max_liq,
              "best": best, "verdict": verdict_a, "todays_long": today},
        "B": {"variants_brent": named_b, "brent": strip(res_b_brent), "silver": strip(res_b_silver),
              "scores": scores_b, "verdict": verdict_b, "weekend_trades": weekend_trade_rows(),
              "live_is_wk_flat": ties},
        "C": {"variants": named_c, "brent": strip(res_c_brent), "silver": strip(res_c_silver), "scores": scores_c,
              "verdict": verdict_c, "weekend_long": weekend_trade},
    }
    out_path = os.path.join(a.out_dir, "report.json")
    with open(out_path, "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    _print(report)
    print(f"\nreport: {out_path}\nsummary: {os.path.join(a.out_dir, 'summary.csv')}"
          f"\nentries: {os.path.join(a.out_dir, 'entries.csv')}")
    return 0


def lock_families() -> Dict[str, List[str]]:
    """Study C's grid neighbours. Trigger axis: the fixed locks, the trails
    and break-even plus fees at each lock level. Lock axis: at each trigger,
    lock 0, break-even plus fees, 0.5R, 1R. Target-share triggers on their
    own axis. A plateau has to hold on every axis a variant sits on."""
    fams: Dict[str, List[str]] = {}
    for kind in ("lock", "trail"):
        for lock in LOCK_LEVELS:
            fams[f"{kind}_l{lock:g}"] = [f"wk_{kind}_t{t:g}_l{lock:g}" for t in LOCK_TRIGGERS if lock < t]
    fams["befees"] = [f"wk_befees_t{t:g}" for t in LOCK_TRIGGERS]
    for t in LOCK_TRIGGERS:
        fams[f"lock_t{t:g}"] = ([f"wk_lock_t{t:g}_l0", f"wk_befees_t{t:g}"]
                                + [f"wk_lock_t{t:g}_l{lock:g}" for lock in LOCK_LEVELS[1:] if lock < t])
    for lock in (0.0, 0.5):
        fams[f"tgt_l{lock:g}"] = [f"wk_tgt{int(f * 100)}_l{lock:g}" for f in TARGET_FRACS]
    return fams


def _table(title: str, res: Dict[str, Any]) -> None:
    print(f"\n== {title}")
    for ds, r in res.items():
        print(f"\n{ds}  {r['window'][0]} -> {r['window'][1]}")
        print(f"  {'variant':<16} {'$end':>8} {'dd%':>5} {'n':>3} {'win':>4} {'avgR':>6} {'worst$':>7} "
              f"{'worst%':>6} {'liq':>5} {'wk':>3} {'s>w':>3} {'w>s':>3} {'gate':>4}")
        for v, s in r["variants"].items():
            a = s.get("vs_live") or {}
            win = f"{s['win_rate']:.0%}" if s["win_rate"] is not None else "-"
            ar = f"{s['avg_r']:.2f}" if s["avg_r"] is not None else "-"
            print(f"  {v:<16} {s['final_equity']:8.2f} {s['max_dd_pct']:5.1f} {s['trades']:>3} {win:>4} {ar:>6} "
                  f"{s['worst_loss_usd']:7.2f} {s['worst_loss_pct']:6.1f} {s['max_stop_liq_frac']:5.2f} "
                  f"{s['weekend_opened']:>3} {a.get('stop_to_win', ''):>3} {a.get('win_to_stop', ''):>3} "
                  f"{sum(a.get('gate_blocked', {}).values()) if a else '':>4}")


def _scores_print(title: str, scores: Dict[str, Any]) -> None:
    for g, sc in scores.items():
        print(f"\nscore {title} [{g}] (beats/loses live of N, geo-mean final/live):")
        for v, x in sorted(sc.items(), key=lambda kv: -kv[1]["geo_mean_ratio"]):
            print(f"  {v:<16} {x['beats']:>2}/{x['loses']:>2} of {x['of']:<2} {x['geo_mean_ratio']:.3f}")


def _print(rep: Dict[str, Any]) -> None:
    print(f"brent data end {rep['brent_data_end']}, silver data end {rep['silver_data_end']}")
    A, B, C = rep["A"], rep["B"], rep["C"]
    _table("A: Brent stop buffer", A["datasets"])
    _scores_print("A", A["scores"])
    print(f"\nA best {A['best']}, verdict {json.dumps(A['verdict'])}")
    t = A["todays_long"]
    print(f"\n2026-11-02 long (low after entry {t['low_after_entry']}, data end {t['data_end']}):")
    for v, r in t["variants"].items():
        print(f"  {v:<16} {json.dumps(r)}")
    _table("B: weekend off, Brent", B["brent"])
    _table("B: weekend off, silver", B["silver"])
    for desk, sc in B["scores"].items():
        _scores_print(f"B {desk}", sc)
    print(f"\nB verdict {json.dumps(B['verdict'])}")
    if "live_is_wk_flat" in B:
        print(f"B live == wk_flat (the 2026-11-05 live rule): {json.dumps(B['live_is_wk_flat'])}")
    for r in B["weekend_trades"]:
        print(f"  {json.dumps(r)}")
    _table("C: weekend-only lock, Brent", C["brent"])
    _table("C: weekend-only lock, silver", C["silver"])
    for desk, sc in C["scores"].items():
        _scores_print(f"C {desk}", sc)
    print(f"\nC verdict {json.dumps(C['verdict'])}")
    print("\nSat 10-31 Brent long under each lock:")
    for v, r in C["weekend_long"]["variants"].items():
        print(f"  {v:<16} {json.dumps(r)}")


if __name__ == "__main__":
    raise SystemExit(main())
