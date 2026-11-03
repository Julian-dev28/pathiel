#!/usr/bin/env python3
"""The "wiggle" scalp eval, 2026-11-03 (docs/strategy/SCALP_TEST_2026-11-03.md).

Operator: "can u test a strategy where we play the wiggle? like something
small like 5% of the portfolio at 20x leverage intraday/intrahour swing
trade so we can still profit in a sideways market like this?" Brent is
chopping 103-107 while the oil desk holds a far-target fade of the 24h
range. This tests a SEPARATE, small, fast mean-reversion book
(`services.strategies.backtest.scalp_sim`) meant to bank the chop itself,
on `xyz:BRENTOIL`, `xyz:CL` (WTI) and `xyz:SILVER`.

Method
------
1. Fetch the longest 15m (and 5m, where the venue has it) history available
   through `data.fetch_candles_paged_cached` (pages past the venue's 5000-
   bar-per-request cap), plus 1h candles for the regime label.
2. Walk-forward: split the window in half by time. Grid-search BOTH rule
   families (`range_fade`, `mean_revert`) on the FIRST half only (ranked by
   net $ on the slice), then run the chosen params, unchanged, on the
   SECOND half (out-of-sample) -- the number this eval actually trusts.
3. Plateau check: does the chosen combo's immediate neighbour on each grid
   axis (held in-sample) also clear a bare "profitable" bar, or is it a
   lone spike?
4. Random-entry control (`scalp_sim.simulate_random_control`): the SAME
   stop distance, minimum reward, leverage, sizing and fees as the chosen
   rule, but entries at a random bar and side instead of the rule's own
   trigger, at the rule's own trade frequency, many seeds, on the SAME
   out-of-sample window.
5. Gates study: the chosen variant with the regime chop gate and the
   thesis-direction gate each on and off, in- and out-of-sample.
6. Sizing: the chosen variant at `slice_frac` 0.05 (the ask) and 0.15 (the
   live risk cap elsewhere in this repo), `risk_frac` 0.5 (reported, not
   swept -- the cap on how much of the SLICE's own margin one stop may
   cost).
7. Netting: Hyperliquid nets one position per coin per account. A Brent
   scalp on the SAME account as the oil desk would merge into the oil
   desk's own Brent position, so Brent is reported two ways: ignoring that
   (informational only, "if it had its own book") and gated to only open
   while the oil desk's own journaled position is flat
   (`scalp_sim.oil_desk_flat_at`, built from a real `desk_tick_sim` replay
   of the oil journal over the SAME window). `xyz:SILVER` has the same
   problem with the live metals desk (also active) if a metals theses dir
   is given. `xyz:CL` (WTI) has no active desk, so it is reported straight.

Read-only on everything live: candles come from the venue's public,
read-only `/info` endpoint and the eval's own disk cache; theses are read
from the journal directories (never written). No LLM calls, no pathiel MCP
tool, no live order.

    python -m services.strategies.evals.scalp_test \\
        [--oil-theses-dir <pathia>/.state/strategies/oil/theses] \\
        [--metals-theses-dir <pathia>/.state/strategies/metals/theses]

Writes /tmp/scalp-test/report.json and summary.csv.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pathiel.models.types import Candle

from services.strategies.backtest import data as D
from services.strategies.backtest.desk_tick_sim import live_params as desk_live_params
from services.strategies.backtest.desk_tick_sim import simulate as desk_simulate
from services.strategies.backtest.regime import RegimeConfig, label_series
from services.strategies.backtest.scalp_sim import (
    Params, Run, oil_desk_flat_at, simulate, simulate_random_control)
from services.strategies.evals.desk_tick_backtest import journaled_direction
from services.strategies.evals.market_scout import fetch_market_meta
from services.strategies.evals.strategy_sweep import daily_marks, period_returns

DAY_MS = D.DAY_MS
HOUR_MS = D.HOUR_MS
FIFTEEN_MS = 900_000
FIVE_MS = 300_000
START_EQUITY = 64.0          # the whole-account equity convention this repo's evals use throughout
RISK_FRAC = 0.5               # the cap: a stop may cost at most this share of the SLICE's own margin
SLICE_FRACS = (0.05, 0.15)    # the ask (5%) and the live full-account risk cap elsewhere (15%)

COINS: Tuple[str, ...] = ("xyz:BRENTOIL", "xyz:CL", "xyz:SILVER")
TARGET_15M_DAYS = 90           # ask for the longest history the venue will hand back, paged
TARGET_5M_DAYS = 20
MAX_LEVERAGE_FALLBACK = 20.0

# ── grids (pure, small per the ask) ─────────────────────────────────────────
RANGE_LOOKBACK_H = (1.0, 2.0, 4.0)
RANGE_TARGET_FRAC = (0.5, 0.75, 1.0)
RANGE_STOP_K = (0.5, 1.0, 1.5)
MEANREV_WINDOW_BARS = (8, 16, 32)      # ~2h / 4h / 8h of 15m bars
MEANREV_Z_ENTRY = (1.5, 2.0, 2.5)
MEANREV_Z_STOP = (3.0, 3.5)
RANDOM_SEEDS = tuple(range(20))


def _iso(ms: Optional[int]) -> Optional[str]:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%MZ")


def desk_open_at_end(desk_run: Any) -> Optional[Dict[str, Any]]:
    """`scalp_sim.oil_desk_flat_at`'s `open_position_at_end` shape ({"entry_
    time": ...}) from a `desk_tick_sim.Run`, whose own `open_position_at_end`
    has no `entry_time` key (it lives in `open_position_levels["opened_at"]`
    instead -- the two simulators' Trade/Run shapes differ). None when the
    desk closed out by the end of the window."""
    if desk_run.open_position_at_end is None:
        return None
    opened_at = (desk_run.open_position_levels or {}).get("opened_at")
    return {"entry_time": opened_at} if opened_at is not None else None


def thesis_side_at(theses):
    """`desk_tick_backtest.journaled_direction` returns `desk_tick_sim.
    Direction` objects (side, invalidation_px, ...) for the desk's own
    simulator. `scalp_sim.simulate`'s `direction_at` is a plain string
    ("long" | "short" | None) -- this adapts one to the other so the same
    journaled thesis can gate both simulators without re-parsing anything."""
    raw = journaled_direction(theses)

    def at(t: int) -> Optional[str]:
        d = raw(t)
        return d.side if d is not None else None
    return at


# ── grid builders ────────────────────────────────────────────────────────

def range_fade_grid(base: Params) -> Dict[str, Params]:
    out: Dict[str, Params] = {}
    for lb in RANGE_LOOKBACK_H:
        for tf in RANGE_TARGET_FRAC:
            for sk in RANGE_STOP_K:
                out[f"rf_lb{lb:g}_tf{tf:g}_sk{sk:g}"] = dataclasses.replace(
                    base, rule="range_fade", lookback_hours=lb, target_frac=tf, stop_atr_k=sk)
    return out


def mean_revert_grid(base: Params) -> Dict[str, Params]:
    out: Dict[str, Params] = {}
    for w in MEANREV_WINDOW_BARS:
        for ze in MEANREV_Z_ENTRY:
            for zs in MEANREV_Z_STOP:
                if zs <= ze:
                    continue
                out[f"mr_w{w}_ze{ze:g}_zs{zs:g}"] = dataclasses.replace(
                    base, rule="mean_revert", ma_window=w, z_entry=ze, z_stop=zs)
    return out


def range_fade_neighbours(name: str) -> List[str]:
    """The grid's immediate neighbours on each of its three axes, for the
    plateau check. `name` must be one `range_fade_grid` produced."""
    lb_s, tf_s, sk_s = name[len("rf_lb"):].split("_tf")[0], None, None
    lb = float(lb_s)
    rest = name.split("_tf", 1)[1]
    tf_s, sk_s = rest.split("_sk")
    tf, sk = float(tf_s), float(sk_s)
    out = []
    for axis, grid, val in (("lb", RANGE_LOOKBACK_H, lb), ("tf", RANGE_TARGET_FRAC, tf),
                            ("sk", RANGE_STOP_K, sk)):
        i = grid.index(val)
        for j in (i - 1, i + 1):
            if 0 <= j < len(grid):
                vals = {"lb": lb, "tf": tf, "sk": sk}
                vals[axis] = grid[j]
                out.append(f"rf_lb{vals['lb']:g}_tf{vals['tf']:g}_sk{vals['sk']:g}")
    return [n for n in out if n != name]


def mean_revert_neighbours(name: str) -> List[str]:
    body = name[len("mr_w"):]
    w_s, rest = body.split("_ze")
    ze_s, zs_s = rest.split("_zs")
    w, ze, zs = int(w_s), float(ze_s), float(zs_s)
    out = []
    for axis, grid, val in (("w", MEANREV_WINDOW_BARS, w), ("ze", MEANREV_Z_ENTRY, ze),
                            ("zs", MEANREV_Z_STOP, zs)):
        i = grid.index(val)
        for j in (i - 1, i + 1):
            if 0 <= j < len(grid):
                vals = {"w": w, "ze": ze, "zs": zs}
                vals[axis] = grid[j]
                if vals["zs"] <= vals["ze"]:
                    continue
                out.append(f"mr_w{vals['w']}_ze{vals['ze']:g}_zs{vals['zs']:g}")
    return [n for n in out if n != name]


# ── slice-relative metrics (pure given a Run) ───────────────────────────────

def slice_curve(run: Run, params: Params) -> List[Tuple[int, float]]:
    """The equity curve re-based onto the SLICE's own capital: every $ of
    P&L the whole-account curve carries came from this book alone, so a
    constant shift (not a rescale) turns it into "if this were the whole
    account". Used for the slice's own drawdown and daily %."""
    slice_capital = params.slice_frac * params.starting_equity
    shift = slice_capital - params.starting_equity
    return [(t, mark + shift) for t, mark in run.equity_curve]


def max_drawdown_pct(curve: Sequence[Tuple[int, float]]) -> float:
    peak, dd = (curve[0][1] if curve else 0.0), 0.0
    for _, mark in curve:
        peak = max(peak, mark)
        if peak > 0:
            dd = max(dd, (peak - mark) / peak)
    return dd


def slice_ruined(curve: Sequence[Tuple[int, float]]) -> bool:
    """True if the SLICE's own re-based equity (`slice_curve`) ever goes to
    zero or below. The sizing in `scalp_sim.simulate` draws margin off the
    WHOLE account's running equity (barely dented by a 5-15% book's own
    losses), so the sim keeps trading at close to full size long after an
    isolated sub-account holding only the slice's capital would have been
    wiped out and stopped. A drawdown over 100% of the slice (or this flag)
    means the back-test from that point on is no longer a faithful stand-in
    for a real ring-fenced allocation -- read it as a disqualifying result,
    not a literal return number."""
    return any(mark <= 0 for _, mark in curve)


def summarize(run: Run, start_ms: int, end_ms: int, params: Params) -> Dict[str, Any]:
    days = max((end_ms - start_ms) / DAY_MS, 1e-9)
    slice_capital = params.slice_frac * params.starting_equity
    net_usd = round(run.final_equity - params.starting_equity, 4)
    s_curve = slice_curve(run, params)
    slice_final = slice_capital + net_usd
    daily_pct = (slice_final / slice_capital) ** (1.0 / days) - 1.0 if slice_final > 0 and slice_capital > 0 else -1.0
    marks = daily_marks(s_curve, start_ms)
    day_rets = period_returns(marks, slice_capital, 1) if marks else []
    trades = run.trades
    n = len(trades)
    wins = sum(1 for t in trades if t.net_pnl_usd > 0)
    rs = [t.r_multiple for t in trades if t.r_multiple is not None]
    gross = sum(t.gross_pnl_usd for t in trades)
    fees = run.fees_total_usd
    fee_share_of_gross = round(fees / abs(gross), 4) if abs(gross) > 1e-9 else None
    return {
        "days": round(days, 2), "trades": n, "trades_per_day": round(n / days, 2) if days > 0 else None,
        "win_rate": round(wins / n, 3) if n else None, "avg_r": round(sum(rs) / len(rs), 3) if rs else None,
        "net_usd": net_usd, "per_day_pct_of_slice": round(daily_pct * 100, 3),
        "worst_day_pct_of_slice": round(min(day_rets) * 100, 2) if day_rets else None,
        "max_dd_pct_of_slice": round(max_drawdown_pct(s_curve) * 100, 2),
        "fees_usd": round(fees, 4), "gross_usd": round(gross, 4), "fee_share_of_gross": fee_share_of_gross,
        "liquidated": run.liquidated, "refused": dict(run.refused), "open_at_end": run.open_position_at_end is not None,
        "slice_ruined": slice_ruined(s_curve),
    }


def net_score(summary: Dict[str, Any]) -> float:
    """What the walk-forward grid search ranks on: net $ on the slice, with
    an account liquidation or a wiped-out slice (`slice_ruined`) treated as
    a hard floor so neither can ever look like a win just because the sim
    kept trading off the undiminished whole account after the slice itself
    was gone."""
    return -1e9 if (summary["liquidated"] or summary["slice_ruined"]) else summary["net_usd"]


# ── walk-forward on one window ───────────────────────────────────────────

def _window(bars: Sequence[Candle], start_ms: int, end_ms: int) -> List[Candle]:
    return [c for c in bars if start_ms <= c.t < end_ms]


def walk_forward(bars: Sequence[Candle], grid: Dict[str, Params], neighbours_of, start_ms: int, end_ms: int,
                 direction_at=None, active_at=None, regime_series=None) -> Dict[str, Any]:
    """Grid-search `grid` on the first half of [start_ms, end_ms), run the
    winner unchanged on the second half, and check the plateau against the
    IN-SAMPLE neighbours. Pure given its inputs."""
    mid = start_ms + (end_ms - start_ms) // 2
    in_bars, out_bars = _window(bars, start_ms, mid), _window(bars, mid, end_ms)
    in_sample = {name: summarize(simulate(in_bars, p, direction_at, active_at, regime_series), start_ms, mid, p)
                for name, p in grid.items()}
    best = max(grid, key=lambda n: net_score(in_sample[n]))
    best_params = grid[best]
    out_sample = summarize(simulate(out_bars, best_params, direction_at, active_at, regime_series), mid, end_ms,
                           best_params)
    nbrs = neighbours_of(best)
    plateau = bool(nbrs) and all(in_sample[n]["net_usd"] > 0 and not in_sample[n]["liquidated"]
                                 and not in_sample[n]["slice_ruined"] for n in nbrs if n in in_sample)
    return {"window": [_iso(start_ms), _iso(mid), _iso(end_ms)], "chosen": best,
            "chosen_params": {f.name: getattr(best_params, f.name) for f in dataclasses.fields(best_params)
                              if f.name not in ("regime_cfg",)},
            "in_sample": in_sample[best], "out_of_sample": out_sample, "plateau": plateau,
            "neighbours": {n: in_sample.get(n) for n in nbrs}, "grid_size": len(grid)}


def random_control(bars: Sequence[Candle], params: Params, start_ms: int, end_ms: int, entries_per_day: float,
                   seeds: Sequence[int] = RANDOM_SEEDS) -> Dict[str, Any]:
    w_bars = _window(bars, start_ms, end_ms)
    runs = [summarize(simulate_random_control(w_bars, params, seed, entries_per_day), start_ms, end_ms, params)
           for seed in seeds]
    nets = [r["net_usd"] for r in runs]
    return {"seeds": len(seeds), "entries_per_day": entries_per_day, "mean_net_usd": round(sum(nets) / len(nets), 2),
           "best_net_usd": round(max(nets), 2), "worst_net_usd": round(min(nets), 2),
           "share_positive": round(sum(1 for x in nets if x > 0) / len(nets), 2)}


# ── gates and sizing studies on the already-chosen variant ────────────────

def gates_study(bars: Sequence[Candle], base_params: Params, start_ms: int, mid_ms: int, end_ms: int,
                direction_at=None, regime_series=None) -> Dict[str, Any]:
    in_bars, out_bars = _window(bars, start_ms, mid_ms), _window(bars, mid_ms, end_ms)
    out: Dict[str, Any] = {}
    gate_variants = {"base": {}, "regime_chop_only": {"regime_filter": "chop_only"}}
    if direction_at is not None:
        gate_variants["thesis_only"] = {}   # applied via direction_at itself below, not a Params field
    for name, ov in gate_variants.items():
        p = dataclasses.replace(base_params, **ov)
        d_at = direction_at if name == "thesis_only" else None
        rs = regime_series if name == "regime_chop_only" else None
        out[name] = {
            "in_sample": summarize(simulate(in_bars, p, d_at, None, rs), start_ms, mid_ms, p),
            "out_of_sample": summarize(simulate(out_bars, p, d_at, None, rs), mid_ms, end_ms, p),
        }
    return out


def sizing_study(bars: Sequence[Candle], base_params: Params, start_ms: int, end_ms: int) -> Dict[str, Any]:
    w_bars = _window(bars, start_ms, end_ms)
    out: Dict[str, Any] = {}
    for f in SLICE_FRACS:
        p = dataclasses.replace(base_params, slice_frac=f)
        out[f"slice_{int(f * 100)}pct"] = summarize(simulate(w_bars, p), start_ms, end_ms, p)
    return out


# ── data ─────────────────────────────────────────────────────────────────

def fetch_coin(coin: str, cache_dir: str, now_ms: int, days_15m: int = TARGET_15M_DAYS,
              days_5m: int = TARGET_5M_DAYS) -> Dict[str, Any]:
    end = now_ms
    start_15 = end - days_15m * DAY_MS
    c15 = D.fetch_candles_paged_cached(coin, "15m", start_15, end, cache_dir)
    start_5 = end - days_5m * DAY_MS
    c5 = D.fetch_candles_paged_cached(coin, "5m", start_5, end, cache_dir)
    start_1h = end - (days_15m + 30) * DAY_MS   # extra lookback so the regime label is warm on day 1
    c1h = D.fetch_candles_paged_cached(coin, "1h", start_1h, end, cache_dir)
    return {"c15": c15, "c5": c5, "c1h": c1h}


# ── main ─────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--oil-theses-dir", default=os.environ.get("PATHIEL_OIL_THESES_DIR", ""),
                    help="READ ONLY: the live oil journal's theses (skipped when empty)")
    ap.add_argument("--metals-theses-dir", default=os.environ.get("PATHIEL_METALS_THESES_DIR", ""),
                    help="READ ONLY: the live metals journal's theses (skipped when empty)")
    ap.add_argument("--coins", nargs="+", default=list(COINS))
    ap.add_argument("--cache-dir", default="/tmp/scalp-test/cache")
    ap.add_argument("--out-dir", default="/tmp/scalp-test")
    ap.add_argument("--days-15m", type=int, default=TARGET_15M_DAYS)
    ap.add_argument("--days-5m", type=int, default=TARGET_5M_DAYS)
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    os.makedirs(a.cache_dir, exist_ok=True)
    t0 = time.time()
    log_path = os.path.join(a.out_dir, "progress.log")

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} scalp-test: {msg}"
        print(line, flush=True)
        with open(log_path, "a") as fh:
            fh.write(line + "\n")

    now_ms = int(time.time() * 1000)
    meta = fetch_market_meta(list(a.coins))
    report: Dict[str, Any] = {"generated_at": _iso(now_ms), "coins": {}}

    base_by_coin: Dict[str, Params] = {}
    data_by_coin: Dict[str, Dict[str, Any]] = {}
    for coin in a.coins:
        max_lev = float((meta.get(coin) or {}).get("maxLeverage") or MAX_LEVERAGE_FALLBACK)
        lev = min(20.0, max_lev)
        data_by_coin[coin] = fetch_coin(coin, a.cache_dir, now_ms, a.days_15m, a.days_5m)
        base_by_coin[coin] = Params(leverage=lev, max_leverage=max_lev, starting_equity=START_EQUITY,
                                    risk_frac=RISK_FRAC, slice_frac=0.05)
        c15 = data_by_coin[coin]["c15"]
        log(f"{coin}: {len(c15)} 15m bars ({_iso(c15[0].t) if c15 else None} -> "
            f"{_iso(c15[-1].t + FIFTEEN_MS) if c15 else None}), max_lev {max_lev:g}, trade_lev {lev:g}")

    # journaled directions + the oil/metals desk's OWN replay (for the netting gate)
    oil_theses = D.load_journaled_theses(a.oil_theses_dir) if a.oil_theses_dir else []
    metals_theses = D.load_journaled_theses(a.metals_theses_dir) if a.metals_theses_dir else []
    report["theses"] = {"oil_theses": len(oil_theses), "metals_theses": len(metals_theses)}

    for coin in a.coins:
        c15, c1h = data_by_coin[coin]["c15"], data_by_coin[coin]["c1h"]
        c5 = data_by_coin[coin]["c5"]
        if len(c15) < 60:
            log(f"{coin}: too little 15m history ({len(c15)} bars), skipped")
            continue
        base = base_by_coin[coin]
        start_ms, end_ms = c15[0].t, c15[-1].t + FIFTEEN_MS
        regime_series = label_series(c1h, RegimeConfig()) if len(c1h) > 48 else None

        direction_at = None
        is_brent = coin == "xyz:BRENTOIL"
        is_silver = coin == "xyz:SILVER"
        theses = oil_theses if is_brent else (metals_theses if is_silver else [])
        if theses:
            direction_at = thesis_side_at(theses)   # already this coin's own desk journal; no cross-coin filtering

        coin_out: Dict[str, Any] = {"window": [_iso(start_ms), _iso(end_ms)], "bars_15m": len(c15),
                                    "bars_5m": len(c5), "trade_leverage": base.leverage, "risk_frac": base.risk_frac}

        # ── walk-forward, both rule families, 15m
        rf_grid = range_fade_grid(base)
        mr_grid = mean_revert_grid(base)
        coin_out["range_fade_15m"] = walk_forward(c15, rf_grid, range_fade_neighbours, start_ms, end_ms,
                                                   regime_series=regime_series)
        coin_out["mean_revert_15m"] = walk_forward(c15, mr_grid, mean_revert_neighbours, start_ms, end_ms,
                                                    regime_series=regime_series)
        log(f"{coin}: 15m walk-forward done ({time.time() - t0:.0f}s) -- "
            f"range_fade OOS ${coin_out['range_fade_15m']['out_of_sample']['net_usd']}, "
            f"mean_revert OOS ${coin_out['mean_revert_15m']['out_of_sample']['net_usd']}")

        # ── 5m, if there's enough history
        if len(c5) >= 60:
            s5, e5 = c5[0].t, c5[-1].t + FIVE_MS
            base5 = dataclasses.replace(base, bar_minutes=5)
            rf_grid5 = range_fade_grid(base5)
            coin_out["range_fade_5m"] = walk_forward(c5, rf_grid5, range_fade_neighbours, s5, e5,
                                                      regime_series=regime_series)
            log(f"{coin}: 5m walk-forward done ({time.time() - t0:.0f}s)")

        # ── pick the coin's single best (15m, either rule family) for the
        # gates/sizing/control/netting studies below. Judged on the IN-
        # SAMPLE score, same as the walk-forward grid search itself -- the
        # out-of-sample number is reported, never used to pick (using it
        # here would leak the holdout into the choice). Pure net_usd across
        # families would also let a rule that never clears its own min-R
        # gate ("mean_revert" low z_entry: every combo does this at these
        # thresholds, see the module docstring) look like a "winner" by
        # scoring a flat 0 against a rule that traded and lost -- so a
        # family with zero in-sample trades is only picked when the other
        # family ALSO never traded.
        def _family_rank(k: str) -> Tuple[bool, float]:
            s = coin_out[k]["in_sample"]
            return (s["trades"] > 0, net_score(s))

        best_family = max(("range_fade_15m", "mean_revert_15m"), key=_family_rank)
        chosen_params = dataclasses.replace(base, **{k: v for k, v in coin_out[best_family]["chosen_params"].items()
                                                      if k not in ("starting_equity", "leverage", "max_leverage",
                                                                   "risk_frac", "slice_frac")})
        coin_out["best_family"] = best_family
        mid_ms = start_ms + (end_ms - start_ms) // 2

        # ── random control, out-of-sample, at the chosen rule's own trade rate
        entries_per_day = max(0.5, coin_out[best_family]["out_of_sample"]["trades_per_day"] or 1.0)
        coin_out["random_control_oos"] = random_control(c15, chosen_params, mid_ms, end_ms, entries_per_day)
        log(f"{coin}: random control done ({time.time() - t0:.0f}s)")

        # ── gates study (regime chop-only, thesis-direction)
        coin_out["gates"] = gates_study(c15, chosen_params, start_ms, mid_ms, end_ms, direction_at, regime_series)

        # ── sizing study (5% vs 15% of equity)
        coin_out["sizing"] = sizing_study(c15, chosen_params, start_ms, end_ms)

        # ── netting
        netting: Dict[str, Any] = {"coin": coin}
        if is_brent and oil_theses:
            d_at = journaled_direction(oil_theses)
            desk_run = desk_simulate(c15, c1h, d_at, desk_live_params())
            flat_at = oil_desk_flat_at(desk_run.trades, desk_open_at_end(desk_run))
            flat_frac = sum(1 for c in c15 if flat_at(c.t)) / len(c15)
            gated = simulate(c15, chosen_params, direction_at=None, active_at=flat_at, regime_series=None)
            netting.update({
                "conflicts_with": "the oil desk (same account, same coin: Hyperliquid nets one position per "
                                  "coin per account)",
                "oil_desk_trades_in_window": len(desk_run.trades),
                "share_of_ticks_oil_desk_flat": round(flat_frac, 3),
                "brent_scalp_gated_to_oil_desk_flat": summarize(gated, start_ms, end_ms, chosen_params),
                "subaccount_needed_for_concurrent_run": True,
            })
        elif is_silver and metals_theses:
            d_at = journaled_direction(metals_theses)
            desk_run = desk_simulate(c15, c1h, d_at, dataclasses.replace(desk_live_params(), leverage=base.leverage,
                                                                         max_leverage=base.max_leverage))
            flat_at = oil_desk_flat_at(desk_run.trades, desk_open_at_end(desk_run))
            flat_frac = sum(1 for c in c15 if flat_at(c.t)) / len(c15)
            gated = simulate(c15, chosen_params, direction_at=None, active_at=flat_at, regime_series=None)
            netting.update({
                "conflicts_with": "the metals desk (same account, same coin)",
                "metals_desk_trades_in_window": len(desk_run.trades),
                "share_of_ticks_metals_desk_flat": round(flat_frac, 3),
                "silver_scalp_gated_to_metals_desk_flat": summarize(gated, start_ms, end_ms, chosen_params),
                "subaccount_needed_for_concurrent_run": True,
            })
        else:
            netting.update({"conflicts_with": None, "subaccount_needed_for_concurrent_run": False,
                           "note": "no active desk trades this coin on this account; the scalp can run standalone"})
        coin_out["netting"] = netting

        report["coins"][coin] = coin_out
        log(f"{coin}: done ({time.time() - t0:.0f}s)")

    # ── adopt bar: positive OOS net on Brent or WTI, beats the random
    # control's mean, a plateau (not a spike), tolerable drawdown (<=50% of
    # the slice -- the same half-of-liquidation spirit as the rest of this
    # repo's risk gates, applied here to the slice's own capital).
    verdicts: Dict[str, Any] = {}
    for coin in ("xyz:BRENTOIL", "xyz:CL"):
        c = report["coins"].get(coin)
        if not c:
            continue
        fam = c["best_family"]
        oos = c[fam]["out_of_sample"]
        ctrl = c["random_control_oos"]
        why = []
        positive = oos["net_usd"] > 0 and not oos["liquidated"] and not oos["slice_ruined"]
        beats_control = oos["net_usd"] > ctrl["mean_net_usd"]
        plateau = c[fam]["plateau"]
        dd_ok = (oos["max_dd_pct_of_slice"] or 0) <= 50.0
        for label, ok in (("positive OOS net after fees", positive), ("beats the random control", beats_control),
                          ("plateau, not a spike", plateau), ("drawdown tolerable on the slice", dd_ok)):
            if not ok:
                why.append(label)
        verdicts[coin] = {"family": fam, "variant": c[fam]["chosen"], "oos_net_usd": oos["net_usd"],
                          "control_mean_net_usd": ctrl["mean_net_usd"], "adopt": not why, "fails": why}
    report["verdicts"] = verdicts
    report["adopt"] = any(v["adopt"] for v in verdicts.values())
    report["seconds"] = round(time.time() - t0, 1)

    out_path = os.path.join(a.out_dir, "report.json")
    with open(out_path, "w") as fh:
        json.dump(report, fh, indent=1, default=str)

    summary_path = os.path.join(a.out_dir, "summary.csv")
    fields = ["coin", "family", "variant", "scope", "net_usd", "per_day_pct_of_slice", "win_rate", "trades_per_day",
              "fee_share_of_gross", "max_dd_pct_of_slice", "worst_day_pct_of_slice", "liquidated"]
    with open(summary_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for coin, c in report["coins"].items():
            for fam in ("range_fade_15m", "mean_revert_15m", "range_fade_5m"):
                if fam not in c:
                    continue
                for scope in ("in_sample", "out_of_sample"):
                    s = c[fam][scope]
                    w.writerow({"coin": coin, "family": fam, "variant": c[fam]["chosen"], "scope": scope,
                               **{k: s.get(k) for k in fields[4:]}})

    print(json.dumps({"verdicts": verdicts, "adopt": report["adopt"]}, indent=1, default=str))
    print(f"\nreport: {out_path}\nsummary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
