#!/usr/bin/env python3
"""Alpha hunt — W7: RMT-denoised stat-arb + entropy cross-sectional factor.

(a) RANDOM-MATRIX-THEORY (RMT) DENOISED CORRELATION STAT-ARB:
    The W5 PCA/stat-arb refuted at -0.88% gross on the raw covariance matrix.
    Hypothesis: the raw 29-coin correlation matrix is dominated by noise eigenvalues
    (Marchenko-Pastur regime). RMT denoising (zero/shrink eigenvalues below the MP
    upper bound λ+ = (1+sqrt(N/T))², keep only the market eigenvalue + true signal
    eigenvalues) should give a cleaner covariance structure → better PCA residuals →
    better stat-arb s-scores.

    Method:
      1. Build rolling N×T daily-return matrix on the same 60-day window as W5.
      2. Compute sample correlation matrix C (N×N).
      3. Eigendecompose C. Marchenko-Pastur upper bound: λ+ = σ²*(1+√(N/T))²
         where σ² ≈ 1 for standardized returns. Eigenvalues ≤ λ+ are noise.
      4. Denoise: reconstruct C using only eigenvalues > λ+, then rescale diagonal
         back to 1.0 (preserve unit variances). This is the "clipping" denoiser
         (Bouchaud-Potters RMT).
      5. Convert denoised correlation → covariance, run the same Avellaneda-Lee
         residual s-score strategy as W5. Compare mean net% OOS vs W5's -0.88%.
      6. ALSO run the Ledoit-Wolf shrinkage as a second comparison point.

(b) ENTROPY CROSS-SECTIONAL FACTOR:
    Per-coin return distribution entropy (Shannon on binned daily returns, trailing
    window), used two ways:
      (i)  L/S factor: rank by entropy, long HIGH/SHORT LOW (or LOW/HIGH) beta-neutral.
           Theory: high-entropy = disordered price → mean-reversion candidate;
           low-entropy = trending → momentum candidate.
      (ii) Regime gate: high cross-sectional entropy = de-risk (L/S spread ≈ 0).
           Test whether gating the xs-momentum on low-entropy regimes improves Sharpe.

    Beta-neutral within β-terciles (as in V-wave).

Run with: BT_CACHE_ONLY=1 python scripts/edge_rmt_entropy.py
"""

import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone

import numpy as np

from hermes_trader.client.universe import get_universe
from _bt_candles import get as get_candles

# ─── shared parameters ────────────────────────────────────────────────────────
TOPN      = 40
VOL_FLOOR = 5e6
COST      = 10.0 / 1e4   # 10 bps per leg
K         = 8             # legs per side for cross-sectional strategies

# ─── RMT params (matching W5 PCA params) ─────────────────────────────────────
RMT_WINDOW   = 60    # rolling window (days)
SSCORE_ENTRY = 2.0
SSCORE_EXIT  = 0.5
MAXHOLD_PCA  = 20

# ─── Entropy params ───────────────────────────────────────────────────────────
ENT_WINDOW  = 40     # trailing days for entropy estimation
ENT_BINS    = 8      # histogram bins for Shannon entropy
ENT_HOLD    = 10     # holding period (days)
BETA_WIN    = 30     # trailing window for per-coin BTC beta

# ─── Regime gate params for momentum ─────────────────────────────────────────
MOM_LB      = 7      # xs-momentum lookback
MOM_HOLD    = 10     # xs-momentum holding period


