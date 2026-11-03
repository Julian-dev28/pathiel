"""Silver stop buffer floor backtest, 2026-11-02: the live per-tick ATR stop
vs a floor under it.

Correction (2026-11-03): this eval's original run (15:05 the same day)
predates `Params.risk_cap_frac` (added ~17:17-17:59,
`LOSS_AUDIT_2026-11-02.md`), so its published numbers are full-port size,
not today's live rule. `bp`/`sp` below now carry the 15% cap
(`risk_cap_frac=LIVE_RISK_CAP_FRAC`) on top of the SAME no-floor baseline
this file always used -- not the full `live_params.live_silver_params`,
which would also bake this file's own candidate floor onto that baseline.
Because the floor candidates WIDEN the stop (never narrow it, `floored_
buffer`), the cap can shrink a floored variant's size more than the
no-floor baseline's, the same interaction `BRENT_STOP_BUFFER_TEST_2026-11-
02.md`'s buffer sweep has -- see `docs/strategy/SILVER_STOP_FLOOR_TEST_
2026-11-02.md`'s correction note for whether a re-run changed the verdict.

Operator, 2026-11-02 ("yes"). The stop-cap test (STOP_CAP_TEST_2026-11-02.md)
found the live silver stop is not the one the 30-day silver test validated.
The test ran a fixed 0.41 buffer (1.3 x silver's MEAN 1h ATR14); live runs
1.3 x the 1h ATR14 AT EACH TICK (`next_trade.stop_buffer_for`), about 0.21
on the quiet night of 2026-11-02. On the 30 replayed theses that cost $31
($142.97 vs $173.95) and doubled the stop-outs.

The rule tested: silver buffer = max(1.3 x ATR14_1h at the tick, FLOOR).

Variants (`variants()`; desk_tick_sim.Params.stop_buffer_atr /
stop_buffer_floor / stop_buffer_floor_frac):
  live          1.3 x ATR14 per tick, no floor (the live rule today; the baseline)
  floor_<x>     max(1.3 x ATR14, x dollars), x in FLOORS
  pct_<p>       max(1.3 x ATR14, p% of the mid), p in FLOOR_PCTS (scales with price)
  atr_<m>       m x ATR14 per tick, no floor: the "just raise the multiplier" control
  fixed_test    the 30-day test's fixed buffer (1.3 x the replay cache's mean ATR, 0.41)

Datasets, live rules otherwise (`scouting_params`: market entry off the 24h
range, 2R gate, target 0.5% inside the 24h edge, limit target, 20x, $64):
  silver_theses       the 30-day silver Opus replay (SILVER_THESIS_TEST_2026-10-31.md), its cache
  silver_journal      the live metals journal's SILVER theses (READ ONLY; the gold one is dropped),
                      fresh candles: 2026-10-31 to now
  silver_<series>     no-thesis controls on the replay window: always_long / always_short / daily_sign
  silver_<series>_recent  the same controls on the last 30 days of fresh candles
  brent_*             Brent's fixed 0.75 with every floor set: must be byte-identical (the live
                      floor would sit in stop_buffer_for's non-Brent branch, after Brent's return)

Per variant: final equity from $64, return, max drawdown, trades, win rate,
avg R, days with 2+ stops, entries the floor widened, and ticks / episodes
where the floored stop failed a gate (2R or the 20x line) that the bare ATR
stop passed (`Run.floor_blocked`). Per baseline entry (`attribute`): the
same entry tick re-judged with the variant's stop by the sim's own `_setup`
(so the same gates), then both stops walked on by the stop-cap test's
`walk_entry`. Each entry is classed stop_to_win (the floor saved it),
win_to_stop (the floor cost it), both_stop, both_win, gate_blocked
(the wider stop failed 2R or 20x, no trade), or same.

    python -m services.strategies.evals.silver_stop_floor_test \\
        --metals-theses-dir <pathia>/.state/strategies/metals/theses \\
        --oil-theses-dir <pathia>/.state/strategies/oil/theses \\
        --oil-replay-dir /tmp/thesis-replay-30b/oil/theses \\
        --silver-theses-dir /tmp/thesis-replay-silver/theses \\
        --silver-cache-dir /tmp/thesis-backtest/cache --cache-dir /tmp/silver-floor/cache

Writes /tmp/silver-floor/report.json, summary.csv (one row per dataset x
variant) and entries.csv (the per-entry attribution). Reads candles through
the eval disk cache and the venue's public /info; never touches the live
account, the live config or the journal.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import os
import statistics
import time
from collections import Counter
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from pathiel.models.types import Candle

from services.strategies.backtest import data as D
from services.strategies.backtest.desk_tick_sim import Params, Run, _levels, _setup, simulate
from services.strategies.evals.desk_tick_backtest import DAY_MS, HOUR_MS, journaled_direction
from services.strategies.evals.live_params import LIVE_RISK_CAP_FRAC, live_brent_params
from services.strategies.evals.profit_lock_test import score
from services.strategies.evals.stop_cap_test import (
    FIFTEEN_MS, START, _iso, closed_only, same_market, stops_per_day, walk_entry)
from services.strategies.next_trade import ATR_BUFFER_MULT

# The operator's grid plus 0.45 / 0.60 and 0.9% / 1.0% to show where the
# curve turns (a wider stop fails 2R, then the 20x line, more often).
FLOORS = (0.25, 0.30, 0.35, 0.41, 0.45, 0.50, 0.60)
FLOOR_PCTS = (0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
ATR_MULTS = (1.6, 2.0)
CONTROLS = ("always_long", "always_short", "daily_sign")
BASELINE = "live"


# ── the grid (pure) ────────────────────────────────────────────────────────

def variants(fixed_buffer: float) -> Dict[str, Dict[str, Any]]:
    """name -> Params overrides on the silver base params. `fixed_buffer` is
    the 30-day test's fixed buffer (1.3 x the replay cache's mean ATR)."""
    live = {"stop_buffer_atr": ATR_BUFFER_MULT}
    out: Dict[str, Dict[str, Any]] = {BASELINE: dict(live)}
    for x in FLOORS:
        out[f"floor_{x:.2f}"] = {**live, "stop_buffer_floor": x}
    for p in FLOOR_PCTS:
        out[f"pct_{p:.1f}"] = {**live, "stop_buffer_floor_frac": p / 100.0}
    for m in ATR_MULTS:
        out[f"atr_{m:.1f}"] = {"stop_buffer_atr": m}
    out["fixed_test"] = {"stop_buffer_atr": None, "stop_buffer": fixed_buffer}
    return out


def brent_variants() -> Dict[str, Dict[str, Any]]:
    """Brent's live params with each floor set and stop_buffer_atr off: the
    floor must not touch a fixed buffer."""
    out: Dict[str, Dict[str, Any]] = {BASELINE: {}}
    for x in FLOORS:
        out[f"floor_{x:.2f}"] = {"stop_buffer_floor": x}
    for p in FLOOR_PCTS:
        out[f"pct_{p:.1f}"] = {"stop_buffer_floor_frac": p / 100.0}
    return out


# ── per-entry attribution (pure) ───────────────────────────────────────────

def _outcome(reason: str, r_net: float) -> str:
    if reason == "end":
        return "open"
    return "win" if r_net > 0 else "loss"


def _walk(side: str, t: int, entry: float, stop: float, target: float, c15, by_close, direction_at,
          params: Params) -> Dict[str, Any]:
    """Walk one entry on (stop_cap_test.walk_entry) and price it: R before and
    after fees, and the return on equity at full size (leverage x the net
    price move), which is the number that compares across stop widths since
    the desk sizes by margin, not by risk. Fees at the live rates (taker in;
    maker on a limit target, else taker); no slippage or funding."""
    reason, px, t_exit = walk_entry(side, t, entry, stop, target, c15, by_close, direction_at, params)
    sign = 1.0 if side == "long" else -1.0
    risk = abs(entry - stop)
    exit_fee = params.maker_fee if (reason == "target" and params.target_mode != "market") else params.taker_fee
    net_move = sign * (px - entry) - (params.taker_fee * entry + exit_fee * px)
    r_net = net_move / risk if risk > 0 else 0.0
    return {"reason": reason, "exit_px": round(px, 4), "exit_time": _iso(t_exit), "stop_px": round(stop, 4),
            "r_net": round(r_net, 3), "eq_pct": round(params.leverage * net_move / entry * 100, 2),
            "outcome": _outcome(reason, r_net)}


def classify(base: Dict[str, Any], var: Optional[Dict[str, Any]], gate: Optional[str]) -> str:
    """One entry, the live stop vs the variant's: stop_to_win, win_to_stop,
    both_stop, both_win, gate_blocked, or same (identical stop) / other."""
    if gate is not None:
        return "gate_blocked"
    assert var is not None
    if var["stop_px"] == base["stop_px"]:
        return "same"
    b, v = base["outcome"], var["outcome"]
    if b == "loss" and v == "win":
        return "stop_to_win"
    if b == "win" and v == "loss":
        return "win_to_stop"
    if b == "loss" and v == "loss":
        return "both_loss"
    if b == "win" and v == "win":
        return "both_win"
    return "other"


def attribute(run: Run, c15: Sequence[Candle], c1h: Sequence[Candle], direction_at: Callable[[int], Any],
              base: Params, var: Params) -> List[Dict[str, Any]]:
    """Every entry the baseline run opened, re-judged at the same tick with
    the variant's params by the sim's own `_setup` (same levels, same mid,
    same gates), then both setups walked on. A variant setup that fails a
    gate is `gate_blocked` (the floor cost that entry)."""
    bars = {c.t: c for c in c15}
    hours = sorted(c1h, key=lambda c: c.t)
    by_close = {c.t + HOUR_MS: c for c in hours}
    need = max(15, base.entry_lookback_h, base.target_lookback_h)
    rows: List[Dict[str, Any]] = []
    for tr in run.trades:
        t = tr.entry_time
        bar = bars.get(t)
        d = direction_at(t)
        if bar is None or d is None:
            continue
        closed = [c for c in hours if c.t + HOUR_MS <= t][-need:]
        lv = _levels(closed, base.entry_lookback_h, base.target_lookback_h)
        if lv is None:
            continue
        b_setup = _setup(d, bar.o, lv, base, base.leverage)
        v_setup = _setup(d, bar.o, lv, var, var.leverage)
        if not b_setup or b_setup.get("refused"):
            continue            # an AI-level or edge entry: not this rule's trade
        b = _walk(d.side, t, b_setup["entry_px"], b_setup["stop_px"], b_setup["target_px"], c15, by_close,
                  direction_at, base)
        row: Dict[str, Any] = {"time": _iso(t), "side": d.side, "entry_px": round(bar.o, 4),
                               "target_px": round(b_setup["target_px"], 4), "atr14": round(lv["atr14"], 4),
                               "live_buffer": round(ATR_BUFFER_MULT * lv["atr14"], 4),
                               "base": b, "sim_exit": tr.exit_reason}
        if not v_setup:
            row["var"], row["gate"] = None, "no_setup"
        elif v_setup.get("refused"):
            row["var"], row["gate"] = None, v_setup["refused"]
        else:
            row["var"] = _walk(d.side, t, v_setup["entry_px"], v_setup["stop_px"], v_setup["target_px"], c15,
                               by_close, direction_at, var)
            row["gate"] = None
        row["class"] = classify(b, row["var"], row["gate"])
        rows.append(row)
    return rows


def attribution_summary(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Counts per class, gate blocks by gate, and the summed return on
    equity of each side of the comparison (positive delta = the variant's
    stop did better on the baseline's own entries)."""
    cls = Counter(r["class"] for r in rows)
    gates = Counter(r["gate"] for r in rows if r["gate"])
    blocked = [r for r in rows if r["gate"]]
    base_eq = sum(r["base"]["eq_pct"] for r in rows)
    var_eq = sum(r["var"]["eq_pct"] for r in rows if r["var"])
    return {"entries": len(rows), "classes": dict(sorted(cls.items())), "gate_blocked": dict(sorted(gates.items())),
            # what the entries the floor's gates refused did under the live stop
            "gate_blocked_live": dict(sorted(Counter(r["base"]["outcome"] for r in blocked).items())),
            "gate_blocked_live_eq_pct": round(sum(r["base"]["eq_pct"] for r in blocked), 1),
            "stop_to_win": cls.get("stop_to_win", 0), "win_to_stop": cls.get("win_to_stop", 0),
            "base_eq_pct_sum": round(base_eq, 1), "var_eq_pct_sum": round(var_eq, 1),
            "eq_pct_delta": round(var_eq - base_eq, 1)}


