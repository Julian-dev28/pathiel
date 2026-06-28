"""BTC regime + crowdedness regime + BTC influence on the cross-section.
1) Is BTC up/down regime a VALIDATED forward signal (does the trend persist)?
2) Crowded-long vs crowded-short (aggregate funding) -> forward market return?
3) How much does BTC actually DRIVE the alts (beta / R^2)?
"""
import statistics, math
import alpha_lib as A
import funding_lib as F

DAY = 86_400_000
d = A.load_dataset(); fd = F.load_funding()
coins = [c for c in d["coins"] if c != "BTC"]

# --- daily closes per coin + BTC ---
def daily(coin):
    return {b[A.T] // DAY: b[A.C] for b in A.candles(d, coin, "1d")}
bt = daily("BTC"); btd = sorted(bt)
cl = {c: daily(c) for c in coins}

# market return = equal-weight mean daily return across coins
def mkt_ret(day, prev):
    rs = []
    for c in coins:
        if day in cl[c] and prev in cl[c] and cl[c][prev] > 0:
            rs.append(cl[c][day] / cl[c][prev] - 1.0)
    return statistics.mean(rs) if len(rs) >= 10 else None

# aggregate funding per day = mean over coins of that day's mean hourly funding (crowdedness)
def agg_funding():
    perday = {}
    for c in d["coins"]:
        by = {}
        for t, rate, prem in F.rows(fd, c):
            by.setdefault(t // DAY, []).append(rate)
        for day, v in by.items():
            perday.setdefault(day, []).append(statistics.mean(v))
    return {day: statistics.mean(v) for day, v in perday.items() if len(v) >= 10}
af = agg_funding()

def btc_up(day):
    prior = [x for x in btd if x <= day]
    if not prior: return None
    day = prior[-1]; i = btd.index(day)
    return None if i < 20 else bt[day] > bt[btd[i - 20]]

# ============ Part 1: BTC regime -> forward market return ============
print("# 1) BTC regime (20d) -> forward EQUAL-WEIGHT market return (is the regime directional?)")
for h in (1, 3, 5):
    up_f, dn_f = [], []
    for i in range(20, len(btd) - h):
        day = btd[i]; u = btc_up(day)
        if u is None: continue
        fwd = mkt_ret(btd[i + h], day)
        if fwd is None: continue
        (up_f if u else dn_f).append(fwd)
    def s(xs): return f"n={len(xs):<4} mean {100*statistics.mean(xs):+.2f}% up%={sum(1 for x in xs if x>0)/len(xs):.2f}" if xs else "n=0"
    print(f"  fwd {h}d:  BTC-UP   {s(up_f)}")
    print(f"           BTC-DOWN {s(dn_f)}")

# ============ Part 2: crowdedness (aggregate funding z) -> forward market return ============
print("\n# 2) Crowdedness = aggregate-funding z vs own 30d. high z = crowded LONG. fwd market return:")
afd = sorted(af)
events = []  # (day, z, ...)
for i in range(30, len(afd)):
    day = afd[i]; hist = [af[afd[j]] for j in range(i - 30, i)]
    mu, sd = statistics.mean(hist), statistics.pstdev(hist)
    if sd <= 0: continue
    events.append((day, (af[day] - mu) / sd))
for label, lo, hi in [("crowded LONG  (z>=+1.5)", 1.5, 99), ("neutral       (-1.5..1.5)", -1.5, 1.5),
                      ("crowded SHORT (z<=-1.5)", -99, -1.5)]:
    for h in (3, 5):
        fr = []
        for day, z in events:
            if not (lo <= z < hi if hi != 99 else z >= lo) and not (hi == 99 and z >= lo) and not (lo == -99 and z <= hi):
                # simpler bucket test below
                pass
        # clean bucket
        fr = []
        for day, z in events:
            inb = (z >= 1.5) if hi == 99 else ((z <= -1.5) if lo == -99 else (-1.5 <= z < 1.5))
            if not inb: continue
            if day not in btd: continue
            i = btd.index(day) if day in btd else None
            if i is None or i + h >= len(btd): continue
            f = mkt_ret(btd[i + h], day)
            if f is not None: fr.append(f)
        if h == 3:
            print(f"  {label}: ", end="")
        print(f"fwd{h}d n={len(fr)} {100*statistics.mean(fr):+.2f}% (win {sum(1 for x in fr if x>0)/len(fr):.2f}) " if fr else f"fwd{h}d n=0 ", end="")
    print()

# ============ Part 3: BTC influence (beta / R^2 of alts on BTC) ============
print("\n# 3) BTC influence: regress each alt's daily return on BTC's same-day return")
betas, r2s = [], []
btc_r = {btd[i]: bt[btd[i]] / bt[btd[i - 1]] - 1.0 for i in range(1, len(btd))}
for c in coins:
    xs, ys = [], []
    days = sorted(cl[c])
    for i in range(1, len(days)):
        day, prev = days[i], days[i - 1]
        if day in btc_r and cl[c][prev] > 0:
            xs.append(btc_r[day]); ys.append(cl[c][day] / cl[c][prev] - 1.0)
    if len(xs) < 50: continue
    n = len(xs); mx, my = sum(xs)/n, sum(ys)/n
    cov = sum((a-mx)*(b-my) for a, b in zip(xs, ys)); vx = sum((a-mx)**2 for a in xs)
    if vx <= 0: continue
    beta = cov/vx
    sx = math.sqrt(vx); sy = math.sqrt(sum((b-my)**2 for b in ys))
    r = cov/(sx*sy) if sx>0 and sy>0 else 0
    betas.append(beta); r2s.append(r*r)
print(f"  median alt beta to BTC: {statistics.median(betas):.2f}  (alts move ~{statistics.median(betas):.1f}x BTC)")
print(f"  median R^2 (BTC explains): {statistics.median(r2s):.2f}  -> ~{100*statistics.median(r2s):.0f}% of an alt's daily variance IS BTC")
print(f"  beta range: {min(betas):.2f}..{max(betas):.2f}")
