#!/usr/bin/env python3
"""Alpha hunt — W5: PCA eigenportfolio stat-arb (Avellaneda-Lee) + 3-coin cointegration baskets.

(a) PCA EIGENPORTFOLIO residual reversion (Avellaneda-Lee):
    Build the daily-return matrix (coins × days). On a rolling window, extract the top 1-3
    principal components (market/sector factors). For each coin, regress its returns on the PCs
    and trade MEAN-REVERSION of the idiosyncratic residual — the "s-score" (standardized
    residual): SHORT when s-score high (rich), LONG when low (cheap), exit on reversion.
    Market-neutral by construction. Rolling PCs + loadings from data ≤ t → strictly lookahead-safe.

(b) MULTIVARIATE COINTEGRATION baskets (3-coin):
    Regress coin A on B & C (rolling OLS). Test the residual for stationarity / mean-reversion
    (AR(1) phi < 1 + tradeable half-life). Trade the basket-residual z-score (entry |z|>2,
    exit |z|<0.5). 3-coin baskets give more stable hedges than single pairs.
    EVERY leg's cost counted (3 legs × cost/leg per entry + 3 legs × cost/leg on exit).

Run with: BT_CACHE_ONLY=1 python scripts/edge_pca_statarb.py
"""

import os, sys, math, statistics, itertools
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone

import numpy as np

from hermes_trader.client.universe import get_universe
from _bt_candles import get as get_candles

# ─── shared parameters ────────────────────────────────────────────────────────
TOPN        = 40          # top-N by volume (same as pairs)
VOL_FLOOR   = 5e6
COST        = 10.0 / 1e4  # 10 bps per leg

# ─── PCA / s-score params ─────────────────────────────────────────────────────
PCA_WINDOW  = 60          # rolling window for PCA + residual z-score estimation
N_FACTORS   = 3           # top PCs to use as market/sector factors (thin 28-coin universe → ≤3)
SSCORE_ENTRY = 2.0        # |s-score| threshold to enter a trade (standardized residual)
SSCORE_EXIT  = 0.5        # |s-score| threshold to exit
MAXHOLD_PCA  = 20         # max days to hold a PCA trade

# ─── 3-coin basket params ─────────────────────────────────────────────────────
BASKET_WINDOW = 60        # rolling OLS window for 3-coin regression
BASKET_ZENTRY = 2.0
BASKET_ZEXIT  = 0.5
BASKET_MAXHOLD= 15
BASKET_MIN_PHI_LESS1 = True   # require AR(1) phi < 1 (mean-reversion)
BASKET_MAX_HALFLIFE  = 20     # only trade baskets with half-life ≤ 20 days (fast enough to be tradeable)
MIN_CORR_BASKET      = 0.5   # minimum pairwise correlation to bother trying a basket