def blocked_episodes(rows: Sequence[Dict[str, Any]], tick_ms: int = FIFTEEN_MS) -> Dict[str, int]:
    """Run.floor_blocked_rows grouped into episodes: consecutive ticks on the
    same side and gate are one setup the floor kept refusing. gate -> count."""
    out: Counter = Counter()
    prev: Optional[Dict[str, Any]] = None
    for r in rows:
        if prev is None or r["time"] - prev["time"] > tick_ms or r["side"] != prev["side"] or r["gate"] != prev["gate"]:
            out[r["gate"]] += 1
        prev = r
    return dict(sorted(out.items()))


def summarize(run: Run, start_ms: int, end_ms: int) -> Dict[str, Any]:
    rs = [t.r_multiple for t in run.trades if t.r_multiple is not None]
    n = len(run.trades)
    exits = Counter(t.exit_reason for t in run.trades)
    spd = stops_per_day(run)
    return {
        "days": round((end_ms - start_ms) / DAY_MS, 1),
        "final_equity": round(run.final_equity, 2),
        "return_pct": round((run.final_equity / START - 1.0) * 100, 1),
        "max_dd_pct": round(run.max_drawdown_pct * 100, 1),
        "trades": n,
        "win_rate": round(sum(1 for t in run.trades if t.net_pnl_usd > 0) / n, 3) if n else None,
        "avg_r": round(sum(rs) / len(rs), 3) if rs else None,
        "total_r": round(sum(rs), 2),
        "liquidated": run.liquidated,
        "exits": dict(sorted(exits.items())),
        "open_at_end": None if run.open_position_at_end is None else {
            k: round(v, 4) if isinstance(v, float) else v for k, v in run.open_position_at_end.items()},
        "stop_days_2plus": spd["2"] + spd["3"] + spd["4+"],
        "floor_widened": run.floor_widened,
        "floor_blocked_ticks": dict(sorted(run.floor_blocked.items())),
        "floor_blocked_episodes": blocked_episodes(run.floor_blocked_rows),
        "refused": dict(sorted(run.refused.items())),
    }


