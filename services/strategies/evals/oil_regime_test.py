"""Oil desk regime test, 2026-11-02 (docs/strategy/OIL_REGIME_TEST_2026-11-02.md).

Operator, 2026-11-02: "i'll just wait for you to fix the issues with oil".
Does the oil desk's live rule still work in the market Brent is in now, and
does a regime-aware rule do better?

  1. History: every xyz:BRENTOIL 1h candle the venue serves (candleSnapshot
     returns the last 5,000: about 208 days), the last 5,000 15m candles
     (about 52 days), and xyz:CL (WTI) the same way as a labelled proxy.
  2. A causal regime label per UTC day (services/strategies/backtest/
     regime.py): trend_up / trend_down / chop from the 20-day return, the
     48h efficiency ratio and the 24h range over ATR14, read at the day's
     open. Each threshold also shifted -25% and +25%.
  3. The live rule by regime: every trade tagged with the label of its entry
     day; trades, win rate, mean and summed R, and the compounded return of
     the days in each regime. Always long, always short, daily sign, the 30
     replayed theses and the live journal.
  4. Fixes vs live, each causal: regime filters (no_chop, with_trend,
     trend_only, veto_20d; day or hour refresh; every threshold shift), a
     with-trend side override, range-scaled stops and targets, and
     no_break_entry and the weekend-only lock split by regime. The
     2026-11-02 Brent long as a regression row.

Live rule: desk_tick_sim.live_params() (market entry off the 24h range, 2R
gate, stop 0.75 beyond the edge, target 0.5% inside the far edge, limit
target, 20x) with size slippage, funding and the 15% risk-per-trade cap.
$225 start (the account). 15-minute ticks where 15m candles exist; the
208-day runs tick hourly (the 52-day overlap gives the 15m-vs-1h gap).

Adopt bar (the other tests' rule): beats live on most scored Brent datasets
with a geo-mean of 1 or more; a plateau (every threshold shift, or each grid
neighbour, also does); mean max drawdown no worse; no stop past half the way
to liquidation (at the fill, market slippage allowed). Plus one guard this
history needs: live loses money on most of these datasets, so sitting out
(no trades at all) already "beats live on most"; a candidate must beat live
on MORE datasets than sitting out does.

    python -m services.strategies.evals.oil_regime_test \\
        [--oil-theses-dir <pathia>/.state/strategies/oil/theses]

Writes /tmp/oil-regime/report.json, regime_days.csv, live_trades.csv,
variants.csv, progress.log. Public candles and funding only (cached under
--cache-dir); never touches the account, the live config or the journal.
No LLM calls.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import os
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple


from services.strategies.backtest import data as D
from services.strategies.backtest.desk_tick_sim import Direction, Params, Run, simulate
from services.strategies.backtest.regime import (
    DAY_MS, HOUR_MS, LABELS, RegimeConfig, RegimeSeries, against_20d, label_series)

START = 225.0
RISK_CAP = 0.15
FIFTEEN_MS = 900_000
BASELINE = "live"
CFG = RegimeConfig()
SHIFTS = tuple((th, f) for th in RegimeConfig.THRESHOLDS for f in (0.75, 1.25))
FILTER_MODES = ("no_chop", "with_trend", "trend_only", "veto_20d")
REFRESH = ("day", "hour")
RANGE_K_FLOOR = (0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.5, 0.6)
RANGE_K_SCALED = (0.2, 0.25, 0.3, 0.35)
TARGET_RANGE_FRACS = (0.1, 0.15, 0.2, 0.25, 0.3)
CONTROLS = ("always_long", "always_short", "daily_sign")
MAX_STOP_FRAC_OF_LIQ = 0.5
# The sim gates every stop at 0.5 of the way to liquidation at the MID (the
# live check_leverage); the market fill's slippage moves the entry, so at the
# fill live itself reaches 0.509 to 0.512. The rule is read at the fill with
# that slippage allowed: a variant fails only past 0.5 + FILL_SLIP_TOL.
FILL_SLIP_TOL = 0.015
WARMUP_DAYS = 22            # 20-day return plus two days of slack before a full-history run starts

# The live oil journal around the 2026-11-02 long (created UTC, side, entry,
# 1h-close invalidation), as in test_loss_fix.py / LOSS_AUDIT_2026-11-02.md.
NOV2_THESES = (((2026, 11, 1, 18, 19), "long", 111.7, 110.83), ((2026, 11, 2, 3, 48), "long", 110.0, 109.55),
               ((2026, 11, 2, 8, 40), "short", 110.2, 112.83))
NOV2_FROM = (2026, 11, 2, 3, 45)      # the live entry's 15m bar
NOV2_LIVE_NET = -33.87


def _ms(parts: Sequence[int]) -> int:
    return int(datetime(*parts, tzinfo=timezone.utc).timestamp() * 1000)


def _iso(ms: Optional[int], fmt: str = "%Y-%m-%d %H:%MZ") -> Optional[str]:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime(fmt)


def cfg_name(th: Optional[str] = None, f: Optional[float] = None) -> str:
    return "" if th is None else f"@{th}x{f:g}"


# ── variants (pure) ────────────────────────────────────────────────────────

def variants() -> Dict[str, Dict[str, Any]]:
    """name -> Params overrides on the live params. An "override_*" variant
    carries `_override` (the refresh) and `_cfg`: the eval wraps the
    direction series instead of setting a sim option."""
    out: Dict[str, Dict[str, Any]] = {BASELINE: {}}
    for mode in FILTER_MODES:
        for ref in REFRESH:
            out[f"{mode}_{ref}"] = {"regime_filter": mode, "regime_refresh": ref}
            for th, f in SHIFTS:
                out[f"{mode}_{ref}{cfg_name(th, f)}"] = {"regime_filter": mode, "regime_refresh": ref,
                                                         "regime_cfg": CFG.shifted(th, f)}
    for ref in REFRESH:
        out[f"override_{ref}"] = {"_override": ref, "_cfg": CFG}
        for th, f in SHIFTS:
            out[f"override_{ref}{cfg_name(th, f)}"] = {"_override": ref, "_cfg": CFG.shifted(th, f)}
    for k in RANGE_K_FLOOR:
        out[f"rstop_max_{k:g}"] = {"range_buffer_k": k}
    for k in RANGE_K_SCALED:
        out[f"rstop_only_{k:g}"] = {"range_buffer_k": k, "range_buffer_floor": False}
    for f in TARGET_RANGE_FRACS:
        out[f"rtarget_{f:g}"] = {"target_offset_range_frac": f}
    out["no_break"] = {"no_break_entry": True}
    out["wk_befees_t1"] = {"lock_weekend_only": True, "lock_trigger_r": 1.0, "lock_r": 0.0, "lock_plus_fees": True}
    out["wk_lock_t1_l0.5"] = {"lock_weekend_only": True, "lock_trigger_r": 1.0, "lock_r": 0.5}
    return out


def families() -> Dict[str, List[str]]:
    """Each candidate's neighbours for the plateau rule. Regime filters and
    the override: the six threshold shifts of the same rule (all must hold).
    Grids: the next value either side."""
    fam: Dict[str, List[str]] = {}
    for base in [f"{m}_{r}" for m in FILTER_MODES for r in REFRESH] + [f"override_{r}" for r in REFRESH]:
        fam[base] = [f"{base}{cfg_name(th, f)}" for th, f in SHIFTS]
    for name, grid in (("rstop_max", [f"rstop_max_{k:g}" for k in RANGE_K_FLOOR]),
                       ("rstop_only", [f"rstop_only_{k:g}" for k in RANGE_K_SCALED]),
                       ("rtarget", [f"rtarget_{f:g}" for f in TARGET_RANGE_FRACS])):
        for i, v in enumerate(grid):
            fam[v] = [grid[j] for j in (i - 1, i + 1) if 0 <= j < len(grid)]
    return fam


def sim_overrides(ov: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in ov.items() if not k.startswith("_")}


# ── direction wrappers (pure, causal) ──────────────────────────────────────

def from_start(d_at: Callable[[int], Optional[Direction]], start_ms: int) -> Callable[[int], Optional[Direction]]:
    """No call before `start_ms` (an hourly-tick run walks every 1h bar it is
    given; the bars before the window are there for the levels)."""
    return lambda t: d_at(t) if t >= start_ms else None


def override_direction(d_at: Callable[[int], Optional[Direction]], series: RegimeSeries, cfg: RegimeConfig,
                       refresh: str = "day") -> Callable[[int], Optional[Direction]]:
    """The with-trend side override: where the call's side fights the
    20-day trend and the 48h run does not say the trend broke
    (`regime.against_20d`), take the 20-day trend's side instead. The
    thesis's entry ceiling and invalidation belong to the other side, so
    the overridden call carries neither. Reads the regime at the day's open
    ("day") or the last 1h close ("hour"): causal."""
    def at(t: int) -> Optional[Direction]:
        d = d_at(t)
        if d is None:
            return None
        f = series.at(t - t % DAY_MS if refresh == "day" else t)
        if against_20d(d.side, f, cfg):
            return Direction(side="short" if d.side == "long" else "long", regime_id=d.regime_id)
        return d
    return at


def thesis_direction(theses: Sequence[Tuple[Sequence[int], str, float, float]]) -> Callable[[int], Optional[Direction]]:
    """A direction series from (created, side, entry, invalidation) rows."""
    rows = sorted((_ms(c), s, e, inv) for c, s, e, inv in theses)

    def at(t: int) -> Optional[Direction]:
        cur = None
        for ct, side, entry, inv in rows:
            if ct <= t:
                cur = Direction(side=side, entry_ceiling=entry, invalidation_px=inv, regime_id=str(ct))
        return cur
    return at


# ── per-run numbers (pure) ─────────────────────────────────────────────────

def day_of(t: int) -> int:
    return t // DAY_MS


def entry_label(series: RegimeSeries, t: int) -> str:
    """The label of a trade's entry day (at that day's open); "warmup" if none."""
    return series.label_at(t - t % DAY_MS) or "warmup"


def day_returns(run: Run, start_ms: int, start_equity: float) -> Dict[int, float]:
    """Each UTC day's return from the last mark of the day before (the
    start equity before the first day)."""
    by_day: Dict[int, float] = {}
    prev_mark = start_equity
    for t, mark in run.equity_curve:
        if t < start_ms:
            prev_mark = mark
            continue
        by_day[day_of(t)] = mark
    out: Dict[int, float] = {}
    prev = prev_mark
    for d in sorted(by_day):
        out[d] = (by_day[d] / prev - 1.0) if prev > 0 else 0.0
        prev = by_day[d]
    return out


def by_regime(run: Run, series: RegimeSeries, start_ms: int, start_equity: float) -> Dict[str, Dict[str, Any]]:
    """Trades tagged with their entry day's label, and the compounded return
    of the days in each label."""
    out: Dict[str, Dict[str, Any]] = {}
    groups: Dict[str, List[Any]] = defaultdict(list)
    for tr in run.trades:
        groups[entry_label(series, tr.entry_time)].append(tr)
    rets = day_returns(run, start_ms, start_equity)
    day_groups: Dict[str, List[float]] = defaultdict(list)
    for d, r in rets.items():
        day_groups[series.label_at(d * DAY_MS) or "warmup"].append(r)
    for lab in list(LABELS) + ["warmup"]:
        trs = groups.get(lab, [])
        rs = [t.r_multiple for t in trs if t.r_multiple is not None]
        comp = 1.0
        for r in day_groups.get(lab, []):
            comp *= 1.0 + r
        if not trs and not day_groups.get(lab):
            continue
        out[lab] = {"days": len(day_groups.get(lab, [])), "trades": len(trs),
                    "wins": sum(1 for t in trs if t.net_pnl_usd > 0),
                    "win_rate": round(sum(1 for t in trs if t.net_pnl_usd > 0) / len(trs), 3) if trs else None,
                    "mean_r": round(sum(rs) / len(rs), 3) if rs else None, "sum_r": round(sum(rs), 2),
                    "days_return_pct": round(100.0 * (comp - 1.0), 1)}
    return out


def sign_stability(base: Dict[str, Dict[str, Any]], shifted: Dict[str, Dict[str, Dict[str, Any]]],
                   dead: float = 0.5) -> Dict[str, int]:
    """Per label: in how many threshold shifts the summed R keeps the base
    labels' sign (+1, -1, or 0 inside +-`dead` R)."""
    def sgn(x: float) -> int:
        return 0 if abs(x) < dead else (1 if x > 0 else -1)

    out: Dict[str, int] = {}
    for lab in LABELS:
        b = sgn(base.get(lab, {}).get("sum_r", 0.0) or 0.0)
        out[lab] = sum(1 for x in shifted.values() if sgn(x.get(lab, {}).get("sum_r", 0.0) or 0.0) == b)
    return out


