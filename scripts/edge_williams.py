#!/usr/bin/env python3
"""Larry Williams Volatility-Breakout system on our universe — the system that catches
the START of a run (vs our gates which block the LATE chase). Lookahead-free, OOS, costs.

Rules (Williams):
  - breakout level = today's UTC open +/- k * (yesterday's high-low range)
  - LONG when an intraday bar trades above open + k*range; SHORT below open - k*range
  - exit: target = entry +/- T*range, stop = entry -/+ S*range, else end-of-UTC-day (time stop)
  - "Trade ONLY with the trend": gate by the daily 200-EMA direction (we already ship this)
  - "breakouts with volume confirmation": require the breakout bar volume >= 1.5x its trailing avg
Tests k in {0.2,0.25,0.3}, long-only (our edge) and long+short, with/without trend+volume filters.
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
TOPN = 45
COST = 12.0 / 1e4
CACHE = ".williams-cache.json"


def _cached(coin, tf, n):
    key = f"{coin}|{tf}|{n}"
    c = {}
    if os.path.exists(CACHE):
        try:
            c = json.load(open(CACHE))
        except Exception:
            c = {}
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


def _day(ts_ms):
    return ts_ms // 86_400_000


def backtest(data, daily, k, target_mult, stop_mult, allow_short, use_trend, use_vol):
    trades = []  # (day_frac, ret, coin, side)
    for coin, hours in data.items():
        dd = daily.get(coin, [])
        if len(hours) < 48 or len(dd) < 30:
            continue
        # daily MA direction — LOOKAHEAD-SAFE: for day d we may only use closes STRICTLY
        # BEFORE d (the prior day's MA), since day d's close isn't known intraday.
        dcloses = [b["c"] for b in dd]
        ma = ema(dcloses, min(200, len(dcloses)))
        ma_by_day = {}
        prior = {}
        for i in range(1, len(dd)):
            d = dd[i]["t"] // 86_400_000
            if i - 1 < len(ma):
                ma_by_day[d] = 1 if dcloses[i - 1] > ma[i - 1] else -1   # PRIOR day's MA state
            prior[d] = (dd[i - 1]["h"] - dd[i - 1]["l"], dd[i]["o"])
        # group hours by day
        byday = defaultdict(list)
        for b in hours:
            byday[_day(b["t"])].append(b)
        days = sorted(byday)
        n_all = len(days)
        for di, d in enumerate(days):
            if d not in prior or d not in ma_by_day:
                continue
            rng, dopen = prior[d]
            if rng <= 0 or dopen <= 0:
                continue
            up_trig = dopen + k * rng
            dn_trig = dopen - k * rng
            bars = byday[d]
            pos = None
            for j, b in enumerate(bars):
                if pos is None:
                    trend = ma_by_day[d]
                    vol_ok = True
                    if use_vol:
                        look = [x["v"] for x in hours if x["t"] < b["t"]][-20:]
                        vol_ok = len(look) >= 10 and look and b["v"] >= (sum(look) / len(look)) * 1.5
                    # Fill REALISM: if the bar gapped past the trigger, fill at the worse
                    # of trigger/open (can't get the trigger price on a gap). Then check if
                    # THIS bar's adverse extreme already hit the stop (conservative — we
                    # don't know intra-bar order, so assume the stop could fire same bar).
                    if b["h"] >= up_trig and (not use_trend or trend == 1) and vol_ok:
                        ep = max(up_trig, b["o"]); stp = ep - stop_mult * rng
                        if b["l"] <= stp:
                            trades.append((di / max(1, n_all), (stp / ep - 1) - COST, coin, "long"))
                        else:
                            pos = ("long", ep)
                    elif allow_short and b["l"] <= dn_trig and (not use_trend or trend == -1) and vol_ok:
                        ep = min(dn_trig, b["o"]); stp = ep + stop_mult * rng
                        if b["h"] >= stp:
                            trades.append((di / max(1, n_all), (1 - stp / ep) - COST, coin, "short"))
                        else:
                            pos = ("short", ep)
                else:
                    side, ep = pos
                    tgt = ep + target_mult * rng if side == "long" else ep - target_mult * rng
                    stp = ep - stop_mult * rng if side == "long" else ep + stop_mult * rng
                    sgn = 1 if side == "long" else -1
                    hit = None
                    if side == "long":
                        if b["l"] <= stp:
                            hit = stp
                        elif b["h"] >= tgt:
                            hit = tgt
                    else:
                        if b["h"] >= stp:
                            hit = stp
                        elif b["l"] <= tgt:
                            hit = tgt
                    if hit is None and j == len(bars) - 1:
                        hit = b["c"]  # time stop = end of UTC day
                    if hit is not None:
                        trades.append((di / max(1, n_all), (hit / ep - 1) * sgn - COST, coin, side))
                        pos = None
    return trades


def report(name, trades):
    if not trades:
        print(f"  {name:42s} | n=0"); return
    rets = [r for _, r, _, _ in trades]
    w = [x for x in rets if x > 0]
    eq = peak = mdd = 0.0
    for x in rets:
        eq += x; peak = max(peak, eq); mdd = min(mdd, eq - peak)
    sh = statistics.mean(rets) / statistics.pstdev(rets) if len(rets) > 1 and statistics.pstdev(rets) else 0
    h1 = [r for fr, r, _, _ in trades if fr < 0.5]; h2 = [r for fr, r, _, _ in trades if fr >= 0.5]
    a1 = statistics.mean(h1) * 100 if h1 else 0; a2 = statistics.mean(h2) * 100 if h2 else 0
    rob = "Y" if (a1 > 0 and a2 > 0) else "-"
    print(f"  {name:42s} | {len(rets):4d} | {sum(rets)*100:+7.1f}% | {statistics.mean(rets)*100:+5.2f}% | "
          f"{len(w)/len(rets)*100:3.0f}% | {mdd*100:6.1f}% | {sh:+.3f} | {a1:+.2f}/{a2:+.2f} {rob}")


def main():
    uni = [m for m in get_universe(include_hip3=False) if float(m.get("dayNtlVlm") or 0) >= VOL_FLOOR]
    uni = sorted(uni, key=lambda m: float(m.get("dayNtlVlm") or 0), reverse=True)[:TOPN]
    data, daily = {}, {}
    for m in uni:
        c = m.get("name") or m.get("coin")
        h = _cached(c, "1h", 1000)
        d = _cached(c, "1d", 250)
        if len(h) >= 200 and len(d) >= 30:
            data[c] = h; daily[c] = d
    print(f"# Larry Williams Volatility-Breakout | {len(data)} coins | cost {COST*1e4:.0f}bps | OOS by day")
    print(f"# {'variant':42s} | {'n':>4s} | {'sumRet':>7s} | {'avg/t':>5s} | {'win':>3s} | {'maxDD':>6s} | {'shrp':>6s} | OOS1/2 rob")
    for k in (0.2, 0.25, 0.3):
        t = backtest(data, daily, k, target_mult=k * 2, stop_mult=k * 2, allow_short=False, use_trend=True, use_vol=True)
        report(f"k={k} long, trend+vol (full Williams)", t)
    # ablations at the best-looking k
    K = 0.25
    report("k=0.25 long, NO filters (raw breakout)", backtest(data, daily, K, K*2, K*2, False, False, False))
    report("k=0.25 long, trend only", backtest(data, daily, K, K*2, K*2, False, True, False))
    report("k=0.25 long, vol only", backtest(data, daily, K, K*2, K*2, False, False, True))
    report("k=0.25 long+short, trend+vol", backtest(data, daily, K, K*2, K*2, True, True, True))
    # wider target (let winners run) — Williams 1:1 vs trend-ride
    report("k=0.25 long, trend+vol, target3x/stop1x", backtest(data, daily, K, K*3, K*1, False, True, True))


if __name__ == "__main__":
    main()
