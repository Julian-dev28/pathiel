#!/usr/bin/env python
"""W-CP1: copy-trade wallet 0xe282...df29 with a leverage multiplier.

Operator request 2026-09-21: a copy mode that mirrors this wallet's newly
opened positions, with leverage on top. Backtest first (EVIDENCE_DOCTRINE).

THE RULE UNDER TEST
-------------------
  copy      every order he places on a position he opened from flat:
            opens, adds, partial and full closes. Positions he already held
            when the copy starts are ignored.
  size      open/add: m x (his order notional / his equity) x our equity,
            i.e. our leverage on the order = m x his.
            reduce: the same fraction of our position as he took off his.
  costs     12.5bps per side (25bps round trip, the doctrine's figure).
  limits    orders under $10 are skipped (Hyperliquid minimum); an add is
            clipped to the margin our cross account has free at max leverage.
  ruin      checked every 4h bar on the adverse extreme (low for longs, high
            for shorts): equity <= sum(notional / (2 x maxLeverage)) is a
            liquidation, and the run ends at zero.

His equity comes from rebuilding his perp account from fills, funding and
the ledger (in `build_orders`). Against Hyperliquid's own weekly perp
account-value curve it is within ~3% through 2026-05. From 2026-06 he runs
unified margin, collateral splits across spot and perp, and HL's perp-only
number stops being his equity; the rebuild ends 16% under spot USDC + perp
(counts every USDC send as a flow, whichever dex it leaves from).

Inputs: W-WH1_cache_*.json (W-WH1_fetch.py), W-CP1_cache_maxlev.json.
Output: W-CP1_results.json.
"""
from __future__ import annotations

import bisect
import importlib
import json
import os
import random
import sys
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
lib = importlib.import_module("W-WH1_lib")

ME = "0xe2823659be02e0f48a4660e4da008b5e1abfdf29"
COST = 0.00125
MIN_NTL = 10.0
MS_4H = 4 * 3600 * 1000
MULTS = [0.5, 1.0, 1.5, 2.0, 3.0]
N_NULL = 2000


def load(name):
    with open(os.path.join(HERE, name)) as f:
        return json.load(f)


def ledger_flow(d: dict) -> float:
    """USDC into (+) or out of (-) his margin. Sends count from any dex: under
    unified margin a spot-sourced send draws the same collateral."""
    t = d["type"]
    if t == "deposit":
        return float(d["usdc"])
    if t == "withdraw":
        return -float(d["usdc"]) - float(d.get("fee", 0))
    if t == "vaultDeposit":
        return -float(d["usdc"])
    if t == "vaultWithdraw":
        return float(d["netWithdrawnUsd"])
    if t == "vaultCreate":
        return -float(d["usdc"]) - float(d["fee"])
    if t == "vaultLeaderCommission":
        return float(d["usdc"])
    if t == "accountClassTransfer":
        return float(d["usdc"]) * (1 if d["toPerp"] else -1)
    if t == "send" and d["token"] == "USDC":
        v = 0.0
        if d["destination"].lower() == ME:
            v += float(d["usdcValue"])
        if d["user"].lower() == ME:
            v -= float(d["usdcValue"]) + float(d["fee"])
        return v
    return 0.0


class Bars:
    def __init__(self, candles: dict):
        self.t = {c: [b[0] for b in v] for c, v in candles.items()}
        self.b = candles

    def idx(self, coin, t):
        """Index of the bar containing t, or None."""
        ts = self.t.get(coin)
        if not ts:
            return None
        i = bisect.bisect_right(ts, t) - 1
        return i if i >= 0 and ts[i] + MS_4H > t else None

    def close_before(self, coin, t):
        """Close of the last bar finished by t (what a live copier can see)."""
        ts = self.t.get(coin)
        i = bisect.bisect_right(ts, t - MS_4H) - 1 if ts else -1
        return self.b[coin][i][4] if i >= 0 else None


