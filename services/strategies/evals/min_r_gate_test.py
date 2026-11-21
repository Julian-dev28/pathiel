"""Oil desk min_r gate sweep, 2026-11-18.

Pre-registration (written before this file ran against real data)
--------------------------------------------------------------------
Operator, 2026-11-18: "drop the minimum from 2R to 1.5R, test it." The live
oil entry gate (`pathiel.agents.bracket.DEFAULT_MIN_R`, replayed exactly by
`desk_tick_sim.live_params().min_r`, both 2.0) refuses any setup that pays
under 2R on the offset target. Operator's complaint: too few trades.
Question: does 1.5R make more money, or just more trades?

Method
  Grid `MIN_R_GRID` = (1.25, 1.5, 1.75, 2.0, 2.5) on
  `services.strategies.evals.live_params.live_brent_params()` -- the one
  place every other Brent eval builds its "live" baseline (its own
  docstring: earlier harnesses hand-rolled this and drifted from the real
  live rule). `min_r` is the only field that changes; 2.0 ties
  `live_brent_params()` exactly by construction
  (`test_min_r_gate_eval.py::test_2_0_ties_live_brent_params_exactly`).

  Datasets, the same set and loaders `stop_cap_test.py` uses for Brent:
    brent_journal    the live oil journal's theses (READ ONLY), first thesis to the data end
    brent_replay30   30 replayed Opus theses (price only, /tmp/thesis-replay-30b), READ ONLY
    brent_<series>_<n>d   no-thesis controls (always_long/always_short/daily_sign), 30 and 50 days
  `brent_journal` and `brent_replay30` are PRIMARY: they carry a real call
  (the operator's actual thesis), so they gate the verdict. The no-thesis
  controls are CONTEXT ONLY -- they can't tell a real edge from a lucky
  trend and never gate the verdict (same reasoning `stop_cap_test.py` and
  `brent_stop_buffer_test.py` already use: controls make the write-up
  legible, they don't decide it). $64 starting equity, the shared
  convention every sibling eval's "live sizing" number already uses
  (`live_params.py`'s module docstring; `stop_cap_test.START`) -- not a
  claim about the live account's real balance.

  Per value, per dataset: trades, trades/week, win rate, mean R, net R
  (sum of r_multiple), net $ (sum of realized net_pnl_usd, i.e. the
  compounding path's own dollar contribution -- `summarize_trades`), max
  drawdown % and worst losing streak (longest run of non-winning trades,
  `worst_losing_streak`). Split by first half / second half of the
  dataset's own window (`stop_reclaim_test.thirds(start, end, n=2)`, the
  existing halves/thirds helper -- not reimplemented) and by long/short.

  ADOPTION BAR (fixed before any table below was read; `adoption_verdict`):
    1. Net $ win in BOTH halves: 1.5R's net $ > 2.0R's net $ in both
       halves of EVERY primary dataset.
    2. Drawdown not materially worse: "materially" = more than
       `MATERIAL_DD_PP` (5.0) percentage points. 1.5R's full-window
       max_dd_pct must sit within that of 2.0R's, on every primary dataset.
    3. Plateau: at min_r 1.25 and 1.75, the net-$ edge over 2.0R
       (adv(m) = net$(m) - net$(2.0)) must be at least `PLATEAU_FRAC`
       (0.5, i.e. half) of 1.5R's own edge, on every primary dataset where
       1.5R actually has a positive edge.
  All three must pass to ADOPT. Two of three with a stated reason is
  LEANING. Fewer is NOT ADOPTED. No live constant, config, or
  `place_bracket` changes here either way -- adoption is the operator's
  call on this result.

  Separately: replay the last `--replay-days` (7) days of real desk ticks
  (`/tmp/oil-loop/progress.log`'s `action=NONE` lines that quote "pays X R")
  and count how many would have entered at 1.5R -- R >= 1.5 AND no OTHER
  reason (chase, no-entry-after-a-break, a headline/EIA blackout, a tool
  fault) blocking it, since none of those move with min_r. Consecutive
  qualifying ticks on the same held-open setup are folded into one episode
  (`group_episodes`); each episode is matched to the min_r=1.5
  `brent_journal` run's own trades by entry time (`match_episode_trade`),
  so "what it would have done" is read off the SAME sim the grid above
  runs on real, publicly-read candles -- not a second hand-rolled walk.
  The anchor for "last 7 days" is the log's own last timestamp, not
  wall-clock time, so a re-run of this file reproduces the same window.

Reads candles through the eval disk cache and the venue's public /info;
reads the oil journal (READ ONLY); never touches the live account, the
live config, place_bracket, or the desk-tick agent. No LLM calls.

    python -m services.strategies.evals.min_r_gate_test \\
        --oil-theses-dir <pathia>/.state/strategies/oil/theses \\
        --oil-replay-dir /tmp/thesis-replay-30b/oil/theses
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from services.strategies.backtest import data as D
from services.strategies.backtest.desk_tick_sim import Params, Run, Trade, simulate
from services.strategies.evals.desk_tick_backtest import DAY_MS, HOUR_MS, journaled_direction
from services.strategies.evals.live_params import live_brent_params
from services.strategies.evals.stop_cap_test import FIFTEEN_MS, START, _iso, closed_only
from services.strategies.evals.stop_reclaim_test import thirds

MIN_R_GRID = (1.25, 1.5, 1.75, 2.0, 2.5)
PROPOSED = 1.5
LIVE = 2.0           # pathiel.agents.bracket.DEFAULT_MIN_R == desk_tick_sim.live_params().min_r
CONTROLS = ("always_long", "always_short", "daily_sign")
CONTROL_DAYS = (30, 50)
MATERIAL_DD_PP = 5.0
PLATEAU_FRAC = 0.5
PLATEAU_NEIGHBORS = (1.25, 1.75)     # the grid's immediate neighbours of PROPOSED, not the whole grid (2.5 sits
                                     # past LIVE and is context, never part of the plateau check)
DEFAULT_TICK_LOG = "/tmp/oil-loop/progress.log"
REPLAY_EPISODE_GAP_MS = 20 * 60 * 1000     # one skipped 15m tick still joins the same episode
REPLAY_MATCH_TOLERANCE_MS = 30 * 60 * 1000


def _default_oil_theses_dir() -> str:
    env = os.environ.get("PATHIEL_OIL_THESES_DIR", "")
    if env:
        return env
    state = os.environ.get("PATHIEL_STATE_DIR") or os.path.expanduser("~/Documents/code/pathia/.state")
    return os.path.join(state, "strategies", "oil", "theses")


# ── the grid (pure) ────────────────────────────────────────────────────────

def variants(grid: Sequence[float] = MIN_R_GRID) -> Dict[str, Dict[str, Any]]:
    """name -> Params overrides; name is the plain `min_r` value, e.g.
    "1.5". Pure."""
    return {_r_name(r): {"min_r": r} for r in grid}


def _r_name(r: float) -> str:
    return f"{r:g}"


# ── per-trade summaries (pure) ─────────────────────────────────────────────

def is_win(t: Trade) -> bool:
    return t.net_pnl_usd > 0


def worst_losing_streak(trades: Sequence[Trade]) -> int:
    """Longest run of consecutive (by entry time) non-winning trades --
    what the operator actually feels as a losing streak, not just a
    stopped-out streak (a scratch counts, a target does not)."""
    worst = cur = 0
    for t in sorted(trades, key=lambda x: x.entry_time):
        if is_win(t):
            cur = 0
        else:
            cur += 1
            worst = max(worst, cur)
    return worst


def summarize_trades(trades: Sequence[Trade], days: float) -> Dict[str, Any]:
    """trades/week, win rate, mean R, net R, net $, worst losing streak.
    No drawdown here -- that needs the equity curve, not just the trade
    list (see `max_drawdown_pct_curve`). Pure."""
    rs = [t.r_multiple for t in trades if t.r_multiple is not None]
    n = len(trades)
    return {
        "trades": n,
        "trades_per_week": round(n / days * 7, 2) if days > 0 else None,
        "win_rate": round(sum(1 for t in trades if is_win(t)) / n, 3) if n else None,
        "mean_r": round(sum(rs) / len(rs), 3) if rs else None,
        "net_r": round(sum(rs), 2) if rs else 0.0,
        "net_usd": round(sum(t.net_pnl_usd for t in trades), 2),
        "worst_losing_streak": worst_losing_streak(trades),
    }


def max_drawdown_pct_curve(curve: Sequence[Tuple[int, float]]) -> float:
    """Peak-to-trough drawdown on this curve SEGMENT alone: the peak
    resets at the segment's own first point (the same running-peak rule
    `simulate_steps` applies to the whole run, applied here to a slice).
    0.0 for an empty segment. Pure."""
    peak: Optional[float] = None
    dd = 0.0
    for _, mark in curve:
        peak = mark if peak is None else max(peak, mark)
        if peak and peak > 0:
            dd = max(dd, (peak - mark) / peak)
    return round(dd * 100, 1)


def window_trades(trades: Sequence[Trade], start_ms: int, end_ms: int) -> List[Trade]:
    return [t for t in trades if start_ms <= t.entry_time < end_ms]


def curve_window(curve: Sequence[Tuple[int, float]], start_ms: int, end_ms: int) -> List[Tuple[int, float]]:
    return [(t, m) for t, m in curve if start_ms <= t < end_ms]


def full_summary(run: Run, start_ms: int, end_ms: int) -> Dict[str, Any]:
    """The whole window: `summarize_trades` plus the run's own drawdown,
    refusal count and whether it got liquidated. Pure on `run`'s fields."""
    days = (end_ms - start_ms) / DAY_MS
    s = summarize_trades(run.trades, days)
    s["max_dd_pct"] = round(run.max_drawdown_pct * 100, 1)
    s["liquidated"] = run.liquidated
    s["refused_min_r"] = int(run.refused.get("min_r", 0))
    s["final_equity"] = round(run.final_equity, 2)
    return s


