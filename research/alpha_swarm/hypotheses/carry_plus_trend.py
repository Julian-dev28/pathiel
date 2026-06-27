"""A15 carry_plus_trend 💰 — combine funding-carry with price-momentum.
DATA-BLOCKED: dataset funding is a single scalar snapshot per coin (current rate),
NOT a time series, so carry cannot be backtested historically. Below: the combination
logic, unit-tested on SYNTHETIC funding history so it's wired and ready the moment
data_logger funding history exists.

Rule (once history exists): day i, carry_c = trailing-mean funding (high funding paid TO
shorts => short rich-funding / long cheap-funding); trend_c = trailing-L return rank.
combined score = z(carry rank) + z(trend rank); long top / short bottom, market-neutral.
"""
import statistics

def zrank(d):
    """dict coin->value -> dict coin->z-scored cross-sectional rank."""
    items = list(d.items())
    vals = [v for _, v in items]
    mu = statistics.mean(vals); sd = statistics.pstdev(vals) + 1e-12
    return {c: (v - mu) / sd for c, v in items}

def combined_score(carry, trend, w_carry=1.0, w_trend=1.0):
    """carry: dict coin->avg funding (higher = expensive long => short signal, so NEGATE).
       trend: dict coin->trailing return (higher = momentum long).
       Returns combined long/short score (positive = long)."""
    zc = zrank(carry); zt = zrank(trend)
    return {c: w_trend * zt[c] - w_carry * zc[c] for c in carry if c in trend}

def pick(score, m):
    s = sorted(score.items(), key=lambda kv: kv[1])
    return [c for c, _ in s[-m:]], [c for c, _ in s[:m]]  # longs, shorts

# ---- synthetic unit test ----
if __name__ == "__main__":
    # coin A: cheap funding (-) + strong trend (+) => should be a LONG
    # coin D: expensive funding (+) + weak trend (-) => should be a SHORT
    carry = {"A": -0.01, "B": 0.0, "C": 0.005, "D": 0.02, "E": -0.003, "F": 0.01}
    trend = {"A": 0.30, "B": 0.05, "C": -0.02, "D": -0.25, "E": 0.10, "F": -0.10}
    score = combined_score(carry, trend)
    longs, shorts = pick(score, 2)
    assert "A" in longs, f"A should be long, got {longs}"
    assert "D" in shorts, f"D should be short, got {shorts}"
    # carry-only sanity: most expensive funding is the strongest short tilt
    zc = zrank(carry)
    assert max(zc, key=zc.get) == "D"
    print("carry_plus_trend logic unit-tests PASS (synthetic).")
    print("longs:", longs, "shorts:", shorts)
    print("STATUS: BLOCKED-DATA — needs data_logger funding history to backtest.")
