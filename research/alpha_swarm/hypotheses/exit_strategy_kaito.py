"""Exit-strategy test for KAITO-like momentum-breakout LONGS.

KAITO: TA-confirmed breakout long, 5x, closed by DSL floor_breach at +4.16% spot
(+20.6% ROE) after peaking slightly higher and retracing through the profit floor.
Question: is the floor_breach (tight profit-trail) the best exit for this class, or
does a wider trail / ATR trail / fixed TP / scale-out bank more?

Method (5m candles, lookahead-safe): find breakout-long entries (new 4h high + a
momentum burst + volume confirm), fill at next-bar open, then walk the forward path
and apply each exit policy intrabar (high/low). Net of fees, OOS both halves. The
realized SPOT return is what we compare (leverage just scales it equally).
"""
from __future__ import annotations
import statistics
import alpha_lib as A

d = A.load_dataset()
O, H, L, C, V = A.O, A.H, A.L, A.C, A.V
COINS = [c for c in d["coins"] if c != "BTC"]

IV = "5m"
LOOKBACK = 48          # 4h breakout window
BASE = 12              # require recent base (not already extended)
BURST = 0.012          # >=1.2% 5m burst bar
VOLX = 1.5             # volume >= 1.5x trailing mean
HORIZON = 144          # 12h max hold
FEE = 0.0006           # round-trip ~6bps (per leg 3bps)


def atr(bars, i, n=14):
    trs = []
    for j in range(max(1, i - n + 1), i + 1):
        h, l, pc = bars[j][H], bars[j][L], bars[j - 1][C]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs) if trs else 0.0


def find_entries(bars):
    out = []
    last = -10_000
    for i in range(LOOKBACK, len(bars) - HORIZON - 1):
        if i - last < HORIZON:           # one entry per trade window (cooldown)
            continue
        c, c1 = bars[i][C], bars[i - 1][C]
        if c1 <= 0:
            continue
        burst = c / c1 - 1.0
        if burst < BURST:
            continue
        if bars[i][H] < max(b[H] for b in bars[i - LOOKBACK:i]):   # new 4h high
            continue
        base_gain = c / bars[i - BASE][C] - 1.0 if bars[i - BASE][C] > 0 else 0
        if base_gain > 0.20:             # skip already-extended (chasing) — KAITO entered fresh
            continue
        vmean = statistics.mean([b[V] for b in bars[i - LOOKBACK:i]]) or 1e-9
        if bars[i][V] < VOLX * vmean:
            continue
        out.append(i)
        last = i
    return out


def sim(bars, i, policy, **kw):
    """Return signed SPOT fractional return for a long entered at open[i+1]."""
    entry = bars[i + 1][O]
    if entry <= 0:
        return None
    fwd = bars[i + 1: i + 1 + HORIZON]
    a = atr(bars, i)
    peak = entry
    if policy == "hold":
        h = kw["h"]
        return fwd[min(h, len(fwd)) - 1][C] / entry - 1.0
    if policy == "tp":
        tp = entry * (1 + kw["x"])
        for b in fwd:
            if b[H] >= tp:
                return kw["x"]
        return fwd[-1][C] / entry - 1.0
    if policy == "floor":            # profit-trail: arm at `arm`, give back `gb` of peak gain
        gb, arm = kw["gb"], kw["arm"]
        armed = False
        for b in fwd:
            peak = max(peak, b[H])
            gain = peak / entry - 1.0
            if gain >= arm:
                armed = True
            if armed:
                floor = entry * (1 + gain * (1 - gb))
                if b[L] <= floor:
                    return floor / entry - 1.0
        return fwd[-1][C] / entry - 1.0
    if policy == "atr":              # trailing stop k*ATR below peak high
        k = kw["k"]
        for b in fwd:
            peak = max(peak, b[H])
            stop = peak - k * a
            if b[L] <= stop:
                return stop / entry - 1.0
        return fwd[-1][C] / entry - 1.0
    if policy == "scaleout":         # 50% at +1 ATR, trail rest with floor(gb=.35,arm=.01)
        tp = entry + 1.0 * a
        banked = None
        rest_ret = None
        armed = False
        for b in fwd:
            if banked is None and b[H] >= tp:
                banked = tp / entry - 1.0
            peak = max(peak, b[H])
            gain = peak / entry - 1.0
            if gain >= 0.01:
                armed = True
            if armed:
                floor = entry * (1 + gain * 0.65)
                if b[L] <= floor:
                    rest_ret = floor / entry - 1.0
                    break
        if rest_ret is None:
            rest_ret = fwd[-1][C] / entry - 1.0
        if banked is None:
            banked = rest_ret
        return 0.5 * banked + 0.5 * rest_ret
    return None