def build_orders(fills, funding, ledger, bars):
    """His fills -> copy instructions, one per (coin, order), with his equity
    at the moment the order started.

    Returns [{t, coin, trip, kind: 'add'|'reduce', frac, px}] where for 'add'
    frac is signed order notional / his equity, for 'reduce' the fraction of
    the trip's position closed.
    """
    fills = [f for f in lib.chain_order(fills) if lib.is_perp(f["coin"])]
    ev = ([(x["time"], 0, x) for x in ledger]
          + [(x["time"], 1, x) for x in funding if x["delta"]["type"] == "funding"]
          + [(f["time"], 2, f) for f in fills])
    ev.sort(key=lambda e: (e[0], e[1]))

    cash = 0.0
    pos: dict[str, float] = {}
    ent: dict[str, float] = {}
    last: dict[str, float] = {}
    trip: dict[str, int | None] = {}   # None = held before the data starts
    n_trips = 0
    orders: list[dict] = []
    cur = None                         # the order being aggregated

    def equity(t):
        u = 0.0
        for c, p in pos.items():
            if abs(p) > 1e-12:
                m = bars.close_before(c, t) or last.get(c, ent[c])
                u += p * (m - ent[c])
        return cash + u

    def flush():
        nonlocal cur
        if cur:
            for o in cur["out"]:
                if o["kind"] == "add":
                    o["px"] = o.pop("ntl") / o.pop("sz")
                orders.append(o)
        cur = None

    for t, k, x in ev:
        if k == 0:
            cash += ledger_flow(x["delta"])
            continue
        if k == 1:
            cash += float(x["delta"]["usdc"])
            continue
        c, d, px = x["coin"], lib.signed_size(x), float(x["px"])
        if c not in pos:
            pos[c] = float(x["startPosition"])
            trip[c] = None
            ent[c] = px
        if cur is None or cur["key"] != (c, x["oid"]):
            flush()
            cur = {"key": (c, x["oid"]), "eq": equity(t), "out": []}
        p = pos[c]
        pieces = []
        if abs(p) > 1e-12 and (p > 0) != (d > 0):
            red = min(abs(d), abs(p))
            pieces.append(("reduce", red))
            if abs(d) > abs(p) + 1e-12:
                pieces.append(("open", abs(d) - abs(p)))
        else:
            pieces.append(("open" if abs(p) < 1e-12 else "add", abs(d)))
        for kind, q in pieces:
            p = pos[c]
            sgn = 1.0 if d > 0 else -1.0
            if kind == "reduce":
                frac = q / abs(p)
                if trip[c] is not None:
                    prev = cur["out"][-1] if cur["out"] else None
                    if prev and prev["kind"] == "reduce" and prev["trip"] == trip[c]:
                        prev["frac"] = 1 - (1 - prev["frac"]) * (1 - frac)
                    else:
                        cur["out"].append({"t": t, "coin": c, "trip": trip[c],
                                           "kind": "reduce", "frac": frac, "px": px})
                pos[c] = p + sgn * q
                if abs(pos[c]) < 1e-9:
                    pos[c] = 0.0
                    trip[c] = None
            else:
                if kind == "open":
                    n_trips += 1
                    trip[c] = n_trips
                    ent[c] = px
                else:
                    ent[c] = (ent[c] * abs(p) + px * q) / (abs(p) + q)
                pos[c] = p + sgn * q
                if trip[c] is not None and cur["eq"] > 0:
                    prev = cur["out"][-1] if cur["out"] else None
                    if prev and prev["kind"] == "add" and prev["trip"] == trip[c]:
                        prev["frac"] += sgn * q * px / cur["eq"]
                        prev["ntl"] += q * px
                        prev["sz"] += q
                    else:
                        cur["out"].append({"t": t, "coin": c, "trip": trip[c], "kind": "add",
                                           "frac": sgn * q * px / cur["eq"],
                                           "ntl": q * px, "sz": q})
        last[c] = px
        fee = float(x["fee"]) if x["feeToken"] == "USDC" else 0.0
        cash += float(x["closedPnl"]) - fee
    flush()
    return orders


