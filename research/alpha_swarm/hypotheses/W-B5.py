"""W-B5 regime_age_timing — BTC up/down regime persistence (run-length); does entry
timing by regime AGE (fresh vs stale) change XS-momentum / extreme_fade EV?
Survivor-safe event study first, then EV-by-age."""
import statistics
import laneB2_common as B
import alpha_lib
px = B.px; N = B.N; coins = B.coins
SMA = 20

# regime sign at each bar i: +1 if BTC close > 20d SMA else -1 (known at i)
def btc_sma(i):
    cs = [px.close("BTC", j) for j in range(i - SMA + 1, i + 1)]
    cs = [c for c in cs if c is not None]
    return statistics.mean(cs) if len(cs) >= SMA else None

regime = {}     # i -> +1/-1
age = {}         # i -> consecutive bars in current regime (1 = first bar)
prev_sign = None; run = 0
for i in range(N):
    c = px.close("BTC", i); m = btc_sma(i)
    if c is None or m is None:
        regime[i] = None; age[i] = None; continue
    sg = 1 if c > m else -1
    run = run + 1 if sg == prev_sign else 1
    prev_sign = sg
    regime[i] = sg; age[i] = run

# event study: run-length distribution
runs = []
cur = None; length = 0
for i in range(N):
    if regime[i] is None: continue
    if regime[i] == cur: length += 1
    else:
        if cur is not None: runs.append((cur, length))
        cur = regime[i]; length = 1
if cur is not None: runs.append((cur, length))
up_runs = [l for s, l in runs if s == 1]; dn_runs = [l for s, l in runs if s == -1]
print("=" * 100)
print("W-B5 REGIME-AGE TIMING (BTC vs 20d SMA)")
print("=" * 100)
print(f"run-length: UP runs n={len(up_runs)} median={statistics.median(up_runs):.0f} max={max(up_runs)} | "
      f"DOWN runs n={len(dn_runs)} median={statistics.median(dn_runs):.0f} max={max(dn_runs)}")

# XS book rebals tagged by regime sign + age
K, HOLD, M = 14, 7, 6
book = B.xs_book(K, HOLD, M)
for x in book:
    x["reg"] = regime[x["i"]]; x["age"] = age[x["i"]]
# fade trades
FADE_THR, STOP, HOR = -0.12, 0.20, 3
fade = []
for c in coins:
    for i in range(2, N - HOR - 2):
        r = px.dret(c, i)
        if r is None or r >= FADE_THR or regime[i] is None: continue
        eo = px.open(c, i+1)
        if not eo: continue
        fwd = [px.bar(c, j) for j in range(i+1, i+1+HOR+1)]; fwd = [b for b in fwd if b]
        if not fwd: continue
        res = alpha_lib.sweep_stop(eo, "long", fwd, [STOP], HOR)
        fade.append({"t": px.timeline[i+1], "ret": res[STOP], "reg": regime[i], "age": age[i]})

def ev_by_age(trades, label):
    print(f"\n{label}: n={len(trades)}")
    for reg, rname in ((1, "UP"), (-1, "DOWN")):
        sub = [t for t in trades if t["reg"] == reg]
        if len(sub) < 8:
            print(f"  {rname}: n={len(sub)} (thin)"); continue
        ages = sorted(t["age"] for t in sub); med = ages[len(ages)//2]
        fresh = [t for t in sub if t["age"] <= med]; stale = [t for t in sub if t["age"] > med]
        for nm, grp in (("ALL", sub), ("fresh(<=med)", fresh), ("stale(>med)", stale)):
            if len(grp) < 5: continue
            s = alpha_lib.summarize(grp)
            o = s['oos_12bps']
            print(f"  {rname:<4} {nm:<13} n={len(grp):3d} EV12={s['slip12']['mean_ret_pct']:+.4f}% "
                  f"win={s['slip12']['win_rate']:.2f} OOS {o['first_half_mean_pct']}/{o['second_half_mean_pct']} (med age={med})")

ev_by_age(book, "XS-MOMENTUM book by regime+age")
ev_by_age(fade, "EXTREME-FADE-long by regime+age")
