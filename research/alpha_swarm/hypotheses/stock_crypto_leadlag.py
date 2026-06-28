#!/usr/bin/env python3
"""Stock(HIP-3) vs crypto lead-lag: does SP500 / SKHX / xyz markets LEAD crypto, or just move with it?
contemp = same-bar corr (co-move). lead = does X[i] predict Y[i+1] (the tradeable part)."""
import os, sys, statistics, math
sys.path.insert(0, "/Users/julian_dev/Documents/code/hermes-trader")
for line in open("/Users/julian_dev/Documents/code/hermes-trader/.env.local"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("="); os.environ.setdefault(k.strip(), v.strip())
from hermes_trader.client.hl_client import fetch_hl_candles

IV = "5m"; N = 5000
EQUITY = ["xyz:SP500", "xyz:SKHX", "xyz:NVDA", "xyz:XYZ100", "xyz:MSTR"]
CRYPTO = ["BTC", "ETH", "SOL"]

def fetch(coin):
    try:
        cs = fetch_hl_candles(coin, IV, N)
        return {int(c.t): (float(c.c), float(c.v)) for c in cs} if cs else {}
    except Exception as e:
        print(f"  WARN {coin}: {e}"); return {}

series = {m: fetch(m) for m in EQUITY + CRYPTO}
for m in EQUITY + CRYPTO:
    print(f"  {m}: {len(series[m])} bars")

def pearson(xs, ys):
    n = len(xs)
    if n < 30: return None
    mx, my = sum(xs)/n, sum(ys)/n
    cov = sum((a-mx)*(b-my) for a, b in zip(xs, ys))
    sx = math.sqrt(sum((a-mx)**2 for a in xs)); sy = math.sqrt(sum((b-my)**2 for b in ys))
    return cov/(sx*sy) if sx > 0 and sy > 0 else None

def rets(coin, only_active=True):
    """time-sorted [(t, ret)], optionally only bars where the EQUITY traded (vol>0) to drop stale off-hours."""
    s = series[coin]; ts = sorted(s)
    out = {}
    for i in range(1, len(ts)):
        c0, _ = s[ts[i-1]]; c1, v1 = s[ts[i]]
        if c0 <= 0: continue
        if only_active and v1 <= 0: continue
        out[ts[i]] = c1/c0 - 1.0
    return out

def aligned(a, b, lag=0):
    """return paired (a[t], b[t+lag*IV]) over common timestamps. lag in bars."""
    
    xs, ys = [], []
    for t, av in a.items():
        bt = t + lag*300_000
        if bt in b: xs.append(av); ys.append(b[bt])
    return xs, ys

print(f"\n# stock(HIP-3) vs crypto lead-lag, {IV}, equity-active bars only")
print(f"{'equity':<12}{'crypto':<7}{'contemp':>9}{'eq->cr+1':>10}{'cr->eq+1':>10}  read")
print("-"*64)
for eq in EQUITY:
    er = rets(eq, only_active=True)
    if len(er) < 50:
        print(f"{eq:<12} (only {len(er)} active bars — skip)"); continue
    for cr in CRYPTO:
        crr = rets(cr, only_active=False)
        c0 = pearson(*aligned(er, crr, 0))
        c_eq_leads = pearson(*aligned(er, crr, 1))    # equity[i] vs crypto[i+1]
        c_cr_leads = pearson(*aligned(crr, er, 1))    # crypto[i] vs equity[i+1]
        def f(x): return f"{x:+.3f}" if x is not None else "  n/a"
        read = ""
        if c0 is not None:
            if c_eq_leads and abs(c_eq_leads) > 0.05 and abs(c_eq_leads) > abs(c0)*0.6:
                read = "EQ may lead"
            elif c_cr_leads and abs(c_cr_leads) > 0.05 and abs(c_cr_leads) > abs(c0)*0.6:
                read = "crypto leads"
            elif abs(c0 or 0) > 0.1:
                read = "contemp co-move only"
            else:
                read = "~uncorrelated"
        print(f"{eq:<12}{cr:<7}{f(c0):>9}{f(c_eq_leads):>10}{f(c_cr_leads):>10}  {read}")
