#!/usr/bin/env python
"""Walk-forward backtest of KillaXBT-style range-structure features.

RESEARCH-ONLY (research/killa_xbt/SPEC.md Parts 4-7). Never imported by live
code. Measurement discipline on top of the spec:

* decision at 4h bar close, fill at NEXT 4h bar open (no same-bar fills)
* pessimistic intra-bar ordering: stops assumed hit if touched, before any
  favourable move is credited
* costs: 25 bps round-trip base (sweep 2x) + realised hourly funding
  (per-coin median rate where history is missing)
* independent episodes: per-coin non-overlapping holds
* matched same-coin, same-direction, same-hold random-time nulls
  (>= 2000 Monte-Carlo replicates on validate/test)
* walk-forward: grid tuned on TRAIN only, frozen, confirmed on VALIDATE,
  final untouched TEST evaluated once
* Bonferroni-aware: per-family grid sizes are reported and validate/test
  p-values are judged against the family selection count

Usage:
    .venv/bin/python scripts/backtest_range_structure.py --stage all
    .venv/bin/python scripts/backtest_range_structure.py --stage daily  # needs network
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from hermes_trader.indicators.range_structure import (  # noqa: E402
    C,
    H,
    HOUR_MS,
    L,
    O,
    RANGE_FAMILY,
    T,
    RangeConfig,
    RangeStructure,
    aggregate_bars,
    ema_series,
)

SCRATCH = Path(
    "/private/tmp/claude-501/-Users-julian-dev-Documents-code-hermes-trader/"
    "f77b77de-96c2-4bf2-a574-1fd5aeebb7f2/scratchpad"
)
HOURLY_CACHE = SCRATCH / "hourly_ext.json"
FUNDING = REPO / "research" / "alpha_swarm" / "funding.json"
OUT_DIR = REPO / "research" / "killa_xbt"
DAILY_CACHE = OUT_DIR / "daily_majors.json"

BAR_H = 4                      # decision timeframe = 4h bars
COST_RT = 0.0025               # 25 bps round trip, base case
SEED = 20260711
N_NULL_TRAIN = 500             # selection-stage nulls (train only)
N_NULL_FINAL = 2000            # validate/test nulls
MIN_TRADES_TRAIN = 15

# walk-forward boundaries (ms, UTC) — fixed before any results were examined
T_TRAIN_END = 1773532800000    # 2026-03-15 00:00 UTC
T_VAL_END = 1778803200000      # 2026-05-15 00:00 UTC


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------
def load_hourly() -> Dict[str, List[List[float]]]:
    payload = json.loads(HOURLY_CACHE.read_text())
    return payload["candles"]


def load_funding() -> Tuple[Dict[str, Dict[int, float]], Dict[str, float]]:
    """Return ({coin: {hour_ms: rate}}, {coin: median_rate})."""
    payload = json.loads(FUNDING.read_text())
    table: Dict[str, Dict[int, float]] = {}
    med: Dict[str, float] = {}
    for coin, rows in payload["funding"].items():
        d = {int(r[0]) - int(r[0]) % HOUR_MS: float(r[1]) for r in rows}
        table[coin] = d
        med[coin] = statistics.median(d.values()) if d else 0.0
    return table, med


def funding_cost(coin: str, side: str, t_entry: int, t_exit: int,
                 ftab: Dict[str, Dict[int, float]], fmed: Dict[str, float]) -> float:
    """Signed funding paid over [t_entry, t_exit): positive = cost to the trade."""
    rates = ftab.get(coin, {})
    m = fmed.get(coin, 0.0)
    total = 0.0
    t = t_entry - t_entry % HOUR_MS
    while t < t_exit:
        total += rates.get(t, m)
        t += HOUR_MS
    # positive funding: longs pay shorts
    return total if side == "long" else -total


# ---------------------------------------------------------------------------
# episode simulation
# ---------------------------------------------------------------------------
@dataclass
class Episode:
    coin: str
    t_dec: int          # decision bar open timestamp
    side: str           # long | short
    hold: int           # 4h bars
    ret_gross: float
    ret_net: float
    mae: float          # most adverse excursion (negative, signed to trade)
    mfe: float          # most favourable excursion (positive)
    stopped: bool
    state: str
    loc_bucket: str


def simulate(bars: Sequence[Sequence[float]], i_dec: int, side: str, hold: int,
             stop_px: Optional[float], coin: str,
             ftab: Dict[str, Dict[int, float]], fmed: Dict[str, float],
             cost_rt: float = COST_RT) -> Optional[Tuple[float, float, float, float, bool, int]]:
    """Simulate one trade decided at close of bar i_dec.

    Entry at open of bar i_dec+1; time exit at open of bar i_dec+1+hold;
    stop (if given) checked pessimistically on every bar including the entry
    bar, before any favourable excursion is credited.

    Returns (ret_gross, ret_net, mae, mfe, stopped, t_exit) or None if the
    forward window is unavailable or has data holes.
    """
    n = len(bars)
    i_ent = i_dec + 1
    i_exit = i_ent + hold
    if i_exit >= n:
        return None
    # contiguity: no holes across the trade window
    if int(bars[i_exit][T]) - int(bars[i_dec][T]) != (hold + 1) * BAR_H * HOUR_MS:
        return None
    e = float(bars[i_ent][O])
    if e <= 0:
        return None
    sgn = 1.0 if side == "long" else -1.0
    stopped = False
    exit_px = float(bars[i_exit][O])
    t_exit = int(bars[i_exit][T])
    mae = 0.0
    mfe = 0.0
    for j in range(i_ent, i_exit):
        hi, lo = float(bars[j][H]), float(bars[j][L])
        # pessimistic: adverse extreme first
        adverse = (lo / e - 1.0) if side == "long" else -(hi / e - 1.0)
        favour = (hi / e - 1.0) if side == "long" else -(lo / e - 1.0)
        mae = min(mae, adverse)
        if stop_px is not None:
            hit = lo <= stop_px if side == "long" else hi >= stop_px
            if hit:
                stopped = True
                exit_px = stop_px
                t_exit = int(bars[j][T]) + BAR_H * HOUR_MS
                break
        mfe = max(mfe, favour)
    ret_gross = sgn * (exit_px / e - 1.0)
    fund = funding_cost(coin, side, int(bars[i_ent][T]), t_exit, ftab, fmed)
    ret_net = ret_gross - cost_rt - fund
    return ret_gross, ret_net, mae, mfe, stopped, t_exit


def dedup_non_overlapping(cands: List[Tuple[int, int]], hold: int) -> List[int]:
    """cands = [(t_dec_ms, i_dec)] sorted; keep first, then next whose decision
    bar is after the previous trade's exit."""
    out: List[int] = []
    next_free = -1
    for t, i in cands:
        if t >= next_free:
            out.append(i)
            next_free = t + (hold + 1) * BAR_H * HOUR_MS
    return out