def half_summaries(run: Run, start_ms: int, end_ms: int) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """First half / second half of [start_ms, end_ms) (`thirds(..., n=2)`,
    the existing halves helper). Each half's drawdown resets at that
    half's own first equity mark. Pure on `run`'s fields."""
    (a1, b1), (a2, b2) = thirds(start_ms, end_ms, n=2)
    out = []
    for a, b in ((a1, b1), (a2, b2)):
        days = (b - a) / DAY_MS
        s = summarize_trades(window_trades(run.trades, a, b), days)
        s["max_dd_pct"] = max_drawdown_pct_curve(curve_window(run.equity_curve, a, b))
        out.append(s)
    return out[0], out[1]


def side_summaries(run: Run, start_ms: int, end_ms: int) -> Dict[str, Dict[str, Any]]:
    """Long-only / short-only cuts of the trade list, trades/week against
    the FULL window's day count (so the two sides are comparable to each
    other and to the full row). Pure on `run`'s fields."""
    days = (end_ms - start_ms) / DAY_MS
    return {side: summarize_trades([t for t in run.trades if t.side == side], days) for side in ("long", "short")}


# ── adoption bar (pure) ─────────────────────────────────────────────────────

def adv_usd(by_r: Dict[Any, Dict[str, Any]], r: float, baseline: float = LIVE) -> Optional[float]:
    """net $ at `r` minus net $ at `baseline` (the live gate); None if
    either row is missing. Pure."""
    rn, bn = _r_name(r), _r_name(baseline)
    if rn not in by_r or bn not in by_r:
        return None
    return round(by_r[rn]["net_usd"] - by_r[bn]["net_usd"], 2)


