#!/usr/bin/env python
"""W-WH2: backtest the 0xe282 no-stop ladder, exactly as pre-registered in
findings/W-WH2_scale_in_ladder.md. Reads W-WH2_cache_daily.json and the frozen
calibration in W-WH1_results.json; no network. Writes W-WH2_results.json.

`simulate` is pure and is what tests/test_wh_ladder.py drives.
"""
from __future__ import annotations

import json
import math
import os
import random
import sys
import time
from datetime import datetime, timezone
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
DAY = 86_400_000
LOOKBACK = 30
FEE = 0.00125                 # per side, 25 bps round trip
FUNDING_PER_DAY = 1.25e-5 * 24
N_NULL = 2000
ALPHA = 0.05 / 4

UNIVERSES = {
    "A_majors": ["BTC", "ETH", "SOL", "BNB", "XRP"],
    "B_wallet": ["AAVE", "CRV", "GMX", "HYPE", "LINK", "NEAR", "ONDO",
                 "RENDER", "RUNE", "UNI", "VVV", "WLD", "ZEC"],
}
LEVERAGES = [1, 3]


# Frozen 2026-09-13 from W-WH1_results.json["calibration"] (fills through
# 2026-09-11). Literals, not a read of that file: the wallet keeps trading, and a
# re-fetch would move its medians and silently re-register the test.
FROZEN = {"trigger_dd": -0.211, "k": 5, "gap": -0.0717, "tp": 0.0514}


def frozen_params() -> dict:
    return dict(FROZEN)


def calibration_drift() -> dict:
    """How far W-WH1's current calibration has moved from the frozen literals."""
    with open(os.path.join(HERE, "W-WH1_results.json")) as f:
        c = json.load(f)["calibration"]
    now = {"trigger_dd": round(c["trigger_dd_median"], 3), "k": int(c["rungs_median"]),
           "gap": round(c["ladder_gap_from_span"], 4), "tp": round(c["tp_median_wins"], 4)}
    return {k: (FROZEN[k], now[k]) for k in FROZEN if FROZEN[k] != now[k]}


def align(candles: dict[str, list], coins: list[str]) -> tuple[list[int], dict]:
    """Union daily calendar; per coin, bars keyed by index with a trigger flag.

    The trigger at bar i uses only bars i-30..i-1 highs and bar i's close.
    """
    days = sorted({b[0] for c in coins for b in candles[c]})
    idx = {t: i for i, t in enumerate(days)}
    series = {}
    for c in coins:
        bars = candles[c]
        rows = [None] * len(days)
        for j, (t, o, h, l, cl) in enumerate(bars):
            prior_high = max(b[2] for b in bars[j - LOOKBACK:j]) if j >= LOOKBACK else None
            rows[idx[t]] = (o, h, l, cl, prior_high)
        series[c] = rows
    return days, series