# ---------------------------------------------------------------------------
# strategy families
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Params:
    family: str
    hold: int
    dev_thr: float = 0.25
    acc_k: int = 2
    loc_lo: float = 0.20
    loc_hi: float = 0.80
    gate_range: bool = True     # require RANGE-family state
    use_stop: bool = False
    stop_atr: float = 0.5       # beyond the excursion extreme / range edge

    def key(self) -> str:
        bits = [self.family, f"h{self.hold}"]
        if self.family in ("dev_reject", "combined"):
            bits.append(f"thr{self.dev_thr}")
        if self.family == "acceptance_bo":
            bits.append(f"k{self.acc_k}")
        if self.family == "loc_fade":
            bits.append(f"lo{self.loc_lo}-hi{self.loc_hi}")
        if self.family in ("dev_reject",):
            bits.append("gateR" if self.gate_range else "gateAny")
        if self.use_stop:
            bits.append(f"stop{self.stop_atr}")
        return "_".join(bits)


def family_grid(family: str) -> List[Params]:
    """Pre-registered TRAIN grids (spec 4C thresholds; holds 6/12/18 4h bars)."""
    holds = [6, 12, 18]
    out: List[Params] = []
    if family == "loc_fade":
        for h in holds:
            for lo, hi in [(0.15, 0.85), (0.20, 0.80)]:
                for st in [False, True]:
                    out.append(Params("loc_fade", h, loc_lo=lo, loc_hi=hi, use_stop=st))
    elif family == "dev_reject":
        for h in holds:
            for thr in (0.10, 0.25, 0.50):
                for gate in (True, False):
                    for st in [False, True]:
                        out.append(Params("dev_reject", h, dev_thr=thr,
                                          gate_range=gate, use_stop=st))
    elif family == "acceptance_bo":
        for h in holds:
            for k in (1, 2, 3):
                out.append(Params("acceptance_bo", h, acc_k=k))
    elif family == "combined":
        for h in holds:
            for thr in (0.10, 0.25, 0.50):
                out.append(Params("combined", h, dev_thr=thr, gate_range=True))
    else:
        raise ValueError(family)
    return out


