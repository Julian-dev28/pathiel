"""Lane 1 - SELECTION: which 5m volume-influx events actually RUN?

Event = green 5m candle with vol >= 1.5x trailing-6-bar mean vol.
Label = runner if forward MFE over next 96 bars (8h) >= 10% (also track >=20%).
Classifier = logistic regression in pure numpy (NO sklearn), lookahead-safe features
(computed only from bars <= i). Time-sorted OOS split: fit first half, score second half.

Reports: OOS AUC composite vs each feature alone; top-quartile-by-score runner-rate and
net-of-12bps tight-floor EV vs the rest; EXCESS over a matched random-entry null.
SURVIVOR universe => upper bound. Read-only. No live code/config touched.
"""
import json
from pathlib import Path
import numpy as np

SCR = Path("/private/tmp/claude-501/-Users-julian-dev-Documents-code-hermes-trader/02a15a26-058b-42af-a0f8-8bc0ce9ab5f5/scratchpad")
DS = SCR / "movers_5m.json"
O, H, L, C, V = 1, 2, 3, 4, 5
TR = 6           # trailing window for influx ratio
FWD = 96         # 8h forward horizon (96 * 5m)
FEE = 0.0012     # 12 bps round-trip
RUN10 = 0.10
RUN20 = 0.20
RNG = np.random.default_rng(7)

d = json.loads(DS.read_text())
coins = d["meta"]["coins"]

def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0

FEATS = [
    "influx_mag",      # vol / trailing-6 mean
    "ft2_vol",         # 2nd candle vol / influx vol
    "ft2_green",       # 2nd candle green (1/0)
    "ft3_vol",         # 3rd candle vol / influx vol
    "ft3_green",       # 3rd candle green (1/0)
    "compression",     # recent (6) range / longer (48) range  (low = coiled)
    "consec_green",    # consecutive green candles before influx
    "body_wick",       # influx body / total range
    "prior_1h_ret",    # return over prior 12 bars (1h)
    "influx_move",     # influx candle's own % move (close/open-1)
    "trail_vol",       # coin trailing volatility (std of 24-bar returns)
    "tod_sin", "tod_cos",  # time-of-day cyclic
]

