"""B1 hurst_regime_router — route momentum to trending coins (VR>1), reversion to
reverting coins (VR<1). Measure LIFT over un-routed (mom-only, rev-only) baselines.
Meta-overlay: the deciding number is Sharpe/EV lift, not standalone EV."""
import math, statistics, random
import alpha_lib as A

d = A.load_dataset()
coins = d["coins"]
random.seed(7)

def log_rets(cl):
    return [math.log(cl[k]/cl[k-1]) for k in range(1, len(cl)) if cl[k-1] > 0 and cl[k] > 0]

def variance_ratio(rets, q=5):
    if len(rets) < q + 5:
        return 1.0
    var1 = statistics.pvariance(rets)
    if var1 <= 0:
        return 1.0
    # q-period overlapping sums
    qsums = [sum(rets[k:k+q]) for k in range(len(rets)-q+1)]
    varq = statistics.pvariance(qsums)
    return varq / (q * var1)

W = 60   # trailing window for VR
LMOM = 14  # momentum lookback
LREV = 3   # reversion lookback
HOR = 5
STOP = 0.25

# BTC regime not needed here; this is per-coin routing.

def build(mode):
    """mode: 'router','mom','rev','random'. Returns list of trades."""
    trades = []
    for c in coins:
        cd = A.candles(d, c, "1d")
        if len(cd) < W + LMOM + 5:
            continue
        cl = [b[A.C] for b in cd]
        i = W + LMOM
        while i < len(cd) - HOR - 1:
            window = cl[i-W:i+1]
            rets = log_rets(window)
            vr = variance_ratio(rets, q=5)
            mom = 1 if (cl[i]/cl[i-LMOM] - 1) > 0 else -1
            rev = -1 if (cl[i]/cl[i-LREV] - 1) > 0 else 1
            if mode == "router":
                side_sign = mom if vr > 1.0 else rev
            elif mode == "mom":
                side_sign = mom
            elif mode == "rev":
                side_sign = rev
            else:
                side_sign = random.choice([-1, 1])
            side = "long" if side_sign > 0 else "short"
            entry_px = cd[i+1][A.O]
            fwd = cd[i+1:]
            r = A.sweep_stop(entry_px, side, fwd, [STOP], HOR)[STOP]
            trades.append({"t": cd[i+1][A.T], "ret": r})
            i += HOR  # non-overlapping per coin
    return trades

print("mode    n     EV@12bps  win    sharpe   h1      h2     verdict")
res = {}
for mode in ["router", "mom", "rev", "random"]:
    tr = build(mode)
    s = A.summarize(tr)
    res[mode] = s
    sl = s["slip12"]; oos = s["oos_12bps"]
    print(f"{mode:7s} {s['n']:5d}  {sl['mean_ret_pct']:7.3f}  {sl['win_rate']:.3f}  "
          f"{sl['sharpe_like']:6.3f}  {str(oos['first_half_mean_pct']):6s}  "
          f"{str(oos['second_half_mean_pct']):6s}  {s['verdict']}")

# LIFT: router vs best un-routed baseline
def ev(m): return res[m]["slip12"]["mean_ret_pct"]
def sh(m): return res[m]["slip12"]["sharpe_like"]
best_base = max(["mom", "rev"], key=lambda m: ev(m))
print(f"\nrouter EV {ev('router'):.3f} vs best base ({best_base}) {ev(best_base):.3f} -> lift {ev('router')-ev(best_base):+.3f}")
print(f"router Sharpe {sh('router'):.3f} vs base {sh(best_base):.3f} -> lift {sh('router')-sh(best_base):+.3f}")
print(f"router vs random {ev('router')-ev('random'):+.3f} EV excess")
