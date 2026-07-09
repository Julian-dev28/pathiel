"""W-F2 funding_delta_term_structure — three questions on the funding TERM STRUCTURE:

A) D4 RE-CHECK with my own code + two fixes D4 skipped: (1) the funding term a short
   actually COLLECTS while in the trade (positive-funding shorts get paid), and
   (2) independent-episode DEDUP (a coin sitting at z>=2 for several days is ONE
   episode, not several; D4 counted every day = cluster pseudo-replication).
B) DELTA BEYOND LEVEL: does the 24h CHANGE in funding (and its acceleration) predict
   forward returns after controlling for the level? Fama-MacBeth daily XS regression
   + a level-neutral delta-spike event study.
C) MEAN-REVERSION SPEED: after a z>=2 positive funding spike, how many days until the
   funding itself halves? (descriptive; feeds live-book grading windows)

Lookahead-safe: signals use funding settled through day-open t; entry at day t+1 open.
Null: mc_null.shuffle_label_p (3000 iters) vs matched same-side pool WITH funding term.
"""
from __future__ import annotations
import bisect, statistics, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import alpha_lib as al
import funding_lib as fl
import mc_null

d = al.load_dataset()
f = fl.load_funding()
HOUR = 3_600_000
DAY = 24 * HOUR

COINS = [c for c in d["coins"] if fl.rows(f, c) and al.candles(d, c, "1d")]
FT, FP = {}, {}
for c in COINS:
    rs = fl.rows(f, c)
    FT[c] = [r[0] for r in rs]
    ps = [0.0]
    for r in rs:
        ps.append(ps[-1] + r[1])
    FP[c] = ps

def cumf(c, t0, t1):
    i0 = bisect.bisect_right(FT[c], t0)
    i1 = bisect.bisect_right(FT[c], t1)
    return FP[c][i1] - FP[c][i0]

def nf(c, t0, t1):
    return bisect.bisect_right(FT[c], t1) - bisect.bisect_right(FT[c], t0)

def f24(c, t):
    n = nf(c, t - DAY, t)
    if n < 18:
        return None
    return cumf(c, t - DAY, t) / n

CD = {c: {b[al.T]: b for b in al.candles(d, c, "1d")} for c in COINS}
GRID = sorted(CD["BTC"])

# ---- per-coin daily F24 / z / delta / delta_z tables (all trailing, lookahead-safe)
F24 = {c: {} for c in COINS}
for c in COINS:
    for t in GRID:
        v = f24(c, t)
        if v is not None:
            F24[c][t] = v

def z_of(c, t, series, win=30):
    hist = [series[c].get(t - k * DAY) for k in range(1, win + 1)]
    hist = [h for h in hist if h is not None]
    cur = series[c].get(t)
    if cur is None or len(hist) < 15:
        return None
    m = statistics.mean(hist)
    s = statistics.pstdev(hist) + 1e-12
    return (cur - m) / s

DLT = {c: {} for c in COINS}
for c in COINS:
    for t in GRID:
        a, b = F24[c].get(t), F24[c].get(t - DAY)
        if a is not None and b is not None:
            DLT[c][t] = a - b

STOPS = [0.08, 0.15, 0.20, 0.25, 0.40]