def adoption_verdict(primary: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]], proposed: float = PROPOSED,
                     baseline: float = LIVE, material_dd_pp: float = MATERIAL_DD_PP,
                     plateau_frac: float = PLATEAU_FRAC) -> Dict[str, Any]:
    """`primary`: {dataset: {"full"|"half1"|"half2": {r_name: summary}}}
    for the real-thesis datasets ONLY -- controls never gate this. The
    operator's three-part bar, pre-registered above this run. Pure."""
    reasons: List[str] = []
    both_halves = True
    for ds, windows in primary.items():
        for half in ("half1", "half2"):
            a = adv_usd(windows[half], proposed, baseline)
            if a is None or a <= 0:
                both_halves = False
                reasons.append(f"{ds} {half}: {proposed:g}R net $ does not beat {baseline:g}R (edge {a})")
    dd_ok = True
    for ds, windows in primary.items():
        f = windows["full"]
        rn, bn = _r_name(proposed), _r_name(baseline)
        if rn not in f or bn not in f:
            continue
        gap = round(f[rn]["max_dd_pct"] - f[bn]["max_dd_pct"], 1)
        if gap > material_dd_pp:
            dd_ok = False
            reasons.append(f"{ds}: {proposed:g}R drawdown {gap:.1f}pp worse than {baseline:g}R "
                           f"(bar {material_dd_pp:.1f}pp)")
    plateau_ok = True
    for ds, windows in primary.items():
        f = windows["full"]
        adv15 = adv_usd(f, proposed, baseline)
        if adv15 is None or adv15 <= 0:
            continue
        for nb in PLATEAU_NEIGHBORS:
            adv_nb = adv_usd(f, nb, baseline)
            if adv_nb is None or adv_nb < plateau_frac * adv15:
                plateau_ok = False
                reasons.append(f"{ds}: {nb:g}R's edge ({adv_nb}) falls under {plateau_frac:.0%} of "
                               f"{proposed:g}R's ({adv15})")
    adopt = both_halves and dd_ok and plateau_ok
    passed = sum((both_halves, dd_ok, plateau_ok))
    if adopt:
        verdict = f"ADOPT {proposed:g}R"
    elif passed >= 2:
        verdict = f"LEANING: {passed}/3 bars pass ({reasons[0]})" if reasons else f"LEANING: {passed}/3 bars pass"
    else:
        verdict = "NOT ADOPTED"
    return {"adopt": adopt, "both_halves": both_halves, "dd_ok": dd_ok, "plateau_ok": plateau_ok,
            "reasons": reasons, "verdict": verdict}