rows = []   # dict per event
for coin in coins:
    bars = d["candles"].get(coin, {}).get("5m", [])
    n = len(bars)
    if n < 60 + FWD:
        continue
    vols = [b[V] for b in bars]
    closes = [b[C] for b in bars]
    i = 50
    while i < n - FWD - 3:
        b = bars[i]
        trail = vols[i - TR:i]
        tmean = mean(trail) or 1e-9
        if not (b[C] > b[O] and b[V] >= 1.5 * tmean):
            i += 1
            continue
        rng_i = b[H] - b[L]
        # follow-through (bars i+1, i+2) - these are <= i+2, still BEFORE entry at i+3 open?
        # Lookahead rule: entry is at influx-confirm. We allow 2 follow-through candles to confirm,
        # entry at open of bar i+3. Features use bars <= i+2 only. Forward MFE measured from entry.
        b1, b2 = bars[i + 1], bars[i + 2]
        # compression: range of last 6 vs last 48 (avg per-bar high-low), all <= i
        r6 = mean([bars[j][H] - bars[j][L] for j in range(i - 6, i)])
        r48 = mean([bars[j][H] - bars[j][L] for j in range(i - 48, i)]) or 1e-9
        compression = r6 / r48
        # consecutive green before influx
        cg = 0
        j = i - 1
        while j >= 0 and bars[j][C] > bars[j][O]:
            cg += 1
            j -= 1
        body = abs(b[C] - b[O])
        body_wick = body / rng_i if rng_i > 0 else 0.0
        prior_1h = closes[i] / closes[i - 12] - 1 if closes[i - 12] > 0 else 0.0
        influx_move = b[C] / b[O] - 1 if b[O] > 0 else 0.0
        rets24 = [closes[k] / closes[k - 1] - 1 for k in range(i - 24, i) if closes[k - 1] > 0]
        trail_vol = float(np.std(rets24)) if rets24 else 0.0
        # time of day from ms timestamp
        hod = (b[0] // 3600000) % 24
        tod_sin = np.sin(2 * np.pi * hod / 24)
        tod_cos = np.cos(2 * np.pi * hod / 24)

        feat = [
            b[V] / tmean,
            b1[V] / (b[V] or 1e-9),
            1.0 if b1[C] > b1[O] else 0.0,
            b2[V] / (b[V] or 1e-9),
            1.0 if b2[C] > b2[O] else 0.0,
            compression,
            float(cg),
            body_wick,
            prior_1h,
            influx_move,
            trail_vol,
            tod_sin, tod_cos,
        ]
        # entry at open of bar i+3 (after 2 follow-through candles observed)
        entry = bars[i + 3][O]
        if entry <= 0:
            i += 1
            continue
        fwd = bars[i + 3:i + 3 + FWD]
        mfe = max(x[H] for x in fwd) / entry - 1.0
        # tight-floor realized return (gb ~0.10 trailing, -0.35 hard floor at 0.65x)
        peak = entry
        armed = False
        realized = None
        for x in fwd:
            peak = max(peak, x[H])
            g = peak / entry - 1
            if x[L] <= entry * 0.65:
                realized = -0.35
                break
            if g >= 0.01:
                armed = True
            if armed and x[L] <= peak * 0.90:   # 10% give-back trail
                realized = x[L] / entry - 1
                break
        if realized is None:
            realized = fwd[-1][C] / entry - 1.0
        realized -= FEE

        # RIDE exit: 25% give-back trail, 0.65 hard floor (lets runners run)
        peak = entry; armed = False; ride = None
        for x in fwd:
            peak = max(peak, x[H]); g = peak / entry - 1
            if x[L] <= entry * 0.65:
                ride = -0.35; break
            if g >= 0.02:
                armed = True
            if armed and x[L] <= peak * 0.75:
                ride = x[L] / entry - 1; break
        if ride is None:
            ride = fwd[-1][C] / entry - 1.0
        ride -= FEE

        rows.append({
            "coin": coin, "t": b[0], "feat": feat,
            "run10": 1.0 if mfe >= RUN10 else 0.0,
            "run20": 1.0 if mfe >= RUN20 else 0.0,
            "mfe": mfe, "realized": realized, "ride": ride,
        })
        i += 1   # overlapping events allowed; many will be near-duplicates but time-OOS holds

print(f"events: {len(rows)}")
rows.sort(key=lambda r: r["t"])
X = np.array([r["feat"] for r in rows], float)
t = np.array([r["t"] for r in rows], float)
y10 = np.array([r["run10"] for r in rows], float)
y20 = np.array([r["run20"] for r in rows], float)
realized = np.array([r["realized"] for r in rows], float)

print(f"base runner-rate >=10%: {y10.mean():.4f}   >=20%: {y20.mean():.4f}")
print(f"all-events tight-floor EV (net 12bps): {realized.mean()*100:+.3f}%   win {(realized>0).mean():.3f}")

# time-sorted OOS split
split = len(rows) // 2
tr = slice(0, split)
te = slice(split, len(rows))

def standardize(Xtr, Xte):
    mu = Xtr.mean(0)
    sd = Xtr.std(0)
    sd[sd == 0] = 1.0
    return (Xtr - mu) / sd, (Xte - mu) / sd, mu, sd

def fit_logit(Xs, yv, l2=1.0, iters=400, lr=0.3):
    n, p = Xs.shape
    Xb = np.hstack([Xs, np.ones((n, 1))])
    w = np.zeros(p + 1)
    for _ in range(iters):
        z = Xb @ w
        pr = 1 / (1 + np.exp(-np.clip(z, -30, 30)))
        grad = Xb.T @ (pr - yv) / n
        grad[:-1] += l2 * w[:-1] / n
        w -= lr * grad
    return w

def predict(w, Xs):
    Xb = np.hstack([Xs, np.ones((Xs.shape[0], 1))])
    z = Xb @ w
    return 1 / (1 + np.exp(-np.clip(z, -30, 30)))

def auc(yv, sc):
    # rank-based AUC (Mann-Whitney)
    pos = sc[yv == 1]
    neg = sc[yv == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    order = np.argsort(sc)
    ranks = np.empty(len(sc))
    ranks[order] = np.arange(1, len(sc) + 1)
    # average ties
    # simple tie handling
    rp = ranks[yv == 1].sum()
    return (rp - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))

def run_target(yv, label):
    print(f"\n===== TARGET: {label} (base rate train {yv[tr].mean():.4f} / test {yv[te].mean():.4f}) =====")
    Xtr_s, Xte_s, mu, sd = standardize(X[tr], X[te])
    # composite
    w = fit_logit(Xtr_s, yv[tr])
    sc_te = predict(w, Xte_s)
    comp_auc = auc(yv[te], sc_te)
    print(f"{'COMPOSITE (all feats)':28s} OOS AUC {comp_auc:.4f}")
    # standardized coefficients
    coefs = sorted(zip(FEATS, w[:-1]), key=lambda kv: -abs(kv[1]))
    print("  std coefs (|w| desc):")
    for name, wv in coefs:
        print(f"    {name:14s} {wv:+.4f}")
    # each feature alone
    print("  single-feature OOS AUC:")
    singles = []
    for k, name in enumerate(FEATS):
        wk = fit_logit(Xtr_s[:, [k]], yv[tr])
        sck = predict(wk, Xte_s[:, [k]])
        a = auc(yv[te], sck)
        singles.append((name, a))
    for name, a in sorted(singles, key=lambda kv: -abs(kv[1] - 0.5)):
        print(f"    {name:14s} {a:.4f}")
    return sc_te, comp_auc

sc10, auc10 = run_target(y10, "MFE>=10%")
sc20, auc20 = run_target(y20, "MFE>=20%")

# ---- top-quartile EV lift (use the >=10% composite score on test set) ----
print("\n===== TOP-QUARTILE-BY-SCORE (composite >=10% model, OOS test half) =====")
yte10 = y10[te]
rte = realized[te]
thr = np.quantile(sc10, 0.75)
top = sc10 >= thr
rest = ~top
print(f"top-quartile  n={top.sum():5d}  runner-rate(>=10%) {yte10[top].mean():.4f}  tight-floor EV {rte[top].mean()*100:+.3f}%  win {(rte[top]>0).mean():.3f}")
print(f"rest (bot 75) n={rest.sum():5d}  runner-rate(>=10%) {yte10[rest].mean():.4f}  tight-floor EV {rte[rest].mean()*100:+.3f}%  win {(rte[rest]>0).mean():.3f}")
print(f"all test      n={len(rte):5d}  runner-rate(>=10%) {yte10.mean():.4f}  tight-floor EV {rte.mean()*100:+.3f}%  win {(rte>0).mean():.3f}")

# decile table
print("\n  decile (test, by composite >=10% score): runner-rate / EV")
order = np.argsort(sc10)
for q in range(10):
    lo = int(q / 10 * len(order)); hi = int((q + 1) / 10 * len(order))
    idx = order[lo:hi]
    print(f"    D{q+1:2d}  score[{sc10[idx].min():.3f},{sc10[idx].max():.3f}]  rr {yte10[idx].mean():.4f}  EV {rte[idx].mean()*100:+.3f}%")

# ---- matched random-entry NULL ----
# For each event's (coin,time bucket), pick a RANDOM bar in same coin and measure same tight-floor EV.
# This controls for "the universe just went up". EXCESS = top-quartile EV - null EV.
print("\n===== MATCHED RANDOM-ENTRY NULL (same coins, random times) =====")
# build per-coin bar index for random sampling
null_rets = []
by_coin = {}
for coin in coins:
    bars = d["candles"].get(coin, {}).get("5m", [])
    if len(bars) >= 60 + FWD:
        by_coin[coin] = bars
for r in rows:
    bars = by_coin.get(r["coin"])
    if not bars:
        continue
    k = int(RNG.integers(50, len(bars) - FWD - 3))
    entry = bars[k][O]
    if entry <= 0:
        continue
    fwd = bars[k:k + FWD]
    peak = entry; armed = False; rr = None
    for x in fwd:
        peak = max(peak, x[H]); g = peak / entry - 1
        if x[L] <= entry * 0.65:
            rr = -0.35; break
        if g >= 0.01:
            armed = True
        if armed and x[L] <= peak * 0.90:
            rr = x[L] / entry - 1; break
    if rr is None:
        rr = fwd[-1][C] / entry - 1
    null_rets.append(rr - FEE)
null_rets = np.array(null_rets)
print(f"random-entry null tight-floor EV: {null_rets.mean()*100:+.3f}%  win {(null_rets>0).mean():.3f}  n={len(null_rets)}")
print(f"EXCESS  all-influx vs null:      {(realized.mean()-null_rets.mean())*100:+.3f}%")
print(f"EXCESS  top-quartile vs null:    {(rte[top].mean()-null_rets.mean())*100:+.3f}%")
# ---- ABLATION: drop trail_vol (is the rest of the signal real, or all volatility?) ----
print("\n===== ABLATION: composite WITHOUT trail_vol (>=10% target) =====")
keep = [k for k, nm in enumerate(FEATS) if nm != "trail_vol"]
Xtr_s, Xte_s, _, _ = standardize(X[tr][:, keep], X[te][:, keep])
w = fit_logit(Xtr_s, y10[tr])
sc_no = predict(w, Xte_s)
print(f"  OOS AUC without trail_vol: {auc(y10[te], sc_no):.4f}  (with trail_vol {auc10:.4f})")
# influx_move alone, and trail_vol alone, on EV (top-decile)
for nm in ("trail_vol", "influx_move"):
    k = FEATS.index(nm)
    Xk_tr, Xk_te, _, _ = standardize(X[tr][:, [k]], X[te][:, [k]])
    wk = fit_logit(Xk_tr, y10[tr]); sck = predict(wk, Xk_te)
    o = np.argsort(sck); d10 = o[int(0.9*len(o)):]
    print(f"  {nm:12s} top-decile rr {yte10[d10].mean():.4f}  tight-floor EV {rte[d10].mean()*100:+.3f}%")

# ---- can the MFE be MONETIZED? wider 'ride' exit on the top decile (Lane-3 peek) ----
print("\n===== MONETIZATION: ride exit (25% give-back, 0.65 floor) on top-decile vs all (test) =====")
ride = np.array([r["ride"] for r in rows], float)
ride_te = ride[te]
order10 = np.argsort(sc10)
d10 = order10[int(0.9 * len(order10)):]
print(f"  all test     ride EV {ride_te.mean()*100:+.3f}%  win {(ride_te>0).mean():.3f}")
print(f"  top-decile   ride EV {ride_te[d10].mean()*100:+.3f}%  win {(ride_te[d10]>0).mean():.3f}  rr {yte10[d10].mean():.4f}")
print(f"  top-quartile ride EV {ride_te[top].mean()*100:+.3f}%  win {(ride_te[top]>0).mean():.3f}")

print(f"\nComposite OOS AUC  >=10%: {auc10:.4f}   >=20%: {auc20:.4f}")
