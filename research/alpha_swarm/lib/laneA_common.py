"""Lane A shared substrate: daily price matrix + cross-sectional helpers.

Keeps per-item scripts thin. Lookahead-safe by construction: every helper
takes a bar index i and only reads bars <= i; fills happen at open[i+1].
"""
import statistics
import alpha_lib
from alpha_lib import O, C, H, L, T

_d = None
def D():
    global _d
    if _d is None:
        _d = alpha_lib.load_dataset()
    return _d

def setup(iv="1d"):
    d = D()
    coins = d["coins"]
    btc = alpha_lib.candles(d, "BTC", iv)
    timeline = [b[T] for b in btc]
    tindex = {t: i for i, t in enumerate(timeline)}
    bars = {}  # coin -> {ts: bar}
    for c in coins:
        cd = alpha_lib.candles(d, c, iv)
        bars[c] = {b[T]: b for b in cd}
    return d, coins, timeline, tindex, bars

class Px:
    """Price accessor over a master timeline, by integer bar index."""
    def __init__(self, iv="1d"):
        self.d, self.coins, self.timeline, self.tindex, self.bars = setup(iv)
        self.N = len(self.timeline)
    def bar(self, c, i):
        if i < 0 or i >= self.N:
            return None
        return self.bars[c].get(self.timeline[i])
    def close(self, c, i):
        b = self.bar(c, i); return b[C] if b else None
    def open(self, c, i):
        b = self.bar(c, i); return b[O] if b else None
    def high(self, c, i):
        b = self.bar(c, i); return b[H] if b else None
    def low(self, c, i):
        b = self.bar(c, i); return b[L] if b else None
    def ret(self, c, i, k):
        a, b = self.close(c, i - k), self.close(c, i)
        if a is None or b is None or a == 0: return None
        return b / a - 1.0
    def dret(self, c, i):
        """single-bar close-to-close return ending at i."""
        return self.ret(c, i, 1)
    def daily_rets(self, c, i, k):
        out = []
        for j in range(i - k + 1, i + 1):
            r = self.ret(c, j, 1)
            if r is not None: out.append(r)
        return out
    def vol(self, c, i, k):
        rs = self.daily_rets(c, i, k)
        return statistics.pstdev(rs) if len(rs) >= 2 else None
    def btc_regime(self, i, k=7):
        r = self.ret("BTC", i, k)
        return "down" if (r is not None and r < 0) else "up"

def summarize(trades):
    return alpha_lib.summarize(trades)

def fmt(s):
    if not s or s.get("n", 0) == 0:
        return "no trades"
    oos = s["oos_12bps"]
    return (f"n={s['n']:4d} | EV0={s['slip0']['mean_ret_pct']:+.4f} "
            f"EV12={s['slip12']['mean_ret_pct']:+.4f} EV25={s['slip25']['mean_ret_pct']:+.4f} "
            f"win={s['slip12']['win_rate']:.2f} sh={s['slip12']['sharpe_like']:+.2f} | "
            f"OOS h1={oos['first_half_mean_pct']} h2={oos['second_half_mean_pct']}")

def baseline_random(px, side_mix, hold, n_samp=2000, seed=0):
    """Matched random-entry baseline: random coin, random bar, same hold,
    side drawn from side_mix (fraction long). Returns summarize dict.
    Gives the drift floor an edge must beat on the -44% tape."""
    import random
    rng = random.Random(seed)
    coins = [c for c in px.coins]
    trades = []
    for _ in range(n_samp):
        c = rng.choice(coins)
        i = rng.randint(5, px.N - hold - 2)
        eo = px.open(c, i + 1); xo = px.open(c, i + 1 + hold)
        if eo is None or xo is None or eo == 0: continue
        sign = 1.0 if rng.random() < side_mix else -1.0
        trades.append({"t": px.timeline[i + 1], "ret": sign * (xo / eo - 1.0)})
    return summarize(trades)