def buffer_stats(c15: Sequence[Candle], c1h: Sequence[Candle], start_ms: int, end_ms: int,
                 floors: Sequence[float] = FLOORS, pcts: Sequence[float] = FLOOR_PCTS) -> Dict[str, Any]:
    """The live buffer (1.3 x the tick's ATR14) over every 15m tick in the
    window, and how often each floor would bind (floor above it)."""
    hours = sorted(c1h, key=lambda c: c.t)
    bufs: List[Tuple[float, float]] = []
    j = 0
    for bar in sorted(c15, key=lambda c: c.t):
        if not (start_ms <= bar.t < end_ms):
            continue
        while j < len(hours) and hours[j].t + HOUR_MS <= bar.t:
            j += 1
        lv = _levels(hours[max(0, j - 24):j])
        if lv:
            bufs.append((ATR_BUFFER_MULT * lv["atr14"], bar.o))
    if not bufs:
        return {}
    vals = sorted(b for b, _ in bufs)
    q = lambda f: round(vals[min(len(vals) - 1, int(f * len(vals)))], 3)
    return {"ticks": len(vals), "min": round(vals[0], 3), "p10": q(0.10), "median": round(statistics.median(vals), 3),
            "mean": round(statistics.fmean(vals), 3), "p90": q(0.90), "max": round(vals[-1], 3),
            "binds_pct": {**{f"floor_{x:.2f}": round(sum(1 for b, _ in bufs if b < x) / len(bufs) * 100, 1)
                             for x in floors},
                          **{f"pct_{p:.1f}": round(sum(1 for b, px in bufs if b < p / 100 * px) / len(bufs) * 100, 1)
                             for p in pcts}}}


