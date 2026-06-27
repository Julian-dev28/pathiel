"""Cross-sectional REVERSAL vs MOMENTUM on 1d candles (40 perps).

Hypothesis: short-horizon cross-sectional reversal (last period's winners
underperform next period) is a +EV crypto anomaly NOT in the live stack.

Rule (lookahead-safe):
  - At rebalance day t, rank coins by trailing-k-day return = close[t]/close[t-k]-1,
    decided on bars up to & including t.
  - FILL at open[t+1]. Hold H=rebal days. EXIT at open[t+1+H].
  - REVERSAL: long bottom-m (losers), short top-m (winners).
  - MOMENTUM: long top-m, short bottom-m (SAME ranks, opposite sign).
  - Each trade = one coin-leg, side-signed gross return over the hold.
  - Vol-scaled variant: weight legs by 1/realized-vol (k-day daily-ret stdev),
    reported as portfolio mean per rebalance (separate from per-leg gate).
  - Dispersion gate: cross-sectional stdev of trailing-k returns; top-tercile only.
  - Regime: BTC 7d trailing return sign (down if <0).
"""
import statistics, itertools
import alpha_lib
from alpha_lib import O, C

d = alpha_lib.load_dataset()
coins = d["coins"]

# master timeline from BTC; build ts -> (open, close) per coin
btc = alpha_lib.candles(d, "BTC", "1d")
timeline = [b[alpha_lib.T] for b in btc]
tindex = {t: i for i, t in enumerate(timeline)}

px = {}  # coin -> {ts: (open, close)}
for c in coins:
    cd = alpha_lib.candles(d, c, "1d")
    px[c] = {b[alpha_lib.T]: (b[O], b[C]) for b in cd}

def closeat(c, i):
    t = timeline[i]
    v = px[c].get(t)
    return v[1] if v else None

def openat(c, i):
    t = timeline[i]
    v = px[c].get(t)
    return v[0] if v else None

def trailing_ret(c, i, k):
    a, b = closeat(c, i - k), closeat(c, i)
    if a is None or b is None or a == 0:
        return None
    return b / a - 1.0

def realized_vol(c, i, k):
    rets = []
    for j in range(i - k + 1, i + 1):
        a, b = closeat(c, j - 1), closeat(c, j)
        if a and b and a != 0:
            rets.append(b / a - 1.0)
    if len(rets) < 2:
        return None
    return statistics.pstdev(rets)

def btc_regime(i):
    r = trailing_ret("BTC", i, 7)
    return "down" if (r is not None and r < 0) else "up"

N = len(timeline)

def run(k, m, rebal, book, disp_gate=False, regime=None, vol_scaled=False):
    """book in {reversal, momentum}. Returns list of per-leg trades and
    list of portfolio (vol-scaled) per-rebalance returns."""
    trades = []
    port = []  # (t, vol-scaled portfolio gross ret)
    start = max(k, 8) + 1
    # precompute dispersion terciles if gating
    disp_thresh = None
    if disp_gate:
        disps = []
        for i in range(start, N - rebal - 1):
            rs = [trailing_ret(c, i, k) for c in coins]
            rs = [r for r in rs if r is not None]
            if len(rs) >= 3 * m:
                disps.append(statistics.pstdev(rs))
        if disps:
            disps.sort()
            disp_thresh = disps[int(len(disps) * 2 / 3)]
    for i in range(start, N - rebal - 1):
        # entry fill index = i+1 (open), exit fill index = i+1+rebal (open)
        ei, xi = i + 1, i + 1 + rebal
        if xi >= N:
            break
        ranked = [(trailing_ret(c, i, k), c) for c in coins]
        ranked = [(r, c) for r, c in ranked if r is not None
                  and openat(c, ei) is not None and openat(c, xi) is not None]
        if len(ranked) < 3 * m:
            continue
        if regime is not None and btc_regime(i) != regime:
            continue
        if disp_gate and disp_thresh is not None:
            rs = [r for r, _ in ranked]
            if statistics.pstdev(rs) < disp_thresh:
                continue
        ranked.sort()  # ascending by return: losers first, winners last
        losers = ranked[:m]
        winners = ranked[-m:]
        if book == "reversal":
            longs, shorts = losers, winners
        else:  # momentum
            longs, shorts = winners, losers
        legs = []
        for side, group in (("long", longs), ("short", shorts)):
            sign = 1.0 if side == "long" else -1.0
            for r, c in group:
                eo, xo = openat(c, ei), openat(c, xi)
                if eo == 0:
                    continue
                gross = sign * (xo / eo - 1.0)
                vol = realized_vol(c, i, max(k, 5))
                w = (1.0 / vol) if (vol and vol > 0) else 0.0
                trades.append({"t": timeline[ei], "ret": gross, "coin": c, "side": side})
                legs.append((gross, w))
        if vol_scaled and legs:
            tw = sum(w for _, w in legs)
            if tw > 0:
                pr = sum(g * w for g, w in legs) / tw
                port.append((timeline[ei], pr))
    return trades, port

def fmt(s):
    if s.get("n", 0) == 0:
        return "no trades"
    oos = s["oos_12bps"]
    return (f"n={s['n']:4d} | "
            f"EV0={s['slip0']['mean_ret_pct']:+.4f} "
            f"EV12={s['slip12']['mean_ret_pct']:+.4f} "
            f"EV25={s['slip25']['mean_ret_pct']:+.4f} "
            f"win={s['slip12']['win_rate']:.2f} sh={s['slip12']['sharpe_like']:+.2f} | "
            f"OOS h1={oos['first_half_mean_pct']} h2={oos['second_half_mean_pct']}")

print("=" * 120)
print("CROSSOVER MAP: per-leg EV (mean signed gross % per leg) by k-horizon, m, rebal")
print("REVERSAL = long losers/short winners ; MOMENTUM = long winners/short losers (same ranks)")
print("=" * 120)
for rebal in (1, 2, 3):
    for m in (4, 6, 8):
        for k in (1, 2, 3, 5):
            tr, _ = run(k, m, rebal, "reversal")
            tm, _ = run(k, m, rebal, "momentum")
            sr, sm = alpha_lib.summarize(tr), alpha_lib.summarize(tm)
            if sr.get("n", 0) == 0:
                continue
            rev_ev0 = sr["slip0"]["mean_ret_pct"]
            mom_ev0 = sm["slip0"]["mean_ret_pct"]
            sign = "REV+" if rev_ev0 > 0 else ("MOM+" if mom_ev0 > 0 else "both-")
            print(f"\nrebal={rebal} m={m} k={k}  [{sign}]")
            print(f"  REV : {fmt(sr)}")
            print(f"  MOM : {fmt(sm)}")
