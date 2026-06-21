#!/usr/bin/env python3
"""Candidate batch: Williams %R, Ultimate Oscillator, and 6 bar patterns (Limit Point,
Inside Day x2, Outside Day, Shock Day, Three-Bar/Hit). All defined on raw OHLC, tested on
candle history. Lookahead-hunted (detectors use bars[<=i]; stop-entry patterns enter only
when a LATER bar breaks the level; forward path strictly after entry, stop vs intrabar
low/high). Uniform exit for comparability: the pattern's stop + live DSL trail
(protect 1.25%/retrace 0.20). OOS split by trade-time. Cost 12bps/round-trip.
COT filter = N/A (HL perps are not CFTC futures — no COT data).
"""
import json
import os
import statistics
import time
from hermes_trader.client.universe import get_universe
from hermes_trader.client.hl_client import fetch_hl_candles
from hermes_trader.indicators.math import candle_val

VOL_FLOOR = 5e6
TOPN = 40
COST = 12.0 / 1e4
PROTECT, RETRACE = 0.0125, 0.20
CACHE = ".barpattern-cache.json"
TF = "1h"
BARS = 1000


def _cached(coin):
    c = {}
    if os.path.exists(CACHE):
        try:
            c = json.load(open(CACHE))
        except Exception:
            c = {}
    if c.get(coin):
        return c[coin]
    b = []
    delay = 1.0
    for _ in range(6):  # integrity: persistent backoff, never cache a gap
        try:
            cd = fetch_hl_candles(coin, TF, BARS)
            if cd:
                b = [{"o": candle_val(x, "o"), "h": candle_val(x, "h"), "l": candle_val(x, "l"),
                      "c": candle_val(x, "c"), "v": candle_val(x, "v")} for x in cd]
                break
        except Exception:
            pass
        time.sleep(delay); delay = min(delay * 2, 16)
    if b:
        c[coin] = b
        try:
            json.dump(c, open(CACHE, "w"))
        except Exception:
            pass
    return b


def _atr(bars, i, n=14):
    if i < n:
        return 0.0
    trs = [max(bars[j]["h"] - bars[j]["l"], abs(bars[j]["h"] - bars[j - 1]["c"]),
               abs(bars[j]["l"] - bars[j - 1]["c"])) for j in range(i - n + 1, i + 1)]
    return sum(trs) / n


def _williams_r(bars, i, n=14):
    if i < n:
        return None
    hh = max(b["h"] for b in bars[i - n + 1:i + 1])
    ll = min(b["l"] for b in bars[i - n + 1:i + 1])
    return ((hh - bars[i]["c"]) / (hh - ll) * -100) if hh > ll else -50


def _uo(bars, i):
    if i < 28:
        return None
    def avg(p):
        bp = tr = 0.0
        for j in range(i - p + 1, i + 1):
            pc = bars[j - 1]["c"]
            bp += bars[j]["c"] - min(bars[j]["l"], pc)
            tr += max(bars[j]["h"], pc) - min(bars[j]["l"], pc)
        return (bp / tr) if tr else 0.0
    return 100 * (4 * avg(7) + 2 * avg(14) + 1 * avg(28)) / 7


# ── detectors: return (side, entry_px, stop_px) for a CLOSE-entry, or
#    ('stop', side, level, stop_px) for a buy/sell-STOP entry; else None ──
def d_williams(bars, i):
    if i < 15:
        return None
    prev, cur = _williams_r(bars, i - 1), _williams_r(bars, i)
    if prev is None or cur is None:
        return None
    if prev < -80 <= cur:                          # crossed back UP out of oversold
        return ("long", bars[i]["c"], min(b["l"] for b in bars[i - 5:i + 1]))
    if prev > -20 >= cur:                           # crossed back DOWN out of overbought
        return ("short", bars[i]["c"], max(b["h"] for b in bars[i - 5:i + 1]))
    return None