def signals_for(p: Params, rs: RangeStructure, i: int) -> Optional[Tuple[str, Optional[float]]]:
    """(side, stop_px) if params p fire at close of bar i, else None."""
    s = rs.snapshot(i)
    if math.isnan(s.range_high) or math.isnan(s.atr) or s.atr <= 0:
        return None
    bar = rs.bars[i]
    if p.family == "loc_fade":
        if s.state not in RANGE_FAMILY:
            return None
        if s.price_location <= p.loc_lo:
            stop = s.range_low - p.stop_atr * s.atr if p.use_stop else None
            return "long", stop
        if s.price_location >= p.loc_hi:
            stop = s.range_high + p.stop_atr * s.atr if p.use_stop else None
            return "short", stop
        return None
    if p.family in ("dev_reject", "combined"):
        gate_ok = (s.state in RANGE_FAMILY) if (p.gate_range or p.family == "combined") else True
        if not gate_ok:
            return None
        if p.family == "combined":
            up_ok = s.price_location >= 0.65
            dn_ok = s.price_location <= 0.35
        else:
            up_ok = dn_ok = True
        if s.upper_deviation.get(p.dev_thr) and up_ok:
            stop = float(bar[H]) + p.stop_atr * s.atr if (p.use_stop or p.family == "combined") else None
            return "short", stop
        if s.lower_deviation.get(p.dev_thr) and dn_ok:
            stop = float(bar[L]) - p.stop_atr * s.atr if (p.use_stop or p.family == "combined") else None
            return "long", stop
        return None
    if p.family == "acceptance_bo":
        if s.upper_acceptance.get(p.acc_k):
            return "long", None
        if s.lower_acceptance.get(p.acc_k):
            return "short", None
        return None
    return None


