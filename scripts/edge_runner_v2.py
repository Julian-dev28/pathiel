#!/usr/bin/env python3
"""Early-runner Step 3+4: does entering EARLY (first volume thrust) beat chasing the
breakout? Tests Entry A (first-thrust) vs Entry C (breakout) with PnL, the FALSE-POSITIVE
rate (how often the early signal fires with NO runner after), entry-vs-peak capture, OOS.
Reuses math.atr + one consistent trailing-mean volume. Lookahead-safe (entry decided from
bars<=i; forward path strictly after). Exit = live config (atr-style 2.5% stop + 0.1 trail).
Runner def tuned looser (X=5%, Z=2.5) to get dozens of instances.
"""
import statistics
from hermes_trader.client.universe import get_universe
from hermes_trader.client.hl_client import fetch_hl_candles
from hermes_trader.indicators.math import candle_val, atr

VOL_FLOOR = 5e6
TOPN = 45
TF = "5m"
BARS = 5000
RUN_X, RUN_Y, RUN_Z, RUN_N = 0.05, 4, 2.5, 20   # runner: >=5% in 4 bars + vol>=2.5x avg20
THRUST = 2.0          # first-thrust: vol >= 2x trailing avg
BREAK_LB = 48         # breakout lookback
STOP, PROTECT, RETRACE = 0.025, 0.0125, 0.10    # live exit (wider stop + tight 0.1 trail)
COST = 0.0012
FWD = 24              # max forward bars (~2h on 5m)


def avgvol(bars, i, n=RUN_N):
    return sum(candle_val(b, "v") for b in bars[i - n:i]) / n if i >= n else 0.0


def is_runner_after(bars, i):
    """Did a runner (>=RUN_X in RUN_Y bars + vol) start within the next few bars of i?"""
    for j in range(i, min(i + 6, len(bars) - RUN_Y)):
        c0 = candle_val(bars[j], "c")
        av = avgvol(bars, j)
        if c0 > 0 and av > 0 and candle_val(bars[j], "v") >= RUN_Z * av:
            fmax = max(candle_val(bars[k], "c") for k in range(j, j + RUN_Y + 1))
            if fmax / c0 - 1 >= RUN_X:
                return True
    return False


def fwd_exit(bars, i):
    """Long from bars[i].close through the live exit. Returns (ret, peak_ret)."""
    e = candle_val(bars[i], "c")
    if e <= 0:
        return None
    peak = e; armed = False; peak_ret = 0.0
    for j in range(i + 1, min(i + 1 + FWD, len(bars))):
        hi, lo = candle_val(bars[j], "h"), candle_val(bars[j], "l")
        peak_ret = max(peak_ret, hi / e - 1)
        if lo <= e * (1 - STOP):
            return (-STOP - COST, peak_ret)
        peak = max(peak, hi)
        if (peak - e) / e >= PROTECT:
            armed = True
        if armed:
            fl = peak - (peak - e) * RETRACE
            if lo <= fl:
                return (fl / e - 1 - COST, peak_ret)
    return (candle_val(bars[min(i + FWD, len(bars) - 1)], "c") / e - 1 - COST, peak_ret)