def d_uo(bars, i):
    # proxy for the divergence version: UO crosses back above 30 (oversold) / below 70
    if i < 29:
        return None
    prev, cur = _uo(bars, i - 1), _uo(bars, i)
    if prev is None or cur is None:
        return None
    if prev < 30 <= cur:
        return ("long", bars[i]["c"], min(b["l"] for b in bars[i - 5:i + 1]))
    if prev > 70 >= cur:
        return ("short", bars[i]["c"], max(b["h"] for b in bars[i - 5:i + 1]))
    return None


def d_limit(bars, i):
    if i < 2:
        return None
    a, m, c = bars[i - 2], bars[i - 1], bars[i]
    if m["l"] < a["l"] and m["l"] < c["l"] and c["l"] > m["l"]:      # bullish pivot held
        return ("long", c["c"], m["l"] * 0.999)
    if m["h"] > a["h"] and m["h"] > c["h"] and c["h"] < m["h"]:
        return ("short", c["c"], m["h"] * 1.001)
    return None


def d_inside_close(bars, i):
    if i < 1:
        return None
    p, c = bars[i - 1], bars[i]
    if c["h"] < p["h"] and c["l"] > p["l"]:
        if c["c"] > c["o"]:
            return ("long", c["c"], p["l"])
        if c["c"] < c["o"]:
            return ("short", c["c"], p["h"])
    return None


def d_inside_breakout(bars, i):
    if i < 1:
        return None
    p, c = bars[i - 1], bars[i]
    if c["h"] < p["h"] and c["l"] > p["l"]:        # inside bar -> stop entry at its extreme
        return ("stop-long", c["h"], c["l"])       # buy-stop at inside-bar high, stop at its low
    return None


def d_outside(bars, i):
    if i < 11:
        return None
    p, c = bars[i - 1], bars[i]
    if c["h"] > p["h"] and c["l"] < p["l"]:
        downtrend = p["c"] <= min(b["c"] for b in bars[i - 10:i])
        uptrend = p["c"] >= max(b["c"] for b in bars[i - 10:i])
        if downtrend:
            return ("long", c["c"], c["l"])
        if uptrend:
            return ("short", c["c"], c["h"])
    return None


def d_shock(bars, i):
    if i < 1:
        return None
    p, c = bars[i - 1], bars[i]
    if c["o"] > p["c"] and c["c"] > c["o"]:        # up shock -> buy-stop at shock high
        return ("stop-long", c["h"], p["l"])
    if c["o"] < p["c"] and c["c"] < c["o"]:
        return ("stop-short", c["l"], p["h"])
    return None


def d_threebar(bars, i):
    if i < 15:
        return None
    p, c = bars[i - 1], bars[i]
    rng = c["h"] - c["l"]
    avgrng = sum(b["h"] - b["l"] for b in bars[i - 14:i]) / 14
    hit_down = c["l"] < p["l"] and c["c"] > p["c"] and rng > avgrng     # bullish key reversal
    hit_up = c["h"] > p["h"] and c["c"] < p["c"] and rng > avgrng
    if hit_down:
        return ("stop-long", c["h"], c["l"])       # buy-stop at hit-day high, stop at its low
    if hit_up:
        return ("stop-short", c["l"], c["h"])
    return None


def _forward(bars, entry_i, side, entry_px, stop_px):
    """DSL-trail forward replay from entry_i+1. Returns signed spot return net cost, or None."""
    peak = entry_px
    armed = False
    for j in range(entry_i + 1, min(entry_i + 49, len(bars))):
        hi, lo = bars[j]["h"], bars[j]["l"]
        if side == "long":
            if lo <= stop_px:
                return (stop_px / entry_px - 1) - COST
            peak = max(peak, hi)
            if (peak - entry_px) / entry_px >= PROTECT:
                armed = True
            if armed:
                fl = peak - (peak - entry_px) * RETRACE
                if lo <= fl:
                    return (fl / entry_px - 1) - COST
        else:
            if hi >= stop_px:
                return (1 - stop_px / entry_px) - COST
            peak = min(peak, lo)
            if (entry_px - peak) / entry_px >= PROTECT:
                armed = True
            if armed:
                fl = peak + (entry_px - peak) * RETRACE
                if hi >= fl:
                    return (1 - fl / entry_px) - COST
    last = bars[min(entry_i + 48, len(bars) - 1)]["c"]
    return ((last / entry_px - 1) if side == "long" else (1 - last / entry_px)) - COST


