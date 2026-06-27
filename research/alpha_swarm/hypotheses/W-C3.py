"""W-C3 engulf_orthogonality — is C9 additive to the live XS-momentum book, or a fast
1-day momentum restatement?

Live book (hermes_trader/agents/xs_momentum.py): ranking 'pct_k' (pctk_score, trailing 14d
high/low channel location), k=8 per leg, market-neutral. (Vol gate ignored here — we compare
raw factor co-movement on a daily-overlapping cadence.)

Build DAILY PnL series (always-on, 1-day forward, market-neutral) for:
  live      = 0.5*mean(1d-fwd long8) - 0.5*mean(1d-fwd short8)   [pct_k]
  eng_sym   = signed engulf book (long bull set / short bear set), flat-day=0
  eng_short = short bear-engulf set only, flat-day=0   (W-C2: the real leg)
Then: return correlation, OLS residual alpha = a + b*live (alpha & t-stat) FULL+both halves.
Additive ONLY if low corr AND residual alpha>0 both halves.
"""
from __future__ import annotations
import statistics, math
import alpha_lib as A
from alpha_lib import O, H, L, C

d = A.load_dataset()
SER = {c: A.candles(d, c, "1d") for c in d["coins"] if len(A.candles(d, c, "1d")) >= 60}
N = min(len(b) for b in SER.values())
ARR = {c: SER[c][-N:] for c in SER}


def pctk(bars, i, n=14):
    seg = bars[i - n + 1:i + 1]
    if len(seg) < n:
        return None
    hi = max(b[H] for b in seg); lo = min(b[L] for b in seg); cur = bars[i][C]
    if hi <= lo:
        return None
    return (cur - lo) / (hi - lo) - 0.5


def engulf(bars, i):
    po, pc = bars[i - 1][O], bars[i - 1][C]
    o, c = bars[i][O], bars[i][C]
    if c > o and pc < po and o <= pc and c >= po:
        return 1
    if c < o and pc > po and o >= pc and c <= po:
        return -1
    return 0


def fwd1(bars, i):
    """open[i+1] -> open[i+2] 1-day return."""
    if i + 2 >= len(bars):
        return None
    oe = bars[i + 1][O]
    if oe <= 0:
        return None
    return bars[i + 2][O] / oe - 1.0


def live_day(i):
    pk = [(c, pctk(ARR[c], i)) for c in ARR]
    pk = [(c, v) for c, v in pk if v is not None]
    if len(pk) < 16:
        return None
    pk.sort(key=lambda x: x[1], reverse=True)
    longs = [c for c, _ in pk[:8]]; shorts = [c for c, _ in pk[-8:]]
    lr = [fwd1(ARR[c], i) for c in longs]; sr = [fwd1(ARR[c], i) for c in shorts]
    lr = [x for x in lr if x is not None]; sr = [x for x in sr if x is not None]
    if not lr or not sr:
        return None
    return 0.5 * statistics.mean(lr) - 0.5 * statistics.mean(sr)


def engulf_day(i, short_only=False):
    longs, shorts = [], []
    for c in ARR:
        bars = ARR[c]
        if i < 2 or i + 2 >= len(bars):
            continue
        s = engulf(bars, i)
        if s == 1 and not short_only:
            longs.append(c)
        elif s == -1:
            shorts.append(c)
    lr = [fwd1(ARR[c], i) for c in longs]; sr = [fwd1(ARR[c], i) for c in shorts]
    lr = [x for x in lr if x is not None]; sr = [x for x in sr if x is not None]
    if short_only:
        return -statistics.mean(sr) if sr else 0.0   # flat day = 0
    # symmetric, equal-weight across whichever legs exist
    parts = []
    if lr:
        parts.append(statistics.mean(lr))
    if sr:
        parts.append(-statistics.mean(sr))
    return statistics.mean(parts) if parts else 0.0


def ols(y, x):
    n = len(y); mx = statistics.mean(x); my = statistics.mean(y)
    sxx = sum((xi - mx) ** 2 for xi in x)
    if sxx <= 0 or n <= 2:
        return None
    beta = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / sxx
    alpha = my - beta * mx
    resid = [yi - (alpha + beta * xi) for xi, yi in zip(x, y)]
    s2 = sum(r * r for r in resid) / (n - 2)
    se = math.sqrt(s2 * (1.0 / n + mx * mx / sxx))
    return {"alpha": alpha, "beta": beta, "t": (alpha / se if se > 0 else float('nan')), "n": n}


def corr(x, y):
    n = len(x); mx = statistics.mean(x); my = statistics.mean(y)
    sx = statistics.pstdev(x); sy = statistics.pstdev(y)
    if sx <= 0 or sy <= 0:
        return float('nan')
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (n * sx * sy)


def sharpe(xs):
    sd = statistics.pstdev(xs)
    return statistics.mean(xs) / sd if sd > 0 else float('nan')


# build aligned daily series
rows = []
for i in range(20, N - 2):
    lv = live_day(i)
    if lv is None:
        continue
    rows.append((i, lv, engulf_day(i, False), engulf_day(i, True)))
print(f"aligned daily obs = {len(rows)}  (N={N})")

for label, idx in (("ENGULF symmetric", 2), ("ENGULF short-only", 3)):
    L_ = [r[1] for r in rows]; E_ = [r[idx] for r in rows]
    comb = [0.5 * a + 0.5 * b for a, b in zip(L_, E_)]
    mid = len(rows) // 2

    def stat(sl):
        Ls = [r[1] for r in sl]; Es = [r[idx] for r in sl]
        Cs = [0.5 * a + 0.5 * b for a, b in zip(Ls, Es)]
        o = ols(Es, Ls)
        return (len(sl), corr(Ls, Es), statistics.mean(Es) * 100, sharpe(Ls),
                sharpe(Es), sharpe(Cs), (o["alpha"] * 100 if o else None),
                (o["t"] if o else None), (o["beta"] if o else None))
    print(f"\n===== {label} vs live pct_k book =====")
    print(" half  n   corr   eng_mu%  live_sh eng_sh comb_sh  alpha%/d  t_alpha  beta")
    for nm, sl in (("FULL", rows), ("H1", rows[:mid]), ("H2", rows[mid:])):
        n, cr, mu, lsh, esh, csh, al, t, be = stat(sl)
        print(f" {nm:4} {n:<3} {cr:+.3f} {mu:+.4f}  {lsh:+.3f} {esh:+.3f} {csh:+.3f}  "
              f"{(al if al is not None else 0):+.5f}  {(t if t is not None else 0):+.2f}   "
              f"{(be if be is not None else 0):+.2f}")