def stop_liq_frac(entry: float, stop: float, params: Params) -> Optional[float]:
    from pathiel.agents.bracket import check_leverage
    if entry <= 0 or stop <= 0 or entry == stop:
        return None
    return check_leverage(entry, stop, params.leverage, params.max_leverage).get("stop_frac_of_liq")


def summarize(run: Run, series: RegimeSeries, start_ms: int, end_ms: int) -> Dict[str, Any]:
    p = run.params or Params()
    rs = [t.r_multiple for t in run.trades if t.r_multiple is not None]
    n = len(run.trades)
    liq = [stop_liq_frac(t.entry_px, t.orig_stop_px or t.stop_px, p) for t in run.trades]
    return {
        "days": round((end_ms - start_ms) / DAY_MS, 1),
        "final_equity": round(run.final_equity, 2),
        "return_pct": round((run.final_equity / p.starting_equity - 1.0) * 100, 1),
        "max_dd_pct": round(run.max_drawdown_pct * 100, 1),
        "trades": n,
        "win_rate": round(sum(1 for t in run.trades if t.net_pnl_usd > 0) / n, 3) if n else None,
        "avg_r": round(sum(rs) / len(rs), 3) if rs else None,
        "total_r": round(sum(rs), 2),
        "liquidated": run.liquidated,
        "max_stop_liq_frac": round(max((x for x in liq if x is not None), default=0.0), 3),
        "exits": dict(sorted(Counter(t.exit_reason for t in run.trades).items())),
        "refused": dict(sorted(run.refused.items())),
        "by_regime": by_regime(run, series, start_ms, p.starting_equity),
    }