def simulate(days, series, max_lev, params, lev, start=0, end=None,
             entry_prob=None, rng=None, record=False, stop_frac=None):
    """Run the cross-margined ladder machine over days[start:end].

    entry_prob None -> the pre-registered drawdown trigger.
    entry_prob {coin: p} -> matched null: enter a flat coin with probability p.
    stop_frac None -> no stop, as pre-registered. A float is NOT the tested
    rule: it models the executor's mandatory backup stop (60% under entry at
    1x, book_params.BACKUP_SL_MAX_FRAC_OF_LIQ) to ask whether Pathiel can run
    the rule as tested. Checked on the low after that bar's adds, before TP.
    """
    end = len(days) if end is None else end
    coins = list(series)
    n = len(coins)
    k, gap, tp, dd = params["k"], params["gap"], params["tp"], params["trigger_dd"]
    cash = 1.0
    pos = {c: 0.0 for c in coins}
    cost = {c: 0.0 for c in coins}
    rungs = {c: 0 for c in coins}
    next_px = {c: 0.0 for c in coins}
    rung_ntl = {c: 0.0 for c in coins}
    pending = {c: 0.0 for c in coins}          # rung notional to buy at next open
    open_meta = {}
    flat_days = {c: 0 for c in coins}
    ladders = []
    curve = []
    peak, max_dd, liquidated = 1.0, 0.0, None
    last_close = {}

    for i in range(start, end):
        added = set()
        entered = set()
        # 1. first rungs at the open
        for c in coins:
            bar = series[c][i]
            if pending[c] and bar is not None:
                o = bar[0]
                u = pending[c] / o
                pos[c], cost[c] = u, u * o
                cash -= pending[c] * FEE
                rungs[c], next_px[c], rung_ntl[c] = 1, o * (1 + gap), pending[c]
                open_meta[c] = {"coin": c, "open": days[i], "equity_start": pending[c] * k * n / lev}
                entered.add(c)
            pending[c] = 0.0
        # 2. take profit at the open, adds down to the low
        exits = {}
        for c in coins:
            bar = series[c][i]
            if bar is None or pos[c] <= 0:
                continue
            o, h, l, cl, _ = bar
            if c not in entered:
                target = cost[c] / pos[c] * (1 + tp)
                if o >= target:
                    exits[c] = o
                    continue
            while rungs[c] < k and l <= next_px[c]:
                px = min(o, next_px[c])
                pos[c] += rung_ntl[c] / px
                cost[c] += rung_ntl[c]
                cash -= rung_ntl[c] * FEE
                rungs[c] += 1
                next_px[c] *= 1 + gap
                added.add(c)
            if stop_frac is not None:
                stop_px = cost[c] / pos[c] * (1 - stop_frac)
                if l <= stop_px:
                    exits[c] = min(o, stop_px)
                    continue
            if c not in entered and c not in added:
                target = cost[c] / pos[c] * (1 + tp)
                if h >= target:
                    exits[c] = target
        # 3. liquidation on every coin's low at once, before any exit books
        eq_low = cash
        maint = 0.0
        for c in coins:
            bar = series[c][i]
            if pos[c] > 0 and bar is not None:
                eq_low += pos[c] * bar[2] - cost[c]
                maint += pos[c] * bar[2] / (2 * max_lev[c])
            elif pos[c] > 0:
                eq_low += pos[c] * last_close[c] - cost[c]
        if eq_low <= maint and any(pos[c] > 0 for c in coins):
            liquidated = days[i]
            for c in coins:
                if pos[c] > 0:
                    ladders.append(dict(open_meta[c], close=days[i], rungs=rungs[c],
                                        result="liquidated", pnl=None))
            curve.append((days[i], 0.0))
            max_dd = -1.0
            cash = 0.0
            break
        # 4. book exits
        for c, px in exits.items():
            pnl = pos[c] * px - cost[c] - pos[c] * px * FEE
            cash += pnl
            ladders.append(dict(open_meta.pop(c), close=days[i], rungs=rungs[c],
                                result="tp" if pnl > 0 else "stop", pnl=pnl))
            pos[c] = cost[c] = 0.0
            rungs[c] = 0
        # 5. funding at the close, mark to market
        equity = cash
        for c in coins:
            bar = series[c][i]
            if bar is not None:
                last_close[c] = bar[3]
            if pos[c] > 0:
                cash -= pos[c] * last_close[c] * FUNDING_PER_DAY
                equity -= pos[c] * last_close[c] * FUNDING_PER_DAY
                equity += pos[c] * last_close[c] - cost[c]
        if record:
            curve.append((days[i], equity))
        peak = max(peak, equity)
        max_dd = min(max_dd, equity / peak - 1)
        # 6. triggers for flat coins, filled next open
        if equity <= 0:
            continue
        for c in coins:
            bar = series[c][i]
            if bar is None or pos[c] > 0 or bar[4] is None or i + 1 >= end:
                continue
            flat_days[c] += 1
            if entry_prob is None:
                fire = bar[3] <= (1 + dd) * bar[4]
            else:
                fire = rng.random() < entry_prob[c]
            if fire:
                pending[c] = lev * equity / (k * n)

    if liquidated is None:
        equity = cash + sum(pos[c] * last_close[c] - cost[c] for c in coins if pos[c] > 0)
        for c in coins:
            if pos[c] > 0:
                ladders.append(dict(open_meta[c], close=None, rungs=rungs[c], result="open",
                                    pnl=pos[c] * last_close[c] - cost[c]))
    else:
        equity = 0.0
    return {"terminal": equity, "max_dd": max_dd, "liquidated": liquidated,
            "ladders": ladders, "flat_days": flat_days, "curve": curve}


def buy_and_hold(days, series, start, end):
    coins = list(series)
    total = 0.0
    for c in coins:
        rows = [(i, series[c][i]) for i in range(start, end) if series[c][i] is not None]
        rows = [(i, b) for i, b in rows if b[4] is not None]
        if len(rows) < 2:
            total += 1 / len(coins)
            continue
        entry, last = rows[0][1][0], rows[-1][1][3]
        days_held = rows[-1][0] - rows[0][0]
        # perp at 1x: price return, both fees, funding on a notional that drifts with price
        avg_px = sum(b[3] for _, b in rows) / len(rows)
        r = last / entry - 1 - 2 * FEE - FUNDING_PER_DAY * days_held * avg_px / entry
        total += (1 + r) / len(coins)
    return total


_NULL_CTX: tuple = ()


def _null_init(*ctx):
    global _NULL_CTX
    _NULL_CTX = ctx


def _null_draw(seed):
    days, series, max_lev, params, lev, probs = _NULL_CTX
    return simulate(days, series, max_lev, params, lev,
                    entry_prob=probs, rng=random.Random(seed))["terminal"]


