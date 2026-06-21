#!/usr/bin/env python3
"""Early-runner research (Steps 1-2): is there a detectable tell in the bars BEFORE a
volume-explosion runner, or only the breakout itself? Label runners on 5m history, then
compare the pre-runner window's features against RANDOM non-runner windows (the control
that separates a real precursor from "stuff that's always near S/R"). Reuses math.atr +
a single consistent trailing-mean volume (no parallel RVOL impl). Lookahead-safe (features
use only pre-trigger bars). Step 3 (entry backtest) only if a precursor proves out here.
"""
import random
import statistics
from hermes_trader.client.universe import get_universe
from hermes_trader.client.hl_client import fetch_hl_candles
from hermes_trader.indicators.math import candle_val, atr

VOL_FLOOR = 5e6
TOPN = 40
TF = "5m"
BARS = 5000          # ~17 days of 5m
# runner definition (tunable starting points)
RUN_X = 0.06         # >=6% move
RUN_Y = 4            # within 4 bars
RUN_Z = 3.0          # trigger-bar vol >= 3x trailing avg
RUN_N = 20           # trailing avg window
M = 12               # pre-runner window length (bars)
random.seed(7)


def _avgvol(bars, i, n):
    if i < n:
        return 0.0
    return sum(candle_val(b, "v") for b in bars[i - n:i]) / n


def label_runners(bars):
    """Return trigger indices where a runner starts (lookahead-safe label, but the LABEL
    legitimately looks forward — it's the event definition; features must not)."""
    out = []
    i = RUN_N + M
    while i < len(bars) - RUN_Y - 1:
        c0 = candle_val(bars[i], "c")
        fwd_max = max(candle_val(bars[j], "c") for j in range(i, i + RUN_Y + 1))
        vol_ok = candle_val(bars[i], "v") >= RUN_Z * _avgvol(bars, i, RUN_N) if _avgvol(bars, i, RUN_N) > 0 else False
        if c0 > 0 and (fwd_max / c0 - 1) >= RUN_X and vol_ok:
            out.append(i)
            i += RUN_Y + M        # don't double-count the same move
            continue
        i += 1
    return out


def window_features(bars, trig_i):
    """Features of the M bars BEFORE trig_i (strictly pre-trigger, no lookahead)."""
    w = bars[trig_i - M:trig_i]
    if len(w) < M:
        return None
    vols = [candle_val(b, "v") for b in w]
    # rising-volume: short MA (last 3) vs long baseline (the window mean)
    short_v = statistics.mean(vols[-3:]); base_v = statistics.mean(vols)
    rising_vol = short_v > base_v * 1.2
    # first-thrust: any bar in window with vol >= 2x the trailing-N avg at that point
    first_thrust = any(candle_val(bars[trig_i - M + k], "v") >= 2.0 * _avgvol(bars, trig_i - M + k, RUN_N)
                       for k in range(M) if _avgvol(bars, trig_i - M + k, RUN_N) > 0)
    # range contraction: ATR over the window's 2nd half < 1st half
    a_first = atr(bars[trig_i - M - 14:trig_i - M // 2], 14)
    a_last = atr(bars[trig_i - M // 2 - 14:trig_i], 14)
    contracting = bool(a_first and a_last and a_last[-1] < a_first[-1])
    # higher-lows: count consecutive low[i]>low[i-1] runs, max
    lows = [candle_val(b, "l") for b in w]
    best = run = 0
    for k in range(1, len(lows)):
        run = run + 1 if lows[k] > lows[k - 1] else 0
        best = max(best, run)
    higher_lows = best >= 4
    return {"rising_vol": rising_vol, "first_thrust": first_thrust,
            "contracting": contracting, "higher_lows": higher_lows}


def main():
    uni = [m for m in get_universe(include_hip3=False) if float(m.get("dayNtlVlm") or 0) >= VOL_FLOOR]
    uni = sorted(uni, key=lambda m: float(m.get("dayNtlVlm") or 0), reverse=True)[:TOPN]
    runner_feats, random_feats = [], []
    total_runners = 0
    coins = 0
    for m in uni:
        c = m.get("name") or m.get("coin")
        try:
            bars = fetch_hl_candles(c, TF, BARS)
        except Exception:
            continue
        if len(bars) < 500:
            continue
        coins += 1
        trigs = label_runners(bars)
        total_runners += len(trigs)
        trig_set = set(trigs)
        for ti in trigs:
            f = window_features(bars, ti)
            if f:
                runner_feats.append(f)
        # random non-runner windows (same count region, not near a runner)
        cand = [i for i in range(RUN_N + M, len(bars) - RUN_Y - 1)
                if all(abs(i - t) > M + RUN_Y for t in trig_set)]
        for ri in random.sample(cand, min(len(trigs) * 3, len(cand))):
            f = window_features(bars, ri)
            if f:
                random_feats.append(f)

    print(f"# STEP 1: runners labeled = {total_runners} across {coins} coins "
          f"({TF}, ~{BARS} bars) | def: >={RUN_X*100:.0f}% in {RUN_Y} bars + vol>={RUN_Z}x avg{RUN_N}")
    if total_runners < 30:
        print(f"# WARNING: only {total_runners} runners — too few for firm conclusions.")
    print(f"# STEP 2: pre-runner ({M} bars) features vs {len(random_feats)} random non-runner windows")
    print(f"# {'feature':16s} | {'runner %':>9s} | {'random %':>9s} | {'lift':>6s} | precursor?")
    keys = ["rising_vol", "first_thrust", "contracting", "higher_lows"]
    for k in keys:
        rp = sum(f[k] for f in runner_feats) / len(runner_feats) * 100 if runner_feats else 0
        np_ = sum(f[k] for f in random_feats) / len(random_feats) * 100 if random_feats else 0
        lift = (rp / np_) if np_ > 0 else 0
        flag = "YES" if (rp > np_ + 8 and lift >= 1.3) else "weak" if rp > np_ + 3 else "no"
        print(f"  {k:16s} | {rp:8.0f}% | {np_:8.0f}% | {lift:5.2f}x | {flag}")
    print("# precursor? = meaningfully more common before runners than in random windows.")
    print("# If all 'no/weak' → the honest finding is: no reliable early tell, only the breakout.")


if __name__ == "__main__":
    main()