def backtest(data, detector):
    trades = []  # (frac, ret)
    for bars in data.values():
        n = len(bars)
        i = 30
        while i < n - 2:
            sig = detector(bars, i)
            if sig:
                if sig[0].startswith("stop"):       # stop-entry: wait up to 6 bars for trigger
                    side = "long" if "long" in sig[0] else "short"
                    level, stop_px = sig[1], sig[2]
                    entered = False
                    for k in range(i + 1, min(i + 7, n - 1)):
                        if (side == "long" and bars[k]["h"] >= level) or (side == "short" and bars[k]["l"] <= level):
                            r = _forward(bars, k, side, level, stop_px)
                            if r is not None:
                                trades.append((i / n, r)); entered = True
                            i = k + 6
                            break
                    if not entered:
                        i += 1
                    continue
                else:
                    side, entry_px, stop_px = sig
                    r = _forward(bars, i, side, entry_px, stop_px)
                    if r is not None:
                        trades.append((i / n, r))
                    i += 6
                    continue
            i += 1
    return trades


def report(name, trades):
    if not trades:
        print(f"  {name:26s} | n=0 (no signals or no data)"); return
    r = [x for _, x in trades]
    w = [x for x in r if x > 0]
    eq = pk = dd = 0.0
    for x in r:
        eq += x; pk = max(pk, eq); dd = min(dd, eq - pk)
    gp = sum(w); gl = abs(sum(x for x in r if x <= 0))
    sh = (statistics.mean(r) / statistics.pstdev(r)) if len(r) > 1 and statistics.pstdev(r) else 0
    h1 = [x for f, x in trades if f < 0.5]; h2 = [x for f, x in trades if f >= 0.5]
    a1 = statistics.mean(h1) if h1 else 0; a2 = statistics.mean(h2) if h2 else 0
    rob = "Y" if (a1 > 0 and a2 > 0) else "-"
    print(f"  {name:26s} | {len(r):4d} | {statistics.mean(r)*100:+5.2f}% | {len(w)/len(r)*100:3.0f}% | "
          f"{(gp/gl if gl else 9.9):4.2f} | {dd*100:6.1f}% | {sh:+.3f} | {a1*100:+.2f}/{a2*100:+.2f} {rob}")


def main():
    uni = [m for m in get_universe(include_hip3=False) if float(m.get("dayNtlVlm") or 0) >= VOL_FLOOR]
    uni = sorted(uni, key=lambda m: float(m.get("dayNtlVlm") or 0), reverse=True)[:TOPN]
    data = {}
    for m in uni:
        c = m.get("name") or m.get("coin")
        b = _cached(c)
        if len(b) >= 100:
            data[c] = b
    L = min((len(v) for v in data.values()), default=0)
    print(f"# bar patterns + oscillators | {len(data)} coins, {TF} ~{L} bars | cost {COST*1e4:.0f}bps | uniform exit (pattern stop + DSL trail) | OOS by time")
    print(f"# {'technique':26s} | {'n':>4s} | {'avg/t':>5s} | {'win':>3s} | {'pf':>4s} | {'maxDD':>6s} | {'shrp':>6s} | OOS 1/2 rob")
    for name, det in (
        ("Williams %R trap", d_williams),
        ("Ultimate Osc (proxy)", d_uo),
        ("Limit Point (3-bar pivot)", d_limit),
        ("Inside Day (close-dir)", d_inside_close),
        ("Inside Day (breakout)", d_inside_breakout),
        ("Outside Day (reversal)", d_outside),
        ("Shock Day (gap+cont)", d_shock),
        ("Three-Bar (Hit+breakout)", d_threebar),
    ):
        report(name, backtest(data, det))
    print("# COT filter: N/A — HL perps are not CFTC futures (no Commitment-of-Traders data).")
    print("# Note: Ultimate Osc is the oversold-cross proxy, NOT the full two-low divergence (flagged).")


if __name__ == "__main__":
    main()