def monthly_returns(run: Run, start_ms: int, start_equity: float) -> Dict[str, float]:
    """Each calendar month's return (UTC), from the last mark before it."""
    out: Dict[str, float] = {}
    prev = start_equity
    cur_m, last = None, None
    for t, mark in run.equity_curve:
        if t < start_ms:
            prev = mark
            continue
        m = _iso(t, "%Y-%m")
        if cur_m is not None and m != cur_m:
            out[cur_m] = round(100.0 * (last / prev - 1.0), 1) if prev > 0 else 0.0
            prev = last
        cur_m, last = m, mark
    if cur_m is not None:
        out[cur_m] = round(100.0 * (last / prev - 1.0), 1) if prev > 0 else 0.0
    return out


# ── scoring and the adopt rule (pure) ──────────────────────────────────────

def score(rows: Dict[str, Dict[str, Dict[str, Any]]], baseline: str = BASELINE) -> Dict[str, Dict[str, Any]]:
    """Per variant across datasets: beats / loses live on final equity, the
    geo-mean of final/live (a wipe-out counts as 0.01), and the mean max
    drawdown next to live's."""
    names = [v for v in next(iter(rows.values())) if v != baseline]
    out: Dict[str, Dict[str, Any]] = {}
    live_dd = [by_v[baseline]["max_dd_pct"] for by_v in rows.values()]
    flat_beats = sum(1 for by_v in rows.values() if by_v[baseline]["final_equity"] < START - 1e-9)
    for v in names:
        ratios, wins, losses, dds = [], 0, 0, []
        for by_v in rows.values():
            b, x = by_v[baseline]["final_equity"], by_v[v]["final_equity"]
            ratios.append(max(x, 0.01 * max(b, 1e-9)) / b if b > 0 else (1.0 if x <= 0 else 2.0))
            wins += x > b + 1e-9
            losses += x < b - 1e-9
            dds.append(by_v[v]["max_dd_pct"])
        g = 1.0
        for r in ratios:
            g *= r
        out[v] = {"beats": wins, "loses": losses, "of": len(ratios),
                  "geo_mean_ratio": round(g ** (1.0 / len(ratios)), 4),
                  "mean_dd": round(sum(dds) / len(dds), 1), "live_mean_dd": round(sum(live_dd) / len(live_dd), 1),
                  "flat_beats": flat_beats}
    return out