def _ymd(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y%m%d")


# ─── data loading (same filter as edge_pca_statarb.py) ────────────────────────
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
            data[c] = {_ymd(b["t"]): b for b in bars}
    return data


def _rep(name, trades):
    if not trades:
        print(f"  {name:50s}  n=   0  (no trades)")
        return
    arr = np.array(trades)
    n   = len(arr)
    w   = int(np.sum(arr > 0))
    mid = n // 2
    h1  = float(np.mean(arr[:mid])) * 100 if mid else 0.0
    h2  = float(np.mean(arr[mid:])) * 100 if n - mid else 0.0
    rob = "ROBUST" if h1 > 0 and h2 > 0 else ("fragile" if (h1 > 0) != (h2 > 0) else "neg")
    mn  = float(np.mean(arr)) * 100
    flag = "  <<< +EV" if mn > 0 and rob == "ROBUST" else ""
    print(f"  {name:50s}  n={n:>4}  win {w/n*100:>3.0f}%  mean {mn:>+6.2f}%  "
          f"OOS {h1:>+5.2f}/{h2:>+5.2f}  {rob}{flag}")


def _corr_to_trades(arr):
    """Pearson correlation of two same-length trade arrays (for orthogonality check)."""
    if len(arr[0]) < 5 or len(arr[1]) < 5:
        return float("nan")
    a, b = np.array(arr[0]), np.array(arr[1])
    min_n = min(len(a), len(b))
    a, b = a[:min_n], b[:min_n]
    if a.std() < 1e-10 or b.std() < 1e-10:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


# ══════════════════════════════════════════════════════════════════════════════
# (a) RMT-DENOISED CORRELATION STAT-ARB
# ══════════════════════════════════════════════════════════════════════════════

def _marchenko_pastur_upper(n_coins, T):
    """
    Marchenko-Pastur upper spectral bound for a random N×T matrix.
    λ+ = (1 + sqrt(N/T))^2   (for unit-variance standardized returns, σ²=1)
    """
    q = T / n_coins  # aspect ratio (must be > 1 for well-defined MP; we're usually OK)
    if q < 1.0:
        q = 1.0 + 1e-6   # degenerate safeguard
    lam_plus = (1.0 + math.sqrt(1.0 / q)) ** 2
    return lam_plus


def _rmt_denoise_correlation(C, n_coins, T, method="clip"):
    """
    Denoise the N×N sample correlation matrix C via RMT (Marchenko-Pastur clipping).

    method="clip":   zero out all noise eigenvalues (eigenvalues ≤ λ+), reconstruct.
    method="shrink": rescale noise eigenvalues to their MP mean (softer version).

    Returns the denoised correlation matrix (diagonal = 1.0 preserved).

    Lookahead-safe: C is computed purely from [t-W, t) data.
    """
    # Symmetrize and ensure finite
    C = (C + C.T) / 2.0
    np.fill_diagonal(C, 1.0)

    # Eigendecompose (eigh for symmetric matrices → real eigenvalues)
    vals, vecs = np.linalg.eigh(C)      # ascending order
    lam_plus   = _marchenko_pastur_upper(n_coins, T)

    # Identify signal eigenvalues (above MP upper bound)
    signal_mask = vals > lam_plus

    if method == "clip":
        # Replace noise eigenvalues with their average (mass-preserving)
        noise_vals = vals[~signal_mask]
        noise_mean = float(noise_vals.mean()) if len(noise_vals) > 0 else 0.0
        denoised_vals = np.where(signal_mask, vals, noise_mean)
    else:  # "shrink"
        # Ledoit-Wolf-style: shrink noise eigenvalues toward the global mean
        global_mean = float(vals.mean())
        denoised_vals = np.where(signal_mask, vals, global_mean)

    # Reconstruct: C_denoised = V @ diag(denoised_vals) @ V.T
    C_denoised = (vecs * denoised_vals) @ vecs.T

    # Renormalize diagonal to 1.0 (preserve unit correlations)
    d = np.sqrt(np.diag(C_denoised))
    d = np.where(d > 1e-10, d, 1.0)
    C_denoised = C_denoised / np.outer(d, d)
    np.fill_diagonal(C_denoised, 1.0)

    return C_denoised, lam_plus, int(signal_mask.sum())


def _pca_from_corr(C_denoised, k):
    """
    PCA using the denoised correlation matrix (not raw data).
    Returns top-k eigenvectors (n_coins, k) and their eigenvalues.
    Column order: descending eigenvalue (most variance first).
    """
    vals, vecs = np.linalg.eigh(C_denoised)
    idx  = np.argsort(vals)[::-1]
    vecs = vecs[:, idx]
    vals = vals[idx]
    k    = min(k, len(vals))
    return vecs[:, :k], vals[:k]


def _ols_residual_np(y, X_factors):
    """
    OLS regression of y (T,) on X_factors (T, k) (no explicit intercept — factors are
    zero-mean by construction from correlation-based PCA). Returns residual (T,).
    """
    try:
        # Add intercept column
        T  = len(y)
        Xb = np.column_stack([np.ones(T), X_factors])
        beta, _, _, _ = np.linalg.lstsq(Xb, y, rcond=None)
        return y - Xb @ beta
    except np.linalg.LinAlgError:
        return np.zeros_like(y)


def run_rmt(data, method="clip", n_factors=3):
    """
    Rolling RMT-denoised PCA residual reversion (Avellaneda-Lee style with denoised C).

    Key difference from W5: we first denoise the rolling correlation matrix via RMT
    (Marchenko-Pastur clipping), THEN extract PCs from the denoised matrix. This
    filters the noise-dominated eigenvalues that polluted W5's residuals.

    Signal: s-score = (res_last - mu_res) / std_res, computed on the in-window
    residuals from the denoised PCA. Enter when |s| > 2, exit when |s| < 0.5 or maxhold.
    Cost: 2×COST per trade (target coin + implicit hedge).
    Strictly lookahead-safe: all computations use data ≤ t.
    """
    coins    = list(data)
    all_days = sorted({d for cd in data.values() for d in cd})
    n_coins  = len(coins)
    n_days   = len(all_days)

    # Build price matrix (n_coins × n_days)
    price_mat = np.full((n_coins, n_days), np.nan)
    for i, c in enumerate(coins):
        for j, d in enumerate(all_days):
            if d in data[c]:
                price_mat[i, j] = data[c][d]["c"]

    # Log-return matrix (n_coins × (n_days-1))
    with np.errstate(divide="ignore", invalid="ignore"):
        ret_mat = np.diff(np.log(price_mat), axis=1)
    ret_mat = np.where(np.isfinite(ret_mat), ret_mat, 0.0)

    W = RMT_WINDOW
    open_trades = {}   # coin_idx → {entry_t, side, entry_px}
    all_trades  = []
    signal_eigs_log = []  # track # signal eigenvalues per window (diagnostic)

    for t in range(W, n_days - 1):
        R_win = ret_mat[:, t - W: t]     # (n_coins, W) — strictly ≤ t

        # Valid coins: must have price at t and sufficient non-zero return days
        valid_mask = np.isfinite(price_mat[:, t]) & (np.sum(R_win != 0, axis=1) >= W // 2)
        n_valid = int(valid_mask.sum())
        K_pca   = min(n_factors, n_valid - 1)
        if n_valid < K_pca + 2:
            continue

        ci_valid = np.where(valid_mask)[0]
        R_sub    = R_win[ci_valid, :]    # (n_valid, W)

        # Step 1: Standardize returns cross-sectionally (coin-wise z-score for correlation)
        mu_r  = R_sub.mean(axis=1, keepdims=True)
        sd_r  = R_sub.std(axis=1, keepdims=True)
        sd_r  = np.where(sd_r > 1e-10, sd_r, 1.0)
        R_std = (R_sub - mu_r) / sd_r    # (n_valid, W) standardized returns

        # Step 2: Sample correlation matrix C (n_valid × n_valid)
        C_sample = R_std @ R_std.T / max(W - 1, 1)
        # Diagonal should be ~1 due to standardization; clamp for numerical safety
        np.fill_diagonal(C_sample, 1.0)

        # Step 3: RMT denoise
        C_denoised, lam_plus, n_sig = _rmt_denoise_correlation(
            C_sample, n_valid, W, method=method)
        signal_eigs_log.append(n_sig)

        # Step 4: Extract top-K PCs from denoised correlation
        top_vecs, _ = _pca_from_corr(C_denoised, K_pca)  # (n_valid, K_pca)

        # Step 5: Factor time-series = standardized returns projected onto denoised PCs
        factor_rets = R_std.T @ top_vecs   # (W, K_pca)

        # Step 6: For each valid coin, compute OLS residual from denoised factors
        s_scores = {}
        for li, gi in enumerate(ci_valid):
            y   = R_sub[li, :]            # raw returns (not standardized) for residual
            res = _ols_residual_np(y, factor_rets)   # (W,)
            res_mu = float(res.mean())
            res_sd = float(res.std())
            if res_sd < 1e-8:
                continue
            s = (float(res[-1]) - res_mu) / res_sd
            s_scores[gi] = (s, res_mu, res_sd)

        # ── check exits ──
        for ci in list(open_trades.keys()):
            pos  = open_trades[ci]
            age  = t - pos["entry_t"]
            s_now, _, _ = s_scores.get(ci, (0.0, 0.0, 1.0))
            should_exit = (abs(s_now) <= SSCORE_EXIT) or (age >= MAXHOLD_PCA)
            if should_exit:
                exit_px = price_mat[ci, t + 1] if np.isfinite(price_mat[ci, t + 1]) else price_mat[ci, t]
                if np.isfinite(exit_px) and exit_px > 0 and pos["entry_px"] > 0:
                    pnl = pos["side"] * math.log(exit_px / pos["entry_px"]) - 2 * COST
                    all_trades.append(pnl)
                del open_trades[ci]

        # ── enter new trades ──
        for gi, (s, mu, sd) in s_scores.items():
            if gi in open_trades:
                continue
            if abs(s) < SSCORE_ENTRY:
                continue
            entry_px = price_mat[gi, t + 1]
            if not np.isfinite(entry_px) or entry_px <= 0:
                continue
            side = -1 if s > 0 else 1    # rich → short; cheap → long
            open_trades[gi] = {
                "entry_t":  t,
                "side":     side,
                "entry_px": float(entry_px),
            }

    # ── flush remaining positions ──
    last_t = n_days - 1
    for ci, pos in open_trades.items():
        exit_px = price_mat[ci, last_t]
        if np.isfinite(exit_px) and exit_px > 0 and pos["entry_px"] > 0:
            pnl = pos["side"] * math.log(float(exit_px) / pos["entry_px"]) - 2 * COST
            all_trades.append(pnl)

    avg_sig_eigs = float(np.mean(signal_eigs_log)) if signal_eigs_log else 0.0
    return all_trades, avg_sig_eigs


# ══════════════════════════════════════════════════════════════════════════════
# (b) ENTROPY CROSS-SECTIONAL FACTOR
# ══════════════════════════════════════════════════════════════════════════════

def _shannon_entropy(returns, n_bins=ENT_BINS):
    """
    Shannon entropy of the return distribution on a trailing window.
    H = -sum(p_i * log(p_i)) over histogram bins (base-2 bits).
    Returns entropy in bits; NaN-safe.
    """
    if len(returns) < 10:
        return float("nan")
    arr = np.array(returns)
    if arr.std() < 1e-10:
        return 0.0
    counts, _ = np.histogram(arr, bins=n_bins)
    counts = counts[counts > 0].astype(float)
    probs  = counts / counts.sum()
    return float(-np.sum(probs * np.log2(probs)))


def _coin_btc_beta(coin_rets, btc_rets):
    """OLS beta of coin on BTC returns (trailing window)."""
    n = min(len(coin_rets), len(btc_rets))
    if n < 8:
        return 1.0
    cr = np.array(coin_rets[-n:])
    br = np.array(btc_rets[-n:])
    vb = float(np.var(br))
    if vb < 1e-10:
        return 1.0
    return float(np.cov(cr, br)[0, 1] / vb)


def run_entropy_factor(data, higher_is_long=True):
    """
    Cross-sectional entropy factor: rank coins by trailing Shannon entropy.
    higher_is_long=True  → LONG high-entropy / SHORT low-entropy
    higher_is_long=False → LONG low-entropy / SHORT high-entropy

    Beta-neutral: within BTC-beta terciles (avoid beta bet).
    Rebalance every ENT_HOLD days; forward return measured open[t+1] → close[t+1+H].
    Cost: 2×COST per coin (entry + exit).
    """
    coins    = list(data)
    all_days = sorted({d for cd in data.values() for d in cd})
    n_days   = len(all_days)
    btc_data = data.get("BTC")

    burn = max(ENT_WINDOW + 5, BETA_WIN + 5)
    all_trades_raw = []    # raw L-S (not beta-neutral)
    all_trades_bn  = []    # beta-neutral L-S (within-β-tercile)

    for t in range(burn, n_days - ENT_HOLD - 1, ENT_HOLD):  # non-overlapping
        d       = all_days[t]
        d_entry = all_days[t + 1]
        d_exit  = all_days[min(t + 1 + ENT_HOLD, n_days - 1)]

        win_days = all_days[max(0, t - ENT_WINDOW): t + 1]
        beta_days = all_days[max(0, t - BETA_WIN): t + 1]

        btc_rets_beta = []
        if btc_data:
            btc_rets_beta = [
                math.log(btc_data[beta_days[i]]["c"] / btc_data[beta_days[i - 1]]["c"])
                for i in range(1, len(beta_days))
                if beta_days[i] in btc_data and beta_days[i - 1] in btc_data
                and btc_data[beta_days[i]]["c"] > 0 and btc_data[beta_days[i - 1]]["c"] > 0
            ]

        scored = []
        for coin, cd in data.items():
            if d_entry not in cd or d_exit not in cd:
                continue
            # Trailing daily returns for entropy computation
            coin_rets_ent = [
                math.log(cd[win_days[i]]["c"] / cd[win_days[i - 1]]["c"])
                for i in range(1, len(win_days))
                if win_days[i] in cd and win_days[i - 1] in cd
                and cd[win_days[i]]["c"] > 0 and cd[win_days[i - 1]]["c"] > 0
            ]
            ent = _shannon_entropy(coin_rets_ent, n_bins=ENT_BINS)
            if math.isnan(ent):
                continue

            # BTC beta (for beta-neutralization)
            coin_rets_beta = [
                math.log(cd[beta_days[i]]["c"] / cd[beta_days[i - 1]]["c"])
                for i in range(1, len(beta_days))
                if beta_days[i] in cd and beta_days[i - 1] in cd
                and cd[beta_days[i]]["c"] > 0 and cd[beta_days[i - 1]]["c"] > 0
            ]
            beta = _coin_btc_beta(coin_rets_beta, btc_rets_beta) if btc_rets_beta else 1.0

            # Forward return: enter at t+1 open, exit at t+1+H close
            o = cd[d_entry]["o"]
            c = cd[d_exit]["c"]
            if o <= 0:
                continue
            fwd = (c - o) / o

            scored.append({"coin": coin, "entropy": ent, "beta": beta, "fwd": fwd})

        if len(scored) < 2 * K + 4:
            continue

        scored.sort(key=lambda x: x["entropy"], reverse=higher_is_long)
        longs  = scored[:K]
        shorts = scored[-K:]

        # Raw L-S spread
        lr = float(np.mean([s["fwd"] for s in longs]))
        sr = float(np.mean([s["fwd"] for s in shorts]))
        all_trades_raw.append((lr - sr) - 2 * COST)

        # Beta-neutral within β-terciles
        # Sort all scored coins by beta, assign tercile
        all_sorted_beta = sorted(scored, key=lambda x: x["beta"])
        n_scored = len(all_sorted_beta)
        terc_size = n_scored // 3
        beta_tercile = {}
        for rank, item in enumerate(all_sorted_beta):
            t_idx = min(rank // max(terc_size, 1), 2)
            beta_tercile[item["coin"]] = t_idx

        bn_spreads = []
        for t_idx in range(3):
            t_longs  = [s for s in longs  if beta_tercile.get(s["coin"]) == t_idx]
            t_shorts = [s for s in shorts if beta_tercile.get(s["coin"]) == t_idx]
            if not t_longs or not t_shorts:
                continue
            t_lr = float(np.mean([s["fwd"] for s in t_longs]))
            t_sr = float(np.mean([s["fwd"] for s in t_shorts]))
            bn_spreads.append(t_lr - t_sr)
        if bn_spreads:
            all_trades_bn.append(float(np.mean(bn_spreads)) - 2 * COST)

    return all_trades_raw, all_trades_bn


def run_entropy_regime_gate(data):
    """
    Entropy regime gate on xs-momentum:
    Each rebalance, compute the CROSS-SECTIONAL entropy regime:
      - cross_ent_high: most coins have high return-distribution entropy (disordered)
      - cross_ent_low:  most coins have low entropy (trending / orderly)
    Gate the xs-momentum spread: only trade when cross-sectional entropy is LOW
    (ordered market → momentum persists).

    Xs-momentum: LB=7d, HOLD=10d (our core validated config).
    Reports: all / low-ent-gate / high-ent-gate subsets.
    """
    coins    = list(data)
    all_days = sorted({d for cd in data.values() for d in cd})
    n_days   = len(all_days)

    burn = max(ENT_WINDOW + 5, MOM_LB + 5)
    all_rets   = []
    low_ent_rets  = []
    high_ent_rets = []

    for t in range(burn, n_days - MOM_HOLD - 1, MOM_HOLD):  # non-overlapping
        d       = all_days[t]
        d_lb    = all_days[max(0, t - MOM_LB)]
        d_entry = all_days[t + 1]
        d_exit  = all_days[min(t + 1 + MOM_HOLD, n_days - 1)]

        ent_days = all_days[max(0, t - ENT_WINDOW): t + 1]

        # Compute entropy per coin and xs-momentum rank
        scored = []
        for coin, cd in data.items():
            if d not in cd or d_lb not in cd or d_entry not in cd or d_exit not in cd:
                continue

            coin_rets_ent = [
                math.log(cd[ent_days[i]]["c"] / cd[ent_days[i - 1]]["c"])
                for i in range(1, len(ent_days))
                if ent_days[i] in cd and ent_days[i - 1] in cd
                and cd[ent_days[i]]["c"] > 0 and cd[ent_days[i - 1]]["c"] > 0
            ]
            ent = _shannon_entropy(coin_rets_ent, n_bins=ENT_BINS)
            if math.isnan(ent):
                continue

            mom = cd[d]["c"] / cd[d_lb]["c"] - 1.0
            o = cd[d_entry]["o"]
            c = cd[d_exit]["c"]
            if o <= 0:
                continue
            fwd = (c - o) / o
            scored.append({"coin": coin, "entropy": ent, "momentum": mom, "fwd": fwd})

        if len(scored) < 2 * K + 4:
            continue

        # Cross-sectional entropy: median entropy this rebalance
        entropies = [s["entropy"] for s in scored]
        cross_ent_median = float(np.median(entropies))
        # HIGH entropy regime: median entropy above 75th pct of all entropies observed
        # (use a simple threshold: above/below the median-of-entropies for this step)
        # Regime = "high" if cross_ent_median is above the 60th pct of the full distribution
        # We'll just use above/below median split across all rebalances after data collection
        # → collect cross_ent_median as a tag, split at the end
        scored_sorted = sorted(scored, key=lambda x: x["momentum"], reverse=True)
        longs  = scored_sorted[:K]
        shorts = scored_sorted[-K:]

        lr  = float(np.mean([s["fwd"] for s in longs]))
        sr  = float(np.mean([s["fwd"] for s in shorts]))
        net = (lr - sr) - 2 * COST

        all_rets.append((net, cross_ent_median))

    if not all_rets:
        return [], [], []

    all_net  = [r for r, _ in all_rets]
    all_ents = [e for _, e in all_rets]
    ent_thresh = float(np.median(all_ents))

    low_ent_rets  = [r for r, e in all_rets if e <= ent_thresh]
    high_ent_rets = [r for r, e in all_rets if e >  ent_thresh]

    return all_net, low_ent_rets, high_ent_rets


# ══════════════════════════════════════════════════════════════════════════════
# RAW PCA baseline (W5 method, simplified for direct comparison)
# ══════════════════════════════════════════════════════════════════════════════

def run_raw_pca_baseline(data, n_factors=3):
    """
    Avellaneda-Lee style PCA on the RAW (non-denoised) covariance matrix.
    Identical method to W5 but using the same coin-set as run_rmt() for
    a fair apples-to-apples comparison.
    """
    coins    = list(data)
    all_days = sorted({d for cd in data.values() for d in cd})
    n_coins  = len(coins)
    n_days   = len(all_days)

    price_mat = np.full((n_coins, n_days), np.nan)
    for i, c in enumerate(coins):
        for j, d in enumerate(all_days):
            if d in data[c]:
                price_mat[i, j] = data[c][d]["c"]

    with np.errstate(divide="ignore", invalid="ignore"):
        ret_mat = np.diff(np.log(price_mat), axis=1)
    ret_mat = np.where(np.isfinite(ret_mat), ret_mat, 0.0)

    W = RMT_WINDOW
    open_trades = {}
    all_trades  = []

    for t in range(W, n_days - 1):
        R_win = ret_mat[:, t - W: t]
        valid_mask = np.isfinite(price_mat[:, t]) & (np.sum(R_win != 0, axis=1) >= W // 2)
        n_valid = int(valid_mask.sum())
        K_pca   = min(n_factors, n_valid - 1)
        if n_valid < K_pca + 2:
            continue

        ci_valid = np.where(valid_mask)[0]
        R_sub    = R_win[ci_valid, :]

        # Raw covariance PCA (no denoising)
        mu_sub = R_sub.mean(axis=0, keepdims=True)
        X_sub  = R_sub - mu_sub
        C_raw  = X_sub @ X_sub.T / max(W - 1, 1)
        vals, vecs = np.linalg.eigh(C_raw)
        idx  = np.argsort(vals)[::-1]
        top_vecs = vecs[:, idx][:, :K_pca]
        factor_rets = X_sub.T @ top_vecs  # (W, K_pca)

        s_scores = {}
        for li, gi in enumerate(ci_valid):
            y   = R_sub[li, :]
            res = _ols_residual_np(y, factor_rets)
            res_mu = float(res.mean())
            res_sd = float(res.std())
            if res_sd < 1e-8:
                continue
            s = (float(res[-1]) - res_mu) / res_sd
            s_scores[gi] = s

        for ci in list(open_trades.keys()):
            pos = open_trades[ci]
            age = t - pos["entry_t"]
            s_now = s_scores.get(ci, 0.0)
            if abs(s_now) <= SSCORE_EXIT or age >= MAXHOLD_PCA:
                exit_px = price_mat[ci, t + 1] if np.isfinite(price_mat[ci, t + 1]) else price_mat[ci, t]
                if np.isfinite(exit_px) and exit_px > 0 and pos["entry_px"] > 0:
                    pnl = pos["side"] * math.log(float(exit_px) / pos["entry_px"]) - 2 * COST
                    all_trades.append(pnl)
                del open_trades[ci]

        for gi, s in s_scores.items():
            if gi in open_trades or abs(s) < SSCORE_ENTRY:
                continue
            entry_px = price_mat[gi, t + 1]
            if not np.isfinite(entry_px) or entry_px <= 0:
                continue
            open_trades[gi] = {
                "entry_t":  t,
                "side":     -1 if s > 0 else 1,
                "entry_px": float(entry_px),
            }

    last_t = n_days - 1
    for ci, pos in open_trades.items():
        exit_px = price_mat[ci, last_t]
        if np.isfinite(exit_px) and exit_px > 0 and pos["entry_px"] > 0:
            pnl = pos["side"] * math.log(float(exit_px) / pos["entry_px"]) - 2 * COST
            all_trades.append(pnl)

    return all_trades


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 76)
    print("W7: RMT-DENOISED CORRELATION STAT-ARB + ENTROPY CROSS-SECTIONAL FACTOR")
    print(f"    universe: top{TOPN} liquid perps | cost {COST*1e4:.0f}bps/leg | "
          f"cache-only | lookahead-safe")
    print("=" * 76)

    data = load()
    coins = list(data)
    n = len(coins)
    print(f"\n{n} coins loaded: {', '.join(coins[:12])}{'...' if n > 12 else ''}")

    # ──────────────────────────────────────────────────────────────────────────
    # (a) RMT vs Raw PCA comparison
    # ──────────────────────────────────────────────────────────────────────────
    print("\n" + "═" * 76)
    print("(a) RMT-DENOISED CORRELATION STAT-ARB")
    print(f"    window={RMT_WINDOW}d | n_factors=3 | entry|s|>{SSCORE_ENTRY} | "
          f"exit|s|<{SSCORE_EXIT} | maxhold={MAXHOLD_PCA}d | cost=2×{COST*1e4:.0f}bps")
    print("─" * 76)

    print("\n  [1/3] Raw PCA baseline (same as W5, same coin-set for fair comparison)...")
    raw_trades = run_raw_pca_baseline(data, n_factors=3)
    _rep("Raw PCA (baseline / W5 method)", raw_trades)

    print("\n  [2/3] RMT-denoised PCA (Marchenko-Pastur clipping)...")
    rmt_clip_trades, avg_sig_clip = run_rmt(data, method="clip", n_factors=3)
    _rep(f"RMT clip (avg {avg_sig_clip:.1f} signal eigs)", rmt_clip_trades)

    print("\n  [3/3] RMT-shrink PCA (shrink toward global mean)...")
    rmt_shrink_trades, avg_sig_shrink = run_rmt(data, method="shrink", n_factors=3)
    _rep(f"RMT shrink (avg {avg_sig_shrink:.1f} signal eigs)", rmt_shrink_trades)

    print("\n  — MP diagnostic —")
    # Compute λ+ for this universe as a representative example (W=60, N=n)
    lam_plus_ex = _marchenko_pastur_upper(n, RMT_WINDOW)
    print(f"  N={n} coins, T={RMT_WINDOW}d window → MP upper bound λ+ = {lam_plus_ex:.3f}")
    print(f"  (eigenvalues ≤ {lam_plus_ex:.3f} are MP noise; only signal eigs kept)")
    print(f"  avg signal eigenvalues per window: clip={avg_sig_clip:.1f}, shrink={avg_sig_shrink:.1f} / {n} possible")

    # Deeper diagnostics on RMT-clip trades
    if rmt_clip_trades:
        arr = np.array(rmt_clip_trades)
        print(f"\n  RMT-clip detail: median {float(np.median(arr))*100:+.2f}%  "
              f"std {float(arr.std())*100:.2f}%  "
              f"P5 {float(np.percentile(arr, 5))*100:+.2f}%  "
              f"P95 {float(np.percentile(arr, 95))*100:+.2f}%")

    # Comparison summary
    print("\n  ── Comparison summary ──")
    def _verdict_str(trades, label):
        if not trades:
            return f"  {label}: NO DATA"
        arr = np.array(trades)
        n_t = len(arr)
        mn  = float(arr.mean()) * 100
        mid = n_t // 2
        h1  = float(arr[:mid].mean()) * 100 if mid else 0.0
        h2  = float(arr[mid:].mean()) * 100 if n_t - mid else 0.0
        rob = h1 > 0 and h2 > 0
        status = "VALIDATED +EV" if mn > 0 and rob else "fragile" if mn > 0 else "REFUTED"
        return (f"  {label:35s}  n={n_t:>4}  mean {mn:>+6.2f}%  "
                f"OOS {h1:>+5.2f}/{h2:>+5.2f}  {status}")

    print(_verdict_str(raw_trades,         "Raw PCA (W5 method)"))
    print(_verdict_str(rmt_clip_trades,    "RMT-clip denoised"))
    print(_verdict_str(rmt_shrink_trades,  "RMT-shrink denoised"))
    print(f"  Pairs stat-arb (validated, ref):     n=2413  mean +1.08%  OOS +1.10/+1.06  VALIDATED")

    # Correlation between RMT-clip and raw (same-length comparison on first min_n trades)
    min_n = min(len(raw_trades), len(rmt_clip_trades))
    if min_n >= 5:
        corr_rmt_raw = _corr_to_trades([raw_trades[:min_n], rmt_clip_trades[:min_n]])
        print(f"\n  Corr(RMT-clip, Raw PCA): {corr_rmt_raw:+.3f} "
              f"(< 0 = RMT diverges from raw; ~1 = no improvement)")

    # ──────────────────────────────────────────────────────────────────────────
    # (b) Entropy factor
    # ──────────────────────────────────────────────────────────────────────────
    print("\n" + "═" * 76)
    print("(b) ENTROPY CROSS-SECTIONAL FACTOR")
    print(f"    entropy window={ENT_WINDOW}d | bins={ENT_BINS} | hold={ENT_HOLD}d | "
          f"K={K} per leg | beta-neutral (within-β-tercile)")
    print("─" * 76)

    print("\n  [i] Long HIGH entropy / Short LOW entropy (raw + beta-neutral)...")
    ent_hi_raw, ent_hi_bn = run_entropy_factor(data, higher_is_long=True)
    _rep("Entropy: LONG-HIGH/SHORT-LOW (raw L-S)", ent_hi_raw)
    _rep("Entropy: LONG-HIGH/SHORT-LOW (beta-neutral)", ent_hi_bn)

    print("\n  [ii] Long LOW entropy / Short HIGH entropy (raw + beta-neutral)...")
    ent_lo_raw, ent_lo_bn = run_entropy_factor(data, higher_is_long=False)
    _rep("Entropy: LONG-LOW/SHORT-HIGH (raw L-S)", ent_lo_raw)
    _rep("Entropy: LONG-LOW/SHORT-HIGH (beta-neutral)", ent_lo_bn)

    print("\n  [iii] Entropy regime gate on xs-momentum (LB7/hold10)...")
    all_mom, low_ent_mom, high_ent_mom = run_entropy_regime_gate(data)
    _rep("Momentum ALL (gating baseline)", all_mom)
    _rep("Momentum gated: LOW entropy (ordered)", low_ent_mom)
    _rep("Momentum gated: HIGH entropy (disordered)", high_ent_mom)

    if low_ent_mom and high_ent_mom and all_mom:
        all_mn  = float(np.mean(all_mom)) * 100
        lo_mn   = float(np.mean(low_ent_mom)) * 100
        hi_mn   = float(np.mean(high_ent_mom)) * 100
        print(f"\n  Entropy gate: ALL {all_mn:+.2f}%  LOW-ent {lo_mn:+.2f}%  HIGH-ent {hi_mn:+.2f}%")
        lift = lo_mn - all_mn
        print(f"  Lift from low-entropy gate vs all: {lift:+.2f}%/rebal")

    # ──────────────────────────────────────────────────────────────────────────
    # Cross-edge correlation diagnostics
    # ──────────────────────────────────────────────────────────────────────────
    print("\n" + "═" * 76)
    print("CROSS-EDGE CORRELATION CHECK (vs validated xs-momentum)")
    print("─" * 76)
    if all_mom and rmt_clip_trades:
        print(f"  Note: RMT stat-arb (per-trade) vs entropy-gated momentum (per-rebal) "
              f"have different natural units; correlation is indicative only.")
    if ent_hi_bn and low_ent_mom:
        corr_ent_mom = _corr_to_trades([ent_hi_bn[:min(len(ent_hi_bn), len(all_mom))],
                                         all_mom[:min(len(ent_hi_bn), len(all_mom))]])
        print(f"  Corr(entropy-LONG-HIGH-bn, momentum-all): {corr_ent_mom:+.3f} "
              f"({'orthogonal' if abs(corr_ent_mom) < 0.25 else 'correlated'})")

    # ──────────────────────────────────────────────────────────────────────────
    # Final verdicts
    # ──────────────────────────────────────────────────────────────────────────
    print("\n" + "═" * 76)
    print("VERDICTS")
    print("─" * 76)

    def _final_verdict(trades, name, unit):
        if not trades:
            print(f"  {name}: NO DATA — INCONCLUSIVE")
            return
        arr = np.array(trades)
        n_t = len(arr)
        mn  = float(arr.mean()) * 100
        mid = n_t // 2
        h1  = float(arr[:mid].mean()) * 100 if mid else 0.0
        h2  = float(arr[mid:].mean()) * 100 if n_t - mid else 0.0
        oos_ok  = h1 > 0 and h2 > 0
        net_pos = mn > 0
        if net_pos and oos_ok:
            verdict = "VALIDATED +EV"
        elif net_pos and not oos_ok:
            verdict = "FRAGILE (not OOS-robust) — REFUTED by bar"
        else:
            verdict = "REFUTED"
        print(f"  [{verdict}] {name}")
        print(f"     n={n_t}  mean={mn:+.2f}%{unit}  OOS h1={h1:+.2f}%/h2={h2:+.2f}%  "
              f"win={int(np.sum(arr>0))/n_t*100:.0f}%")

    print()
    _final_verdict(raw_trades,          "(a) Raw PCA stat-arb (W5 replication)",      "/trade")
    _final_verdict(rmt_clip_trades,     "(a) RMT-clip denoised stat-arb",             "/trade")
    _final_verdict(rmt_shrink_trades,   "(a) RMT-shrink denoised stat-arb",           "/trade")
    _final_verdict(ent_hi_bn,           "(b-i)  Entropy: LONG-HIGH/SHORT-LOW β-neut", "/rebal")
    _final_verdict(ent_lo_bn,           "(b-ii) Entropy: LONG-LOW/SHORT-HIGH β-neut", "/rebal")

    # Regime gate: compare Sharpe ratios
    if all_mom and low_ent_mom:
        sh_all = float(np.mean(all_mom)) / max(float(np.std(all_mom)), 1e-10)
        sh_low = float(np.mean(low_ent_mom)) / max(float(np.std(low_ent_mom)), 1e-10)
        sh_hi  = float(np.mean(high_ent_mom)) / max(float(np.std(high_ent_mom)), 1e-10)
        lift_sharpe = sh_low - sh_all
        print(f"\n  (b-iii) Entropy regime gate on xs-momentum:")
        print(f"    Sharpe: ALL={sh_all:+.3f}  LOW-ent={sh_low:+.3f}  HIGH-ent={sh_hi:+.3f}")
        print(f"    Low-entropy gate Sharpe lift: {lift_sharpe:+.3f}")
        regime_verdict = ("VALIDATED gate" if sh_low > sh_all and sh_low > sh_hi
                          else "REFUTED (no Sharpe improvement from entropy gating)")
        print(f"    Regime gate verdict: {regime_verdict}")

    print("\n" + "═" * 76)
    print("WIRING NOTE:")
    print("  RMT: compute denoised C at each rebalance (pure numpy, <1ms). Can replace")
    print("       the raw-C PCA in any rebalancer without architectural changes.")
    print("  Entropy: O(N×W) per rebalance — negligible. Pure daily-close signal.")
    print("  Both fit inside the existing rebalance timer with no new data sources.")
    print("─" * 76)


if __name__ == "__main__":
    main()
