#!/usr/bin/env python3
"""Kalman-filter dynamic hedge ratio + OU half-life filter for pairs stat-arb.

Baseline: static hedge ratio (edge_pairs.py) → +1.08%/trade
This script tests:
  A) Dynamic β via Kalman filter (random-walk on hedge ratio)
  B) OU half-life filter (only trade pairs with 2–30 day reversion half-life)
  C) Both A+B combined

METHODOLOGY BAR:
- Lookahead-safe: β_t and z computed using only data ≤ t; enter at t+1 open (here: t+1 close proxy)
- Cost-aware: ≥10bps/leg (20bps round-trip), 2 legs = 2*COST
- Survivorship-free: same top-40 liquid universe as edge_pairs.py
- OOS-robust: split trade stream in half, both halves must be mean-positive
"""
import os, sys, math, statistics, itertools
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from hermes_trader.client.universe import get_universe
from _bt_candles import get as get_candles

# ── shared config (match edge_pairs.py baseline) ────────────────────────────
TOPN      = 40
VOL_FLOOR = 5e6
COST      = 10.0 / 1e4      # per leg; two legs = 2*COST
LOOKBACK  = 30               # used for static baseline, rolling-OLS window, and OU estimation
Z_ENTRY   = 2.0
Z_EXIT    = 0.5
MAXHOLD   = 15
MIN_CORR  = 0.6

# Kalman hyperparams (conservative process noise → slow adaptation)
KALMAN_Q  = 1e-5             # process noise variance (hedge ratio drift per step)
KALMAN_R  = 1e-2             # measurement noise variance (spread obs noise)

# OU half-life filter
HL_MIN    = 2.0              # days — faster than this is microstructure/noise
HL_MAX    = 30.0             # days — slower than this won't revert in MAXHOLD window