def regime_delta(rows: Dict[str, Dict[str, Dict[str, Any]]], name: str, baseline: str = BASELINE
                 ) -> Dict[str, Dict[str, float]]:
    """Pooled over datasets, per entry-day label: live's trades and summed R,
    and the variant's minus live's. Where a rule gains or loses, by regime."""
    out: Dict[str, Dict[str, float]] = {}
    for lab in LABELS:
        dn = dr = ln = lr = 0.0
        for by_v in rows.values():
            b = by_v[baseline]["by_regime"].get(lab, {})
            x = by_v[name]["by_regime"].get(lab, {})
            dn += x.get("trades", 0) - b.get("trades", 0)
            dr += x.get("sum_r", 0.0) - b.get("sum_r", 0.0)
            ln += b.get("trades", 0)
            lr += b.get("sum_r", 0.0)
        out[lab] = {"live_trades": int(ln), "live_sum_r": round(lr, 2), "d_trades": int(dn), "d_sum_r": round(dr, 2)}
    return out


def good(s: Optional[Dict[str, Any]]) -> bool:
    """Beats live on most datasets with a geo-mean ratio of 1 or more."""
    return bool(s) and s["beats"] > s["of"] / 2.0 and s["geo_mean_ratio"] >= 1.0


def verdict(scores: Dict[str, Dict[str, Any]], name: str, fam: Dict[str, List[str]],
            max_liq: Dict[str, float], thesis_scores: Optional[Dict[str, Dict[str, Any]]] = None
            ) -> Dict[str, Any]:
    """The adopt rule for one candidate (module docstring). A candidate with
    no neighbours (a single variant) has no plateau to show: it cannot pass.
    `thesis_scores` (the thesis datasets alone) is reported, not a rule."""
    s = scores[name]
    nb = fam.get(name, [])
    nb_good = [n for n in nb if good(scores.get(n))]
    plateau = bool(nb) and len(nb_good) == len(nb)
    liq_ok = max_liq.get(name, 0.0) <= MAX_STOP_FRAC_OF_LIQ + FILL_SLIP_TOL + 1e-9
    dd_ok = s["mean_dd"] <= s["live_mean_dd"] + 1e-9
    most = good(s)
    beats_flat = s["beats"] > s["flat_beats"]
    fails = [w for w, ok in (("beats live on most datasets", most), ("beats live where sitting out does not",
                                                                     beats_flat),
                             ("plateau", plateau), ("drawdown no worse", dd_ok),
                             ("stop inside half the way to liquidation", liq_ok)) if not ok]
    th = (thesis_scores or {}).get(name)
    return {"adopt": not fails, "beats": s["beats"], "loses": s["loses"], "of": s["of"], "flat_beats": s["flat_beats"],
            "theses": f"{th['beats']}/{th['loses']} of {th['of']}" if th else None,
            "geo_mean_ratio": s["geo_mean_ratio"], "mean_dd": s["mean_dd"], "live_mean_dd": s["live_mean_dd"],
            "neighbours_good": f"{len(nb_good)}/{len(nb)}", "fails": fails}