def simulate(orders, bars, grid, max_lev, m, e0, rates=None, candle_px=False,
             record=False):
    """Copy `orders` at multiplier m from equity e0 across bar times `grid`.

    rates: {coin: (times, hourly rates)} or None to skip funding.
    candle_px: price our orders at the last closed 4h bar instead of his fill.
    """
    cash = e0
    book: dict[int, list] = {}          # trip -> [coin, signed size, entry]
    stats = {"placed": 0, "skipped_min": 0, "clipped": 0, "funding": 0.0, "costs": 0.0}
    curve = []
    peak, max_dd = e0, 0.0
    liq = None
    oi = 0
    last_t = grid[0]

    def mark(coin, t):
        return bars.close_before(coin, t)

    def eq_at(t):
        e = cash
        for coin, sz, en in book.values():
            p = mark(coin, t)
            e += sz * ((p if p is not None else en) - en)
        return e

    for T in grid:
        bar_end = T + MS_4H
        while oi < len(orders) and orders[oi]["t"] < bar_end:
            o = orders[oi]
            oi += 1
            px = mark(o["coin"], o["t"]) if candle_px else o["px"]
            if px is None:
                continue
            if o["kind"] == "reduce":
                pos = book.get(o["trip"])
                if not pos:
                    continue
                q = pos[1] * o["frac"]
                cash += q * (px - pos[2]) - abs(q) * px * COST
                stats["costs"] += abs(q) * px * COST
                pos[1] -= q
                if o["frac"] > 1 - 1e-9 or abs(pos[1]) * px < 1e-6:
                    del book[o["trip"]]
                continue
            e = eq_at(o["t"])
            ntl = m * o["frac"] * e
            used = sum(abs(sz) * (mark(c, o["t"]) or en) / max_lev[c]
                       for c, sz, en in book.values())
            room = max(0.0, e - used) * max_lev[o["coin"]] * 0.95
            if abs(ntl) > room:
                ntl = room if ntl > 0 else -room
                stats["clipped"] += 1
            if abs(ntl) < MIN_NTL:
                stats["skipped_min"] += 1
                continue
            q = ntl / px
            cash -= abs(ntl) * COST
            stats["costs"] += abs(ntl) * COST
            stats["placed"] += 1
            pos = book.get(o["trip"])
            if pos:
                pos[2] = (pos[2] * pos[1] + px * q) / (pos[1] + q)
                pos[1] += q
            else:
                book[o["trip"]] = [o["coin"], q, px]
        if rates and book:
            for coin, sz, en in book.values():
                ts, rs = rates.get(coin, ([], []))
                a, b = bisect.bisect_left(ts, max(last_t, T)), bisect.bisect_left(ts, bar_end)
                if b > a:
                    p = mark(coin, bar_end) or en
                    f = -sz * p * sum(rs[a:b])
                    cash += f
                    stats["funding"] += f
        last_t = bar_end
        # liquidation check on the adverse extreme of this bar
        adv, maint = cash, 0.0
        for coin, sz, en in book.values():
            i = bars.idx(coin, T)
            p = (bars.b[coin][i][3] if sz > 0 else bars.b[coin][i][2]) if i is not None else en
            adv += sz * (p - en)
            maint += abs(sz) * p / (2 * max_lev[coin])
        if (book and adv <= maint) or eq_at(bar_end) <= 0:
            liq = T
            cash, book = 0.0, {}
        e = eq_at(bar_end)
        peak = max(peak, e)
        if peak > 0:
            max_dd = min(max_dd, e / peak - 1)
        if record:
            curve.append((bar_end, round(e, 2)))
        if liq is not None:
            break
    final = eq_at(grid[-1] + MS_4H) if liq is None else 0.0
    out = {"multiple": round(final / e0, 4), "max_dd": round(max_dd, 4), "liquidated": liq, **stats}
    if record:
        out["curve"] = curve[::6]
    return out


# ── null: every trip shifted to a random time on its own coin ──

_CTX = None


def _init(ctx):
    global _CTX
    _CTX = ctx


def shifted(orders, trip_span, lo, hi, rng):
    out = []
    offs = {}
    for tr, (coin, t0, t1) in trip_span.items():
        a, b = lo[coin] - t0, hi[coin] - t1
        offs[tr] = rng.randint(a, b) if b > a else 0
    for o in orders:
        o2 = dict(o)
        o2["t"] = o["t"] + offs[o["trip"]]
        out.append(o2)
    out.sort(key=lambda o: o["t"])
    return out


def _null(seed):
    orders, trip_span, lo, hi, bars, grid, max_lev, m, e0 = _CTX
    rng = random.Random(seed)
    return simulate(shifted(orders, trip_span, lo, hi, rng), bars, grid, max_lev, m, e0,
                    candle_px=True)["multiple"]