POLICIES = [
    ("floor gb=.10 (≈LIVE)", "floor", {"gb": 0.10, "arm": 0.0125}),
    ("floor gb=.35",         "floor", {"gb": 0.35, "arm": 0.0125}),
    ("floor gb=.50",         "floor", {"gb": 0.50, "arm": 0.0125}),
    ("floor gb=.65",         "floor", {"gb": 0.65, "arm": 0.0125}),
    ("atr 1.5x",             "atr",   {"k": 1.5}),
    ("atr 2.5x",             "atr",   {"k": 2.5}),
    ("atr 4x",               "atr",   {"k": 4.0}),
    ("tp +3%",               "tp",    {"x": 0.03}),
    ("tp +5%",               "tp",    {"x": 0.05}),
    ("tp +8%",               "tp",    {"x": 0.08}),
    ("hold 1h",              "hold",  {"h": 12}),
    ("hold 4h",              "hold",  {"h": 48}),
    ("hold 12h",             "hold",  {"h": 143}),
    ("scaleout 50%+trail",   "scaleout", {}),
]

# collect entries with timestamps for OOS split
events = []   # (t, {policy_name: ret})
mfe_list = []
for coin in COINS:
    bars = A.candles(d, coin, IV)
    if len(bars) < LOOKBACK + HORIZON + 5:
        continue
    for i in find_entries(bars):
        entry = bars[i + 1][O]
        fwd = bars[i + 1: i + 1 + HORIZON]
        mfe = max(b[H] for b in fwd) / entry - 1.0
        mfe_list.append(mfe)
        rets = {}
        for name, pol, kw in POLICIES:
            r = sim(bars, i, pol, **kw)
            if r is not None:
                rets[name] = r
        events.append((bars[i][0], rets))

n = len(events)
ts = sorted(e[0] for e in events)
mid = ts[n // 2] if n else 0
print(f"# KAITO-like breakout-long exit test — n={n} entries, mean MFE={100*statistics.mean(mfe_list):.2f}%")
print(f"{'policy':<22} {'net_mean%':>9} {'win':>5} {'capture_MFE':>11} {'OOS h1/h2 (net%)':>20}")
print("-" * 74)
rows = []
for name, pol, kw in POLICIES:
    rs = [(t, ev[name]) for t, ev in events if name in ev]
    vals = [r - FEE for _, r in rs]
    if not vals:
        continue
    h1 = [r - FEE for t, r in rs if t <= mid]
    h2 = [r - FEE for t, r in rs if t > mid]
    mean = statistics.mean(vals)
    win = sum(1 for v in vals if v > 0) / len(vals)
    cap = mean / statistics.mean(mfe_list) if mfe_list else 0
    rows.append((mean, name, win, cap,
                 statistics.mean(h1) if h1 else 0, statistics.mean(h2) if h2 else 0))
for mean, name, win, cap, m1, m2 in sorted(rows, reverse=True):
    flag = "✅" if (m1 > 0 and m2 > 0) else "  "
    print(f"{name:<22} {100*mean:>9.3f} {win:>5.2f} {100*cap:>10.0f}% {100*m1:>8.2f} /{100*m2:>7.2f} {flag}")
