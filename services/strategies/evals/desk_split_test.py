"""Desk split backtest, 2026-11-02: oil/silver 100/0 to 0/100 on one account.

Operator, 2026-11-02: "what will be our success if we split the portfolio
50/50 oil and silver? seems like our oil run lowkey ended, it was fire for
like a week but it fell off".

Both desks run on one 15-minute clock over the same window
(`services.strategies.backtest.portfolio`), each sized off its share of the
WHOLE account's marked equity at every entry, as live (`_desk_slice`:
desk_equity_share x account equity). Live rules otherwise: market entry off
the 24h range, 2R gate, target 0.5% inside the far 24h edge, limit target,
fixed stop, 20x; Brent stop 0.75 beyond the edge with size slippage and
funding; silver max(1.3 x ATR14_1h, 0.8% of the mid) with its funding.
Two sizing tables: the live 15% risk cap (`risk_cap_frac` 0.15, a stop costs
at most 15% of the desk's slice) and no cap (full port, the rule before
2026-11-02; a 100% cap never binds at 20x, so it is the same thing).

Splits (oil/silver): 100/0, 90/10 (live), 70/30, 50/50, 30/70, 0/100.

Datasets ($225 start, the account today), both desks on the same window:
  journal_shared   live oil + metals journals (READ ONLY) from the first
                   silver thesis (2026-10-31) to the data end    scored
  journal_full     the same from the first oil thesis; silver has no
                   calls before 10-31, so it only sits idle      context
  replay           the replayed Brent and silver theses over their shared
                   window (2026-10-01 to 10-30)                  scored
  <ctrl>_replay    always_long / always_short / daily_sign per coin on the
                   replay window, both desks the same control    scored
  <ctrl>_30d       the same on the last 30 days                  scored

Verdict rules: a split is recommended only if it beats 90/10 on final
equity on most scored datasets, its neighbours on the split grid also do
(a plateau; a neighbour that is 90/10 itself does not count, so 100/0 can
never be one), and its mean max drawdown is no worse than 90/10's.

Also: each desk's weekly P&L over the last 6 weeks (standalone, 100% of the
start, cap on) next to the real fills; Brent's regime by week (efficiency
ratio, ATR, 24h-range breaks, 20-day trend) from 1h and 1d candles; the
real since-10-31 fills rescaled to each split (linear, no compounding).

    python -m services.strategies.evals.desk_split_test \\
        --oil-theses-dir <pathia>/.state/strategies/oil/theses \\
        --metals-theses-dir <pathia>/.state/strategies/metals/theses

Writes /tmp/desk-split/report.json, splits.csv, weekly.csv, regime.csv and
progress.log. Public candles and funding through the eval disk cache, read
only; no LLM calls; never touches the account, the config or the journals.
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

from pathiel.models.types import Candle

from services.strategies.backtest import data as D
from services.strategies.backtest.desk_tick_sim import Params, simulate
from services.strategies.backtest.portfolio import Desk, portfolio_stats, simulate_portfolio
from services.strategies.evals import brent_stop_buffer_test as B
from services.strategies.evals.desk_tick_backtest import DAY_MS, HOUR_MS, journaled_direction

START = 225.0
FIFTEEN_MS = 900_000
WEEK_MS = 7 * DAY_MS
LIVE = "90/10"
SPLITS: Tuple[Tuple[str, float, float], ...] = (
    ("100/0", 1.0, 0.0), ("90/10", 0.9, 0.1), ("70/30", 0.7, 0.3),
    ("50/50", 0.5, 0.5), ("30/70", 0.3, 0.7), ("0/100", 0.0, 1.0))
SPLIT_ORDER = [s[0] for s in SPLITS]
CAPS: Dict[str, Optional[float]] = {"cap15": 0.15, "nocap": None}
CONTROLS = ("always_long", "always_short", "daily_sign")
WEEKS = 6
# Monday 1970-01-05 00:00 UTC is day 4: UTC weeks start on Monday.
_MONDAY_OFFSET = 4 * DAY_MS

# The real closed trades (net, fees in), rebuilt from the wallet's fills by
# loss_audit.py (LOSS_AUDIT_2026-11-02.md, /tmp/loss-audit/trades.csv):
# (desk, entry, exit, net $, the desk's share when it opened).
REAL_TRADES: Tuple[Tuple[str, str, str, float, float], ...] = (
    ("oil", "2026-10-22 16:13", "2026-10-22 17:44", 1.05, 1.0),
    ("oil", "2026-10-23 07:34", "2026-10-23 15:43", 3.81, 1.0),
    ("oil", "2026-10-23 16:34", "2026-10-25 13:36", -6.04, 1.0),
    ("oil", "2026-10-26 01:10", "2026-10-26 02:56", -5.81, 1.0),
    ("oil", "2026-10-26 03:13", "2026-10-26 15:50", 19.86, 1.0),
    ("oil", "2026-10-27 14:33", "2026-10-30 01:40", 49.02, 1.0),
    ("oil", "2026-10-31 16:18", "2026-11-01 18:18", 7.83, 0.9),
    ("silver", "2026-11-02 00:51", "2026-11-02 01:05", -1.82, 0.1),
    ("silver", "2026-11-02 01:18", "2026-11-02 05:30", -2.56, 0.1),
    ("oil", "2026-11-02 03:50", "2026-11-02 08:35", -33.87, 0.9),
)


def _ms(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp() * 1000)


def _iso(ms: Optional[int]) -> Optional[str]:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%MZ")


def week_start(t: int) -> int:
    """The Monday 00:00 UTC that starts `t`'s week."""
    return t - (t - _MONDAY_OFFSET) % WEEK_MS