# ── real-tick replay (pure parsing + grouping; file read is the only I/O) ──

_LINE_RE = re.compile(
    r"^DESK-TICK\s+(?P<desk>\S+)\s+(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?Z)\s+"
    r"coin=(?P<coin>\S+)\s+mid=(?P<mid>[-0-9.]+)\s+pos=(?P<pos>\S+)\s+(?:\S+=\S+\s+)*"
    r"action=(?P<action>\S+)\s*(?P<reason>.*)$")
_PAYS_RE = re.compile(r"pays\s+([0-9]+(?:\.[0-9]+)?)R", re.IGNORECASE)
_TS_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?Z")
# Reasons a NONE tick refuses entry independent of min_r: chasing past the
# thesis entry, a break of the 24h range, a headline/EIA blackout, or a
# tool/signer fault. Lowering the gate can never unblock these, so a row
# that cites one is excluded from "would enter at 1.5R" regardless of its R.
_OTHER_BLOCK_PHRASES = ("thesis entry", "mid above entry", "mid below entry", "never chase",
                        "no entry after a break", "blackout", "reconnect pathiel", "signer refused",
                        "desk_refresh timed out")


def _ts_to_ms(ts: str) -> int:
    m = _TS_RE.match(ts)
    y, mo, d, h, mi = (int(m.group(i)) for i in range(1, 6))
    s = int(m.group(6) or 0)
    return int(datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).timestamp() * 1000)


def parse_tick_line(line: str) -> Optional[Dict[str, Any]]:
    """One `DESK-TICK ...` progress-log row, reduced to what the min_r
    replay needs. None for a line this format doesn't match (the older
    `OIL-TICK` header, a blank line, a non-tick log line). Pure."""
    m = _LINE_RE.match(line.strip())
    if not m:
        return None
    reason = m.group("reason")
    pr = _PAYS_RE.search(reason)
    low = reason.lower()
    return {
        "ts_ms": _ts_to_ms(m.group("ts")), "desk": m.group("desk"), "coin": m.group("coin"),
        "mid": float(m.group("mid")), "pos": m.group("pos"), "action": m.group("action"), "reason": reason,
        "pays_r": float(pr.group(1)) if pr else None,
        "blocked_other": any(p in low for p in _OTHER_BLOCK_PHRASES),
    }


def would_enter_at(row: Dict[str, Any], min_r: float, eps: float = 1e-9) -> bool:
    """True when lowering (or raising) the gate to `min_r` would have let
    this NONE tick enter: it quoted an R, that R clears the gate, and no
    OTHER reason was blocking it too. Pure."""
    return (row.get("action") == "NONE" and row.get("pays_r") is not None
            and row["pays_r"] + eps >= min_r and not row.get("blocked_other"))


def qualifying_ticks(rows: Sequence[Dict[str, Any]], min_r: float) -> List[Dict[str, Any]]:
    return [r for r in rows if would_enter_at(r, min_r)]


