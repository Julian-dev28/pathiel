#!/usr/bin/env python3
"""Volatility-scaled stop vs the current fixed stop — path-replay on OUR realized trades.
Question (from the EIGEN whipsaw): would an ATR-scaled stop hold through high-vol noise
and catch runs, WITHOUT bleeding more on the persistent losers?

Lookahead-hunted:
  - entry ATR computed from bars STRICTLY BEFORE entry_time
  - forward path processed in strict time order; within each bar: update peak from HIGH,
    then test the stop/trailing-floor against the LOW (the live engine's intrabar order)
  - the hard stop is tested against each bar low regardless of peak
  - SAME replay for fixed and vol stops, so the comparison is apples-to-apples
Reports total realized-style PnL (ROE * notional) under each stop, OOS split, costs.
"""
import json
import os
import statistics
import time
from hermes_trader.client.hl_client import fetch_hl_candles
from hermes_trader.indicators.math import candle_val, atr as atr_fn

CACHE = ".volstop-cache15.json"
PROTECT = 0.0125     # live DSL protect_pct
RETRACE = 0.20       # live DSL retrace
COST_ROE = lambda lev: (4.5 + 5.0) / 1e4 * 2 * lev   # taker+slip both sides, in ROE


def _cached(coin):
    c = {}
    if os.path.exists(CACHE):
        try:
            c = json.load(open(CACHE))
        except Exception:
            c = {}
    if coin in c:
        return c[coin]
    bars = []
    for _ in range(3):
        try:
            cd = fetch_hl_candles(coin, "15m", 1200)
            if cd:
                bars = [{"t": x.t, "h": candle_val(x, "h"), "l": candle_val(x, "l"),
                         "c": candle_val(x, "c"), "o": candle_val(x, "o")} for x in cd]
                break
        except Exception:
            time.sleep(1.5)
    c[coin] = bars
    try:
        json.dump(c, open(CACHE, "w"))
    except Exception:
        pass
    return bars


def entry_atr_pct(bars, entry_ms):
    pre = [b for b in bars if b["t"] <= entry_ms]
    if len(pre) < 16:
        return None
    # ATR(14) on the pre-entry slice
    class C:
        def __init__(s, b): s.t, s.h, s.l, s.c, s.o = b["t"], b["h"], b["l"], b["c"], b["o"]
    arr = atr_fn([C(b) for b in pre], 14)
    if not arr:
        return None
    a = arr[-1]; px = pre[-1]["c"]
    return (a / px) if (a == a and px > 0) else None


def replay(bars, entry_ms, entry_px, side, lev, stop_frac):
    """Return signed ROE (%) under a given spot stop_frac, with the live trailing engine.
    side: +1 long / -1 short."""
    fwd = [b for b in bars if b["t"] > entry_ms]
    if not fwd:
        return None
    peak = entry_px
    armed = False
    for b in fwd:
        hi, lo, cl = b["h"], b["l"], b["c"]
        if side == 1:
            # update peak from HIGH, then test stop/floor against LOW
            peak = max(peak, hi)
            stop_px = entry_px * (1 - stop_frac)
            if lo <= stop_px:
                return ((stop_px / entry_px - 1) * lev) * 100
            if (peak - entry_px) / entry_px >= PROTECT:
                armed = True
            if armed:
                floor = peak - (peak - entry_px) * RETRACE
                if lo <= floor:
                    return ((floor / entry_px - 1) * lev) * 100
        else:
            peak = min(peak, lo)
            stop_px = entry_px * (1 + stop_frac)
            if hi >= stop_px:
                return ((1 - stop_px / entry_px) * lev) * 100
            if (entry_px - peak) / entry_px >= PROTECT:
                armed = True
            if armed:
                floor = peak + (entry_px - peak) * RETRACE
                if hi >= floor:
                    return ((1 - floor / entry_px) * lev) * 100
    last = fwd[-1]["c"]
    return (((last / entry_px - 1) if side == 1 else (1 - last / entry_px)) * lev) * 100