def score_vs_live(rows: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """profit_lock_test.score with "live" as the baseline: per variant, how
    many datasets it beats live on (final equity) and the geo-mean ratio."""
    return score({ds: {("baseline" if v == BASELINE else v): s for v, s in by_v.items()}
                  for ds, by_v in rows.items()})


# ── data + runs ────────────────────────────────────────────────────────────

def _window(c15, c1h, start_ms, end_ms):
    return [c for c in c15 if start_ms <= c.t < end_ms], [c for c in c1h if c.t < end_ms]


def run_dataset(c15: Sequence[Candle], c1h: Sequence[Candle], direction_at, base: Params, start_ms: int,
                end_ms: int, named: Dict[str, Dict[str, Any]], attribute_entries: bool = True,
                funding=None) -> Dict[str, Any]:
    """Every variant on one dataset, plus the per-entry attribution vs the
    baseline ("live")."""
    w15, w1h = _window(c15, c1h, start_ms, end_ms)
    out: Dict[str, Any] = {"window": [_iso(start_ms), _iso(end_ms)], "variants": {}, "entries": {}, "trades": {}}
    base_p = dataclasses.replace(base, **named[BASELINE])
    base_run = simulate(w15, w1h, direction_at, base_p, funding)
    for v, ov in named.items():
        p = dataclasses.replace(base, **ov)
        run = base_run if v == BASELINE else simulate(w15, w1h, direction_at, p, funding)
        s = summarize(run, start_ms, end_ms)
        out["trades"][v] = [{"side": t.side, "entry": _iso(t.entry_time), "exit": _iso(t.exit_time),
                             "entry_px": round(t.entry_px, 4), "stop_px": round(t.stop_px, 4),
                             "reason": t.exit_reason, "net_usd": round(t.net_pnl_usd, 2),
                             "r": round(t.r_multiple, 2) if t.r_multiple is not None else None}
                            for t in run.trades]
        if v != BASELINE and attribute_entries:
            rows = attribute(base_run, w15, w1h, direction_at, base_p, p)
            s["vs_live"] = attribution_summary(rows)
            out["entries"][v] = rows
        out["variants"][v] = s
    return out


def brent_identical(c15, c1h, direction_at, base: Params, start_ms: int, end_ms: int, funding=None
                    ) -> Dict[str, Any]:
    """Brent with each floor set: every run must equal the baseline trade for
    trade and point for point."""
    w15, w1h = _window(c15, c1h, start_ms, end_ms)
    ref = simulate(w15, w1h, direction_at, base, funding)
    ref_trades = [dataclasses.asdict(t) for t in ref.trades]
    same = {}
    for v, ov in brent_variants().items():
        run = simulate(w15, w1h, direction_at, dataclasses.replace(base, **ov), funding)
        same[v] = ([dataclasses.asdict(t) for t in run.trades] == ref_trades
                   and run.equity_curve == ref.equity_curve and run.floor_widened == 0 and not run.floor_blocked)
    return {"window": [_iso(start_ms), _iso(end_ms)], "final_equity": round(ref.final_equity, 2),
            "trades": len(ref.trades), "identical": same, "all_identical": all(same.values())}


ENTRY_FIELDS = ["dataset", "variant", "time", "side", "entry_px", "target_px", "atr14", "live_buffer", "class",
                "gate", "base_stop", "base_reason", "base_exit_time", "base_r", "base_eq_pct",
                "var_stop", "var_reason", "var_exit_time", "var_r", "var_eq_pct"]
SUMMARY_FIELDS = ["dataset", "variant", "final_equity", "return_pct", "max_dd_pct", "trades", "win_rate", "avg_r",
                  "total_r", "stop_days_2plus", "floor_widened", "floor_blocked_leverage_ticks",
                  "floor_blocked_min_r_ticks", "floor_blocked_episodes", "stop_to_win", "win_to_stop",
                  "gate_blocked_entries", "gate_blocked_live_losses", "gate_blocked_live_wins", "eq_pct_delta"]


def entry_csv_rows(results: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for ds, r in results.items():
        for v, rows in r.get("entries", {}).items():
            for e in rows:
                b, x = e["base"], e["var"] or {}
                out.append({"dataset": ds, "variant": v, "time": e["time"], "side": e["side"],
                            "entry_px": e["entry_px"], "target_px": e["target_px"], "atr14": e["atr14"],
                            "live_buffer": e["live_buffer"], "class": e["class"], "gate": e["gate"] or "",
                            "base_stop": b["stop_px"], "base_reason": b["reason"], "base_exit_time": b["exit_time"],
                            "base_r": b["r_net"], "base_eq_pct": b["eq_pct"], "var_stop": x.get("stop_px", ""),
                            "var_reason": x.get("reason", ""), "var_exit_time": x.get("exit_time", ""),
                            "var_r": x.get("r_net", ""), "var_eq_pct": x.get("eq_pct", "")})
    return out


def summary_csv_rows(results: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for ds, r in results.items():
        for v, s in r.get("variants", {}).items():
            a = s.get("vs_live") or {}
            out.append({"dataset": ds, "variant": v, **{k: s[k] for k in (
                "final_equity", "return_pct", "max_dd_pct", "trades", "win_rate", "avg_r", "total_r",
                "stop_days_2plus", "floor_widened")},
                "floor_blocked_leverage_ticks": s["floor_blocked_ticks"].get("leverage", 0),
                "floor_blocked_min_r_ticks": s["floor_blocked_ticks"].get("min_r", 0),
                "floor_blocked_episodes": sum(s["floor_blocked_episodes"].values()),
                "stop_to_win": a.get("stop_to_win", ""), "win_to_stop": a.get("win_to_stop", ""),
                "gate_blocked_entries": sum(a.get("gate_blocked", {}).values()) if a else "",
                "gate_blocked_live_losses": a.get("gate_blocked_live", {}).get("loss", 0) if a else "",
                "gate_blocked_live_wins": a.get("gate_blocked_live", {}).get("win", 0) if a else "",
                "eq_pct_delta": a.get("eq_pct_delta", "")})
    return out


def main(argv: Optional[List[str]] = None) -> int:
    from services.strategies.evals.cross_market_backtest import mean_atr
    from services.strategies.evals.market_scout import direction_series, load_coin_candles, scouting_params

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--metals-theses-dir", default="", help="READ ONLY: the live metals journal's theses")
    ap.add_argument("--oil-theses-dir", default="", help="READ ONLY: the live oil journal (Brent proof)")
    ap.add_argument("--oil-replay-dir", default="/tmp/thesis-replay-30b/oil/theses")
    ap.add_argument("--silver-theses-dir", default="/tmp/thesis-replay-silver/theses")
    ap.add_argument("--silver-cache-dir", default="/tmp/thesis-backtest/cache",
                    help="the 30-day silver replay's own candle cache")
    ap.add_argument("--silver-max-leverage", type=float, default=None,
                    help="default: the venue's own maxLeverage, as thesis_backtest reads it")
    ap.add_argument("--cache-dir", default="/tmp/silver-floor/cache", help="fresh Brent + silver candles")
    ap.add_argument("--out-dir", default="/tmp/silver-floor")
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    os.makedirs(a.cache_dir, exist_ok=True)
    t0 = time.time()
    results: Dict[str, Dict[str, Any]] = {}
    log_path = os.path.join(a.out_dir, "progress.log")

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} silver-floor: {msg}"
        print(line, flush=True)
        with open(log_path, "a") as fh:
            fh.write(line + "\n")

    def fresh(coin: str) -> Tuple[List[Candle], List[Candle]]:
        got = load_coin_candles(coin, a.cache_dir)
        asof = int(os.path.getmtime(D._cache_path(a.cache_dir, coin, "candles_15m_4820")) * 1000)
        return closed_only(got["c15"], FIFTEEN_MS, asof), closed_only(got["c1h"], HOUR_MS, asof)

    if a.silver_max_leverage is None:
        from services.strategies.evals.market_scout import fetch_market_meta
        a.silver_max_leverage = float((fetch_market_meta(["xyz:SILVER"]).get("xyz:SILVER") or {})
                                      .get("maxLeverage") or 20)

    # The 30-day replay: its own cache, its own fixed buffer
    s_theses = D.load_journaled_theses(a.silver_theses_dir)
    silver = load_coin_candles("xyz:SILVER", a.silver_cache_dir)
    s15, s1h = silver["c15"], silver["c1h"]
    atr, _ = mean_atr(s1h)
    # risk_cap_frac only (2026-11-02's 15% cap), NOT the full live_silver_params
    # (whose stop_buffer_floor_frac default would bake this file's own candidate
    # floor onto its no-floor baseline) -- this `sp` is the "live rule today, no
    # floor" baseline the floor variants (`variants(fixed)`) are swept against.
    sp = dataclasses.replace(scouting_params(a.silver_max_leverage, atr), starting_equity=START,
                             risk_cap_frac=LIVE_RISK_CAP_FRAC)
    fixed = sp.stop_buffer
    named = variants(fixed)
    s_start = min(th.created_at_ms for th in s_theses)
    s_end = max(c.t for c in s15) + FIFTEEN_MS
    results["silver_theses"] = run_dataset(s15, s1h, journaled_direction(s_theses), sp, s_start, s_end, named)
    results["silver_theses"]["n_theses"] = len(s_theses)
    for s in CONTROLS:
        results[f"silver_{s}"] = run_dataset(s15, s1h, direction_series(s, s1h), sp, s_start, s_end, named)
    log(f"silver replay + controls done ({time.time() - t0:.0f}s)")
    buffers = {"silver_replay_window": buffer_stats(s15, s1h, s_start, s_end)}

    # Fresh silver: the live journal (10-31 on) and the recent-30-day controls
    f15, f1h = fresh("xyz:SILVER")
    f_end = f15[-1].t + FIFTEEN_MS
    m_all = D.load_journaled_theses(a.metals_theses_dir) if a.metals_theses_dir else []
    m_theses = same_market(m_all, f1h) if m_all else []
    if m_theses:
        m_start = min(th.created_at_ms for th in m_theses)
        results["silver_journal"] = run_dataset(f15, f1h, journaled_direction(m_theses), sp, m_start, f_end, named)
        results["silver_journal"]["n_theses"] = len(m_theses)
        results["silver_journal"]["dropped_theses"] = len(m_all) - len(m_theses)
        buffers["silver_journal_window"] = buffer_stats(f15, f1h, m_start, f_end)
        log(f"silver_journal: {len(m_theses)} silver theses done ({time.time() - t0:.0f}s)")
    r_start = f_end - 30 * DAY_MS
    for s in CONTROLS:
        results[f"silver_{s}_recent"] = run_dataset(f15, f1h, direction_series(s, f1h), sp, r_start, f_end, named)
    buffers["silver_recent30_window"] = buffer_stats(f15, f1h, r_start, f_end)
    log(f"silver recent controls done ({time.time() - t0:.0f}s)")

    # Brent: each floor set, must be identical (fixed 0.75, stop_buffer_atr off)
    brent: Dict[str, Any] = {}
    b15, b1h = fresh("xyz:BRENTOIL")
    b_end = b15[-1].t + FIFTEEN_MS
    funding = D.fetch_funding_cached("xyz:BRENTOIL", b15[0].t, b_end, a.cache_dir)
    bp = live_brent_params(size_slippage=True, starting_equity=START)
    for label, tdir, end_rule in (("brent_journal", a.oil_theses_dir, "data"),
                                  ("brent_replay30", a.oil_replay_dir, "last+1d")):
        theses = D.load_journaled_theses(tdir) if tdir else []
        if not theses:
            log(f"{label}: no theses at {tdir!r}, skipped")
            continue
        start = min(th.created_at_ms for th in theses)
        end = b_end if end_rule == "data" else min(b_end, max(th.created_at_ms for th in theses) + DAY_MS)
        brent[label] = brent_identical(b15, b1h, journaled_direction(theses), bp, start, end, funding)
    for s in CONTROLS:
        brent[f"brent_{s}_30d"] = brent_identical(b15, b1h, direction_series(s, b1h), bp, b_end - 30 * DAY_MS,
                                                  b_end, funding)
    log(f"brent proof done ({time.time() - t0:.0f}s)")

    groups = {
        "thesis": [k for k in ("silver_theses", "silver_journal") if k in results],
        "controls_replay": [f"silver_{s}" for s in CONTROLS],
        "controls_recent": [f"silver_{s}_recent" for s in CONTROLS],
        "all": list(results),
    }
    scores = {g: score_vs_live({k: results[k]["variants"] for k in ks}) for g, ks in groups.items() if ks}

    incident = {}
    if "silver_journal" in results:
        day = "2026-11-02"
        incident = {v: [t for t in tr if (t["entry"] or "").startswith(day) or (t["exit"] or "").startswith(day)]
                    for v, tr in results["silver_journal"]["trades"].items()}

    with open(os.path.join(a.out_dir, "entries.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=ENTRY_FIELDS)
        w.writeheader()
        w.writerows(entry_csv_rows(results))
    with open(os.path.join(a.out_dir, "summary.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SUMMARY_FIELDS)
        w.writeheader()
        w.writerows(summary_csv_rows(results))
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()),
        "seconds": round(time.time() - t0, 1),
        "fixed_test_buffer": round(fixed, 4), "silver_max_leverage": a.silver_max_leverage,
        "silver_data_end": _iso(f_end), "variants": named,
        "datasets": {k: {kk: vv for kk, vv in r.items() if kk != "entries"} for k, r in results.items()},
        "scores": scores, "buffers": buffers, "brent_unchanged": brent,
        "brent_all_identical": all(b["all_identical"] for b in brent.values()) if brent else None,
        "incident_2026_11_02": incident,
    }
    out_path = os.path.join(a.out_dir, "report.json")
    with open(out_path, "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    _print(report)
    print(f"\nreport: {out_path}\nsummary: {os.path.join(a.out_dir, 'summary.csv')}"
          f"\nentries: {os.path.join(a.out_dir, 'entries.csv')}")
    return 0


def _print(rep: Dict[str, Any]) -> None:
    print(f"fixed test buffer {rep['fixed_test_buffer']}, silver max leverage {rep['silver_max_leverage']}, "
          f"silver data end {rep['silver_data_end']}")
    for ds, r in rep["datasets"].items():
        print(f"\n{ds}  {r['window'][0]} -> {r['window'][1]}")
        print(f"  {'variant':<11} {'$end':>8} {'ret%':>7} {'dd%':>5} {'n':>3} {'win':>4} {'avgR':>6} {'2+d':>3} "
              f"{'wid':>3} {'blk':>7} {'s>w':>3} {'w>s':>3} {'gate':>4} {'g:l/w':>5} {'dEq%':>7}")
        for v, s in r["variants"].items():
            a = s.get("vs_live") or {}
            win = f"{s['win_rate']:.0%}" if s["win_rate"] is not None else "-"
            ar = f"{s['avg_r']:.2f}" if s["avg_r"] is not None else "-"
            fb = s["floor_blocked_ticks"]
            blk = f"{fb.get('leverage', 0)}/{fb.get('min_r', 0)}"
            g = a.get("gate_blocked_live") or {}
            gl = f"{g.get('loss', 0)}/{g.get('win', 0)}" if a else ""
            print(f"  {v:<11} {s['final_equity']:8.2f} {s['return_pct']:7.1f} {s['max_dd_pct']:5.1f} "
                  f"{s['trades']:>3} {win:>4} {ar:>6} {s['stop_days_2plus']:>3} {s['floor_widened']:>3} {blk:>7} "
                  f"{a.get('stop_to_win', ''):>3} {a.get('win_to_stop', ''):>3} "
                  f"{sum(a.get('gate_blocked', {}).values()) if a else '':>4} {gl:>5} {a.get('eq_pct_delta', ''):>7}")
    for g, sc in rep["scores"].items():
        print(f"\nscore [{g}] (beats/loses live of N, geo-mean final/live):")
        for v, x in sorted(sc.items(), key=lambda kv: -kv[1]["geo_mean_ratio"]):
            print(f"  {v:<11} {x['beats']:>2}/{x['loses']:>2} of {x['of']:<2} {x['geo_mean_ratio']:.3f}")
    print("\nlive buffer (1.3 x ATR14) per tick, and % of ticks each floor binds:")
    for k, b in rep["buffers"].items():
        print(f"  {k}: {json.dumps(b)}")
    print(f"\nbrent unchanged with every floor set: {rep['brent_all_identical']}")
    for v, trs in (rep.get("incident_2026_11_02") or {}).items():
        print(f"  2026-11-02 {v:<11} " + "; ".join(f"{t['side']} {t['entry'][11:]}->{(t['exit'] or 'open')[11:]} "
                                                    f"{t['reason']} ${t['net_usd']}" for t in trs))


if __name__ == "__main__":
    raise SystemExit(main())