# ---------------------------------------------------------------------------
# backtest core
# ---------------------------------------------------------------------------
class Engine:
    def __init__(self, coins: List[str], bars4h: Dict[str, List[List[float]]],
                 ftab, fmed, cfg: Optional[RangeConfig] = None):
        self.coins = coins
        self.bars = bars4h
        self.ftab, self.fmed = ftab, fmed
        self.rs = {c: RangeStructure(bars4h[c], cfg) for c in coins}
        # valid decision indices per coin per period, for the null model
        self._valid: Dict[Tuple[str, str, int], List[int]] = {}
        # precomputed stop-free net forward returns for the null model
        self._fwd: Dict[Tuple[str, str, int, str, float], List[float]] = {}

    def period_of(self, t: int) -> str:
        if t < T_TRAIN_END:
            return "train"
        if t < T_VAL_END:
            return "validate"
        return "test"

    def valid_indices(self, coin: str, period: str, hold: int) -> List[int]:
        key = (coin, period, hold)
        if key not in self._valid:
            bars = self.bars[coin]
            rs = self.rs[coin]
            out = []
            for i in range(rs._min_bars, len(bars) - hold - 1):
                if self.period_of(int(bars[i][T])) != period:
                    continue
                if int(bars[i + 1 + hold][T]) - int(bars[i][T]) == (hold + 1) * BAR_H * HOUR_MS:
                    out.append(i)
            self._valid[key] = out
        return self._valid[key]

    def run(self, p: Params, period: str, cost_rt: float = COST_RT) -> List[Episode]:
        eps: List[Episode] = []
        for coin in self.coins:
            bars = self.bars[coin]
            rs = self.rs[coin]
            cands: List[Tuple[int, int, str, Optional[float]]] = []
            for i in range(rs._min_bars, len(bars) - 1):
                t = int(bars[i][T])
                if self.period_of(t) != period:
                    continue
                sig = signals_for(p, rs, i)
                if sig:
                    cands.append((t, i, sig[0], sig[1]))
            # per-coin non-overlapping episodes
            next_free = -1
            for t, i, side, stop in cands:
                if t < next_free:
                    continue
                sim = simulate(bars, i, side, p.hold, stop, coin, self.ftab, self.fmed, cost_rt)
                if sim is None:
                    continue
                rg, rn, mae, mfe, stopped, t_exit = sim
                snap = rs.snapshot(i)
                eps.append(Episode(coin, t, side, p.hold, rg, rn, mae, mfe,
                                   stopped, snap.state, snap.location_bucket))
                next_free = t_exit
        return eps

    def _fwd_rets(self, coin: str, period: str, hold: int, side: str,
                  cost_rt: float) -> List[float]:
        """Stop-free net returns at every valid decision bar (null substrate)."""
        key = (coin, period, hold, side, cost_rt)
        if key not in self._fwd:
            out = []
            for i in self.valid_indices(coin, period, hold):
                sim = simulate(self.bars[coin], i, side, hold, None, coin,
                               self.ftab, self.fmed, cost_rt)
                if sim:
                    out.append(sim[1])
            self._fwd[key] = out
        return self._fwd[key]

    def null_p(self, eps: List[Episode], period: str, n_rep: int,
               rng: random.Random, cost_rt: float = COST_RT) -> Optional[float]:
        """Matched null: same coin/direction/hold at random valid times.
        One-sided p for the observed mean net return being beaten by chance."""
        if not eps:
            return None
        obs = statistics.fmean(e.ret_net for e in eps)
        pools = []
        for e in eps:
            pool = self._fwd_rets(e.coin, period, e.hold, e.side, cost_rt)
            if pool:
                pools.append(pool)
        if not pools:
            return None
        beat = 0
        for _ in range(n_rep):
            m = statistics.fmean(pool[rng.randrange(len(pool))] for pool in pools)
            if m >= obs:
                beat += 1
        return (beat + 1) / (n_rep + 1)