def main():
    fills = load("W-WH1_cache_fills.json")
    funding = load("W-WH1_cache_funding.json")
    ledger = load("W-WH1_cache_ledger.json")
    candles = load("W-WH1_cache_4h.json")
    max_lev = load("W-CP1_cache_maxlev.json")
    bars = Bars(candles)
    orders = build_orders(fills, funding, ledger, bars)
    orders = [o for o in orders if o["coin"] in candles]
    rates: dict[str, tuple[list, list]] = {}
    for r in sorted((x for x in funding if x["delta"]["type"] == "funding"), key=lambda x: x["time"]):
        ts, rs = rates.setdefault(r["delta"]["coin"], ([], []))
        ts.append(r["time"])
        rs.append(float(r["delta"]["fundingRate"]))

    t0 = orders[0]["t"] // MS_4H * MS_4H
    t1 = max(b[-1][0] for b in candles.values())
    grid = list(range(t0, t1 + 1, MS_4H))
    mid = grid[len(grid) // 2]
    trip_span = {}
    for o in orders:
        c, a, b = trip_span.get(o["trip"], (o["coin"], o["t"], o["t"]))
        trip_span[o["trip"]] = (c, min(a, o["t"]), max(b, o["t"]))
    lo = {c: max(v[0][0] + MS_4H, t0) for c, v in candles.items()}
    hi = {c: v[-1][0] for c, v in candles.items()}
    first_half = [o for o in orders if trip_span[o["trip"]][1] < mid]
    second_half = [o for o in orders if trip_span[o["trip"]][1] >= mid]

    res = {"orders": len(orders), "trips": len(trip_span), "window": [t0, t1], "mid": mid,
           "cells": {}}
    alpha = 0.05 / len(MULTS)
    for e0 in (1000.0, 300.0):
        for m in MULTS:
            key = f"e0={int(e0)} m={m}"
            full = simulate(orders, bars, grid, max_lev, m, e0, rates=rates, record=True)
            cell = {"full": full}
            if e0 == 1000.0:
                cell["h1"] = simulate(first_half, bars, [g for g in grid if g < mid], max_lev, m, e0, rates=rates)
                cell["h2"] = simulate(second_half, bars, [g for g in grid if g >= mid], max_lev, m, e0, rates=rates)
                cand = simulate(orders, bars, grid, max_lev, m, e0, candle_px=True)
                ctx = (orders, trip_span, lo, hi, bars, grid, max_lev, m, e0)
                with Pool(initializer=_init, initargs=(ctx,)) as pool:
                    nulls = pool.map(_null, range(N_NULL), chunksize=50)
                nulls.sort()
                p = (1 + sum(1 for x in nulls if x >= cand["multiple"])) / (N_NULL + 1)
                cell["null"] = {"actual_at_candle": cand["multiple"], "median": nulls[N_NULL // 2],
                                "p": round(p, 4), "alpha": alpha}
                cell["verdict"] = {
                    "net_positive": full["multiple"] > 1,
                    "both_halves": cell["h1"]["multiple"] > 1 and cell["h2"]["multiple"] > 1,
                    "beats_null": p < 0.05,
                    "bonferroni": p < alpha,
                }
            res["cells"][key] = cell
            f = full
            print(f"{key:14} x{f['multiple']:<8} dd {f['max_dd']:+.1%} liq {f['liquidated']} "
                  f"placed {f['placed']} skip {f['skipped_min']} clip {f['clipped']} "
                  f"fund {f['funding']:+.0f}"
                  + (f" | h1 x{cell['h1']['multiple']} h2 x{cell['h2']['multiple']} "
                     f"| null med x{cell['null']['median']} actual@candle x{cell['null']['actual_at_candle']} "
                     f"p {cell['null']['p']}" if "null" in cell else ""))
    # "start copying today": every month start, only trips opened after it
    import datetime
    res["rolling"] = []
    first = {}
    for o in orders:
        first.setdefault(o["trip"], o["t"])
    y, mo = 2025, 3
    while True:
        s = int(datetime.datetime(y, mo, 1, tzinfo=datetime.UTC).timestamp() * 1000)
        if s > t1:
            break
        oo = [o for o in orders if first[o["trip"]] >= s]
        g = list(range(s, t1 + 1, MS_4H))
        row = {"start": s}
        for m in MULTS:
            r = simulate(oo, bars, g, max_lev, m, 1000.0, rates=rates)
            row[str(m)] = {k: r[k] for k in ("multiple", "max_dd", "liquidated")}
        res["rolling"].append(row)
        print(datetime.datetime.fromtimestamp(s / 1000, datetime.UTC).date(), "  ".join(
            f"m={m} x{row[str(m)]['multiple']:.2f} dd{row[str(m)]['max_dd']:+.0%}"
            + (" LIQ" if row[str(m)]["liquidated"] else "") for m in MULTS))
        y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
    with open(os.path.join(HERE, "W-CP1_results.json"), "w") as f:
        json.dump(res, f)


if __name__ == "__main__":
    main()