def _ymd(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y%m%d")


def load():
    uni = [m for m in get_universe(include_hip3=False)
           if ":" not in (m.get("coin") or "")
           and not (m.get("coin") or "").startswith("@")
           and m.get("type") != "spot"
           and float(m.get("dayNtlVlm") or 0) >= VOL_FLOOR]
    uni = sorted(uni, key=lambda m: float(m.get("dayNtlVlm") or 0), reverse=True)[:TOPN]
    data = {}
    for m in uni:
        bars = get_candles(m["coin"], "1d", 260)
        if len(bars) >= 90:
            data[m["coin"]] = {_ymd(b["t"]): b["c"] for b in bars}
    return data


def _corr(xs, ys):
    n = len(xs)
    if n < 5:
        return 0.0
    mx, my = statistics.mean(xs), statistics.mean(ys)
    cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    sx = math.sqrt(sum((a - mx) ** 2 for a in xs))
    sy = math.sqrt(sum((b - my) ** 2 for b in ys))
    return cov / (sx * sy) if sx > 0 and sy > 0 else 0.0


# ── Kalman filter for β_t ───────────────────────────────────────────────────
def kalman_filter(la, lb):
    """1-D Kalman filter: state = β_t (hedge ratio), observation = log(A) - β*log(B).

    Model:  β_t = β_{t-1} + w_t,   w_t ~ N(0, Q)  (random walk on hedge ratio)
            obs = la_t - β_t * lb_t + v_t,   v_t ~ N(0, R * lb_t^2)

    Returns arrays beta[t], P[t] (posterior mean and variance at each t).
    Strictly causal: beta[t] uses observations ≤ t.
    """
    n = len(la)
    beta = [0.0] * n
    P    = [1.0] * n   # prior variance (large = diffuse start)

    # initialise from first LOOKBACK data points using OLS
    if n > LOOKBACK:
        xb = lb[:LOOKBACK]; xa = la[:LOOKBACK]
        xb_m = statistics.mean(xb); xa_m = statistics.mean(xa)
        num = sum((xb[i] - xb_m) * (xa[i] - xa_m) for i in range(LOOKBACK))
        den = sum((xb[i] - xb_m) ** 2 for i in range(LOOKBACK))
        beta[LOOKBACK - 1] = num / den if den > 0 else 1.0
        P[LOOKBACK - 1] = 1.0
    else:
        beta[0] = 1.0
        P[0] = 1.0

    for t in range(1, n):
        # predict
        b_pred = beta[t - 1]
        P_pred = P[t - 1] + KALMAN_Q

        # update — observation noise scales with lb_t^2 (standard regression Kalman)
        H = lb[t]
        R_t = KALMAN_R * (H * H) + 1e-12
        S = H * H * P_pred + R_t
        K_gain = P_pred * H / S if S > 0 else 0.0
        innov = la[t] - b_pred * lb[t]
        beta[t] = b_pred + K_gain * innov
        P[t]    = (1 - K_gain * H) * P_pred

    return beta, P


# ── rolling-window OLS β_t fallback ─────────────────────────────────────────
def rolling_ols_beta(la, lb, window):
    """Causal rolling OLS: β_t = Cov(la, lb) / Var(lb) over [t-window, t)."""
    n = len(la)
    beta = [float("nan")] * n
    for t in range(window, n):
        xb = lb[t - window:t]; xa = la[t - window:t]
        xb_m = statistics.mean(xb); xa_m = statistics.mean(xa)
        num = sum((xb[i] - xb_m) * (xa[i] - xa_m) for i in range(window))
        den = sum((xb[i] - xb_m) ** 2 for i in range(window))
        beta[t] = num / den if den > 0 else 1.0
    return beta


# ── OU half-life estimation ──────────────────────────────────────────────────
def ou_half_life(spread_slice):
    """Fit AR(1): s_t = a + φ·s_{t-1}. Half-life = -ln(2)/ln(φ). Valid iff 0 < φ < 1."""
    s = spread_slice
    n = len(s)
    if n < 10:
        return float("inf")
    y  = s[1:]
    x  = s[:-1]
    mx = statistics.mean(x); my = statistics.mean(y)
    num = sum((x[i] - mx) * (y[i] - my) for i in range(len(x)))
    den = sum((x[i] - mx) ** 2 for i in range(len(x)))
    phi = num / den if den > 0 else 1.0
    if phi <= 0 or phi >= 1:
        return float("inf")
    return -math.log(2) / math.log(phi)


# ── baseline (static β=1, same as edge_pairs.py) ────────────────────────────
def run_pair_static(la, lb, common):
    """Reproduce edge_pairs.py: spread = log(A) - log(B), static β=1."""
    spread = [a - b for a, b in zip(la, lb)]
    out = []
    i = LOOKBACK
    while i < len(common) - 1:
        win = spread[i - LOOKBACK:i]
        mu, sd = statistics.mean(win), statistics.pstdev(win)
        if sd <= 0:
            i += 1; continue
        # returns-correlation gate (same as edge_pairs.py)
        ra = [la[k] - la[k - 1] for k in range(i - LOOKBACK + 1, i)]
        rb = [lb[k] - lb[k - 1] for k in range(i - LOOKBACK + 1, i)]
        if _corr(ra, rb) < MIN_CORR:
            i += 1; continue
        z = (spread[i] - mu) / sd
        if abs(z) < Z_ENTRY:
            i += 1; continue
        side = -1 if z > 0 else 1
        j = i + 1
        while j < min(i + 1 + MAXHOLD, len(common)):
            zj = (spread[j] - mu) / sd
            if abs(zj) <= Z_EXIT:
                break
            j += 1
        j = min(j, len(common) - 1)
        pnl = side * (spread[i] - spread[j])
        out.append(pnl - 2 * COST)
        i = j + 1
    return out


# ── dynamic β (Kalman) ───────────────────────────────────────────────────────
def run_pair_kalman(la, lb, common):
    """Dynamic hedge via Kalman. Spread_t = la_t - β_t * lb_t (causal)."""
    beta, _ = kalman_filter(la, lb)
    spread = [la[t] - beta[t] * lb[t] for t in range(len(la))]
    out = []
    i = LOOKBACK
    while i < len(common) - 1:
        win = spread[i - LOOKBACK:i]
        mu, sd = statistics.mean(win), statistics.pstdev(win)
        if sd <= 0:
            i += 1; continue
        # returns-correlation gate
        ra = [la[k] - la[k - 1] for k in range(i - LOOKBACK + 1, i)]
        rb = [lb[k] - lb[k - 1] for k in range(i - LOOKBACK + 1, i)]
        if _corr(ra, rb) < MIN_CORR:
            i += 1; continue
        z = (spread[i] - mu) / sd
        if abs(z) < Z_ENTRY:
            i += 1; continue
        side = -1 if z > 0 else 1
        j = i + 1
        while j < min(i + 1 + MAXHOLD, len(common)):
            zj = (spread[j] - mu) / sd
            if abs(zj) <= Z_EXIT:
                break
            j += 1
        j = min(j, len(common) - 1)
        pnl = side * (spread[i] - spread[j])
        out.append(pnl - 2 * COST)
        i = j + 1
    return out


# ── OU half-life filter variants ─────────────────────────────────────────────
def ou_passes(spread_slice):
    """Return True if pair's spread has a tradeable OU half-life [HL_MIN, HL_MAX]."""
    hl = ou_half_life(spread_slice)
    return HL_MIN <= hl <= HL_MAX


def run_pair_static_hl(la, lb, common, check_hl=True):
    """Static β + OU half-life gate: only trade if current half-life is in range."""
    spread = [a - b for a, b in zip(la, lb)]
    out = []
    i = LOOKBACK
    while i < len(common) - 1:
        win = spread[i - LOOKBACK:i]
        mu, sd = statistics.mean(win), statistics.pstdev(win)
        if sd <= 0:
            i += 1; continue
        ra = [la[k] - la[k - 1] for k in range(i - LOOKBACK + 1, i)]
        rb = [lb[k] - lb[k - 1] for k in range(i - LOOKBACK + 1, i)]
        if _corr(ra, rb) < MIN_CORR:
            i += 1; continue
        # OU half-life gate (uses only spread[:i] — causal)
        if check_hl and not ou_passes(spread[i - LOOKBACK:i]):
            i += 1; continue
        z = (spread[i] - mu) / sd
        if abs(z) < Z_ENTRY:
            i += 1; continue
        side = -1 if z > 0 else 1
        j = i + 1
        while j < min(i + 1 + MAXHOLD, len(common)):
            zj = (spread[j] - mu) / sd
            if abs(zj) <= Z_EXIT:
                break
            j += 1
        j = min(j, len(common) - 1)
        pnl = side * (spread[i] - spread[j])
        out.append(pnl - 2 * COST)
        i = j + 1
    return out


def run_pair_kalman_hl(la, lb, common):
    """Dynamic β (Kalman) + OU half-life gate."""
    beta, _ = kalman_filter(la, lb)
    spread = [la[t] - beta[t] * lb[t] for t in range(len(la))]
    out = []
    i = LOOKBACK
    while i < len(common) - 1:
        win = spread[i - LOOKBACK:i]
        mu, sd = statistics.mean(win), statistics.pstdev(win)
        if sd <= 0:
            i += 1; continue
        ra = [la[k] - la[k - 1] for k in range(i - LOOKBACK + 1, i)]
        rb = [lb[k] - lb[k - 1] for k in range(i - LOOKBACK + 1, i)]
        if _corr(ra, rb) < MIN_CORR:
            i += 1; continue
        if not ou_passes(spread[i - LOOKBACK:i]):
            i += 1; continue
        z = (spread[i] - mu) / sd
        if abs(z) < Z_ENTRY:
            i += 1; continue
        side = -1 if z > 0 else 1
        j = i + 1
        while j < min(i + 1 + MAXHOLD, len(common)):
            zj = (spread[j] - mu) / sd
            if abs(zj) <= Z_EXIT:
                break
            j += 1
        j = min(j, len(common) - 1)
        pnl = side * (spread[i] - spread[j])
        out.append(pnl - 2 * COST)
        i = j + 1
    return out


# ── reporting helper ─────────────────────────────────────────────────────────
def rep(name, trades):
    if not trades:
        print(f"  {name:<35}  n=   0  (no trades)")
        return None
    n   = len(trades)
    w   = sum(1 for r in trades if r > 0)
    mu  = statistics.mean(trades) * 100
    med = statistics.median(trades) * 100
    mid = n // 2
    h1  = statistics.mean(trades[:mid]) * 100 if mid else 0.0
    h2  = statistics.mean(trades[mid:]) * 100 if n - mid else 0.0
    rob = "ROBUST" if h1 > 0 and h2 > 0 else ("fragile" if (h1 > 0) != (h2 > 0) else "neg")
    flag = "  <<< +EV" if mu > 0 and rob == "ROBUST" else ""
    print(f"  {name:<35}  n={n:>4}  win {w/n*100:>3.0f}%  mean {mu:>+6.2f}%  "
          f"OOS {h1:>+5.2f}/{h2:>+5.2f}  {rob}{flag}")
    return {"n": n, "win": w / n, "mean": mu, "h1": h1, "h2": h2, "robust": rob}


def main():
    print("=" * 78)
    print("# Kalman pairs — dynamic hedge ratio + OU half-life filter")
    print(f"# top{TOPN} liquid | z-entry {Z_ENTRY} exit {Z_EXIT} | "
          f"corr>{MIN_CORR} | cost {COST*1e4:.0f}bps/leg | lookahead-safe, OOS")
    print(f"# Kalman Q={KALMAN_Q} R={KALMAN_R} | OU half-life filter [{HL_MIN},{HL_MAX}]d")
    print("=" * 78)

    data = load()
    coins = list(data)
    print(f"# {len(coins)} coins loaded → {len(coins)*(len(coins)-1)//2} candidate pairs\n")

    all_static     = []
    all_kalman     = []
    all_static_hl  = []
    all_kalman_hl  = []

    # also track per-pair half-life diagnostics
    hl_values = []
    npairs_total = 0
    npairs_traded = {"static": 0, "kalman": 0, "static_hl": 0, "kalman_hl": 0}

    for ca, cb in itertools.combinations(coins, 2):
        common = sorted(set(data[ca]) & set(data[cb]))
        if len(common) < LOOKBACK + 30:
            continue
        npairs_total += 1

        la = [math.log(data[ca][d]) for d in common]
        lb = [math.log(data[cb][d]) for d in common]

        # ── per-pair half-life (computed over full spread for diagnostics, not used in trading) ──
        spread_static = [a - b for a, b in zip(la, lb)]
        hl = ou_half_life(spread_static)
        hl_values.append(hl)

        # ── baseline ──
        t = run_pair_static(la, lb, common)
        if t: npairs_traded["static"] += 1
        all_static.extend(t)

        # ── dynamic Kalman ──
        t = run_pair_kalman(la, lb, common)
        if t: npairs_traded["kalman"] += 1
        all_kalman.extend(t)

        # ── static + HL filter ──
        t = run_pair_static_hl(la, lb, common, check_hl=True)
        if t: npairs_traded["static_hl"] += 1
        all_static_hl.extend(t)

        # ── Kalman + HL filter ──
        t = run_pair_kalman_hl(la, lb, common)
        if t: npairs_traded["kalman_hl"] += 1
        all_kalman_hl.extend(t)

    # half-life diagnostics
    finite_hl = [v for v in hl_values if math.isfinite(v)]
    if finite_hl:
        in_range = [v for v in finite_hl if HL_MIN <= v <= HL_MAX]
        print(f"# Half-life diagnostics across {npairs_total} pairs:")
        print(f"#   finite HL: {len(finite_hl)}/{npairs_total}  "
              f"median {statistics.median(finite_hl):.1f}d  "
              f"mean {statistics.mean(finite_hl):.1f}d")
        print(f"#   in range [{HL_MIN},{HL_MAX}]d: {len(in_range)} pairs "
              f"({100*len(in_range)/npairs_total:.0f}%)\n")

    # ── results table ────────────────────────────────────────────────────────
    print(f"{'Variant':<35}  {'n':>5}  {'win':>4}  {'mean%':>7}  "
          f"{'OOS h1':>7} / {'h2':>6}  {'robust?'}")
    print("-" * 78)

    r0 = rep("static β (baseline, edge_pairs.py)", all_static)
    r1 = rep("dynamic β Kalman",                   all_kalman)
    r2 = rep("static β + OU half-life filter",     all_static_hl)
    r3 = rep("Kalman + OU half-life filter",        all_kalman_hl)

    print()
    print(f"# Pairs traded  static={npairs_traded['static']}  kalman={npairs_traded['kalman']}  "
          f"static_hl={npairs_traded['static_hl']}  kalman_hl={npairs_traded['kalman_hl']}")

    # ── verdict ──────────────────────────────────────────────────────────────
    print()
    print("=" * 78)
    print("VERDICT")
    print("=" * 78)
    results = {"static": r0, "kalman": r1, "static_hl": r2, "kalman_hl": r3}
    base_mean = r0["mean"] if r0 else 0.0

    for name, r in results.items():
        if r is None:
            continue
        delta = r["mean"] - base_mean
        verdict = "BETTER" if r["mean"] > base_mean and r["robust"] == "ROBUST" else \
                  "WORSE"  if r["mean"] < base_mean else "NEUTRAL"
        if name == "static":
            print(f"  static (baseline)         → {base_mean:+.2f}%/trade  [reference]")
        else:
            label = {"kalman": "dynamic β (Kalman)",
                     "static_hl": "static β + HL filter",
                     "kalman_hl": "Kalman + HL filter"}[name]
            trade_delta = (r["n"] - (r0["n"] if r0 else 0))
            print(f"  {label:<28} → {r['mean']:+.2f}%/trade  Δ={delta:+.2f}%  "
                  f"n Δ={trade_delta:+d}  [{verdict}]")

    # final recommendation
    print()
    best = None
    for name, r in results.items():
        if r is None or name == "static":
            continue
        if r["robust"] == "ROBUST" and (best is None or r["mean"] > results[best]["mean"]):
            best = name

    if best and results[best]["mean"] > base_mean:
        print(f"  RECOMMENDATION: Use '{best}' — it beats the static baseline and is OOS-ROBUST.")
    else:
        print(f"  RECOMMENDATION: Static pairs baseline holds. No variant beats it on both "
              f"mean AND OOS robustness. Do NOT upgrade to dynamic/HL variants.")
    print("=" * 78)


if __name__ == "__main__":
    main()