def agg(pnls):
    if not pnls:
        return (0, 0, 0, 0)
    w = [x for x in pnls if x > 0]; eq = pk = dd = 0
    for x in pnls:
        eq += x; pk = max(pk, eq); dd = min(dd, eq - pk)
    return len(pnls), sum(pnls), len(w) / len(pnls) * 100, dd


def main():
    cl = sorted(json.load(open(".agent-memory.json"))["closes"], key=lambda c: c.get("closed_at", 0))
    coins = sorted({c["coin"] for c in cl})
    print(f"# fetching 1h candles for {len(coins)} coins (cached {CACHE})...")
    bars = {c: _cached(c) for c in coins}

    # build per-trade context (notional for $ weighting), entry ATR
    ctx = []
    for c in cl:
        et = c.get("entry_time")
        if not et:
            continue
        b = bars.get(c["coin"]) or []
        ap = entry_atr_pct(b, et)
        if ap is None:
            continue
        ctx.append({
            "coin": c["coin"], "et": et, "ep": c.get("entry_px") or 0,
            "side": 1 if c.get("side") == "long" else -1,
            "lev": float(c.get("leverage") or 10), "notional": float(c.get("notional_usd") or 0) or 300.0,
            "atr": ap, "realized_roe": c.get("realized_pnl_pct") or 0, "bars": b,
        })
    print(f"# {len(ctx)} trades with candle+ATR coverage | trailing protect {PROTECT*100:.2f}% retrace {RETRACE}")

    def run(stop_fn, label):
        usd, half = [], len(ctx) // 2
        for i, t in enumerate(ctx):
            if t["ep"] <= 0:
                continue
            sf = stop_fn(t)
            roe = replay(t["bars"], t["et"], t["ep"], t["side"], t["lev"], sf)
            if roe is None:
                continue
            roe -= COST_ROE(t["lev"])
            usd.append((i, roe / 100 * (t["notional"] / t["lev"])))   # $ = ROE * margin
        d = [u for _, u in usd]; h = len(d) // 2
        n, net, win, dd = agg(d)
        _, o1, _, _ = agg(d[:h]); _, o2, _, _ = agg(d[h:])
        print(f"  {label:38s} | n={n:3d} | net ${net:7.2f} | win {win:3.0f}% | maxDD ${dd:7.2f} | OOS ${o1:+6.2f}/${o2:+6.2f}")
        return net

    # CURRENT fixed stop: min(0.4% spot, 3%ROE/lev)
    def fixed(t): return min(0.004, 0.03 / t["lev"])
    print("\n=== stop comparison (path-replayed on our real trades, cost-aware) ===")
    base = run(fixed, "CURRENT fixed (0.4% / 3%ROE)")
    # VOL stops: clamp(atr_mult * entry_atr, floor, ceil) spot
    for mult, floor, ceil in [(1.0, 0.005, 0.04), (1.5, 0.005, 0.04), (1.5, 0.01, 0.04), (2.0, 0.01, 0.05), (1.5, 0.003, 0.025)]:
        run(lambda t, m=mult, f=floor, c=ceil: max(f, min(m * t["atr"], c)),
            f"VOL atr_mult={mult} clamp[{floor*100:.1f},{ceil*100:.1f}]%")
    # also: vol stop but ROE-capped so high-lev doesn't blow out
    for mult, roecap in [(1.5, 0.12), (2.0, 0.15)]:
        run(lambda t, m=mult, rc=roecap: min(max(0.005, m * t["atr"]), rc / t["lev"]),
            f"VOL atr_mult={mult} ROE-cap {roecap*100:.0f}%")
    print(f"\n# baseline (current fixed) net ${base:.2f} — a VOL stop must clearly beat this on net AND OOS")


if __name__ == "__main__":
    main()
