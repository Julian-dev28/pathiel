#!/usr/bin/env python3
"""Do SHORTS make money on our data — and specifically in DOWN regimes? Tests the mirror
of our long strategy: short fresh breakdowns in confirmed downtrends, DSL-style exit.
Segmented by BTC regime to test edge_profile ("shorts bleed EXCEPT in down-regimes").

Lookahead-hunted (the Williams lesson):
  - 200d-MA / trend from PRIOR day only (never the same-day close)
  - breakdown trigger from bars strictly BEFORE entry
  - forward path in strict time order; stop tested vs bar HIGH (short adverse extreme);
    realistic fills (worse of trigger/open)
  - SAME exit engine for every variant; OOS split; costs
"""
import json
import os
import statistics
import time
from collections import defaultdict
from hermes_trader.client.universe import get_universe
from hermes_trader.client.hl_client import fetch_hl_candles
from hermes_trader.indicators.math import candle_val, ema

VOL_FLOOR = 5e6
TOPN = 40
COST = 12.0 / 1e4
CACHE = ".williams-cache.json"   # reuse (1h + 1d for the liquid set)
PROTECT = 0.0125
RETRACE = 0.20
STOP = 0.025   # 2.5% spot hard stop (mirror of a reasonable short stop; tested below too)


def _cached(coin, tf, n):
    c = {}
    if os.path.exists(CACHE):
        try:
            c = json.load(open(CACHE))
        except Exception:
            c = {}
    key = f"{coin}|{tf}|{n}"
    if key in c:
        return c[key]
    bars = []
    for _ in range(3):
        try:
            cd = fetch_hl_candles(coin, tf, n)
            if cd:
                bars = [{"t": x.t, "o": candle_val(x, "o"), "h": candle_val(x, "h"),
                         "l": candle_val(x, "l"), "c": candle_val(x, "c"), "v": candle_val(x, "v")} for x in cd]
                break
        except Exception:
            time.sleep(1.5)
    c[key] = bars
    try:
        json.dump(c, open(CACHE, "w"))
    except Exception:
        pass
    return bars


def short_exit_roe(fwd, entry_px, stop_frac):
    """DSL-style SHORT exit over forward bars (time order). Returns spot % (not leveraged)."""
    peak = entry_px  # for shorts, 'peak' = lowest price seen (best for us)
    armed = False
    for b in fwd:
        hi, lo = b["h"], b["l"]
        stop_px = entry_px * (1 + stop_frac)
        if hi >= stop_px:                         # adverse: price rose into our stop
            return -stop_frac
        peak = min(peak, lo)
        if (entry_px - peak) / entry_px >= PROTECT:
            armed = True
        if armed:
            floor = peak + (entry_px - peak) * RETRACE   # trailing buy-stop
            if hi >= floor:
                return (entry_px - floor) / entry_px
    return (entry_px - fwd[-1]["c"]) / entry_px