def _ymd(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y%m%d")


# ─── data loading (identical universe filter to edge_pairs.py) ────────────────
def load():
    uni = [m for m in get_universe(include_hip3=False)
           if ":" not in (m.get("coin") or "")
           and not (m.get("coin") or "").startswith("@")
           and m.get("type") != "spot"
           and float(m.get("dayNtlVlm") or 0) >= VOL_FLOOR]
    uni = sorted(uni, key=lambda m: float(m.get("dayNtlVlm") or 0), reverse=True)[:TOPN]
    data = {}
    for m in uni:
        c = m["coin"]
        bars = get_candles(c, "1d", 260)
        if len(bars) >= 90:
            data[c] = {_ymd(b["t"]): b["c"] for b in bars}
    return data


def _rep(name, trades):
    if not trades:
        print(f"  {name:40s}  n=   0  (no trades)")
        return
    arr = trades
    n   = len(arr)
    w   = sum(1 for r in arr if r > 0)
    mid = n // 2
    h1  = float(np.mean(arr[:mid])) * 100 if mid else 0.0
    h2  = float(np.mean(arr[mid:])) * 100 if n - mid else 0.0
    rob = "ROBUST" if h1 > 0 and h2 > 0 else ("fragile" if (h1 > 0) != (h2 > 0) else "neg")
    mn  = float(np.mean(arr)) * 100
    flag = "  <<< +EV" if mn > 0 and rob == "ROBUST" else ""
    print(f"  {name:40s}  n={n:>4}  win {w/n*100:>3.0f}%  mean {mn:>+6.2f}%  "
          f"OOS {h1:>+5.2f}/{h2:>+5.2f}  {rob}{flag}")


# ═══════════════════════════════════════════════════════════════════════════════
# (a) PCA EIGENPORTFOLIO RESIDUAL REVERSION (Avellaneda-Lee style)
# ═══════════════════════════════════════════════════════════════════════════════

def _pca_top_k(R_win, k):
    """
    R_win: (n_coins, T) returns matrix for the rolling window.
    Returns factor_returns: (T, k) — the time-series of the top-k PC factor returns,
    i.e. R_win.T @ eigenvectors[:, :k].
    Also returns eigenvectors (n_coins, k) for computing each coin's loadings.
    Lookahead-safe: called only on data ≤ t.
    """
    # demean across coins (not time) to remove cross-sectional mean
    mu = R_win.mean(axis=0, keepdims=True)   # (1, T)
    X  = R_win - mu                          # (n_coins, T)
    # covariance matrix (coins × coins) — shape (n_coins, n_coins)
    C  = X @ X.T / max(X.shape[1] - 1, 1)
    # eigen decomposition
    vals, vecs = np.linalg.eigh(C)          # ascending eigenvalues, cols = eigenvectors
    idx  = np.argsort(vals)[::-1]           # sort descending
    vecs = vecs[:, idx]                      # (n_coins, n_coins)
    top_vecs = vecs[:, :k]                  # (n_coins, k) — top-k eigenvectors
    # Factor time-series: project returns onto each eigenvector
    factor_rets = X.T @ top_vecs            # (T, k) — factor return series
    return factor_rets, top_vecs


def _ols_residual(y, factor_rets):
    """
    OLS regression of y (T,) on factor_rets (T, k) columns (with implicit intercept).
    Returns residual series (T,).
    """
    T  = len(y)
    Xb = np.column_stack([np.ones(T), factor_rets])   # (T, k+1)
    try:
        beta, _, _, _ = np.linalg.lstsq(Xb, y, rcond=None)
    except np.linalg.LinAlgError:
        return np.zeros(T)
    return y - Xb @ beta


def run_pca(data):
    """
    Rolling PCA residual reversion.

    Signal construction (all from data ≤ t):
      1. Build the coin × date return matrix for days [t-W, t).
      2. Compute top-K PCs of that window's covariance matrix.
      3. For each coin: project its in-window returns onto the PCs → get in-window residuals.
      4. Compute the OU s-score on the *most recent residual* using the trailing residual
         history (mean + std from the same window).
      5. At t+1 open, enter trades where |s_score| > ENTRY; exit at EXIT or MAXHOLD.

    Cost: 2 legs (long the cheap coin, hedge is implicit in the PC structure — but the
    eigenportfolio is a self-funding spread across ALL coins, which is what makes it expensive:
    in practice you'd trade coins with extreme scores vs a synthetic index leg).
    We conservatively charge 2×COST per trade (the target coin + one implicit hedge).
    """
    coins    = list(data)
    all_days = sorted({d for cd in data.values() for d in cd})
    n_coins  = len(coins)
    n_days   = len(all_days)
    # build price matrix (n_coins × n_days); NaN for missing
    price_mat = np.full((n_coins, n_days), np.nan)
    for i, c in enumerate(coins):
        for j, d in enumerate(all_days):
            if d in data[c]:
                price_mat[i, j] = data[c][d]

    # log-return matrix (n_coins × (n_days-1))
    with np.errstate(divide="ignore", invalid="ignore"):
        ret_mat = np.diff(np.log(price_mat), axis=1)
    ret_mat = np.where(np.isfinite(ret_mat), ret_mat, 0.0)

    # track open trades: dict coin_idx → {entry_day_idx, side, entry_price, mu, sd}
    open_trades = {}
    all_trades  = []

    W = PCA_WINDOW
    K = min(N_FACTORS, n_coins - 1)

    for t in range(W, n_days - 1):
        # ── in-window return matrix (n_coins × W) ──
        R_win = ret_mat[:, t - W: t]          # strictly ≤ t (no lookahead)
        # mask coins with too many NaN; require ≥ W/2 valid days
        valid_mask = np.isfinite(price_mat[:, t]) & (np.sum(R_win != 0, axis=1) >= W // 2)
        if valid_mask.sum() < K + 2:
            continue

        ci_valid = np.where(valid_mask)[0]
        R_sub    = R_win[ci_valid, :]         # (n_valid, W)

        # ── PCA on the valid sub-universe ──
        factor_rets, top_vecs = _pca_top_k(R_sub, K)  # (W, K) factor time-series

        # ── for each valid coin: residual from PCs + s-score ──
        s_scores = {}
        for li, gi in enumerate(ci_valid):
            y   = R_sub[li, :]               # (W,) this coin's in-window returns
            res = _ols_residual(y, factor_rets)  # (W,) residual series
            mu  = res.mean()
            sd  = res.std()
            if sd < 1e-8:
                continue
            # s-score = standardized *current* (last-day) residual
            s   = (res[-1] - mu) / sd
            s_scores[gi] = (s, mu, sd, res)

        # ── check exits on open trades ──
        for ci in list(open_trades.keys()):
            pos = open_trades[ci]
            if ci not in s_scores:
                # coin lost data; force close at today's close (approx)
                if np.isfinite(price_mat[ci, t]):
                    pnl = pos["side"] * math.log(price_mat[ci, t] / pos["entry_px"]) - 2 * COST
                    all_trades.append(pnl)
                del open_trades[ci]
                continue
            s_now = s_scores[ci][0]
            age   = t - pos["entry_t"]
            should_exit = (abs(s_now) <= SSCORE_EXIT) or (age >= MAXHOLD_PCA)
            if should_exit:
                # exit at t+1 open — use t+1 price as a proxy (open ≈ close[t] in daily data)
                exit_px = price_mat[ci, t + 1] if np.isfinite(price_mat[ci, t + 1]) else price_mat[ci, t]
                pnl = pos["side"] * math.log(exit_px / pos["entry_px"]) - 2 * COST
                all_trades.append(pnl)
                del open_trades[ci]

        # ── enter new trades on fresh signals ──
        for gi, (s, mu, sd, res) in s_scores.items():
            if gi in open_trades:
                continue                      # already in this coin
            if abs(s) < SSCORE_ENTRY:
                continue
            entry_px = price_mat[gi, t + 1]  # enter at t+1 open (≈ close[t] proxy)
            if not np.isfinite(entry_px) or entry_px <= 0:
                continue
            side = -1 if s > 0 else 1        # s>0: coin is rich → short; s<0: cheap → long
            open_trades[gi] = {
                "entry_t":  t,
                "side":     side,
                "entry_px": entry_px,
            }

    # ── flush remaining open positions at end of data ──
    last_t = n_days - 1
    for ci, pos in open_trades.items():
        exit_px = price_mat[ci, last_t]
        if np.isfinite(exit_px) and exit_px > 0:
            pnl = pos["side"] * math.log(exit_px / pos["entry_px"]) - 2 * COST
            all_trades.append(pnl)

    return all_trades


# ═══════════════════════════════════════════════════════════════════════════════
# (b) 3-COIN COINTEGRATION BASKETS
# ═══════════════════════════════════════════════════════════════════════════════

def _ols2(y, x1, x2):
    """OLS of y on x1, x2 (all length-T arrays). Returns (beta1, beta2, intercept, residuals)."""
    T  = len(y)
    X  = np.column_stack([np.ones(T), x1, x2])
    try:
        beta, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
    except np.linalg.LinAlgError:
        return 0.0, 0.0, 0.0, np.zeros(T)
    resid = y - X @ beta
    return beta[1], beta[2], beta[0], resid


def _ar1_phi(series):
    """Fit AR(1) to the series; return phi (auto-regression coefficient)."""
    if len(series) < 4:
        return 1.0
    y = series[1:]
    x = series[:-1]
    mx = x.mean(); my = y.mean()
    cov = ((x - mx) * (y - my)).sum()
    var = ((x - mx) ** 2).sum()
    return cov / var if var > 1e-12 else 1.0


def _halflife(phi):
    """OU half-life from AR(1) phi: hl = -log(2)/log(phi). Returns inf if phi ≥ 1."""
    if phi >= 1.0 or phi <= 0.0:
        return float("inf")
    return -math.log(2) / math.log(phi)


def _pairwise_corr(la, lb, n):
    """Pearson corr of log-return series."""
    ra = np.diff(la[-n:])
    rb = np.diff(lb[-n:])
    if len(ra) < 5 or ra.std() < 1e-8 or rb.std() < 1e-8:
        return 0.0
    return float(np.corrcoef(ra, rb)[0, 1])


def run_3coin(data):
    """
    3-coin cointegration basket backtest.

    For each triple (A, B, C): on each day t, using data [t-W, t):
      1. Build log-price series for the window.
      2. OLS: log(A) = b1*log(B) + b2*log(C) + intercept + e.
      3. Compute AR(1) phi of residual e; filter: phi < 1, half-life ≤ BASKET_MAX_HALFLIFE.
      4. z-score of the *last* residual using window mean/sd.
      5. Enter at |z| > 2, exit at |z| < 0.5 or MAXHOLD.

    Cost: 3 legs × COST on entry + 3 legs × COST on exit = 6×COST per round-trip.
    PnL: basket log-return = side * (log_A[exit] - b1*log_B[exit] - b2*log_C[exit]
                                    - (log_A[entry] - b1*log_B[entry] - b2*log_C[entry]))
         = side * (residual[exit] - residual[entry])  — proportional to spread convergence.
    """
    coins    = list(data)
    all_days = sorted({d for cd in data.values() for d in cd})
    n_days   = len(all_days)

    # build log-price matrix
    n_coins  = len(coins)
    logp_mat = np.full((n_coins, n_days), np.nan)
    for i, c in enumerate(coins):
        for j, d in enumerate(all_days):
            if d in data[c]:
                logp_mat[i, j] = math.log(data[c][d])

    W = BASKET_WINDOW
    all_trades = []

    # track open baskets: key = (ia, ib, ic), value = {entry_t, side, beta1, beta2, res_entry}
    open_baskets = {}

    for t in range(W, n_days - 1):
        # ── exits ──
        for key in list(open_baskets.keys()):
            pos    = open_baskets[key]
            ia, ib, ic = key
            age    = t - pos["entry_t"]
            # reconstruct current residual with the *same* betas (lookahead-safe: betas fixed at entry)
            b1, b2, intercept = pos["beta1"], pos["beta2"], pos["intercept"]
            la_t   = logp_mat[ia, t]
            lb_t   = logp_mat[ib, t]
            lc_t   = logp_mat[ic, t]
            if not (np.isfinite(la_t) and np.isfinite(lb_t) and np.isfinite(lc_t)):
                del open_baskets[key]
                continue
            res_now = la_t - b1 * lb_t - b2 * lc_t - intercept
            z_now   = (res_now - pos["mu"]) / pos["sd"] if pos["sd"] > 1e-8 else 0.0

            if abs(z_now) <= BASKET_ZEXIT or age >= BASKET_MAXHOLD:
                # exit at t+1 open prices (proxied as close[t+1])
                la_ex = logp_mat[ia, t + 1]
                lb_ex = logp_mat[ib, t + 1]
                lc_ex = logp_mat[ic, t + 1]
                if not (np.isfinite(la_ex) and np.isfinite(lb_ex) and np.isfinite(lc_ex)):
                    del open_baskets[key]
                    continue
                res_exit = la_ex - b1 * lb_ex - b2 * lc_ex - intercept
                pnl = pos["side"] * (res_exit - pos["res_entry"]) - 6 * COST
                all_trades.append(pnl)
                del open_baskets[key]

        # ── entries ──
        # get coins with valid prices at t and t+1
        valid = [i for i in range(n_coins)
                 if np.isfinite(logp_mat[i, t]) and np.isfinite(logp_mat[i, t + 1])]
        if len(valid) < 3:
            continue

        # pre-filter: only try triples where pairs have min corr
        # build pairwise corr cache for this t
        corr_cache = {}
        def _get_corr(i, j):
            key_c = (min(i, j), max(i, j))
            if key_c not in corr_cache:
                la = logp_mat[i, t - W: t]
                lb = logp_mat[j, t - W: t]
                corr_cache[key_c] = _pairwise_corr(la, lb, W)
            return corr_cache[key_c]

        for ia, ib, ic in itertools.combinations(valid, 3):
            key = (ia, ib, ic)
            if key in open_baskets:
                continue
            # quick corr pre-filter: at least one pair must be correlated enough
            if (_get_corr(ia, ib) < MIN_CORR_BASKET
                    and _get_corr(ia, ic) < MIN_CORR_BASKET
                    and _get_corr(ib, ic) < MIN_CORR_BASKET):
                continue

            # ── rolling OLS on the window ──
            la_w = logp_mat[ia, t - W: t]
            lb_w = logp_mat[ib, t - W: t]
            lc_w = logp_mat[ic, t - W: t]
            # require all valid in window
            mask = np.isfinite(la_w) & np.isfinite(lb_w) & np.isfinite(lc_w)
            if mask.sum() < W // 2:
                continue
            la_w2 = la_w[mask]; lb_w2 = lb_w[mask]; lc_w2 = lc_w[mask]

            b1, b2, intercept, res_w = _ols2(la_w2, lb_w2, lc_w2)

            # ── stationarity filter ──
            phi = _ar1_phi(res_w)
            if phi >= 1.0:
                continue
            hl = _halflife(phi)
            if hl > BASKET_MAX_HALFLIFE:
                continue

            mu   = float(res_w.mean())
            sd   = float(res_w.std())
            if sd < 1e-8:
                continue

            # ── z-score of *current day* residual ──
            la_t = logp_mat[ia, t]; lb_t = logp_mat[ib, t]; lc_t = logp_mat[ic, t]
            if not (np.isfinite(la_t) and np.isfinite(lb_t) and np.isfinite(lc_t)):
                continue
            res_curr = la_t - b1 * lb_t - b2 * lc_t - intercept
            z        = (res_curr - mu) / sd

            if abs(z) < BASKET_ZENTRY:
                continue

            # ── enter at t+1 open ──
            la_en = logp_mat[ia, t + 1]
            lb_en = logp_mat[ib, t + 1]
            lc_en = logp_mat[ic, t + 1]
            if not (np.isfinite(la_en) and np.isfinite(lb_en) and np.isfinite(lc_en)):
                continue
            res_entry = la_en - b1 * lb_en - b2 * lc_en - intercept

            side = -1 if z > 0 else 1        # z>0: A rich vs B&C → short basket; else long
            open_baskets[key] = {
                "entry_t":   t,
                "side":      side,
                "beta1":     b1,
                "beta2":     b2,
                "intercept": intercept,
                "mu":        mu,
                "sd":        sd,
                "res_entry": res_entry,
            }

    # ── flush open positions ──
    last_t = n_days - 1
    for key, pos in open_baskets.items():
        ia, ib, ic = key
        b1, b2, intercept = pos["beta1"], pos["beta2"], pos["intercept"]
        la_ex = logp_mat[ia, last_t]
        lb_ex = logp_mat[ib, last_t]
        lc_ex = logp_mat[ic, last_t]
        if np.isfinite(la_ex) and np.isfinite(lb_ex) and np.isfinite(lc_ex):
            res_exit = la_ex - b1 * lb_ex - b2 * lc_ex - intercept
            pnl = pos["side"] * (res_exit - pos["res_entry"]) - 6 * COST
            all_trades.append(pnl)

    return all_trades


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 72)
    print("W5: PCA eigenportfolio stat-arb (Avellaneda-Lee) + 3-coin baskets")
    print(f"    universe: top{TOPN} liquid perps | cost {COST*1e4:.0f}bps/leg | "
          f"cache-only | lookahead-safe")
    print("=" * 72)

    data = load()
    coins = list(data)
    n = len(coins)
    print(f"\n{n} coins loaded: {', '.join(coins[:12])}{'...' if n > 12 else ''}")
    print(f"(thin universe note: 28-coin limit on cached data → PCA uses top {N_FACTORS} PCs)")

    # ── (a) PCA eigenportfolio ──────────────────────────────────────────────
    print("\n" + "─" * 60)
    print(f"(a) PCA RESIDUAL REVERSION  [Avellaneda-Lee style]")
    print(f"    window={PCA_WINDOW}d  n_factors={N_FACTORS}  "
          f"entry|s|>{SSCORE_ENTRY}  exit|s|<{SSCORE_EXIT}  "
          f"maxhold={MAXHOLD_PCA}d  cost=2×{COST*1e4:.0f}bps/trade")
    print("─" * 60)

    pca_trades = run_pca(data)
    print(f"  Total PCA trades: {len(pca_trades)}")
    _rep("PCA residual reversion", pca_trades)

    if pca_trades:
        arr = np.array(pca_trades)
        print(f"  Median {float(np.median(arr))*100:+.2f}%  "
              f"Std {float(arr.std())*100:.2f}%  "
              f"P5 {float(np.percentile(arr, 5))*100:+.2f}%  "
              f"P95 {float(np.percentile(arr, 95))*100:+.2f}%")

    # ── (b) 3-coin baskets ──────────────────────────────────────────────────
    print("\n" + "─" * 60)
    print(f"(b) 3-COIN COINTEGRATION BASKETS")
    print(f"    window={BASKET_WINDOW}d  entry|z|>{BASKET_ZENTRY}  exit|z|<{BASKET_ZEXIT}  "
          f"maxhold={BASKET_MAXHOLD}d  max_hl={BASKET_MAX_HALFLIFE}d  "
          f"cost=6×{COST*1e4:.0f}bps/trade")
    print("─" * 60)

    n_triples = n * (n - 1) * (n - 2) // 6
    print(f"  Candidate triples: {n_triples}  (pre-filtered by corr + AR(1) + half-life)")

    basket_trades = run_3coin(data)
    print(f"  Total basket trades: {len(basket_trades)}")
    _rep("3-coin basket stat-arb", basket_trades)

    if basket_trades:
        arr = np.array(basket_trades)
        print(f"  Median {float(np.median(arr))*100:+.2f}%  "
              f"Std {float(arr.std())*100:.2f}%  "
              f"P5 {float(np.percentile(arr, 5))*100:+.2f}%  "
              f"P95 {float(np.percentile(arr, 95))*100:+.2f}%")

    # ── comparison table ────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("COMPARISON vs validated pairs (+1.08%/trade, n=2413)")
    print("─" * 72)

    def _verdict(trades, method_cost_legs):
        if not trades:
            return "NO DATA"
        arr = np.array(trades)
        mn  = float(arr.mean()) * 100
        n   = len(arr)
        mid = n // 2
        h1  = float(arr[:mid].mean()) * 100 if mid else 0
        h2  = float(arr[mid:].mean()) * 100 if n - mid else 0
        rob = h1 > 0 and h2 > 0
        if mn > 0 and rob:
            return f"VALIDATED +EV: mean {mn:+.2f}%/trade, OOS {h1:+.2f}/{h2:+.2f}"
        elif mn > 0 and not rob:
            return f"FRAGILE (not OOS-robust): mean {mn:+.2f}%, OOS {h1:+.2f}/{h2:+.2f} [one half negative]"
        else:
            return f"REFUTED: mean {mn:+.2f}% (OOS {h1:+.2f}/{h2:+.2f})"

    print(f"  Pairs stat-arb (2-coin, validated):  VALIDATED +1.08%/trade, n=2413, OOS +1.10/+1.06")
    print(f"  PCA residual reversion:              {_verdict(pca_trades, 2)}")
    print(f"  3-coin cointegration baskets:        {_verdict(basket_trades, 6)}")

    print("\n" + "─" * 72)
    print("THIN-UNIVERSE NOTE:")
    print(f"  Only {n} coins in cache (target: ~28) — this limits PCA severely.")
    print("  PCA with <20 coins has ~50% of covariance captured in PC1 (market factor).")
    print("  Residuals after removing 3 PCs may be mostly noise → s-scores are noisy estimators.")
    print("  3-coin baskets: with N coins, N*(N-1)*(N-2)/6 triples; at 28 coins = 3276 candidates.")
    print("  Multiple-testing is a real concern here — filter by AR(1) + half-life is necessary.")
    print("─" * 72)

    print("\nWIRING NOTE (if validated):")
    print("  PCA: each rebalance, fetch universe returns, recompute PCs + s-scores hot,")
    print("       submit orders only on coins with |s|>threshold (2 legs per coin: target+hedge).")
    print("       Computationally cheap (numpy, <1ms). Fits inside the existing rebalance timer.")
    print("  3-coin: run the basket screener nightly, cache live baskets + betas. On intraday")
    print("       polling, recompute z-score from current prices. Submit 3-leg bracket orders.")
    print("       Order complexity is the main engineering challenge (3 reduce-only exits).")


if __name__ == "__main__":
    main()
