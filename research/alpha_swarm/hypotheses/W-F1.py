"""W-F1 xs_funding_carry_momentum — cross-sectional funding fade, TOTAL return
(price + funding actually received/paid), NON-OVERLAPPING holds 1-5d.

Independent recompute of the D1/D2 MARGINAL claims with their two flagged weaknesses
fixed: (1) D2's multi-day holds were OVERLAPPING under daily rebal (inflated Sharpe);
here rebal every h days so each period is an independent bet. (2) Score TOTAL PnL =
price + funding carry in one book (that is the actual trade), fees on turnover.

Rule: at each rebal date t (a 1d-candle open timestamp), signal = mean hourly funding
over (t-L, t] per coin (settled rows only, lookahead-safe: the row stamped t+33ms is
EXCLUDED). LONG bottom-K funding / SHORT top-K, inverse-20d-vol weights, gross 1 net 0.
Fill at day-t open, hold to day-(t+h) open. PnL = sum_c w_c*(price_ret_c - cumfund_c)
(a long PAYS positive funding). Fees = slip_bps * turnover sum|dw|.
Null: 2000 random-rank neutral books on the same dates/weights/costs.
"""
from __future__ import annotations
import bisect, random, statistics, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import alpha_lib as al
import funding_lib as fl

d = al.load_dataset()
f = fl.load_funding()
HOUR = 3_600_000
DAY = 24 * HOUR

COINS = [c for c in d["coins"] if fl.rows(f, c) and al.candles(d, c, "1d")]

# fast per-coin funding: sorted times + prefix sums
FT, FP = {}, {}
for c in COINS:
    rs = fl.rows(f, c)
    FT[c] = [r[0] for r in rs]
    ps = [0.0]
    for r in rs:
        ps.append(ps[-1] + r[1])
    FP[c] = ps

def cumf(c, t0, t1):
    """sum of hourly rates with t0 < time <= t1 (what a LONG pays over the hold)."""
    i0 = bisect.bisect_right(FT[c], t0)
    i1 = bisect.bisect_right(FT[c], t1)
    return FP[c][i1] - FP[c][i0]

def nf(c, t0, t1):
    i0 = bisect.bisect_right(FT[c], t0)
    i1 = bisect.bisect_right(FT[c], t1)
    return i1 - i0

def trail_fund(c, t, L_hours):
    """mean hourly rate over (t - L, t]. None if <70% of rows present."""
    lo, hi = t - L_hours * HOUR, t
    n = nf(c, lo, hi)
    if n < 0.7 * L_hours:
        return None
    return cumf(c, lo, hi) / n

# daily candle maps
CD = {c: {b[al.T]: b for b in al.candles(d, c, "1d")} for c in COINS}
CT = {c: sorted(CD[c]) for c in COINS}
# global funding span; coins with truncated histories (FET/KAITO/PAXG end 04-19,
# INJ/IP/MORPHO end 05-31) are dropped per-date by trail_fund coverage checks
fs = min(min(FT[c]) for c in COINS)
fe = max(max(FT[c]) for c in COINS)
# master timeline: BTC day-open timestamps inside the funding span, with warmup for vol
GRID = [t for t in CT["BTC"] if fs + 21 * DAY <= t and t + DAY <= fe]

def vol20(c, t):
    ts = [x for x in CT[c] if x < t][-21:]
    if len(ts) < 15:
        return None
    rets = [al.pct(CD[c][ts[i - 1]][al.C], CD[c][ts[i]][al.C]) for i in range(1, len(ts))]
    s = statistics.pstdev(rets)
    return s if s > 1e-6 else None

def px_ret(c, t0, t1):
    b0, b1 = CD[c].get(t0), CD[c].get(t1)
    if not b0 or not b1 or not b0[al.O] or not b1[al.O]:
        return None
    return al.pct(b0[al.O], b1[al.O])