def group_episodes(rows: Sequence[Dict[str, Any]], max_gap_ms: int = REPLAY_EPISODE_GAP_MS
                   ) -> List[List[Dict[str, Any]]]:
    """Consecutive qualifying rows folded into one episode when no more
    than `max_gap_ms` separates them -- the same setup the real desk
    re-evaluates tick after tick while it stays flat. A counterfactual
    entry happens once per episode (the first row); later rows in the same
    episode are the position it would already be holding. Pure."""
    out: List[List[Dict[str, Any]]] = []
    for r in sorted(rows, key=lambda x: x["ts_ms"]):
        if out and r["ts_ms"] - out[-1][-1]["ts_ms"] <= max_gap_ms:
            out[-1].append(r)
        else:
            out.append([r])
    return out


def load_tick_log_rows(path: str, days: float = 7.0, now_ms: Optional[int] = None) -> List[Dict[str, Any]]:
    """Every parseable row in `path`, kept only if it falls in the trailing
    `days` of `now_ms` (default: the log's own last parseable timestamp,
    not wall-clock time, so a re-run reproduces the same window). []
    for a missing file -- READ ONLY, never written."""
    if not os.path.isfile(path):
        return []
    with open(path) as fh:
        parsed = [row for ln in fh if (row := parse_tick_line(ln)) is not None]
    if not parsed:
        return []
    anchor = now_ms if now_ms is not None else max(r["ts_ms"] for r in parsed)
    cutoff = anchor - int(days * DAY_MS)
    return [r for r in parsed if r["ts_ms"] >= cutoff]


def match_episode_trade(episode: Sequence[Dict[str, Any]], trades: Sequence[Trade],
                        tolerance_ms: int = REPLAY_MATCH_TOLERANCE_MS) -> Optional[Trade]:
    """The trade (if any) in a sim run whose entry sits within
    `tolerance_ms` of this episode's first qualifying tick -- ties a real
    tick to what the grid's own min_r=1.5 run already did with it, rather
    than a second hand-rolled walk. Pure."""
    if not episode:
        return None
    t0 = episode[0]["ts_ms"]
    cands = [t for t in trades if abs(t.entry_time - t0) <= tolerance_ms]
    return min(cands, key=lambda t: abs(t.entry_time - t0)) if cands else None


def replay_rows(rows: Sequence[Dict[str, Any]], min_r_1_5_trades: Sequence[Trade]) -> List[Dict[str, Any]]:
    """One row per episode that would have entered at 1.5R: its first
    tick, how many ticks long, the highest R it quoted, and (when a trade
    in the matched min_r=1.5 run's own entries lines up) what that trade
    did. Pure."""
    episodes = group_episodes(qualifying_ticks(rows, PROPOSED))
    out = []
    for ep in episodes:
        trade = match_episode_trade(ep, min_r_1_5_trades)
        row = {"first_tick": _iso(ep[0]["ts_ms"]), "ticks": len(ep), "mid_at_entry": ep[0]["mid"],
               "max_r_quoted": max(r["pays_r"] for r in ep), "matched_trade": trade is not None}
        if trade is not None:
            row.update({"side": trade.side, "entry_px": round(trade.entry_px, 4),
                        "exit_reason": trade.exit_reason, "exit_time": _iso(trade.exit_time),
                        "r_multiple": round(trade.r_multiple, 2) if trade.r_multiple is not None else None,
                        "net_usd": round(trade.net_pnl_usd, 2)})
        out.append(row)
    return out


# ── data + runs ──────────────────────────────────────────────────────────

def _window(c15, c1h, start_ms, end_ms):
    return [c for c in c15 if start_ms <= c.t < end_ms], [c for c in c1h if c.t < end_ms]


def run_dataset(c15, c1h, direction_at, base: Params, start_ms: int, end_ms: int,
                named: Dict[str, Dict[str, Any]], funding=None) -> Dict[str, Any]:
    """Every min_r variant on one dataset: full window, both halves, long
    and short cuts. Returns the trades too (min_r=1.5's, for the tick
    replay to match against)."""
    w15, w1h = _window(c15, c1h, start_ms, end_ms)
    out: Dict[str, Any] = {"window": [_iso(start_ms), _iso(end_ms)], "full": {}, "half1": {}, "half2": {},
                           "long": {}, "short": {}, "trades": {}}
    for v, ov in named.items():
        p = dataclasses.replace(base, **ov)
        run = simulate(w15, w1h, direction_at, p, funding)
        out["full"][v] = full_summary(run, start_ms, end_ms)
        h1, h2 = half_summaries(run, start_ms, end_ms)
        out["half1"][v], out["half2"][v] = h1, h2
        sides = side_summaries(run, start_ms, end_ms)
        out["long"][v], out["short"][v] = sides["long"], sides["short"]
        out["trades"][v] = run.trades
    return out