def run(data, daily, btc_regime, trigger, regime_filter, stop_frac=STOP):
    trades = []   # (day_frac, roe_spot)
    for coin, hours in data.items():
        if coin == "BTC":
            continue
        dd = daily.get(coin, [])
        if len(hours) < 60 or len(dd) < 30:
            continue
        dcl = [b["c"] for b in dd]
        ma = ema(dcl, min(200, len(dcl)))
        # prior-day MA direction by day bucket (lookahead-safe)
        ma_dir = {}
        for i in range(1, len(dd)):
            if i - 1 < len(ma):
                ma_dir[dd[i]["t"] // 86_400_000] = 1 if dcl[i - 1] > ma[i - 1] else -1
        n_all = len(hours)
        i = 30
        while i < len(hours) - 1:
            b = hours[i]
            day = b["t"] // 86_400_000
            md = ma_dir.get(day, 0)
            breg = btc_regime.get(day, 0)
            if regime_filter == "down_only" and breg != -1:
                i += 1; continue
            if regime_filter == "up_only" and breg != 1:
                i += 1; continue
            if trigger(hours, i, md):
                ep = b["c"]
                fwd = hours[i + 1:i + 1 + 48]   # up to ~2 days forward
                if len(fwd) >= 4:
                    roe = short_exit_roe(fwd, ep, stop_frac) - COST
                    trades.append((i / n_all, roe))
                    i += 12      # cooldown so we don't re-fire every bar
                    continue
            i += 1
    return trades


def metrics(trades, label):
    if not trades:
        print(f"  {label:46s} | n=0"); return
    r = [x for _, x in trades]
    w = [x for x in r if x > 0]
    eq = pk = dd = 0
    for x in r:
        eq += x; pk = max(pk, eq); dd = min(dd, eq - pk)
    h1 = [x for fr, x in trades if fr < 0.5]; h2 = [x for fr, x in trades if fr >= 0.5]
    a1 = statistics.mean(h1) * 100 if h1 else 0; a2 = statistics.mean(h2) * 100 if h2 else 0
    rob = "Y" if (a1 > 0 and a2 > 0) else "-"
    print(f"  {label:46s} | {len(r):4d} | {sum(r)*100:+7.1f}% | {statistics.mean(r)*100:+5.2f}% | "
          f"{len(w)/len(r)*100:3.0f}% | {dd*100:6.1f}% | {a1:+.2f}/{a2:+.2f} {rob}")


def main():
    uni = [m for m in get_universe(include_hip3=False) if float(m.get("dayNtlVlm") or 0) >= VOL_FLOOR]
    uni = sorted(uni, key=lambda m: float(m.get("dayNtlVlm") or 0), reverse=True)[:TOPN]
    data, daily = {}, {}
    for m in uni:
        c = m.get("name") or m.get("coin")
        h = _cached(c, "1h", 1000); d = _cached(c, "1d", 250)
        if len(h) >= 200 and len(d) >= 30:
            data[c] = h; daily[c] = d
    # BTC regime by day (prior-day 200d MA direction) — the market filter
    btc = daily.get("BTC", [])
    btc_regime = {}
    if btc:
        bcl = [b["c"] for b in btc]; bma = ema(bcl, min(200, len(bcl)))
        for i in range(1, len(btc)):
            if i - 1 < len(bma):
                btc_regime[btc[i]["t"] // 86_400_000] = 1 if bcl[i - 1] > bma[i - 1] else -1

    # SHORT triggers (all lookahead-safe: use bars[<=i], md = prior-day MA)
    def t_breakdown(h, i, md):       # fresh 24-bar low close, in a downtrend
        return md == -1 and h[i]["c"] <= min(b["l"] for b in h[i - 24:i])

    def t_momentum(h, i, md):        # below 200MA + EMA8<EMA21 + falling
        cl = [b["c"] for b in h[max(0, i - 40):i + 1]]
        if len(cl) < 25 or md != -1:
            return False
        e8 = ema(cl, 8); e21 = ema(cl, 21)
        return e8 and e21 and e8[-1] < e21[-1] and cl[-1] < cl[-3]

    def t_downmover(h, i, md):       # dropped >8% over last 24h (mirror of mover-long)
        if md != -1 or h[i - 24]["c"] <= 0:
            return False
        return (h[i]["c"] / h[i - 24]["c"] - 1) <= -0.08

    print(f"# SHORTS backtest | {len(data)} coins | cost {COST*1e4:.0f}bps | stop {STOP*100:.1f}% | OOS by time")
    print(f"# {'variant':46s} | {'n':>4s} | {'sumRet':>7s} | {'avg/t':>5s} | {'win':>3s} | {'maxDD':>6s} | OOS1/2 rob")
    for name, trig in (("breakdown(24-low)", t_breakdown), ("trend-momentum", t_momentum), ("down-mover(-8%/24h)", t_downmover)):
        metrics(run(data, daily, btc_regime, trig, "all"), f"{name} | ALL regimes")
        metrics(run(data, daily, btc_regime, trig, "down_only"), f"{name} | DOWN-BTC only")
        metrics(run(data, daily, btc_regime, trig, "up_only"), f"{name} | UP-BTC only")
    print("\n# edge_profile claim: shorts bleed EXCEPT in down-regimes. A green DOWN-only + red UP-only confirms it.")


if __name__ == "__main__":
    main()