def build_periods(L, h):
    """Non-overlapping periods. Each: {t, coins: {c: (signal, invvol, total_ret)}}"""
    periods = []
    for k in range(0, len(GRID) - h, h):
        t0, t1 = GRID[k], GRID[k + h]
        row = {}
        for c in COINS:
            sig = trail_fund(c, t0, L)
            iv = vol20(c, t0)
            pr = px_ret(c, t0, t1)
            if sig is None or iv is None or pr is None:
                continue
            cf = cumf(c, t0, t1)
            row[c] = (sig, 1.0 / iv, pr - cf, cf)  # long total ret; cf for decomposition
        if len(row) >= 20:
            periods.append({"t": t0, "coins": row})
    return periods

def book_returns(periods, K, pick=None, seed=0):
    """pick=None -> real signal ranks; else a Random for the null. Returns
    (per-period gross rets, per-period turnover)."""
    rng = random.Random(seed)
    rets, turns = [], []
    prev_w: dict[str, float] = {}
    for p in periods:
        row = p["coins"]
        cs = list(row)
        if pick is None:
            ranked = sorted(cs, key=lambda c: row[c][0])
            longs, shorts = ranked[:K], ranked[-K:]
        else:
            sel = rng.sample(cs, 2 * K)
            longs, shorts = sel[:K], sel[K:]
        w = {}
        for side, names in (("L", longs), ("S", shorts)):
            tot = sum(row[c][1] for c in names)
            for c in names:
                w[c] = (0.5 * row[c][1] / tot) * (1 if side == "L" else -1)
        rets.append(sum(w[c] * row[c][2] for c in w))
        turns.append(sum(abs(w.get(c, 0.0) - prev_w.get(c, 0.0))
                         for c in set(w) | set(prev_w)))
        prev_w = w
    return rets, turns

def carry_component(periods, K):
    """mean per-period funding-only pnl of the real book (-sum w*cumf)."""
    out = []
    for p in periods:
        row = p["coins"]
        ranked = sorted(row, key=lambda c: row[c][0])
        longs, shorts = ranked[:K], ranked[-K:]
        w = {}
        for side, names in (("L", longs), ("S", shorts)):
            tot = sum(row[c][1] for c in names)
            for c in names:
                w[c] = (0.5 * row[c][1] / tot) * (1 if side == "L" else -1)
        out.append(sum(-w[c] * row[c][3] for c in w))
    return statistics.mean(out) if out else 0.0

def run(L, h, K=8, n_null=2000):
    periods = build_periods(L, h)
    rets, turns = book_returns(periods, K)
    n = len(rets)
    if n < 8:
        return {"L": L, "h": h, "n": n, "verdict": "thin"}
    out = {"L": L, "h": h, "K": K, "n": n}
    for bps in (0, 12, 25):
        net = [r - (bps / 10000.0) * tu for r, tu in zip(rets, turns)]
        mu = statistics.mean(net)
        sd = statistics.pstdev(net) + 1e-12
        out[f"net{bps}_bpsday"] = round(1e4 * mu / h, 2)
        out[f"sharpe{bps}"] = round(mu / sd * (365 / h) ** 0.5, 2)
    out["turnover"] = round(statistics.mean(turns), 2)
    out["carry_bpsday"] = round(1e4 * carry_component(periods, K) / h, 2)
    # OOS halves @12bps (bps/day)
    net12 = [r - 0.0012 * tu for r, tu in zip(rets, turns)]
    half = n // 2
    out["oos12_h1"] = round(1e4 * statistics.mean(net12[:half]) / h, 2)
    out["oos12_h2"] = round(1e4 * statistics.mean(net12[half:]) / h, 2)
    # permutation null on gross-minus-fee mean (same fee model)
    obs = statistics.mean(net12)
    ge = 0
    for it in range(n_null):
        nr, ntu = book_returns(periods, K, pick=True, seed=1000 + it)
        nm = statistics.mean([r - 0.0012 * tu for r, tu in zip(nr, ntu)])
        if nm >= obs:
            ge += 1
    out["null_p"] = round((ge + 1) / (n_null + 1), 4)
    return out

if __name__ == "__main__":
    print("=== W-F1 xs funding fade, TOTAL (price+funding), non-overlapping holds ===")
    print(f"universe={len(COINS)} coins, grid {len(GRID)} days")
    for L in (24, 72, 168):
        for h in (1, 2, 3, 5):
            r = run(L, h)
            print(f"L={L:3}h h={h}d: " + str(r))
