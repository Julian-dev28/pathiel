"""Funding-extreme reversion test on pathiel's own instruments, 2026-11-03
(docs/strategy/FUNDING_EXTREME_TEST_2026-11-03.md).

Old alpha-swarm research (git history commit
a7710a4532b8d6cc41ccaf2efd90344b60266db0): `research/alpha_swarm/findings/
D4_funding_extreme_reversion.md` found that, on 40 crypto majors, a coin's
own trailing-24h funding z-score vs its own trailing-30d distribution
hitting z >= 2.0 (a crowded long) is a SHORT signal -- 5-day hold, net
+4.6%/event at 25bps, 69% win, both OOS halves positive, null p=0.0006.
`D5_basis_premium_signal.md` found the same thing off the mark/oracle
premium (0.72-0.89 correlated with funding, not independent alpha). The
LONG side (fading a crowded short) was cleanly refuted in D4 (p~0.5-0.7),
and `C5_no_long_edge_in_xyz.md` separately found xyz tokenized-equity
funding structurally charges longs, so there is no "fade the crowded
short" edge expected here either. `research/EVIDENCE_DOCTRINE.md` is the
bar used below: net of a real 25bps round trip, both OOS halves positive,
beats a matched null, Bonferroni-corrected over every cell examined.

This file asks the same question on pathiel's two live HIP-3 instruments,
xyz:BRENTOIL and xyz:SILVER, four ways:

  1. Funding facts: the full public fundingHistory (services.strategies.
     backtest.data.fetch_funding_cached, paged), the funding/premium
     distribution, the average cost a long actually paid per day, and how
     often the z-score crosses +-1.5/2.0/2.5.
  2. Event study: forward 1d/3d/5d returns after a crossing, gross and
     net of a flat 25bps round trip, against a matched null of random
     entry times (2000 draws), split into chronological halves,
     Bonferroni over every (coin, side, threshold, hold) cell examined.
  3. As a FILTER on the live desk rule (services.strategies.evals.
     live_params.live_brent_params / live_silver_params,
     desk_tick_sim.Params.funding_z_filter): "skip a new long while z is
     at or above X" / "skip a new short while z is at or below -X",
     against the live journal, the two oil replay directories, and the
     always-long/always-short/daily-sign controls.
  4. Standalone: short-the-crowded-long at a small reference slice size,
     through the live desk's own stop/target mechanics and fees (not the
     fixed-hold event study above -- a deliberately different mechanic,
     see the module's "standalone" section).

    python -m services.strategies.evals.funding_extreme_test \\
        [--oil-theses-dir <pathia>/.state/strategies/oil/theses]
        [--metals-theses-dir <pathia>/.state/strategies/metals/theses]

Writes /tmp/funding-extreme/report.json, summary.csv, progress.log. Reads
the public, read-only fundingHistory / candleSnapshot / metaAndAssetCtxs
endpoints (cached under --cache-dir) and the journaled theses (READ ONLY,
never written here). Never touches the account, the live config, or the
journal. No LLM calls.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import dataclasses
import json
import os
import random
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from pathiel.models.types import Candle
from services.strategies.backtest import data as D
from services.strategies.backtest.desk_tick_sim import (
    DAY_MS, FUNDING_FILTER_NAME, HOUR_MS, Direction, Params, Run, simulate)
from services.strategies.backtest.funding_signal import funding_z_at, funding_zscore_series
from services.strategies.evals.cross_market_backtest import mean_atr
from services.strategies.evals.desk_tick_backtest import always_direction, build_daily_sign_direction
from services.strategies.evals.desk_tick_backtest import journaled_direction as _journaled_direction
from services.strategies.evals.live_params import live_brent_params, live_silver_params
from services.strategies.evals.market_scout import fetch_market_meta
from services.strategies.evals.oil_regime_test import paged_funding

COINS: Dict[str, str] = {"brent": "xyz:BRENTOIL", "silver": "xyz:SILVER"}
THRESHOLDS: Tuple[float, ...] = (1.5, 2.0, 2.5)
HOLD_DAYS: Tuple[int, ...] = (1, 3, 5)
FEE_FRAC = 0.0025                 # 25bps round trip (EVIDENCE_DOCTRINE.md, matches D4/D5's own "@25bps")
NULL_DRAWS = 2000                 # EVIDENCE_DOCTRINE.md: ">=2000 draws"
NULL_SEED = 42
MIN_EVENTS = 10                   # below this, "insufficient sample" overrides whatever the p-value says
ALPHA = 0.05
# Every cell item 4 of the doctrine has to correct for: 2 coins x 2 sides
# (pos/neg crossing) x 3 thresholds x 3 holds.
BONFERRONI_CELLS = len(COINS) * 2 * len(THRESHOLDS) * len(HOLD_DAYS)
SLICE_EQUITY = 100.0              # the standalone test's reference size -- NOT the live desk's real allocation
FUNDING_LOOKBACK_DAYS = 400        # how far back fundingHistory is asked for (it has less; see data.fetch_funding_cached)


def _iso(ms_: Optional[int], fmt: str = "%Y-%m-%d %H:%MZ") -> Optional[str]:
    if ms_ is None:
        return None
    return datetime.fromtimestamp(ms_ / 1000, tz=timezone.utc).strftime(fmt)


# ── funding facts (pure once the rows are in hand) ──────────────────────────

def funding_facts(funding_sorted: Sequence[Tuple[int, float]], premiums: Sequence[float]) -> Dict[str, Any]:
    """Mean/std of the hourly rate, the implied long cost per day (mean
    hourly rate x 24, as a percent -- negative means longs were PAID, not
    charged), the share of hours a long pays at all, and the premium's own
    mean/std. No z-score here, just the raw window. Pure."""
    rates = [r for _, r in funding_sorted]
    n = len(rates)
    if n == 0:
        return {"n": 0}
    mean_r = sum(rates) / n
    var_r = sum((r - mean_r) ** 2 for r in rates) / n
    pos = sum(1 for r in rates if r > 0)
    out: Dict[str, Any] = {
        "n_hours": n, "from": _iso(funding_sorted[0][0]), "to": _iso(funding_sorted[-1][0]),
        "span_days": round((funding_sorted[-1][0] - funding_sorted[0][0]) / DAY_MS, 1),
        "mean_hourly_rate": mean_r, "std_hourly_rate": var_r ** 0.5,
        "long_cost_pct_per_day": mean_r * 24 * 100, "frac_hours_long_pays": pos / n,
    }
    if premiums:
        mp = sum(premiums) / len(premiums)
        vp = sum((p - mp) ** 2 for p in premiums) / len(premiums)
        out["mean_premium"], out["std_premium"] = mp, vp ** 0.5
    return out


def threshold_hours(z_map: Dict[int, float], thresholds: Sequence[float] = THRESHOLDS) -> Dict[str, Dict[str, Any]]:
    """Per threshold: the share of z-scored hours at/above +T and at/below
    -T (every hour an extreme funding spike lasts), next to the
    independent EVENT count (collapse_events -- a 10-hour spike is one
    event). Pure."""
    n = len(z_map)
    out: Dict[str, Dict[str, Any]] = {}
    for t in thresholds:
        pos_hours = sum(1 for z in z_map.values() if z >= t)
        neg_hours = sum(1 for z in z_map.values() if z <= -t)
        out[f"{t:g}"] = {
            "hours_total": n,
            "pos_hours": pos_hours, "pos_hours_pct": round(100 * pos_hours / n, 2) if n else None,
            "neg_hours": neg_hours, "neg_hours_pct": round(100 * neg_hours / n, 2) if n else None,
            "pos_events": len(collapse_events(z_map, t, "pos")),
            "neg_events": len(collapse_events(z_map, t, "neg")),
        }
    return out


# ── events and forward returns (pure) ───────────────────────────────────────

def collapse_events(z_map: Dict[int, float], threshold: float, side: str) -> List[int]:
    """Chronological list of the FIRST hour of every run where z crosses
    the threshold ("pos": z >= threshold, "neg": z <= -threshold) -- one
    event per spike, not one per hour it lasts. Pure."""
    times = sorted(z_map)
    out: List[int] = []
    armed = False
    for t in times:
        z = z_map[t]
        hit = (z >= threshold) if side == "pos" else (z <= -threshold)
        if hit and not armed:
            out.append(t)
        armed = hit
    return out


def close_series(c1h: Sequence[Candle]) -> Tuple[List[int], List[float]]:
    """(close times, closes) of `c1h`, by the bar's CLOSE time (t + 1h) --
    the only time a close is actually known. Sorted ascending. Pure."""
    rows = sorted(((c.t + HOUR_MS, c.c) for c in c1h), key=lambda r: r[0])
    return [t for t, _ in rows], [c for _, c in rows]


def price_at_or_after(times: Sequence[int], closes: Sequence[float], t: int) -> Optional[float]:
    """The first known close at or after `t`, else None (past the data's
    end, or before its start if `t` is negative-offset). Pure."""
    lo = bisect.bisect_left(times, t)
    return closes[lo] if lo < len(times) else None


def forward_return(times: Sequence[int], closes: Sequence[float], t0: int, hold_days: float) -> Optional[float]:
    """Close-to-close return from the first known close at/after `t0` to
    the first known close at/after `t0 + hold_days` days. None if either
    end is outside the data. Pure."""
    entry = price_at_or_after(times, closes, t0)
    exitp = price_at_or_after(times, closes, t0 + int(hold_days * DAY_MS))
    if entry is None or exitp is None or entry <= 0:
        return None
    return exitp / entry - 1.0


def side_pnl(forward_ret: float, event_side: str) -> float:
    """The position P&L fraction an `event_side` ("short"|"long") trade
    earns from a raw price return. Pure."""
    return -forward_ret if event_side == "short" else forward_ret


def usable_events(events: Sequence[int], candle_start: Optional[int], candle_end: Optional[int],
                   max_hold_days: float) -> List[int]:
    """`events` whose forward return is computable at the LONGEST hold
    under test, so the same event set is used across every hold (1d/3d/5d
    stay comparable). Pure."""
    if candle_start is None or candle_end is None:
        return []
    limit = candle_end - int(max_hold_days * DAY_MS)
    return [t for t in events if candle_start <= t <= limit]


def event_study(events: Sequence[int], times: Sequence[int], closes: Sequence[float], event_side: str,
                hold_days: float, fee_frac: float = FEE_FRAC) -> Dict[str, Any]:
    """Per-event gross/net P&L (side-adjusted, `fee_frac` a flat round-trip
    cost), the win rate, and a chronological first/second half split
    (EVIDENCE_DOCTRINE.md item 2). Pure."""
    rows = []
    for t in events:
        r = forward_return(times, closes, t, hold_days)
        if r is None:
            continue
        gross = side_pnl(r, event_side)
        rows.append({"t": t, "gross": round(gross, 5), "net": round(gross - fee_frac, 5)})
    n = len(rows)
    dropped = len(events) - n
    if n == 0:
        return {"n": 0, "dropped": dropped, "mean_gross": None, "mean_net": None, "win_rate": None,
                "half1_n": 0, "half1_mean_net": None, "half2_n": 0, "half2_mean_net": None,
                "both_halves_positive": False, "rows": rows}
    mid = (n + 1) // 2
    h1, h2 = rows[:mid], rows[mid:]
    h1_mean = sum(r["net"] for r in h1) / len(h1) if h1 else None
    h2_mean = sum(r["net"] for r in h2) / len(h2) if h2 else None
    return {
        "n": n, "dropped": dropped,
        "mean_gross": sum(r["gross"] for r in rows) / n, "mean_net": sum(r["net"] for r in rows) / n,
        "win_rate": sum(1 for r in rows if r["net"] > 0) / n,
        "half1_n": len(h1), "half1_mean_net": h1_mean, "half2_n": len(h2), "half2_mean_net": h2_mean,
        "both_halves_positive": bool(h1_mean is not None and h2_mean is not None and h1_mean > 0 and h2_mean > 0),
        "rows": rows,
    }


def matched_null(eligible: Sequence[int], times: Sequence[int], closes: Sequence[float], event_side: str,
                 hold_days: float, n: int, fee_frac: float = FEE_FRAC, draws: int = NULL_DRAWS,
                 seed: int = NULL_SEED) -> List[float]:
    """`draws` random samples of `n` timestamps from `eligible`, scored the
    SAME way (`event_side`, `hold_days`, `fee_frac`) as the real events --
    "same coin, same holding rule, random entry times" (EVIDENCE_DOCTRINE.
    md item 3). Returns every draw's own mean net P&L. `n<=0` or too small
    an `eligible` pool returns []. Deterministic for a given `seed`."""
    if n <= 0 or len(eligible) < n:
        return []
    rng = random.Random(seed)
    means: List[float] = []
    for _ in range(draws):
        sample = rng.sample(eligible, n)
        vals = []
        for t in sample:
            r = forward_return(times, closes, t, hold_days)
            if r is not None:
                vals.append(side_pnl(r, event_side) - fee_frac)
        if vals:
            means.append(sum(vals) / len(vals))
    return means


def null_p_value(observed_mean: float, null_means: Sequence[float]) -> Optional[float]:
    """One-sided: the share of null draws whose mean net P&L is AT LEAST
    the observed one. Laplace-corrected (+1/+1) so a clean sweep never
    reports p=0. None with no null draws. Pure."""
    if not null_means:
        return None
    beat = sum(1 for m in null_means if m >= observed_mean)
    return (beat + 1) / (len(null_means) + 1)


def bonferroni_alpha(alpha: float, n_cells: int) -> float:
    return alpha / max(1, n_cells)


def doctrine_verdict(n: int, mean_net: Optional[float], both_halves_positive: bool, p: Optional[float],
                     alpha_corrected: float, min_n: int = MIN_EVENTS) -> Dict[str, Any]:
    """The EVIDENCE_DOCTRINE.md gates, mechanically: positive net of fees,
    both halves positive, beats the matched null at the Bonferroni-
    corrected alpha -- plus a floor on `n`. D4's own panel pooled 40 coins
    for n=53 at z>=2.0; one instrument alone fires far fewer independent
    events (see the funding-facts section), and "beats a matched null"
    cannot mean anything at single-digit n whatever the p-value prints.
    Pure."""
    checks = {
        "enough_events": n >= min_n,
        "positive_net": bool(mean_net is not None and mean_net > 0),
        "both_halves_positive": bool(both_halves_positive),
        "beats_null": bool(p is not None and p < alpha_corrected),
    }
    return {**checks, "n": n, "mean_net": mean_net, "p": p, "alpha_corrected": round(alpha_corrected, 6),
            "pass": all(checks.values())}


# ── the filter on the live desk (Params.funding_z_filter) ──────────────────

def filter_overrides(thresholds: Sequence[float] = THRESHOLDS) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {"live": {}}
    for t in thresholds:
        out[f"funding_z_{t:g}"] = {"funding_z_filter": t}
    return out


def summarize_run(run: Run, start_equity: float) -> Dict[str, Any]:
    rs = [t.r_multiple for t in run.trades if t.r_multiple is not None]
    n = len(run.trades)
    return {
        "final_equity": round(run.final_equity, 2),
        "return_pct": round((run.final_equity / start_equity - 1.0) * 100, 2) if start_equity else None,
        "trades": n, "win_rate": round(sum(1 for t in run.trades if t.net_pnl_usd > 0) / n, 3) if n else None,
        "total_r": round(sum(rs), 2) if rs else 0.0,
        "max_dd_pct": round(run.max_drawdown_pct * 100, 2),
        "refused_funding_z": int(run.refused.get(FUNDING_FILTER_NAME, 0)),
        "liquidated": run.liquidated,
    }


def run_filter_variants(c15: Sequence[Candle], c1h: Sequence[Candle], direction_at: Callable[[int], Any],
                        base: Params, funding: Sequence[Dict[str, Any]], ticks: int,
                        thresholds: Sequence[float] = THRESHOLDS) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for name, ov in filter_overrides(thresholds).items():
        p = dataclasses.replace(base, tick_minutes=ticks, **ov)
        run = simulate([], c1h, direction_at, p, funding) if ticks >= 60 else simulate(c15, c1h, direction_at, p,
                                                                                        funding)
        out[name] = summarize_run(run, base.starting_equity)
    return out


# ── standalone short-the-crowded-long (a reference, not the event study) ───

def crowded_short_direction(z_times: Sequence[int], z_values: Sequence[float], threshold: float
                            ) -> Callable[[int], Optional[Direction]]:
    """SHORT whenever the z-score at (or last known before) `t` is at or
    above `threshold`, else flat (None) -- the standalone rule, with no
    thesis behind it. Its exit is the live desk's OWN stop/target/time
    rule (desk_tick_sim), not a fixed h-day hold -- deliberately a
    different mechanic from `event_study` above; see the module docstring.
    Pure factory."""
    def at(t: int) -> Optional[Direction]:
        z = funding_z_at(z_times, z_values, t)
        return Direction(side="short") if z is not None and z >= threshold else None
    return at


def standalone_short(c1h: Sequence[Candle], funding: Sequence[Dict[str, Any]], funding_sorted: Sequence[Tuple[int,
                     float]], base: Params, threshold: float, starting_equity: float = SLICE_EQUITY
                     ) -> Dict[str, Any]:
    z_map = funding_zscore_series(funding_sorted)
    z_times = sorted(z_map)
    z_values = [z_map[t] for t in z_times]
    d_at = crowded_short_direction(z_times, z_values, threshold)
    p = dataclasses.replace(base, starting_equity=starting_equity, tick_minutes=60)
    run = simulate([], c1h, d_at, p, funding)
    return {**summarize_run(run, starting_equity), "z_events_fired": len(collapse_events(z_map, threshold, "pos"))}


# ── data (the only parts that touch the network) ───────────────────────────

def load_coin(coin: str, cache_dir: str, now_ms: int) -> Dict[str, Any]:
    """Full public fundingHistory (paged) and 1h candles paged over the
    same requested window -- trimmed by the venue to whichever it serves
    less of. On this HIP-3 dex, as of 2026-11-03, candle retention is
    shorter than funding retention (see FUNDING_EXTREME_TEST_2026-11-03.md,
    Caveats): the event study below only uses the candle-covered window,
    the raw funding facts use the full funding window."""
    funding_start = now_ms - FUNDING_LOOKBACK_DAYS * DAY_MS
    funding = D.fetch_funding_cached(coin, funding_start, now_ms, cache_dir,
                                     fetch=lambda co, s, e: paged_funding(co, s, e))
    funding = sorted({int(r["time"]): r for r in funding}.values(), key=lambda r: int(r["time"]))
    funding_sorted = [(int(r["time"]), float(r["fundingRate"])) for r in funding if r.get("fundingRate") is not None]
    premiums = [float(r["premium"]) for r in funding if r.get("premium") is not None]
    c1h = sorted(D.fetch_candles_paged_cached(coin, "1h", funding_start, now_ms, cache_dir), key=lambda c: c.t)
    return {"coin": coin, "funding": funding, "funding_sorted": funding_sorted, "premiums": premiums, "c1h": c1h}


def control_direction(name: str, c1h: Sequence[Candle]) -> Callable[[int], Any]:
    if name == "always_long":
        return always_direction("long")
    if name == "always_short":
        return always_direction("short")
    if name == "daily_sign":
        direction_at, _, _ = build_daily_sign_direction(c1h)
        return direction_at
    raise ValueError(f"unknown control {name!r}")


def paged_15m(coin: str, start_ms: int, end_ms: int, cache_dir: str) -> List[Candle]:
    return sorted(D.fetch_candles_paged_cached(coin, "15m", start_ms, end_ms, cache_dir), key=lambda c: c.t)


def silver_base_params(silver_c1h: Sequence[Candle], cache_dir: str, starting_equity: float) -> Params:
    meta = fetch_market_meta(["xyz:SILVER"])
    venue_max_leverage = float((meta.get("xyz:SILVER") or {}).get("maxLeverage") or 20.0)
    atr_price_mean, _ = mean_atr(silver_c1h)
    atr_price_mean = atr_price_mean or 0.05
    return live_silver_params(venue_max_leverage, atr_price_mean, starting_equity=starting_equity)


# ── main ─────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--oil-theses-dir",
                    default=os.environ.get("PATHIEL_OIL_THESES_DIR",
                                           os.path.expanduser("~/Documents/code/pathia/.state/strategies/oil/theses")),
                    help="READ ONLY: the live oil journal's theses")
    ap.add_argument("--metals-theses-dir",
                    default=os.environ.get("PATHIEL_METALS_THESES_DIR",
                                           os.path.expanduser(
                                               "~/Documents/code/pathia/.state/strategies/metals/theses")),
                    help="READ ONLY: the live metals journal's theses")
    ap.add_argument("--oil-replay-30b-dir", default="/tmp/thesis-replay-30b/oil/theses")
    ap.add_argument("--oil-replay-sep-dir", default="/tmp/thesis-replay-sep/oil/theses")
    ap.add_argument("--cache-dir", default="/tmp/funding-extreme/cache")
    ap.add_argument("--out-dir", default="/tmp/funding-extreme")
    ap.add_argument("--standalone-threshold", type=float, default=2.0)
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    os.makedirs(a.cache_dir, exist_ok=True)
    t0 = time.time()
    log_path = os.path.join(a.out_dir, "progress.log")

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} funding-extreme: {msg}"
        print(line, flush=True)
        with open(log_path, "a") as fh:
            fh.write(line + "\n")

    now_ms = int(time.time() * 1000)
    coins = {k: load_coin(c, a.cache_dir, now_ms) for k, c in COINS.items()}
    for k, cd in coins.items():
        log(f"{k}: {len(cd['funding_sorted'])} funding rows, {len(cd['c1h'])} 1h candles")

    # ── 1. funding facts
    facts: Dict[str, Any] = {}
    z_maps: Dict[str, Dict[int, float]] = {}
    for k, cd in coins.items():
        z_maps[k] = funding_zscore_series(cd["funding_sorted"])
        facts[k] = {**funding_facts(cd["funding_sorted"], cd["premiums"]),
                    "z_scored_hours": len(z_maps[k]), "thresholds": threshold_hours(z_maps[k])}
    log(f"funding facts done ({time.time() - t0:.0f}s)")

    # ── 2. event study
    event_rows: List[Dict[str, Any]] = []
    event_studies: Dict[str, Dict[str, Any]] = {}
    alpha_c = bonferroni_alpha(ALPHA, BONFERRONI_CELLS)
    for k, cd in coins.items():
        times, closes = close_series(cd["c1h"])
        candle_start = times[0] if times else None
        candle_end = times[-1] if times else None
        for side, ev_side in (("pos", "short"), ("neg", "long")):
            for T in THRESHOLDS:
                events = usable_events(collapse_events(z_maps[k], T, side), candle_start, candle_end,
                                       max(HOLD_DAYS))
                eligible = [t for t in z_maps[k] if candle_start is not None
                           and candle_start <= t <= (candle_end - int(max(HOLD_DAYS) * DAY_MS))]
                for h in HOLD_DAYS:
                    st = event_study(events, times, closes, ev_side, h)
                    null_means = matched_null(eligible, times, closes, ev_side, h, st["n"]) if st["n"] else []
                    p = null_p_value(st["mean_net"], null_means) if st["mean_net"] is not None else None
                    verdict = doctrine_verdict(st["n"], st["mean_net"], st["both_halves_positive"], p, alpha_c)
                    key = f"{k}_{ev_side}_z{T:g}_{h}d"
                    event_studies[key] = {"coin": k, "side": ev_side, "threshold": T, "hold_days": h, **st,
                                          "null_draws": len(null_means), "p": p, "verdict": verdict}
                    event_rows.append({"coin": k, "side": ev_side, "threshold": T, "hold_days": h,
                                       "n": st["n"], "mean_gross": st["mean_gross"], "mean_net": st["mean_net"],
                                       "win_rate": st["win_rate"], "half1_mean_net": st["half1_mean_net"],
                                       "half2_mean_net": st["half2_mean_net"],
                                       "both_halves_positive": st["both_halves_positive"], "p": p,
                                       "pass": verdict["pass"]})
        log(f"{k}: event study done ({time.time() - t0:.0f}s)")

    # ── 3. filter on the live desk
    theses_dirs = {
        "brent": {"journal": a.oil_theses_dir, "replay_30b": a.oil_replay_30b_dir,
                  "replay_sep": a.oil_replay_sep_dir},
        "silver": {"journal": a.metals_theses_dir},
    }
    base_params = {"brent": live_brent_params(starting_equity=SLICE_EQUITY),
                   "silver": silver_base_params(coins["silver"]["c1h"], a.cache_dir, SLICE_EQUITY)}
    filter_results: Dict[str, Any] = {}
    for k, cd in coins.items():
        bp = base_params[k]
        coin_name = COINS[k]
        c1h = cd["c1h"]
        if c1h:
            for s in ("always_long", "always_short", "daily_sign"):
                run_res = run_filter_variants([], c1h, control_direction(s, c1h), bp, cd["funding"], ticks=60)
                filter_results[f"{k}_{s}"] = {"n_bars": len(c1h), "results": run_res}
        for label, tdir in theses_dirs[k].items():
            ths = D.load_journaled_theses(tdir)
            if not ths:
                filter_results[f"{k}_{label}"] = {"n_theses": 0, "note": "no theses found; skipped"}
                continue
            st_ms = min(th.created_at_ms for th in ths) - 2 * DAY_MS
            en_ms = min(now_ms, max(th.created_at_ms for th in ths) + 7 * DAY_MS)
            c15 = paged_15m(coin_name, st_ms, en_ms, a.cache_dir)
            c1h_win = [c for c in c1h if c.t < en_ms]
            run_res = run_filter_variants(c15, c1h_win, _journaled_direction(ths), bp, cd["funding"], ticks=15)
            filter_results[f"{k}_{label}"] = {"n_theses": len(ths), "from": _iso(st_ms), "to": _iso(en_ms),
                                              "results": run_res}
        log(f"{k}: filter test done ({time.time() - t0:.0f}s)")

    # ── 4. standalone short-the-crowded-long
    standalone: Dict[str, Any] = {}
    for T in THRESHOLDS:
        for k, cd in coins.items():
            standalone[f"{k}_z{T:g}"] = standalone_short(cd["c1h"], cd["funding"], cd["funding_sorted"],
                                                          base_params[k], T, SLICE_EQUITY)
    log(f"standalone done ({time.time() - t0:.0f}s)")

    # ── write
    with open(os.path.join(a.out_dir, "summary.csv"), "w", newline="") as fh:
        fields = ["coin", "side", "threshold", "hold_days", "n", "mean_gross", "mean_net", "win_rate",
                  "half1_mean_net", "half2_mean_net", "both_halves_positive", "p", "pass"]
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(event_rows)

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()), "seconds": round(time.time() - t0, 1),
        "config": {"thresholds": THRESHOLDS, "hold_days": HOLD_DAYS, "fee_frac": FEE_FRAC,
                  "null_draws": NULL_DRAWS, "null_seed": NULL_SEED, "min_events": MIN_EVENTS, "alpha": ALPHA,
                  "bonferroni_cells": BONFERRONI_CELLS, "alpha_corrected": round(alpha_c, 6),
                  "slice_equity": SLICE_EQUITY},
        "funding_facts": facts,
        "event_study": event_studies,
        "filter_test": filter_results,
        "standalone": standalone,
    }
    with open(os.path.join(a.out_dir, "report.json"), "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    _print(report)
    log(f"done ({time.time() - t0:.0f}s)")
    return 0


def _print(rep: Dict[str, Any]) -> None:
    print("\n== funding facts")
    for k, f in rep["funding_facts"].items():
        print(f"  {k}: {f['from']} to {f['to']} ({f['span_days']}d, {f['n_hours']} hours) "
              f"long_cost/day={f['long_cost_pct_per_day']:+.4f}% frac_long_pays={f['frac_hours_long_pays']:.3f}")
        for T, h in f["thresholds"].items():
            print(f"    z>=+{T}: {h['pos_hours']} hrs ({h['pos_hours_pct']}%) / {h['pos_events']} events   "
                  f"z<=-{T}: {h['neg_hours']} hrs ({h['neg_hours_pct']}%) / {h['neg_events']} events")
    print("\n== event study (net of 25bps; pass = all EVIDENCE_DOCTRINE.md gates incl. n>=10)")
    for key, s in rep["event_study"].items():
        v = s["verdict"]
        print(f"  {key:<24} n={s['n']:<3} mean_net={s['mean_net']} halves={s['half1_mean_net']}/"
              f"{s['half2_mean_net']} p={s['p']} pass={v['pass']}")
    print("\n== filter vs live (final equity, trades, funding_z refusals)")
    for ds, x in rep["filter_test"].items():
        if "results" not in x:
            print(f"  {ds}: {x.get('note')}")
            continue
        row = " ".join(f"{v}={r['final_equity']}/{r['trades']}t/{r['refused_funding_z']}r"
                       for v, r in x["results"].items())
        print(f"  {ds}: {row}")
    print("\n== standalone short-the-crowded-long")
    for k, s in rep["standalone"].items():
        print(f"  {k}: {s}")


if __name__ == "__main__":
    raise SystemExit(main())