def main(argv: Optional[List[str]] = None) -> int:
    from services.strategies.evals.market_scout import direction_series, load_coin_candles

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--oil-theses-dir", default=_default_oil_theses_dir(), help="READ ONLY: the live oil journal")
    ap.add_argument("--oil-replay-dir", default="/tmp/thesis-replay-30b/oil/theses")
    ap.add_argument("--cache-dir", default="/tmp/min-r-gate/cache")
    ap.add_argument("--out-dir", default="/tmp/min-r-gate")
    ap.add_argument("--tick-log", default=DEFAULT_TICK_LOG, help="READ ONLY: the real desk-tick progress log")
    ap.add_argument("--replay-days", type=float, default=7.0)
    ap.add_argument("--quick", action="store_true", help="fast smoke run: fewer min_r values, fewer datasets")
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    os.makedirs(a.cache_dir, exist_ok=True)
    t0 = time.time()
    log_path = os.path.join(a.out_dir, "progress.log")

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} min-r-gate: {msg}"
        print(line, flush=True)
        with open(log_path, "a") as fh:
            fh.write(line + "\n")

    grid = (PROPOSED, LIVE) if a.quick else MIN_R_GRID
    named = variants(grid)

    def fresh(coin: str):
        got = load_coin_candles(coin, a.cache_dir)
        asof = int(os.path.getmtime(D._cache_path(a.cache_dir, coin, "candles_15m_4820")) * 1000)
        return closed_only(got["c15"], FIFTEEN_MS, asof), closed_only(got["c1h"], HOUR_MS, asof)

    b15, b1h = fresh("xyz:BRENTOIL")
    b_end = b15[-1].t + FIFTEEN_MS
    funding = D.fetch_funding_cached("xyz:BRENTOIL", b15[0].t, b_end, a.cache_dir)
    bp = live_brent_params(size_slippage=True, starting_equity=START)

    results: Dict[str, Dict[str, Any]] = {}
    primary_keys: List[str] = []

    theses = D.load_journaled_theses(a.oil_theses_dir) if a.oil_theses_dir else []
    if theses:
        start = min(th.created_at_ms for th in theses)
        results["brent_journal"] = run_dataset(b15, b1h, journaled_direction(theses), bp, start, b_end, named,
                                               funding)
        primary_keys.append("brent_journal")
        log(f"brent_journal: {len(theses)} theses done ({time.time() - t0:.0f}s)")
    else:
        log(f"brent_journal: no theses at {a.oil_theses_dir!r}, skipped")

    if not a.quick:
        rtheses = D.load_journaled_theses(a.oil_replay_dir) if a.oil_replay_dir else []
        if rtheses:
            start = min(th.created_at_ms for th in rtheses)
            end = min(b_end, max(th.created_at_ms for th in rtheses) + DAY_MS)
            results["brent_replay30"] = run_dataset(b15, b1h, journaled_direction(rtheses), bp, start, end, named,
                                                     funding)
            primary_keys.append("brent_replay30")
            log(f"brent_replay30: {len(rtheses)} theses done ({time.time() - t0:.0f}s)")
        else:
            log(f"brent_replay30: no theses at {a.oil_replay_dir!r}, skipped")

        for days in CONTROL_DAYS:
            start = b_end - days * DAY_MS
            for s in CONTROLS:
                label = f"brent_{s}_{days}d"
                results[label] = run_dataset(b15, b1h, direction_series(s, b1h), bp, start, b_end, named, funding)
        log(f"controls done ({time.time() - t0:.0f}s)")

    primary = {k: {w: results[k][w] for w in ("full", "half1", "half2")} for k in primary_keys}
    verdict = adoption_verdict(primary) if primary else {"adopt": False, "verdict": "NOT ADOPTED (no primary data)",
                                                          "reasons": ["no real-thesis dataset available"],
                                                          "both_halves": False, "dd_ok": False, "plateau_ok": False}

    # ── 7-day replay of real desk ticks ──
    tick_rows = load_tick_log_rows(a.tick_log, a.replay_days)
    counts = {_r_name(r): len(qualifying_ticks(tick_rows, r)) for r in grid}
    base_trades = results.get("brent_journal", {}).get("trades", {}).get(_r_name(PROPOSED), [])
    replay = replay_rows(tick_rows, base_trades)
    log(f"tick replay: {len(tick_rows)} rows over the last {a.replay_days:g}d, "
        f"{counts.get(_r_name(PROPOSED), 0)} ticks qualify at {PROPOSED:g}R ({len(replay)} episodes)")

    csv_path = os.path.join(a.out_dir, "summary.csv")
    fields = ["dataset", "variant", "window", "trades", "trades_per_week", "win_rate", "mean_r", "net_r", "net_usd",
              "max_dd_pct", "worst_losing_streak"]
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for ds, r in results.items():
            for window in ("full", "half1", "half2", "long", "short"):
                for v, s in r[window].items():
                    w.writerow({"dataset": ds, "variant": v, "window": window,
                               **{k: s.get(k) for k in fields[3:]}})
    replay_csv = os.path.join(a.out_dir, "tick_replay.csv")
    with open(replay_csv, "w", newline="") as fh:
        rfields = ["first_tick", "ticks", "mid_at_entry", "max_r_quoted", "matched_trade", "side", "entry_px",
                  "exit_reason", "exit_time", "r_multiple", "net_usd"]
        w = csv.DictWriter(fh, fieldnames=rfields)
        w.writeheader()
        w.writerows(replay)

    strip = lambda res: {k: {kk: vv for kk, vv in r.items() if kk != "trades"} for k, r in res.items()}  # noqa: E731
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()), "seconds": round(time.time() - t0, 1),
        "brent_data_end": _iso(b_end), "grid": list(grid), "proposed": PROPOSED, "live": LIVE,
        "datasets": strip(results), "primary_datasets": primary_keys, "verdict": verdict,
        "tick_replay": {"rows": len(tick_rows), "qualifying_by_r": counts, "episodes": replay,
                        "replay_days": a.replay_days, "log_path": a.tick_log},
    }
    out_path = os.path.join(a.out_dir, "report.json")
    with open(out_path, "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    _print(report)
    print(f"\nreport: {out_path}\nsummary: {csv_path}\ntick replay: {replay_csv}")
    return 0 if verdict["adopt"] or not primary_keys else 0   # research only: never a failing exit on the verdict


def _row(v: str, s: Dict[str, Any]) -> str:
    win = f"{s['win_rate']:.0%}" if s.get("win_rate") is not None else "-"
    mr = f"{s['mean_r']:.2f}" if s.get("mean_r") is not None else "-"
    dd = f"{s.get('max_dd_pct', 0):.1f}"
    return (f"  {v:<6} {s['trades']:>3} {s.get('trades_per_week', 0) or 0:>6.2f} {win:>5} {mr:>6} "
           f"{s['net_r']:>7.2f} {s['net_usd']:>9.2f} {dd:>6} {s['worst_losing_streak']:>4}")


def _print(rep: Dict[str, Any]) -> None:
    print(f"brent data end {rep['brent_data_end']}, grid {rep['grid']}")
    for ds, r in rep["datasets"].items():
        tag = "PRIMARY" if ds in rep["primary_datasets"] else "context"
        print(f"\n{ds} [{tag}]  {r['window'][0]} -> {r['window'][1]}")
        for window in ("full", "half1", "half2", "long", "short"):
            print(f"  -- {window} --")
            print(f"  {'r':<6} {'n':>3} {'t/wk':>6} {'win':>5} {'meanR':>6} {'netR':>7} {'net$':>9} "
                  f"{'dd%':>6} {'streak':>4}")
            for v, s in r[window].items():
                print(_row(v, s))
    print(f"\nADOPTION VERDICT: {rep['verdict']['verdict']}")
    for why in rep["verdict"].get("reasons", []):
        print(f"  - {why}")
    tr = rep["tick_replay"]
    print(f"\n7-day tick replay ({tr['replay_days']:g}d, {tr['rows']} NONE+pays rows, log {tr['log_path']}):")
    print(f"  qualifying ticks by min_r: {tr['qualifying_by_r']}")
    for ep in tr["episodes"]:
        tail = (f"-> {ep['exit_reason']} {ep.get('r_multiple')}R ${ep.get('net_usd')}" if ep["matched_trade"]
               else "(no matching sim trade)")
        print(f"  {ep['first_tick']}  {ep['ticks']} tick(s)  max {ep['max_r_quoted']:.2f}R  {tail}")


if __name__ == "__main__":
    raise SystemExit(main())