def ts(ms):
    return None if ms is None else datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def summarize(run, days, k):
    lad = run["ladders"]
    closed = [x for x in lad if x["result"] == "tp"]
    span_years = (days[-1] - days[0]) / DAY / 365.25
    term = run["terminal"]
    durations = [((x["close"] or days[-1]) - x["open"]) / DAY for x in lad]
    return {
        "terminal": term,
        "cagr": term ** (1 / span_years) - 1 if term > 0 else -1.0,
        "max_dd": run["max_dd"],
        "liquidated": ts(run["liquidated"]),
        "ladders": len(lad),
        "tp_exits": len(closed),
        "win_rate_closed": 1.0 if closed else math.nan,
        "open_at_end": sum(1 for x in lad if x["result"] == "open"),
        "open_pnl_at_end": sum(x["pnl"] for x in lad if x["result"] == "open"),
        "full_ladders": sum(1 for x in lad if x["rungs"] == k),
        "longest_days": max(durations) if durations else 0,
        "median_days": sorted(durations)[len(durations) // 2] if durations else 0,
    }


def main():
    params = frozen_params()
    with open(os.path.join(HERE, "W-WH2_cache_daily.json")) as f:
        cache = json.load(f)
    today = int(time.time() * 1000) // DAY * DAY
    candles = {c: [b for b in v if b[0] < today] for c, v in cache["candles"].items()}
    max_lev = cache["max_leverage"]
    print(f"frozen params: {params}   W-WH1 calibration drift: {calibration_drift() or 'none'}")
    out = {"params": params, "cells": {}}

    for uni, coins in UNIVERSES.items():
        days, series = align(candles, coins)
        mid = len(days) // 2
        for lev in LEVERAGES:
            cell = f"{uni}_L{lev}"
            t0 = time.time()
            full = simulate(days, series, max_lev, params, lev, record=True)
            h1 = simulate(days, series, max_lev, params, lev, 0, mid)
            h2 = simulate(days, series, max_lev, params, lev, mid, len(days))
            counts = {c: 0 for c in coins}
            for x in full["ladders"]:
                counts[x["coin"]] += 1
            probs = {c: counts[c] / full["flat_days"][c] if full["flat_days"][c] else 0.0
                     for c in coins}
            ctx = (days, series, max_lev, params, lev, probs)
            with Pool(initializer=_null_init, initargs=ctx) as pool:
                null = pool.map(_null_draw, range(N_NULL), chunksize=50)
            p = sum(1 for v in null if v >= full["terminal"]) / N_NULL
            null_sorted = sorted(null)
            res = {
                "span": [ts(days[0]), ts(days[-1])], "midpoint": ts(days[mid]),
                "full": summarize(full, days, params["k"]),
                "half1": summarize(h1, days[:mid], params["k"]),
                "half2": summarize(h2, days[mid:], params["k"]),
                "buy_and_hold_1x": {"full": buy_and_hold(days, series, 0, len(days)),
                                    "half1": buy_and_hold(days, series, 0, mid),
                                    "half2": buy_and_hold(days, series, mid, len(days))},
                "null": {"p": p, "median": null_sorted[N_NULL // 2],
                         "p05": null_sorted[N_NULL // 20], "p95": null_sorted[N_NULL * 19 // 20],
                         "liquidated_share": sum(1 for v in null if v == 0) / N_NULL},
                "ladders": [dict(x, open=ts(x["open"]), close=ts(x["close"])) for x in full["ladders"]],
                "curve_monthly": [(ts(t), round(e, 4)) for t, e in full["curve"][::30]],
            }
            f, a, b = res["full"], res["half1"], res["half2"]
            res["verdict"] = ("VALIDATED" if f["terminal"] > 1 and a["terminal"] > 1
                              and b["terminal"] > 1 and p < ALPHA else "REFUTED")
            out["cells"][cell] = res
            bh = res["buy_and_hold_1x"]
            print(f"\n== {cell}  {res['span'][0]}..{res['span'][1]}  ({time.time() - t0:.0f}s)")
            for label, s, bhv in [("full", f, bh["full"]), ("half1", a, bh["half1"]), ("half2", b, bh["half2"])]:
                print(f"  {label:<5} terminal {s['terminal']:.3f}x  cagr {s['cagr']:+.1%}  maxDD {s['max_dd']:+.1%}  "
                      f"liq {s['liquidated']}  ladders {s['ladders']} (tp {s['tp_exits']}, open {s['open_at_end']}, "
                      f"full {s['full_ladders']})  longest {s['longest_days']:.0f}d  | buy&hold 1x {bhv:.3f}x")
            nl = res["null"]
            print(f"  null: median {nl['median']:.3f}x  [p05 {nl['p05']:.3f}, p95 {nl['p95']:.3f}]  "
                  f"liquidated {nl['liquidated_share']:.1%}  p={p:.4f}  -> {res['verdict']}")

    with open(os.path.join(HERE, "W-WH2_results.json"), "w") as fh:
        json.dump(out, fh, indent=1)
    print("\nwrote W-WH2_results.json")


if __name__ == "__main__":
    sys.exit(main())