# ── data ───────────────────────────────────────────────────────────────────

def paged_funding(coin: str, start_ms: int, end_ms: int, fetch: Optional[Callable] = None,
                  max_pages: int = 40) -> List[Dict[str, Any]]:
    """fundingHistory returns at most 500 rows a call: page forward from
    each batch's last time."""
    if fetch is None:
        from pathiel.client.hl_client import fetch_funding_history as fetch
    out: List[Dict[str, Any]] = []
    seen = set()
    cur = start_ms
    for _ in range(max_pages):
        batch = list(fetch(coin, cur, end_ms) or [])
        fresh = [r for r in batch if int(r.get("time", 0)) not in seen]
        for r in fresh:
            seen.add(int(r["time"]))
        out.extend(fresh)
        if len(batch) < 500 or not fresh:
            break
        cur = max(int(r["time"]) for r in batch) + 1
    return sorted(out, key=lambda r: int(r["time"]))


def load_coin(coin: str, cache_dir: str) -> Dict[str, Any]:
    c1h = D.fetch_candles_cached(coin, "1h", 5000, cache_dir)
    c15 = D.fetch_candles_cached(coin, "15m", 5000, cache_dir)
    asof = int(os.path.getmtime(D._cache_path(cache_dir, coin, "candles_15m_5000")) * 1000)
    c1h = sorted((c for c in c1h if c.t + HOUR_MS <= asof), key=lambda c: c.t)
    c15 = sorted((c for c in c15 if c.t + FIFTEEN_MS <= asof), key=lambda c: c.t)
    end = c15[-1].t + FIFTEEN_MS
    funding = D.fetch_funding_cached(coin, c1h[0].t, end, cache_dir,
                                     fetch=lambda co, s, e: paged_funding(co, s, e))
    return {"c1h": c1h, "c15": c15, "end": end, "funding": funding}


def dataset_specs(coin_key: str, coin: Dict[str, Any], theses: Dict[str, List[D.ThesisDirection]]
                  ) -> Dict[str, Dict[str, Any]]:
    """name -> {ticks, start, end, direction, scored}."""
    from services.strategies.evals.desk_tick_backtest import journaled_direction
    from services.strategies.evals.market_scout import direction_series
    c1h, c15, end = coin["c1h"], coin["c15"], coin["end"]
    full_start = (day_of(c1h[0].t) + WARMUP_DAYS) * DAY_MS
    q_start = (day_of(c15[0].t) + 1) * DAY_MS
    out: Dict[str, Dict[str, Any]] = {}
    for s in CONTROLS:
        out[f"{coin_key}_{s}_full_1h"] = {"ticks": 60, "start": full_start, "end": end,
                                          "direction": direction_series(s, c1h)}
    for s in CONTROLS:
        out[f"{coin_key}_{s}_52d_15m"] = {"ticks": 15, "start": q_start, "end": end,
                                          "direction": direction_series(s, c1h)}
    for label, ths in theses.items():
        if not ths:
            continue
        st = min(t.created_at_ms for t in ths)
        en = end if label == "journal" else min(end, max(t.created_at_ms for t in ths) + DAY_MS)
        out[f"{coin_key}_{label}"] = {"ticks": 15, "start": st, "end": en, "direction": journaled_direction(ths)}
    return out


def run_one(coin: Dict[str, Any], spec: Dict[str, Any], base: Params, ov: Dict[str, Any],
            series_cache: Dict[RegimeConfig, RegimeSeries]) -> Run:
    st, en = spec["start"], spec["end"]
    d_at = spec["direction"]
    if "_override" in ov:
        cfg = ov["_cfg"]
        if cfg not in series_cache:
            series_cache[cfg] = label_series(coin["c1h"], cfg)
        d_at = override_direction(d_at, series_cache[cfg], cfg, ov["_override"])
    p = dataclasses.replace(base, tick_minutes=spec["ticks"], **sim_overrides(ov))
    c1h = [c for c in coin["c1h"] if c.t < en]
    if spec["ticks"] >= 60:
        return simulate([], c1h, from_start(d_at, st), p, coin["funding"])
    c15 = [c for c in coin["c15"] if st <= c.t < en]
    return simulate(c15, c1h, d_at, p, coin["funding"])