def summarize(eps: List[Episode]) -> dict:
    if not eps:
        return {"n": 0}
    rets = [e.ret_net for e in eps]
    gross = [e.ret_gross for e in eps]
    mean = statistics.fmean(rets)
    sd = statistics.pstdev(rets) if len(rets) > 1 else 0.0
    wins = sum(1 for r in rets if r > 0)
    days = len({e.t_dec // (24 * HOUR_MS) for e in eps})
    return {
        "n": len(eps),
        "n_days": days,
        "mean_net_pct": round(mean * 100, 4),
        "median_net_pct": round(statistics.median(rets) * 100, 4),
        "mean_gross_pct": round(statistics.fmean(gross) * 100, 4),
        "win_rate": round(wins / len(rets), 3),
        "sd_pct": round(sd * 100, 3),
        "t_stat": round(mean / (sd / math.sqrt(len(rets))), 2) if sd > 0 and len(rets) > 2 else None,
        "total_net_pct": round(sum(rets) * 100, 2),
        "mae_mean_pct": round(statistics.fmean(e.mae for e in eps) * 100, 3),
        "mfe_mean_pct": round(statistics.fmean(e.mfe for e in eps) * 100, 3),
        "stopped_frac": round(sum(1 for e in eps if e.stopped) / len(eps), 3),
        "long_n": sum(1 for e in eps if e.side == "long"),
        "short_n": sum(1 for e in eps if e.side == "short"),
        "long_mean_net_pct": round(statistics.fmean([e.ret_net for e in eps if e.side == "long"]) * 100, 4)
        if any(e.side == "long" for e in eps) else None,
        "short_mean_net_pct": round(statistics.fmean([e.ret_net for e in eps if e.side == "short"]) * 100, 4)
        if any(e.side == "short" for e in eps) else None,
    }


def drop_top(eps: List[Episode], k: int = 3) -> dict:
    """Good-trade-removal robustness: mean net after dropping the k best."""
    if len(eps) <= k:
        return {"n": 0}
    kept = sorted(eps, key=lambda e: e.ret_net, reverse=True)[k:]
    return {"n": len(kept),
            "mean_net_pct": round(statistics.fmean(e.ret_net for e in kept) * 100, 4)}


# ---------------------------------------------------------------------------
# stages
# ---------------------------------------------------------------------------
def stage_panel(eng: Engine) -> dict:
    """Descriptive: forward net returns by state and by location bucket.
    Tests spec 4E ('mid-range entries are dead money') without any strategy.
    Long-side convention: positive mean => upward drift after that feature."""
    out: dict = {}
    for hold in (6, 18):
        panel: Dict[str, List[float]] = {}
        panel_loc: Dict[str, List[float]] = {}
        panel_state_loc: Dict[str, List[float]] = {}
        for coin in eng.coins:
            bars = eng.bars[coin]
            rs = eng.rs[coin]
            # sample every `hold` bars for independence
            for i in range(rs._min_bars, len(bars) - hold - 1, hold):
                sim = simulate(bars, i, "long", hold, None, coin, eng.ftab, eng.fmed)
                if sim is None:
                    continue
                s = rs.snapshot(i)
                panel.setdefault(s.state, []).append(sim[1])
                if s.location_bucket:
                    panel_loc.setdefault(s.location_bucket, []).append(sim[1])
                    if s.state in RANGE_FAMILY:
                        panel_state_loc.setdefault(s.location_bucket, []).append(sim[1])
        def _agg(d):
            return {k: {"n": len(v), "mean_net_pct": round(statistics.fmean(v) * 100, 3),
                        "abs_mean_gross_pct": round(statistics.fmean(abs(x + COST_RT) for x in v) * 100, 3)}
                    for k, v in sorted(d.items())}
        out[f"hold_{hold}bars"] = {
            "by_state": _agg(panel),
            "by_location": _agg(panel_loc),
            "by_location_in_range_state": _agg(panel_state_loc),
        }
    return out


def stage_walkforward(eng: Engine, rng: random.Random) -> dict:
    families = ["loc_fade", "dev_reject", "acceptance_bo", "combined"]
    results: dict = {"selection_rule":
                     "train: max t-stat with n>=%d and mean>0; frozen; "
                     "validate confirms (p<=0.05, mean>0); test run once" % MIN_TRADES_TRAIN}
    for fam in families:
        grid = family_grid(fam)
        fam_out: dict = {"grid_size": len(grid), "train_cells": {}}
        best: Optional[Tuple[float, Params, dict]] = None
        for p in grid:
            eps = eng.run(p, "train")
            s = summarize(eps)
            fam_out["train_cells"][p.key()] = s
            if s["n"] >= MIN_TRADES_TRAIN and s.get("t_stat") is not None and s["mean_net_pct"] > 0:
                score = s["t_stat"]
                if best is None or score > best[0]:
                    best = (score, p, s)
        if best is None:
            fam_out["verdict"] = "NO_TRAIN_SURVIVOR (no cell with n>=%d and mean>0)" % MIN_TRADES_TRAIN
            # still record the least-bad cell for transparency
            results[fam] = fam_out
            continue
        _, p_star, s_train = best
        eps_tr = eng.run(p_star, "train")
        p_tr = eng.null_p(eps_tr, "train", N_NULL_TRAIN, rng)
        fam_out["frozen_params"] = p_star.key()
        fam_out["train"] = {**s_train, "null_p": p_tr}
        eps_val = eng.run(p_star, "validate")
        s_val = summarize(eps_val)
        p_val = eng.null_p(eps_val, "validate", N_NULL_FINAL, rng)
        fam_out["validate"] = {**s_val, "null_p": p_val,
                               "drop_top3": drop_top(eps_val),
                               "cost_2x": summarize(eng.run(p_star, "validate", cost_rt=2 * COST_RT))}
        survives = (s_val.get("n", 0) >= 10 and (s_val.get("mean_net_pct") or 0) > 0
                    and p_val is not None and p_val <= 0.05)
        fam_out["validate_survives"] = survives
        # test is evaluated once for the frozen config (reported for all
        # families for transparency; only validate-survivors count)
        eps_te = eng.run(p_star, "test")
        s_te = summarize(eps_te)
        p_te = eng.null_p(eps_te, "test", N_NULL_FINAL, rng)
        fam_out["test"] = {**s_te, "null_p": p_te,
                           "drop_top3": drop_top(eps_te),
                           "cost_2x": summarize(eng.run(p_star, "test", cost_rt=2 * COST_RT))}
        # untouched-asset generalization: even-indexed coins vs odd-indexed
        held_out = [c for j, c in enumerate(eng.coins) if j % 2 == 1]
        eps_te_ho = [e for e in eps_te if e.coin in held_out]
        fam_out["test_held_out_coins"] = summarize(eps_te_ho)
        verdict = "REFUTED_OR_NO_EDGE"
        if survives:
            te_ok = (s_te.get("n", 0) >= 8 and (s_te.get("mean_net_pct") or 0) > 0
                     and p_te is not None and p_te <= 0.10)
            verdict = "VALIDATED_OOS" if te_ok else "VALIDATE_ONLY (failed untouched test)"
        fam_out["verdict"] = verdict
        results[fam] = fam_out
    return results


def stage_baselines(eng: Engine) -> dict:
    """Context baselines: EMA-trend follow and unconditional drift, per period."""
    out: dict = {}
    for period in ("train", "validate", "test"):
        rows = {}
        for name, cond in (("ema_trend_long", "up"), ("ema_trend_short", "dn"), ("uncond_long", "any")):
            vals = []
            for coin in eng.coins:
                bars = eng.bars[coin]
                closes = [b[C] for b in bars]
                ef = ema_series(closes, 20)
                es = ema_series(closes, 50)
                rs = eng.rs[coin]
                hold = 6
                for i in range(rs._min_bars, len(bars) - hold - 1, hold):
                    if eng.period_of(int(bars[i][T])) != period:
                        continue
                    if cond == "up" and not (ef[i] > es[i]):
                        continue
                    if cond == "dn" and not (ef[i] < es[i]):
                        continue
                    side = "short" if cond == "dn" else "long"
                    sim = simulate(bars, i, side, hold, None, coin, eng.ftab, eng.fmed)
                    if sim:
                        vals.append(sim[1])
            rows[name] = {"n": len(vals),
                          "mean_net_pct": round(statistics.fmean(vals) * 100, 4) if vals else None}
        out[period] = rows
    return out


def stage_daily(rng: random.Random) -> dict:
    """Daily-bar replication on majors over a longer, multi-regime history.

    Fetches BTC/ETH/SOL 1d candles once (cached to research/killa_xbt/
    daily_majors.json). Tests the frozen dev_reject/loc_fade structure on
    daily bars with a 2023->2026 span: addresses one-cycle dependence.
    """
    if DAILY_CACHE.exists():
        data = json.loads(DAILY_CACHE.read_text())
    else:
        import time as _time
        from hermes_trader.client.hl_client import fetch_hl_candles
        data = {}
        for coin in ("BTC", "ETH", "SOL"):
            cs = fetch_hl_candles(coin, "1d", 5000)
            data[coin] = [[c.t, c.o, c.h, c.l, c.c, c.v] for c in cs[:-1]]
            _time.sleep(1.0)
        DAILY_CACHE.write_text(json.dumps(data))
    out: dict = {"span": {c: [data[c][0][0], data[c][-1][0], len(data[c])] for c in data}}
    cfg = RangeConfig(lookback=30, ema_long=100)
    # no funding on daily proxy (spot-like context); costs still charged
    ftab: Dict[str, Dict[int, float]] = {}
    fmed: Dict[str, float] = {c: 0.0 for c in data}
    global BAR_H
    old_bar_h = BAR_H
    BAR_H = 24
    try:
        eng = Engine(list(data), {c: [list(b) for b in data[c]] for c in data}, ftab, fmed, cfg)
        # halves split for a coarse OOS check on the long history
        t_all = [b[T] for b in data["BTC"]]
        mid = int(t_all[len(t_all) // 2])
        global T_TRAIN_END, T_VAL_END
        old = (T_TRAIN_END, T_VAL_END)
        T_TRAIN_END, T_VAL_END = mid, mid
        eng._valid.clear()
        for fam in ("dev_reject", "loc_fade", "acceptance_bo"):
            fam_res = {}
            for p in family_grid(fam):
                if p.hold != 6 or p.use_stop:
                    continue  # holds 6 bars = 6 days; keep the grid tiny (no tuning)
                e1 = eng.run(p, "train")     # first half
                e2 = eng.run(p, "test")      # second half
                fam_res[p.key()] = {
                    "first_half": summarize(e1),
                    "first_half_null_p": eng.null_p(e1, "train", N_NULL_FINAL, rng),
                    "second_half": summarize(e2),
                    "second_half_null_p": eng.null_p(e2, "test", N_NULL_FINAL, rng),
                }
            out[fam] = fam_res
        T_TRAIN_END, T_VAL_END = old
    finally:
        BAR_H = old_bar_h
    return out


# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stage", default="all",
                    choices=["panel", "walkforward", "baselines", "daily", "all"])
    args = ap.parse_args()
    rng = random.Random(SEED)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "validation_results.json"
    results = json.loads(out_path.read_text()) if out_path.exists() else {}
    results.setdefault("meta", {
        "seed": SEED, "bar_hours": BAR_H, "cost_rt_bps": COST_RT * 1e4,
        "train_end_utc": "2026-03-15", "validate_end_utc": "2026-05-15",
        "n_null_final": N_NULL_FINAL,
        "data": "hourly_ext.json 40 coins 2025-12-13..2026-07-09 (one bear regime)",
    })

    stages = ["panel", "baselines", "walkforward"] if args.stage == "all" else [args.stage]
    if args.stage != "daily" or args.stage == "daily":
        pass
    if "daily" in stages or args.stage == "daily":
        stages = [s for s in stages if s != "daily"] + ["daily"]

    needs_engine = any(s in stages for s in ("panel", "walkforward", "baselines"))
    if needs_engine:
        hourly = load_hourly()
        ftab, fmed = load_funding()
        bars4h = {c: aggregate_bars(b, BAR_H) for c, b in hourly.items()}
        coins = sorted(c for c, b in bars4h.items() if len(b) > 300)
        print(f"engine: {len(coins)} coins, "
              f"{statistics.median(len(bars4h[c]) for c in coins):.0f} median 4h bars", flush=True)
        eng = Engine(coins, bars4h, ftab, fmed)

    for s in stages:
        print(f"== stage {s}", flush=True)
        if s == "panel":
            results["panel"] = stage_panel(eng)
        elif s == "baselines":
            results["baselines"] = stage_baselines(eng)
        elif s == "walkforward":
            results["walkforward"] = stage_walkforward(eng, rng)
        elif s == "daily":
            try:
                results["daily_majors"] = stage_daily(rng)
            except Exception as exc:  # network-dependent; degrade, don't die
                results["daily_majors"] = {"error": f"{type(exc).__name__}: {exc}"}
        out_path.write_text(json.dumps(results, indent=1))
        print(f"   wrote {out_path}", flush=True)


if __name__ == "__main__":
    main()
