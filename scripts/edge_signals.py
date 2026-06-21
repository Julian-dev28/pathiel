#!/usr/bin/env python3
"""Edge battery on candle history (think CTA, not retail TA). Each signal is
long-only, lookahead-free, OOS-split (1st/2nd half), and compared to the
UNCONDITIONAL forward return (the baseline a signal must beat to add value),
net of round-trip cost. A real edge: conditional > baseline AND positive in
BOTH halves.

Signals: time-series momentum, Donchian breakout, short-horizon reversal,
volume-climax reversal, BTC lead-lag, time-of-day.
"""
import statistics
from datetime import datetime, timezone
from hermes_trader.client.universe import get_universe
from hermes_trader.client.hl_client import fetch_hl_candles
from hermes_trader.indicators.math import candle_val

VOL_FLOOR = 5e6
TOPN = 60
BARS = 5000
COST = 15.0 / 1e4   # round-trip


def load():
    uni = [m for m in get_universe(include_hip3=False) if float(m.get("dayNtlVlm") or 0) >= VOL_FLOOR]
    uni = sorted(uni, key=lambda m: float(m.get("dayNtlVlm") or 0), reverse=True)[:TOPN]
    data = {}
    for m in uni:
        c = m.get("name") or m.get("coin")
        try:
            cd = fetch_hl_candles(c, "1h", BARS)
            if len(cd) >= 300:
                data[c] = cd
        except Exception:
            pass
    return data


def fwd_ret(cd, i, H):
    a, b = candle_val(cd[i], "c"), candle_val(cd[i + H], "c")
    return (b / a - 1) if a > 0 else None


def report(name, fired, baseline):
    """fired/baseline: list of (half, fwd_ret). half 0/1."""
    for half in (0, 1):
        pass
    def half_mean(rows, h):
        v = [r for hh, r in rows if hh == h]
        return (statistics.mean(v), len(v)) if v else (0.0, 0)
    f1, n1 = half_mean(fired, 0); f2, n2 = half_mean(fired, 1)
    b1, _ = half_mean(baseline, 0); b2, _ = half_mean(baseline, 1)
    e1 = f1 - b1 - COST; e2 = f2 - b2 - COST          # edge over baseline, net of cost
    robust = "YES" if (e1 > 0 and e2 > 0) else "no"
    print(f"  {name:34s} | edge1 {e1*100:+6.2f}% (n={n1:>4d}) | edge2 {e2*100:+6.2f}% (n={n2:>4d}) | {robust:>4s}")


def main():
    data = load()
    if not data:
        print("no data"); return
    L = min(len(v) for v in data.values())
    half = L // 2
    btc = data.get("BTC")
    print(f"# {len(data)} coins | {L} 1h bars (~{L//24}d) | cost {COST*1e4:.0f}bps | OOS split mid")
    print(f"# signal                             |  1st-half edge over baseline | 2nd-half | robust")

    # baselines per H
    base = {H: [] for H in (6, 12, 24)}
    for cd in data.values():
        for i in range(72, len(cd) - 24):
            h = 0 if i < half else 1
            for H in (6, 12, 24):
                r = fwd_ret(cd, i, H)
                if r is not None:
                    base[H].append((h, r))

    # 1) Time-series momentum: past-L return > 0 -> long, forward H
    for Lb in (24, 48, 72):
        for H in (12, 24):
            fired = []
            for cd in data.values():
                for i in range(Lb, len(cd) - H):
                    p = candle_val(cd[i - Lb], "c"); c0 = candle_val(cd[i], "c")
                    if p > 0 and (c0 / p - 1) > 0:                  # uptrend
                        r = fwd_ret(cd, i, H)
                        if r is not None:
                            fired.append((0 if i < half else 1, r))
            report(f"TS-momentum L{Lb} H{H}", fired, base[H])

    # 2) Donchian breakout: close == max(high, last L) -> long, forward H
    for Lb in (24, 48):
        for H in (12, 24):
            fired = []
            for cd in data.values():
                for i in range(Lb, len(cd) - H):
                    hh = max(candle_val(cd[j], "h") for j in range(i - Lb, i + 1))
                    if candle_val(cd[i], "c") >= hh * 0.999:
                        r = fwd_ret(cd, i, H)
                        if r is not None:
                            fired.append((0 if i < half else 1, r))
            report(f"Donchian-breakout L{Lb} H{H}", fired, base[H])

    # 3) Short-horizon reversal: past-Lb return < -3% -> long (dip), forward H
    for Lb in (4, 8):
        for H in (6, 12):
            fired = []
            for cd in data.values():
                for i in range(Lb, len(cd) - H):
                    p = candle_val(cd[i - Lb], "c"); c0 = candle_val(cd[i], "c")
                    if p > 0 and (c0 / p - 1) < -0.03:
                        r = fwd_ret(cd, i, H)
                        if r is not None:
                            fired.append((0 if i < half else 1, r))
            report(f"Reversal(dip) L{Lb} H{H}", fired, base[H])

    # 4) Volume-climax reversal: vol > 3x avg AND red bar -> long, forward H
    for H in (6, 12):
        fired = []
        for cd in data.values():
            for i in range(24, len(cd) - H):
                vol = candle_val(cd[i], "v")
                avg = sum(candle_val(cd[j], "v") for j in range(i - 24, i)) / 24
                red = candle_val(cd[i], "c") < candle_val(cd[i], "o")
                if avg > 0 and vol >= 3 * avg and red:
                    r = fwd_ret(cd, i, H)
                    if r is not None:
                        fired.append((0 if i < half else 1, r))
        report(f"Volume-climax-reversal H{H}", fired, base[H])

    # 5) BTC lead-lag: BTC up > +0.5% last hour -> long the alt next H
    if btc:
        bt = {int(c.t) // 3_600_000: candle_val(c, "c") for c in btc}
        for H in (6, 12):
            fired = []
            for c, cd in data.items():
                if c == "BTC":
                    continue
                for i in range(2, len(cd) - H):
                    hr = int(cd[i].t) // 3_600_000
                    if hr in bt and (hr - 1) in bt and bt[hr - 1] > 0:
                        if (bt[hr] / bt[hr - 1] - 1) > 0.005:           # BTC just popped
                            r = fwd_ret(cd, i, H)
                            if r is not None:
                                fired.append((0 if i < half else 1, r))
            report(f"BTC-leadlag (BTC+0.5%) H{H}", fired, base[H])

    # 6) Time-of-day: long at hour-of-day buckets, forward 6h (vs baseline)
    for hod in (0, 8, 13, 20):       # UTC: Asia / EU / US-open / US-pm
        fired = []
        for cd in data.values():
            for i in range(24, len(cd) - 6):
                if datetime.fromtimestamp(int(cd[i].t) / 1000, timezone.utc).hour == hod:
                    r = fwd_ret(cd, i, 6)
                    if r is not None:
                        fired.append((0 if i < half else 1, r))
        report(f"TimeOfDay UTC{hod:02d}h H6", fired, base[6])


if __name__ == "__main__":
    main()
