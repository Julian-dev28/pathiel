"""W-B6 cross_asset_vol_spillover — does BTC realized vol LEAD alt realized vol?
If so a BTC-vol sizing signal pre-positions the XS book. Sizing edge, not direction.
1) lead-lag: corr(BTC_vol[t], alt_vol[t+k]) and partial-vs-own-persistence.
2) BTC-vol sizing overlay on the book: Sharpe lift up-size vs down-size vs flat."""
import statistics
import laneB2_common as B
px = B.px; N = B.N; coins = B.coins
RV = 5  # realized-vol window
alts = [c for c in coins if c != "BTC"]

def rvol(c, i, k=RV):
    return px.vol(c, i, k)
def alt_vol(i):
    vs = [rvol(c, i) for c in alts]; vs = [v for v in vs if v is not None]
    return statistics.mean(vs) if vs else None

# build aligned series of (btc_vol[t], alt_vol[t])
idx = [i for i in range(RV+1, N) if rvol("BTC", i) is not None and alt_vol(i) is not None]
bv = {i: rvol("BTC", i) for i in idx}
av = {i: alt_vol(i) for i in idx}

def corr(xs, ys):
    n = len(xs)
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sx, sy = statistics.pstdev(xs), statistics.pstdev(ys)
    if sx == 0 or sy == 0: return 0.0
    return sum((a-mx)*(b-my) for a, b in zip(xs, ys)) / (n * sx * sy)

print("=" * 100)
print("W-B6 CROSS-ASSET VOL SPILLOVER (RV window=5d)")
print("=" * 100)
print("lead-lag corr(BTC_vol[t], alt_vol[t+k]):")
for k in (0, 1, 2, 3):
    pairs = [(bv[i], av[i+k]) for i in idx if (i+k) in av]
    c = corr([p[0] for p in pairs], [p[1] for p in pairs])
    print(f"  k={k}: corr={c:+.3f} (n={len(pairs)})")
# does BTC_vol[t] beat alt_vol[t]'s own persistence at predicting alt_vol[t+1]?
pp = [(bv[i], av[i], av[i+1]) for i in idx if (i+1) in av]
c_btc = corr([p[0] for p in pp], [p[2] for p in pp])
c_own = corr([p[1] for p in pp], [p[2] for p in pp])
print(f"\npredicting alt_vol[t+1]: BTC_vol[t] corr={c_btc:+.3f}  vs  alt_vol[t] (own persistence) corr={c_own:+.3f}")

# 2) BTC-vol sizing overlay on the XS book
K, HOLD, M = 14, 7, 6
book = B.xs_book(K, HOLD, M)
bvs = sorted(bv[x["i"]] for x in book if x["i"] in bv)
med = bvs[len(bvs)//2]
def overlay(direction):
    out = []
    for x in book:
        v = bv.get(x["i"])
        if v is None: out.append(x); continue
        hi = v >= med
        if direction == "up":   w = 1.5 if hi else 0.67   # up-size when BTC vol high
        elif direction == "down": w = 0.67 if hi else 1.5
        else: w = 1.0
        out.append({**x, "ret": w * x["ret"]})
    return out
print("\nBTC-vol sizing overlay on XS book (median split, 1.5x/0.67x):")
flat = B.report("flat", book, HOLD); B.pr(flat)
up = B.report("up-size hi-BTCvol", overlay("up"), HOLD); B.pr(up)
dn = B.report("down-size hi-BTCvol", overlay("down"), HOLD); B.pr(dn)
print(f"\nLIFT vs flat (annSharpe): up={up['ann_sharpe']-flat['ann_sharpe']:+.3f}  down={dn['ann_sharpe']-flat['ann_sharpe']:+.3f}")