# ── scoring (pure) ─────────────────────────────────────────────────────────

def score_vs_live(rows: Dict[str, Dict[str, Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    """{split: beats/loses/of/geo_mean_ratio vs 90/10} over `rows`
    ({dataset: {split: stats}}), on final equity."""
    return B.score_vs_live(rows, LIVE)


def _good(scores: Dict[str, Dict[str, Any]], name: str) -> bool:
    s = scores.get(name)
    return bool(s) and s["beats"] > s["of"] / 2.0 and s["geo_mean_ratio"] >= 1.0


def plateau(scores: Dict[str, Dict[str, Any]], name: str, order: Sequence[str] = SPLIT_ORDER) -> bool:
    """Every neighbour of `name` on the split grid that is not 90/10 itself
    also beats 90/10 on most datasets with a geo-mean of 1 or more; at least
    one such neighbour must exist."""
    if name not in order:
        return False
    i = order.index(name)
    nbrs = [order[j] for j in (i - 1, i + 1) if 0 <= j < len(order) and order[j] != LIVE]
    return bool(nbrs) and all(_good(scores, n) for n in nbrs)


def mean_dd(rows: Dict[str, Dict[str, Dict[str, Any]]], name: str) -> Optional[float]:
    xs = [by[name]["max_dd_pct"] for by in rows.values() if name in by]
    return round(sum(xs) / len(xs), 1) if xs else None


def verdicts(rows: Dict[str, Dict[str, Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    """The three rules per split, against 90/10."""
    scores = score_vs_live(rows)
    live_dd = mean_dd(rows, LIVE)
    out: Dict[str, Dict[str, Any]] = {}
    for name in SPLIT_ORDER:
        if name == LIVE:
            continue
        s = scores[name]
        dd = mean_dd(rows, name)
        most = s["beats"] > s["of"] / 2.0
        plat = plateau(scores, name)
        dd_ok = dd is not None and live_dd is not None and dd <= live_dd + 1e-9
        fails = [f for f, ok in (("beats on most", most), ("plateau", plat), ("drawdown", dd_ok)) if not ok]
        out[name] = {**s, "mean_dd": dd, "live_mean_dd": live_dd, "most": most, "plateau": plat,
                     "dd_ok": dd_ok, "adopt": not fails, "fails": fails}
    return out


def leave_one_out(rows: Dict[str, Dict[str, Dict[str, Any]]]) -> Dict[str, Dict[str, int]]:
    """How fragile each verdict is: drop each scored dataset in turn and
    count the drops under which the split still clears all three rules."""
    out = {n: {"held": 0, "of": len(rows)} for n in SPLIT_ORDER if n != LIVE}
    for ds in rows:
        sub = {k: v for k, v in rows.items() if k != ds}
        for n, v in verdicts(sub).items():
            out[n]["held"] += v["adopt"]
    return out


# always_long and always_short are mirror images on two coins that trended
# opposite ways in October (Brent up, silver down): one always favours the
# oil-heavy splits and the other the silver-heavy ones. These are the
# datasets left once that pair is set aside.
NON_MIRROR = ("journal_shared", "replay", "daily_sign_replay", "daily_sign_30d")


def non_mirror(rows: Dict[str, Dict[str, Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    """score_vs_live on the NON_MIRROR datasets only."""
    sub = {k: v for k, v in rows.items() if k in NON_MIRROR}
    return score_vs_live(sub) if sub else {}


def pooled(rows: Dict[str, Dict[str, Dict[str, Any]]], name: str) -> Dict[str, Any]:
    """Across datasets for one split: median final, worst day, trades, pooled win rate, mean correlation."""
    xs = [by[name] for by in rows.values() if name in by]
    finals = sorted(x["final_equity"] for x in xs)
    n = len(finals)
    med = finals[n // 2] if n % 2 else 0.5 * (finals[n // 2 - 1] + finals[n // 2]) if n else None
    trades = sum(x["trades"] for x in xs)
    wins = sum(round((x["win_rate"] or 0) * x["trades"]) for x in xs)
    cors = [x["corr"] for x in xs if x["corr"] is not None]
    return {"median_final": round(med, 2) if med is not None else None,
            "worst_day_pct": min((x["worst_day_pct"] for x in xs), default=None),
            "max_dd_worst": max((x["max_dd_pct"] for x in xs), default=None),
            "trades": trades, "win_rate": round(wins / trades, 3) if trades else None,
            "corr_mean": round(sum(cors) / len(cors), 2) if cors else None}


# ── real fills, rescaled (pure) ────────────────────────────────────────────

def real_by_split(trades: Sequence[Tuple[str, str, str, float, float]] = REAL_TRADES,
                  since: str = "2026-10-31 00:00") -> Dict[str, float]:
    """The real closed trades opened since `since`, each net scaled by the
    split's share over the share it actually traded at (linear in size:
    no compounding, no margin cap)."""
    out = {}
    for name, oil, silver in SPLITS:
        share = {"oil": oil, "silver": silver}
        out[name] = round(sum(net * share[desk] / was for desk, ent, _, net, was in trades
                              if ent >= since), 2)
    return out


def real_weekly(trades: Sequence[Tuple[str, str, str, float, float]] = REAL_TRADES) -> Dict[str, Dict[int, float]]:
    """{desk: {week start ms: net $ of the trades that closed that week}}."""
    out: Dict[str, Dict[int, float]] = {"oil": {}, "silver": {}}
    for desk, _, ex, net, _ in trades:
        w = week_start(_ms(ex))
        out[desk][w] = round(out[desk].get(w, 0.0) + net, 2)
    return out


# ── weekly P&L and Brent's regime (pure) ───────────────────────────────────

def weekly_returns(curve: Sequence[Tuple[int, float]], start_equity: float, weeks: Sequence[int]) -> Dict[int, Optional[float]]:
    """% change of the marked equity over each week in `weeks` (Monday
    00:00 UTC starts). The week's opening value is the last mark before it
    (the start equity if the curve begins inside or after it). None for a
    week the curve does not reach."""
    pts = sorted(curve)
    out: Dict[int, Optional[float]] = {}
    for w in weeks:
        before = [m for t, m in pts if t < w]
        inside = [m for t, m in pts if w <= t < w + WEEK_MS]
        if not inside:
            out[w] = None
            continue
        open_ = before[-1] if before else start_equity
        out[w] = round(100.0 * (inside[-1] / open_ - 1.0), 1) if open_ > 0 else None
    return out


def regime_by_week(c1h: Sequence[Candle], c1d: Sequence[Candle], weeks: Sequence[int]) -> List[Dict[str, Any]]:
    """Per week: close-to-close %, the efficiency ratio of 1h closes (net
    move over the sum of hourly moves: 1 is a straight line, near 0 is
    chop), mean 1h ATR14 as % of price, 1h bars that broke the prior 24h
    high / low, mean 1h range (high-low) of the week's 24h windows as %, and
    the 20-day trend at the week's last daily close (return over 20 days and
    the close vs its 20-day mean)."""
    bars = sorted(c1h, key=lambda c: c.t)
    days = sorted(c1d, key=lambda c: c.t)
    trs = [None] + [max(b.h - b.l, abs(b.h - a.c), abs(b.l - a.c)) for a, b in zip(bars, bars[1:])]
    out = []
    for w in weeks:
        idx = [i for i, b in enumerate(bars) if w <= b.t < w + WEEK_MS]
        if not idx:
            continue
        i0, i1 = idx[0], idx[-1]
        prev_close = bars[i0 - 1].c if i0 > 0 else bars[i0].o
        net = bars[i1].c - prev_close
        path = sum(abs(bars[i].c - (bars[i - 1].c if i > 0 else bars[i].o)) for i in idx)
        atrs = []
        for i in idx:
            if i >= 14 and all(trs[j] is not None for j in range(i - 13, i + 1)):
                atrs.append(sum(trs[i - 13:i + 1]) / 14.0 / bars[i].c)
        up = dn = 0
        rng = []
        for i in idx:
            if i < 24:
                continue
            prior = bars[i - 24:i]
            hi, lo = max(c.h for c in prior), min(c.l for c in prior)
            up += bars[i].h > hi
            dn += bars[i].l < lo
            rng.append((hi - lo) / bars[i].o)
        dd = [d for d in days if d.t < w + WEEK_MS]
        t20 = s20 = None
        if len(dd) >= 21:
            last = dd[-1].c
            t20 = round(100.0 * (last / dd[-21].c - 1.0), 1)
            sma = sum(d.c for d in dd[-20:]) / 20.0
            s20 = round(100.0 * (last / sma - 1.0), 1)
        out.append({"week": _iso(w)[:10], "close_chg_pct": round(100.0 * net / prev_close, 2),
                    "efficiency": round(abs(net) / path, 2) if path > 0 else None,
                    "atr_pct": round(100.0 * sum(atrs) / len(atrs), 3) if atrs else None,
                    "range24_pct": round(100.0 * sum(rng) / len(rng), 2) if rng else None,
                    "breaks_up": up, "breaks_down": dn, "hours": len(idx),
                    "trend20_pct": t20, "vs_sma20_pct": s20})
    return out


# ── runs ───────────────────────────────────────────────────────────────────

def clip(c15: Sequence[Candle], c1h: Sequence[Candle], start: int, end: int) -> Tuple[List[Candle], List[Candle]]:
    return [c for c in c15 if start <= c.t < end], [c for c in c1h if c.t < end]


def run_split(oil: Tuple, silver: Tuple, oil_share: float, start: int, end: int) -> Dict[str, Any]:
    """One split on one window. `oil` / `silver`: (c15, c1h, direction_at, params, funding)."""
    desks = []
    for name, share, (c15, c1h, d_at, p, f) in (("oil", oil_share, oil), ("silver", round(1.0 - oil_share, 10), silver)):
        w15, w1h = clip(c15, c1h, start, end)
        desks.append(Desk(name, share, w15, w1h, d_at, p, f))
    return portfolio_stats(simulate_portfolio(desks, START))


def parity(desk: Tuple, start: int, end: int, name: str = "oil") -> bool:
    """A one-desk split (100/0, 0/100) equals that desk's single sim on this
    window, byte for byte (repr of the Run, and the account curve)."""
    c15, c1h, d_at, p, f = desk
    w15, w1h = clip(c15, c1h, start, end)
    single = simulate(w15, w1h, d_at, dataclasses.replace(p, starting_equity=START), f)
    other = "silver" if name == "oil" else "oil"
    prun = simulate_portfolio([Desk(name, 1.0, w15, w1h, d_at, p, f), Desk(other, 0.0, [], [], d_at, p, f)], START)
    return repr(prun.runs[name]) == repr(single) and prun.equity_curve == single.equity_curve


def standalone_curve(desk: Tuple, start: int, end: int) -> List[Tuple[int, float]]:
    c15, c1h, d_at, p, f = desk
    w15, w1h = clip(c15, c1h, start, end)
    return simulate(w15, w1h, d_at, dataclasses.replace(p, starting_equity=START), f).equity_curve


SPLIT_FIELDS = ["cap", "dataset", "scored", "window", "split", "final_equity", "return_pct", "max_dd_pct",
                "worst_day_pct", "trades", "topped_up", "win_rate", "corr", "oil_pnl", "silver_pnl", "oil_trades",
                "silver_trades", "liquidated", "ruined", "open_at_end"]


def main(argv: Optional[List[str]] = None) -> int:
    from services.strategies.evals.brent_stop_buffer_test import SILVER_LIVE
    from services.strategies.evals.cross_market_backtest import mean_atr
    from services.strategies.evals.market_scout import direction_series, load_coin_candles, scouting_params
    from services.strategies.evals.stop_cap_test import closed_only, same_market
    from services.strategies.evals.strategy_sweep import base_params

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--oil-theses-dir", default=os.environ.get("PATHIEL_OIL_THESES_DIR", ""))
    ap.add_argument("--metals-theses-dir", default=os.environ.get("PATHIEL_METALS_THESES_DIR", ""))
    ap.add_argument("--oil-replay-dir", default="/tmp/thesis-replay-30b/oil/theses")
    ap.add_argument("--silver-replay-dir", default="/tmp/thesis-replay-silver/theses")
    ap.add_argument("--silver-max-leverage", type=float, default=25.0)
    ap.add_argument("--cache-dir", default="/tmp/desk-split/cache")
    ap.add_argument("--out-dir", default="/tmp/desk-split")
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    t0 = time.time()
    log_path = os.path.join(a.out_dir, "progress.log")

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} desk-split: {msg}"
        print(line, flush=True)
        with open(log_path, "a") as fh:
            fh.write(line + "\n")

    def fresh(coin: str) -> Dict[str, List[Candle]]:
        got = load_coin_candles(coin, a.cache_dir)
        asof = int(os.path.getmtime(D._cache_path(a.cache_dir, coin, "candles_15m_4820")) * 1000)
        return {"c15": closed_only(got["c15"], FIFTEEN_MS, asof), "c1h": closed_only(got["c1h"], HOUR_MS, asof),
                "c1d": closed_only(got["c1d"], DAY_MS, asof)}

    # ── data
    bc, sc = fresh("xyz:BRENTOIL"), fresh("xyz:SILVER")
    b_end, s_end = bc["c15"][-1].t + FIFTEEN_MS, sc["c15"][-1].t + FIFTEEN_MS
    end = min(b_end, s_end)
    b_fund = D.fetch_funding_cached("xyz:BRENTOIL", bc["c15"][0].t, b_end, a.cache_dir)
    s_fund = D.fetch_funding_cached("xyz:SILVER", sc["c15"][0].t, s_end, a.cache_dir)
    atr, _ = mean_atr(sc["c1h"])
    base_s = dataclasses.replace(scouting_params(a.silver_max_leverage, atr), size_slippage=False, **SILVER_LIVE)
    params = {cap: (dataclasses.replace(base_params(), risk_cap_frac=frac),
                    dataclasses.replace(base_s, risk_cap_frac=frac)) for cap, frac in CAPS.items()}

    oil_j = D.load_journaled_theses(a.oil_theses_dir) if a.oil_theses_dir else []
    met_j = D.load_journaled_theses(a.metals_theses_dir) if a.metals_theses_dir else []
    met_j = same_market(met_j, sc["c1h"]) if met_j else []
    oil_r = D.load_journaled_theses(a.oil_replay_dir)
    sil_r = D.load_journaled_theses(a.silver_replay_dir)

    # dataset -> (oil direction, silver direction, start, end, scored)
    sets: Dict[str, Tuple[Callable, Callable, int, int, bool]] = {}
    if oil_j and met_j:
        sets["journal_shared"] = (journaled_direction(oil_j), journaled_direction(met_j),
                                  min(th.created_at_ms for th in met_j), end, True)
        sets["journal_full"] = (journaled_direction(oil_j), journaled_direction(met_j),
                                min(th.created_at_ms for th in oil_j), end, False)
    rep_window = None
    if oil_r and sil_r:
        rs = max(min(th.created_at_ms for th in oil_r), min(th.created_at_ms for th in sil_r))
        re_ = min(end, max(th.created_at_ms for th in oil_r) + DAY_MS, max(th.created_at_ms for th in sil_r) + DAY_MS)
        rep_window = (rs, re_)
        sets["replay"] = (journaled_direction(oil_r), journaled_direction(sil_r), rs, re_, True)
    for s in CONTROLS:
        bd, sd = direction_series(s, bc["c1h"]), direction_series(s, sc["c1h"])
        if rep_window:
            sets[f"{s}_replay"] = (bd, sd, rep_window[0], rep_window[1], True)
        sets[f"{s}_30d"] = (bd, sd, end - 30 * DAY_MS, end, True)
    log(f"brent to {_iso(b_end)}, silver to {_iso(s_end)}; datasets {list(sets)}")

    def desk_tuple(coin_c: Dict[str, List[Candle]], d_at: Callable, p: Params, fund) -> Tuple:
        return (coin_c["c15"], coin_c["c1h"], d_at, p, fund)

    # ── the split grid
    rows: List[Dict[str, Any]] = []
    tables: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]] = {cap: {} for cap in CAPS}
    context: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]] = {cap: {} for cap in CAPS}
    checks: Dict[str, bool] = {}
    for cap, (bp, sp) in params.items():
        for ds, (bd, sd, st, en, scored) in sets.items():
            oil, silver = desk_tuple(bc, bd, bp, b_fund), desk_tuple(sc, sd, sp, s_fund)
            by: Dict[str, Dict[str, Any]] = {}
            for name, oil_share, _ in SPLITS:
                by[name] = run_split(oil, silver, oil_share, st, en)
                x = by[name]
                rows.append({"cap": cap, "dataset": ds, "scored": scored, "window": f"{_iso(st)} to {_iso(en)}",
                             "split": name, **{k: x[k] for k in ("final_equity", "return_pct", "max_dd_pct",
                                                                 "worst_day_pct", "trades", "topped_up", "win_rate",
                                                                 "corr")},
                             "oil_pnl": x["desk_pnl"].get("oil"), "silver_pnl": x["desk_pnl"].get("silver"),
                             "oil_trades": x["desk_trades"].get("oil"), "silver_trades": x["desk_trades"].get("silver"),
                             "liquidated": ";".join(x["liquidated"]), "ruined": x["ruined"],
                             "open_at_end": ";".join(x["open_at_end"])})
            (tables if scored else context)[cap][ds] = by
            checks[f"{cap}:{ds}:oil"] = parity(oil, st, en, "oil")
            checks[f"{cap}:{ds}:silver"] = parity(silver, st, en, "silver")
        log(f"{cap}: {len(sets)} datasets x {len(SPLITS)} splits ({time.time() - t0:.0f}s)")

    results = {cap: {"verdicts": verdicts(tables[cap]), "leave_one_out": leave_one_out(tables[cap]),
                     "non_mirror": non_mirror(tables[cap]),
                     "pooled": {n: pooled(tables[cap], n) for n in SPLIT_ORDER},
                     "finals": {ds: {n: by[n]["final_equity"] for n in SPLIT_ORDER} for ds, by in tables[cap].items()},
                     "context": {ds: {n: by[n]["final_equity"] for n in SPLIT_ORDER} for ds, by in context[cap].items()}}
               for cap in CAPS}

    # ── weekly per desk, standalone at 100% (cap on), and Brent's regime
    w_last = week_start(end - 1)
    weeks = [w_last - i * WEEK_MS for i in range(WEEKS - 1, -1, -1)]
    bp, sp = params["cap15"]
    weekly_start = weeks[0]
    series: Dict[str, Dict[str, Dict[int, Optional[float]]]] = {"oil": {}, "silver": {}}
    for desk, coin_c, p, fund, j, r in (("oil", bc, bp, b_fund, oil_j, oil_r), ("silver", sc, sp, s_fund, met_j, sil_r)):
        if j:
            st = max(weekly_start, min(th.created_at_ms for th in j))
            series[desk]["journal"] = weekly_returns(
                standalone_curve(desk_tuple(coin_c, journaled_direction(j), p, fund), st, end), START, weeks)
        if r:
            st = max(weekly_start, min(th.created_at_ms for th in r))
            en = min(end, max(th.created_at_ms for th in r) + DAY_MS)
            series[desk]["replay"] = weekly_returns(
                standalone_curve(desk_tuple(coin_c, journaled_direction(r), p, fund), st, en), START, weeks)
        for s in CONTROLS:
            series[desk][s] = weekly_returns(
                standalone_curve(desk_tuple(coin_c, direction_series(s, coin_c["c1h"]), p, fund), weekly_start, end),
                START, weeks)
    real_w = real_weekly()
    weekly_rows = []
    for w in weeks:
        row: Dict[str, Any] = {"week": _iso(w)[:10]}
        for desk in ("oil", "silver"):
            row[f"{desk}_real_usd"] = real_w[desk].get(w)
            for k, v in series[desk].items():
                row[f"{desk}_{k}_pct"] = v.get(w)
        weekly_rows.append(row)
    regime = regime_by_week(bc["c1h"], bc["c1d"], weeks)
    regime_silver = regime_by_week(sc["c1h"], sc["c1d"], weeks)

    # ── write
    with open(os.path.join(a.out_dir, "splits.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SPLIT_FIELDS)
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(a.out_dir, "weekly.csv"), "w", newline="") as fh:
        fields = sorted({k for r in weekly_rows for k in r}, key=lambda k: (k != "week", k))
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(weekly_rows)
    with open(os.path.join(a.out_dir, "regime.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["coin"] + list(regime[0]))
        w.writeheader()
        w.writerows([{"coin": "xyz:BRENTOIL", **r} for r in regime] + [{"coin": "xyz:SILVER", **r} for r in regime_silver])
    report = {"generated_at": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()), "seconds": round(time.time() - t0, 1),
              "start_equity": START, "brent_data_end": _iso(b_end), "silver_data_end": _iso(s_end),
              "windows": {ds: [_iso(st), _iso(en), scored] for ds, (_, _, st, en, scored) in sets.items()},
              "theses": {"oil_journal": len(oil_j), "silver_journal": len(met_j), "oil_replay": len(oil_r),
                         "silver_replay": len(sil_r)},
              "parity_100_0": checks, "results": results, "real_since_10_31": real_by_split(),
              "weekly": weekly_rows, "regime_brent": regime, "regime_silver": regime_silver}
    with open(os.path.join(a.out_dir, "report.json"), "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    _print(report)
    print(f"\n{a.out_dir}/report.json\n{a.out_dir}/splits.csv\n{a.out_dir}/weekly.csv\n{a.out_dir}/regime.csv")
    return 0 if all(checks.values()) else 1


def _print(rep: Dict[str, Any]) -> None:
    print(f"brent to {rep['brent_data_end']}, silver to {rep['silver_data_end']}, start ${rep['start_equity']:.0f}")
    print("windows:", json.dumps(rep["windows"]))
    print("theses:", json.dumps(rep["theses"]), " parity 100/0:", all(rep["parity_100_0"].values()))
    for cap, r in rep["results"].items():
        print(f"\n== {cap}: final equity by dataset")
        print("  " + f"{'dataset':<20}" + "".join(f"{n:>9}" for n in SPLIT_ORDER))
        for ds, by in {**r["finals"], **{f"({k})": v for k, v in r["context"].items()}}.items():
            print("  " + f"{ds:<20}" + "".join(f"{by[n]:>9.2f}" for n in SPLIT_ORDER))
        print(f"  {'split':<8}{'beats':>7}{'geo':>8}{'meanDD':>8}{'worstDD':>8}{'median$':>9}{'worstDay':>9}"
              f"{'trades':>7}{'win':>6}{'corr':>6}  verdict")
        print("  leave-one-out (drops under which it still clears):",
              json.dumps({n: f"{x['held']}/{x['of']}" for n, x in r["leave_one_out"].items()}))
        print("  non-mirror datasets:", json.dumps({n: f"{x['beats']}/{x['of']} geo {x['geo_mean_ratio']}"
                                                    for n, x in r["non_mirror"].items()}))
        for n in SPLIT_ORDER:
            p = r["pooled"][n]
            v = r["verdicts"].get(n)
            vs = (f"{v['beats']}/{v['of']}", f"{v['geo_mean_ratio']:.3f}", v["mean_dd"],
                  "ADOPT" if v["adopt"] else "no: " + ", ".join(v["fails"])) if v else ("live", "1.000", None, "")
            dd = vs[2] if vs[2] is not None else (r["verdicts"][SPLIT_ORDER[0]]["live_mean_dd"])
            print(f"  {n:<8}{vs[0]:>7}{vs[1]:>8}{dd:>8}{p['max_dd_worst']:>8}{p['median_final']:>9}"
                  f"{p['worst_day_pct']:>9}{p['trades']:>7}{p['win_rate']!s:>6}{p['corr_mean']!s:>6}  {vs[3]}")
    print("\nreal fills since 10-31, rescaled:", json.dumps(rep["real_since_10_31"]))
    print("\nweekly (standalone desk, % of week open; real in $):")
    for row in rep["weekly"]:
        print("  ", json.dumps(row))
    print("\nBrent regime by week:")
    for row in rep["regime_brent"]:
        print("  ", json.dumps(row))
    print("Silver regime by week:")
    for row in rep["regime_silver"]:
        print("  ", json.dumps(row))


if __name__ == "__main__":
    raise SystemExit(main())