def main():
    uni = [m for m in get_universe(include_hip3=False) if float(m.get("dayNtlVlm") or 0) >= VOL_FLOOR]
    uni = sorted(uni, key=lambda m: float(m.get("dayNtlVlm") or 0), reverse=True)[:TOPN]
    A, A2, A3, C = [], [], [], []   # (frac, ret, peak_ret, ran)
    n_runners = n_coins = 0
    for m in uni:
        c = m.get("name") or m.get("coin")
        try:
            bars = fetch_hl_candles(c, TF, BARS)
        except Exception:
            continue
        if len(bars) < 500:
            continue
        n_coins += 1
        N = len(bars)
        last_a = last_a2 = last_a3 = last_c = -99
        for i in range(RUN_N + 2, N - FWD - RUN_Y):
            av = avgvol(bars, i)
            if av <= 0:
                continue
            frac = i / N
            v = candle_val(bars[i], "v")
            hh = max(candle_val(bars[k], "h") for k in range(i - BREAK_LB, i)) if i >= BREAK_LB else 1e18
            cl = candle_val(bars[i], "c")
            below = cl < hh
            # Entry A: first-thrust (vol >= 2x avg), still below breakout
            if v >= THRUST * av and below and i - last_a > 6:
                ex = fwd_exit(bars, i)
                if ex:
                    A.append((frac, ex[0], ex[1], is_runner_after(bars, i))); last_a = i
            # Entry A2: STRONG thrust (vol >= 3.5x avg) — more selective, still below breakout
            if v >= 3.5 * av and below and i - last_a2 > 6:
                ex = fwd_exit(bars, i)
                if ex:
                    A2.append((frac, ex[0], ex[1], is_runner_after(bars, i))); last_a2 = i
            # Entry A3: coil+rising-vol NEAR a level (the screenshot pattern): mean(last3 vol)>=1.5x
            #   mean(last20 vol), price within 1% BELOW the 48-bar high, and 3 higher-lows.
            if i >= RUN_N + 4 and i - last_a3 > 6 and 0.99 * hh <= cl < hh:
                v3 = sum(candle_val(bars[k], "v") for k in range(i - 3, i)) / 3
                hl = all(candle_val(bars[k], "l") > candle_val(bars[k - 1], "l") for k in range(i - 2, i + 1))
                if v3 >= 1.5 * av and hl:
                    ex = fwd_exit(bars, i)
                    if ex:
                        A3.append((frac, ex[0], ex[1], is_runner_after(bars, i))); last_a3 = i
            # Entry C: breakout (close > 48-high) WITH volume confirm
            if cl > hh and v >= THRUST * av and i - last_c > 6:
                ex = fwd_exit(bars, i)
                if ex:
                    C.append((frac, ex[0], ex[1], is_runner_after(bars, i))); last_c = i
        # count runners for context
        i = RUN_N
        while i < N - RUN_Y:
            c0 = candle_val(bars[i], "c"); av = avgvol(bars, i)
            if c0 > 0 and av > 0 and candle_val(bars[i], "v") >= RUN_Z * av and \
               max(candle_val(bars[k], "c") for k in range(i, i + RUN_Y + 1)) / c0 - 1 >= RUN_X:
                n_runners += 1; i += RUN_Y
            i += 1

    def rep(name, rows):
        if not rows:
            print(f"  {name:24s} | n=0"); return
        r = [x[1] for x in rows]; w = [x for x in r if x > 0]
        fp = sum(1 for x in rows if not x[3]) / len(rows) * 100   # fired but NO runner after
        ran = sum(1 for x in rows if x[3])                        # # actually followed by a runner
        a1 = statistics.mean([x[1] for x in rows if x[0] < 0.5] or [0]) * 100
        a2 = statistics.mean([x[1] for x in rows if x[0] >= 0.5] or [0]) * 100
        print(f"  {name:24s} | {len(r):5d} | {statistics.mean(r)*100:+.3f}% | {len(w)/len(r)*100:3.0f}% | "
              f"caught {ran:3d} | FP {fp:4.1f}% | OOS {a1:+.3f}/{a2:+.3f} {'Y' if a1>0 and a2>0 else '-'}")

    print(f"# Early-runner Step 3+4 | {n_coins} coins {TF} ~{BARS}bars | {n_runners} runners | exit: 2.5% stop + 0.1 trail | cost {COST*1e4:.0f}bps")
    print(f"# {'entry':24s} | {'n':>5s} | {'avg/t':>6s} | {'win':>3s} | caught | {'FP':>5s} | OOS 1/2 rob")
    rep("A: thrust 2x (early)", A)
    rep("A2: thrust 3.5x (early)", A2)
    rep("A3: coil+risingvol@level", A3)
    rep("C: breakout (baseline)", C)
    print("# FP = signal fired with NO runner in next 6 bars (false-positive). caught = # that did precede a runner.")


if __name__ == "__main__":
    main()