def short_trade(c, t_sig, horizon, stop, strict_entry_bar=True):
    """Signal at day-open t_sig -> SHORT at day t_sig+1 open. Returns
    (price_ret_signed, funding_collected, exit_t) or None. strict_entry_bar=True
    lets the stop trigger inside the entry day (conservative; D4 excluded it)."""
    te = t_sig + DAY
    eb = CD[c].get(te)
    if not eb or not eb[al.O]:
        return None
    entry = eb[al.O]
    stop_px = entry * (1 + stop)
    bars = []
    for k in range(0, horizon):
        b = CD[c].get(te + k * DAY)
        if b:
            bars.append((te + k * DAY, b))
    if not bars:
        return None
    start = 0 if strict_entry_bar else 1
    for j in range(start, len(bars)):
        tj, b = bars[j]
        if b[al.H] >= stop_px:
            ret = -al.pct(entry, stop_px)
            return ret, cumf(c, te, tj + DAY // 2), tj
    tl, bl = bars[-1]
    exit_t = tl + DAY  # exit at close of last bar ~= next open
    return -al.pct(entry, bl[al.C]), cumf(c, te, exit_t), exit_t

# =========================================================================
# A) D4 re-check: z>=2 positive-funding spike -> SHORT 5d, stop sweep,
#    + funding term, + episode dedup. Matched same-side null.
# =========================================================================
def d4_recheck(z_thresh=2.0, horizon=5, dedup=True, strict=True):
    events, pool = [], {}   # pool: (c,t) -> per-stop short rets w/ funding
    last_exit = {c: 0 for c in COINS}
    in_episode = {c: False for c in COINS}
    LZ = {}
    for c in COINS:
        for t in GRID:
            z = z_of(c, t, F24)
            if z is None:
                continue
            LZ[(c, t)] = z
            trs = {}
            ok = True
            for sp in STOPS:
                r = short_trade(c, t, horizon, sp, strict_entry_bar=strict)
                if r is None:
                    ok = False
                    break
                trs[sp] = r[0] + r[1]   # price + funding collected
            if not ok:
                continue
            pool[(c, t)] = trs
            if z < 1.0:
                in_episode[c] = False   # episode resets only when z decays below 1
            if z >= z_thresh:
                if dedup and (in_episode[c] or t < last_exit[c]):
                    continue
                in_episode[c] = True
                last_exit[c] = t + (horizon + 1) * DAY
                events.append({"c": c, "t": t, "rets": trs})
    out = {"z": z_thresh, "h": horizon, "dedup": dedup, "strict": strict,
           "n": len(events), "n_pool": len(pool)}
    if len(events) < 8:
        out["verdict"] = "thin"
        return out
    # per-stop net@25bps + pooled-mean stats
    per_stop = {}
    for sp in STOPS:
        rs = [e["rets"][sp] - 0.0025 for e in events]
        per_stop[sp] = round(100 * statistics.mean(rs), 2)
    out["net25_by_stop_pct"] = per_stop
    obs = [statistics.mean(e["rets"].values()) for e in events]
    pool_rets = [statistics.mean(v.values()) for v in pool.values()]
    res = mc_null.shuffle_label_p(obs, pool_rets, n_iter=3000, seed=7)
    out["null"] = {"obs": res["obs_mean"], "null": res["null_mean"],
                   "excess_pct": round(100 * res["excess"], 2), "z": res["z"],
                   "p": res["p_one_sided"]}
    trades = [{"t": e["t"], "ret": statistics.mean(e["rets"].values()) - 0.0025}
              for e in events]
    h1, h2 = al.time_split(trades)
    out["oos25"] = (round(100 * statistics.mean(x["ret"] for x in h1), 2) if h1 else None,
                    round(100 * statistics.mean(x["ret"] for x in h2), 2) if h2 else None)
    out["funding_term_pct"] = round(100 * statistics.mean(
        [e["rets"][0.20] for e in events]) - 100 * statistics.mean(
        [short_trade(e["c"], e["t"], horizon, 0.20, strict)[0] for e in events]), 3)
    return out

# =========================================================================
# B) delta beyond level — Fama-MacBeth XS regression + level-neutral event study
# =========================================================================
def xs_z(vals):
    m = statistics.mean(vals)
    s = statistics.pstdev(vals) + 1e-12
    return [(v - m) / s for v in vals]

def fama_macbeth(h=1):
    """each day: fwd h-d price ret (t+1 open -> t+1+h open) ~ level_z + delta_z (XS-std).
    Non-overlapping: step h days."""
    coefs_l, coefs_d = [], []
    for gi in range(0, len(GRID) - h - 2, h):
        t = GRID[gi]
        rows = []
        for c in COINS:
            lz = z_of(c, t, F24)
            dz = z_of(c, t, DLT)
            b0, b1 = CD[c].get(t + DAY), CD[c].get(t + (1 + h) * DAY)
            if lz is None or dz is None or not b0 or not b1 or not b0[al.O]:
                continue
            rows.append((lz, dz, al.pct(b0[al.O], b1[al.O])))
        if len(rows) < 15:
            continue
        L = xs_z([r[0] for r in rows])
        Dv = xs_z([r[1] for r in rows])
        Y = [r[2] for r in rows]
        # OLS 2-var + intercept via normal equations
        n = len(Y)
        mL, mD, mY = statistics.mean(L), statistics.mean(Dv), statistics.mean(Y)
        sLL = sum((l - mL) ** 2 for l in L)
        sDD = sum((v - mD) ** 2 for v in Dv)
        sLD = sum((l - mL) * (v - mD) for l, v in zip(L, Dv))
        sLY = sum((l - mL) * (y - mY) for l, y in zip(L, Y))
        sDY = sum((v - mD) * (y - mY) for v, y in zip(Dv, Y))
        det = sLL * sDD - sLD * sLD
        if abs(det) < 1e-12:
            continue
        bL = (sDD * sLY - sLD * sDY) / det
        bD = (sLL * sDY - sLD * sLY) / det
        coefs_l.append(bL)
        coefs_d.append(bD)
    def tstat(xs):
        if len(xs) < 5:
            return None
        return round(statistics.mean(xs) / (statistics.stdev(xs) / len(xs) ** 0.5), 2)
    return {"h": h, "n_days": len(coefs_l),
            "level_coef_bps": round(1e4 * statistics.mean(coefs_l), 2),
            "level_t": tstat(coefs_l),
            "delta_coef_bps": round(1e4 * statistics.mean(coefs_d), 2),
            "delta_t": tstat(coefs_d)}

def delta_event(dz_thresh=2.0, lz_cap=1.0, horizon=3, stop=0.20):
    """delta-spike WITHOUT extreme level: dz>=+T & |lz|<cap -> SHORT (fresh crowding-in).
    Matched same-side null over all valid bars. Episode dedup as in A."""
    events, pool = [], []
    in_ep = {c: False for c in COINS}
    for c in COINS:
        for t in GRID:
            lz = z_of(c, t, F24)
            dz = z_of(c, t, DLT)
            if lz is None or dz is None:
                continue
            r = short_trade(c, t, horizon, stop, strict_entry_bar=True)
            if r is None:
                continue
            tot = r[0] + r[1]
            pool.append(tot)
            if dz < 1.0:
                in_ep[c] = False
            if dz >= dz_thresh and abs(lz) < lz_cap:
                if in_ep[c]:
                    continue
                in_ep[c] = True
                events.append({"t": t, "ret": tot})
    out = {"dz": dz_thresh, "lz_cap": lz_cap, "h": horizon, "stop": stop,
           "n": len(events)}
    if len(events) < 8:
        out["verdict"] = "thin"
        return out
    obs = [e["ret"] for e in events]
    res = mc_null.shuffle_label_p(obs, pool, n_iter=3000, seed=11)
    out["net25_pct"] = round(100 * (statistics.mean(obs) - 0.0025), 2)
    out["null"] = {"excess_pct": round(100 * res["excess"], 2), "z": res["z"],
                   "p": res["p_one_sided"]}
    h1, h2 = al.time_split([{"t": e["t"], "ret": e["ret"] - 0.0025} for e in events])
    out["oos25"] = (round(100 * statistics.mean(x["ret"] for x in h1), 2) if h1 else None,
                    round(100 * statistics.mean(x["ret"] for x in h2), 2) if h2 else None)
    return out

# =========================================================================
# C) funding mean-reversion half-life after a z>=2 positive spike
# =========================================================================
def half_life(z_thresh=2.0, max_days=15):
    lives, censored = [], 0
    in_ep = {c: False for c in COINS}
    for c in COINS:
        for t in GRID:
            z = z_of(c, t, F24)
            if z is None:
                continue
            if z < 1.0:
                in_ep[c] = False
            if z >= z_thresh and not in_ep[c]:
                in_ep[c] = True
                v0 = F24[c].get(t)
                if v0 is None or v0 <= 0:
                    continue
                done = False
                for k in range(1, max_days + 1):
                    vk = F24[c].get(t + k * DAY)
                    if vk is not None and vk <= 0.5 * v0:
                        lives.append(k)
                        done = True
                        break
                if not done:
                    censored += 1
    lives.sort()
    if not lives:
        return {"n": 0}
    return {"n_events": len(lives) + censored, "n_reverted": len(lives),
            "censored_gt15d": censored,
            "median_days": lives[len(lives) // 2],
            "p25": lives[len(lives) // 4], "p75": lives[3 * len(lives) // 4]}

if __name__ == "__main__":
    print("=== W-F2 A) D4 re-check (funding term + episode dedup) ===")
    for dedup in (False, True):
        for strict in (False, True):
            r = d4_recheck(2.0, 5, dedup=dedup, strict=strict)
            print(f" dedup={dedup} strict_entry_bar={strict}: {r}")
    print("\n--- z=1.5 variant (dedup+strict) ---")
    print(" ", d4_recheck(1.5, 5, dedup=True, strict=True))

    print("\n=== W-F2 B1) Fama-MacBeth: fwd ret ~ level_z + delta_z ===")
    for h in (1, 3):
        print(" ", fama_macbeth(h))

    print("\n=== W-F2 B2) level-neutral delta-spike event study (short) ===")
    for stop in STOPS:
        print(" ", delta_event(2.0, 1.0, 3, stop))

    print("\n=== W-F2 C) funding half-life after z>=2 positive spike ===")
    print(" ", half_life())