def nov2_rows(coin: Dict[str, Any], base: Params, named: Dict[str, Dict[str, Any]],
              series_cache: Dict[RegimeConfig, RegimeSeries]) -> Dict[str, Any]:
    """The 2026-11-02 long under each variant: the sim from the live entry's
    15m bar to the data end, the three journal calls around it, the full 1h
    history for the levels and the regime. First trade, or why none."""
    t0 = _ms(NOV2_FROM)
    spec = {"ticks": 15, "start": t0, "end": coin["end"], "direction": thesis_direction(NOV2_THESES)}
    out: Dict[str, Any] = {}
    for v, ov in named.items():
        run = run_one(coin, spec, base, ov, series_cache)
        if run.trades:
            tr = run.trades[0]
            out[v] = {"entry": _iso(tr.entry_time), "side": tr.side, "entry_px": round(tr.entry_px, 3),
                      "stop_px": round(tr.orig_stop_px or tr.stop_px, 3), "target_px": round(tr.target_px, 3),
                      "exit": tr.exit_reason, "exit_time": _iso(tr.exit_time), "exit_px": round(tr.exit_px or 0, 3),
                      "net_usd": round(tr.net_pnl_usd, 2), "pct_equity": round(100 * tr.net_pnl_usd / START, 1),
                      "refused": dict(run.refused)}
        else:
            op = run.open_position_at_end
            out[v] = {"entry": None, "open_at_end": op, "refused": dict(run.refused),
                      "final_equity": round(run.final_equity, 2)}
    return out


# ── main ───────────────────────────────────────────────────────────────────

VARIANT_FIELDS = ["dataset", "variant", "final_equity", "return_pct", "max_dd_pct", "trades", "win_rate", "avg_r",
                  "total_r", "max_stop_liq_frac"] + [f"{lab}_{k}" for lab in LABELS for k in ("trades", "sum_r")]
TRADE_FIELDS = ["dataset", "entry", "side", "entry_px", "stop_px", "target_px", "exit", "exit_reason", "net_usd",
                "r", "regime", "ret20", "er", "range_atr"]


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--oil-theses-dir", default=os.environ.get("PATHIEL_OIL_THESES_DIR", ""),
                    help="READ ONLY: the live oil journal's theses (skipped when empty)")
    ap.add_argument("--oil-replay-dir", default="/tmp/thesis-replay-30b/oil/theses")
    ap.add_argument("--cache-dir", default="/tmp/oil-regime/cache")
    ap.add_argument("--out-dir", default="/tmp/oil-regime")
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    os.makedirs(a.cache_dir, exist_ok=True)
    t0 = time.time()
    log_path = os.path.join(a.out_dir, "progress.log")

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} oil-regime: {msg}"
        print(line, flush=True)
        with open(log_path, "a") as fh:
            fh.write(line + "\n")

    coins = {"brent": load_coin("xyz:BRENTOIL", a.cache_dir), "wti": load_coin("xyz:CL", a.cache_dir)}
    history = {k: {"1h_from": _iso(c["c1h"][0].t), "1h_bars": len(c["c1h"]), "15m_from": _iso(c["c15"][0].t),
                   "15m_bars": len(c["c15"]), "end": _iso(c["end"]), "funding_rows": len(c["funding"])}
               for k, c in coins.items()}
    log(f"data {json.dumps(history)}")
    theses = {"replay30": D.load_journaled_theses(a.oil_replay_dir),
              "journal": D.load_journaled_theses(a.oil_theses_dir) if a.oil_theses_dir else []}
    for k, v in theses.items():
        log(f"{k}: {len(v)} theses")

    from services.strategies.evals.strategy_sweep import base_params
    base = dataclasses.replace(base_params(), starting_equity=START, risk_cap_frac=RISK_CAP)
    named = variants()
    fam = families()
    series = {k: {CFG: label_series(c["c1h"], CFG)} for k, c in coins.items()}
    specs = {"brent": dataset_specs("brent", coins["brent"], theses),
             "wti": dataset_specs("wti", coins["wti"], {})}

    # ── regime per day, base and shifted
    day_rows = []
    for k, c in coins.items():
        shifted = {cfg_name(th, f): label_series(c["c1h"], CFG.shifted(th, f)) for th, f in SHIFTS}
        for d in range(day_of(c["c1h"][0].t) + 1, day_of(c["end"]) + 1):
            f = series[k][CFG].at(d * DAY_MS)
            if f is None:
                continue
            row = {"coin": k, "day": _iso(d * DAY_MS, "%Y-%m-%d"),
                   "ret20": None if f.ret20 is None else round(f.ret20, 4),
                   "er48": None if f.er is None else round(f.er, 3),
                   "move48": None if f.move is None else round(f.move, 3),
                   "range_atr": None if f.range_atr is None else round(f.range_atr, 2), "label": f.label}
            for nm, s in shifted.items():
                row[f"label{nm}"] = s.label_at(d * DAY_MS)
            day_rows.append(row)
    label_counts = {k: dict(Counter(r["label"] for r in day_rows if r["coin"] == k)) for k in coins}
    shift_agree = {}
    for k in coins:
        rows = [r for r in day_rows if r["coin"] == k and r["label"]]
        shift_agree[k] = {nm: round(sum(1 for r in rows if r[f"label{nm}"] == r["label"]) / len(rows), 3)
                          for nm in [cfg_name(th, f) for th, f in SHIFTS]}
    nov2_hours = []
    for h in range(0, 13):
        t = _ms((2026, 11, 2, h))
        f = series["brent"][CFG].at(t)
        if f is not None and t <= coins["brent"]["end"]:
            nov2_hours.append({"t": _iso(t), "ret20": round(f.ret20, 4) if f.ret20 is not None else None,
                               "er48": round(f.er, 3), "move48": round(f.move, 2),
                               "range_atr": round(f.range_atr, 2), "label": f.label})
    log(f"regime days done ({time.time() - t0:.0f}s): {json.dumps(label_counts)}")

    # ── runs
    results: Dict[str, Dict[str, Dict[str, Any]]] = {}
    live_trades: List[Dict[str, Any]] = []
    monthly: Dict[str, Dict[str, Dict[str, float]]] = {}
    shift_regime: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for k, sp in specs.items():
        for ds, spec in sp.items():
            results[ds] = {}
            for i, (v, ov) in enumerate(named.items()):
                run = run_one(coins[k], spec, base, ov, series[k])
                s = summarize(run, series[k][CFG], spec["start"], spec["end"])
                results[ds][v] = s
                if spec["ticks"] >= 60:
                    monthly.setdefault(ds, {})[v] = monthly_returns(run, spec["start"], START)
                if v == BASELINE:
                    for th, fx in SHIFTS:
                        cfg = CFG.shifted(th, fx)
                        if cfg not in series[k]:
                            series[k][cfg] = label_series(coins[k]["c1h"], cfg)
                        shift_regime.setdefault(ds, {})[cfg_name(th, fx)] = by_regime(
                            run, series[k][cfg], spec["start"], START)
                    for tr in run.trades:
                        f = series[k][CFG].at(tr.entry_time - tr.entry_time % DAY_MS)
                        live_trades.append({
                            "dataset": ds, "entry": _iso(tr.entry_time), "side": tr.side,
                            "entry_px": round(tr.entry_px, 3), "stop_px": round(tr.orig_stop_px or tr.stop_px, 3),
                            "target_px": round(tr.target_px, 3), "exit": _iso(tr.exit_time),
                            "exit_reason": tr.exit_reason, "net_usd": round(tr.net_pnl_usd, 2),
                            "r": None if tr.r_multiple is None else round(tr.r_multiple, 3),
                            "regime": entry_label(series[k][CFG], tr.entry_time),
                            "ret20": None if f is None or f.ret20 is None else round(f.ret20, 4),
                            "er": None if f is None or f.er is None else round(f.er, 3),
                            "range_atr": None if f is None or f.range_atr is None else round(f.range_atr, 2)})
            log(f"{ds}: {len(named)} variants ({time.time() - t0:.0f}s)")

    # 15m vs 1h ticks on the 52-day overlap (Brent and WTI, live rule)
    tick_gap = {}
    for k in coins:
        for s in CONTROLS:
            spec15 = specs[k][f"{k}_{s}_52d_15m"]
            r1 = run_one(coins[k], {**spec15, "ticks": 60}, base, {}, series[k])
            s1 = summarize(r1, series[k][CFG], spec15["start"], spec15["end"])
            s15 = results[f"{k}_{s}_52d_15m"][BASELINE]
            tick_gap[f"{k}_{s}"] = {"15m": {x: s15[x] for x in ("final_equity", "trades", "win_rate", "avg_r")},
                                    "1h": {x: s1[x] for x in ("final_equity", "trades", "win_rate", "avg_r")}}

    groups = {
        "brent": [ds for ds in specs["brent"]],
        "brent_controls": [ds for ds in specs["brent"] if any(c in ds for c in CONTROLS)],
        "brent_theses": [ds for ds in specs["brent"] if not any(c in ds for c in CONTROLS)],
        "brent_full_1h": [ds for ds in specs["brent"] if ds.endswith("_full_1h")],
        "wti_proxy": [ds for ds in specs["wti"]],
    }
    scores = {g: score({ds: results[ds] for ds in dss}) for g, dss in groups.items() if dss}
    max_liq = {v: max(results[ds][v]["max_stop_liq_frac"] for ds in groups["brent"]) for v in named}
    candidates = [v for v in named if v != BASELINE and "@" not in v]
    verdicts = {v: verdict(scores["brent"], v, fam, max_liq, scores.get("brent_theses")) for v in candidates}
    verdicts_wti = {v: verdict(scores["wti_proxy"], v, fam, {v: 0.0 for v in named}) for v in candidates}
    brent_rows = {ds: results[ds] for ds in groups["brent"]}
    stability = {ds: sign_stability(results[ds][BASELINE]["by_regime"], shift_regime[ds]) for ds in results}
    deltas = {v: regime_delta(brent_rows, v) for v in candidates}
    nov2 = nov2_rows(coins["brent"], base, {v: named[v] for v in [BASELINE] + candidates}, series["brent"])
    log(f"scored ({time.time() - t0:.0f}s)")

    # ── write
    with open(os.path.join(a.out_dir, "regime_days.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(day_rows[0].keys()))
        w.writeheader()
        w.writerows(day_rows)
    with open(os.path.join(a.out_dir, "live_trades.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=TRADE_FIELDS)
        w.writeheader()
        w.writerows(live_trades)
    with open(os.path.join(a.out_dir, "variants.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=VARIANT_FIELDS)
        w.writeheader()
        for ds, by_v in results.items():
            for v, s in by_v.items():
                row = {"dataset": ds, "variant": v, **{x: s[x] for x in VARIANT_FIELDS[2:10]}}
                for lab in LABELS:
                    br = s["by_regime"].get(lab, {})
                    row[f"{lab}_trades"] = br.get("trades", 0)
                    row[f"{lab}_sum_r"] = br.get("sum_r", 0.0)
                w.writerow(row)
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()), "seconds": round(time.time() - t0, 1),
        "history": history, "config": dataclasses.asdict(CFG), "label_counts": label_counts,
        "shift_agreement": shift_agree, "nov2_hours": nov2_hours, "tick_gap": tick_gap,
        "datasets": {ds: {"start": _iso(sp["start"]), "end": _iso(sp["end"]), "ticks": sp["ticks"]}
                     for k in specs for ds, sp in specs[k].items()},
        "groups": groups, "results": results, "scores": scores, "max_stop_liq_frac": max_liq,
        "verdicts": verdicts, "verdicts_wti": verdicts_wti, "nov2": nov2, "monthly": monthly,
        "regime_delta": deltas, "live_by_regime_shifts": shift_regime, "sign_stability": stability,
        "adopt": sorted(v for v, x in verdicts.items() if x["adopt"]),
    }
    with open(os.path.join(a.out_dir, "report.json"), "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    _print(report)
    return 0


def _print(rep: Dict[str, Any]) -> None:
    print(json.dumps(rep["history"], indent=1))
    print("labels", json.dumps(rep["label_counts"]))
    print("shift agreement", json.dumps(rep["shift_agreement"]))
    for h in rep["nov2_hours"]:
        print("  11-02", json.dumps(h))
    print("\n== live by regime (trades / win / mean R / sum R / days / days return %)")
    for ds, by_v in rep["results"].items():
        s = by_v[BASELINE]
        print(f"\n{ds}  final {s['final_equity']} dd {s['max_dd_pct']} n {s['trades']} avgR {s['avg_r']}")
        for lab, x in s["by_regime"].items():
            print(f"  {lab:<10} n {x['trades']:>3} win {x['win_rate']} meanR {x['mean_r']} sumR {x['sum_r']} "
                  f"days {x['days']} ret {x['days_return_pct']}%")
    print("\n== tick gap", json.dumps(rep["tick_gap"]))
    print("\n== live regime sign kept in N of 6 threshold shifts", json.dumps(rep["sign_stability"]))
    for g, sc in rep["scores"].items():
        print(f"\n== score [{g}]")
        for v, x in sorted(sc.items(), key=lambda kv: -kv[1]["geo_mean_ratio"]):
            if "@" in v:
                continue
            print(f"  {v:<22} {x['beats']:>2}/{x['loses']:>2} of {x['of']:<2} geo {x['geo_mean_ratio']:.3f} "
                  f"dd {x['mean_dd']} (live {x['live_mean_dd']})")
    print("\n== finals / trades per dataset")
    show = [BASELINE, "no_chop_day", "with_trend_day", "with_trend_hour", "trend_only_day", "veto_20d_hour",
            "override_hour", "rstop_max_0.2", "rstop_max_0.4", "no_break", "wk_befees_t1"]
    for ds, by_v in rep["results"].items():
        print(f"  {ds:<28} " + " ".join(f"{v}={by_v[v]['final_equity']:.0f}/{by_v[v]['trades']}" for v in show))
    print("\n== regime delta (Brent pooled: d trades, d sum R)")
    for v, x in rep["regime_delta"].items():
        print(f"  {v:<22} " + " ".join(f"{lab}: {y['d_trades']:+d} {y['d_sum_r']:+.1f}" for lab, y in x.items()))
    print("\n== verdicts (Brent)")
    for v, x in rep["verdicts"].items():
        print(f"  {v:<22} {json.dumps(x)}")
    print("\n== 11-02 long")
    for v, x in rep["nov2"].items():
        print(f"  {v:<22} {json.dumps(x)}")
    print("\nadopt:", rep["adopt"])


if __name__ == "__main__":
    raise SystemExit(main())
