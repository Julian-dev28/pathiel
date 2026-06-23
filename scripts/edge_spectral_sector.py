#!/usr/bin/env python3
"""Z2 Final-wave: Spectral/Wavelet, DFA/Hurst-factor, Sector-RS.

(a) WAVELET/FOURIER CYCLE — tradeable periodicity in BTC / cross-section?
    FFT periodogram on daily returns; dominant-cycle phase used to time entries.
    Lookahead-safe: estimate cycle from close[t-win:t], enter t+1.

(b) DFA / HURST AS CROSS-SECTIONAL FACTOR — distinct from refuted regime-switch.
    Per-coin Hurst exponent via DFA over a trailing window.
    Cross-sectional L-S beta-neutral: long persistent / short anti-persistent AND reverse.

(c) SECTOR RELATIVE-STRENGTH — best shot.
    Cluster universe into 'sectors' via return-correlation (hierarchical agglomerative).
    Compute each coin's momentum RELATIVE to its cluster median.
    Cross-sectional L-S on within-cluster relative strength vs plain momentum (+2.32% bar).

Run: BT_CACHE_ONLY=1 python3 scripts/edge_spectral_sector.py
"""
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone
from hermes_trader.client.universe import get_universe
from _bt_candles import get as get_candles

# ─── shared constants ────────────────────────────────────────────────────────
VOL_FLOOR = 5e6
TOPN = 50
K = 8           # names per L-S leg
HOLD = 10       # hold days (match xs-momentum baseline)
COST = 10.0 / 1e4   # 10 bps / name round-trip
BETA_WIN = 30   # OLS beta window for beta-neutralization


# ═══════════════════════════════════════════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════════════════════

def _ymd(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y%m%d")


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _stdev(xs):
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    v = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return math.sqrt(v) if v > 0 else 0.0


def _daily_rets(closes):
    return [closes[i] / closes[i - 1] - 1.0
            for i in range(1, len(closes)) if closes[i - 1] > 0]


def _ols_beta(cr, br):
    n = min(len(cr), len(br))
    if n < 8:
        return 1.0
    cr, br = cr[-n:], br[-n:]
    mb = _mean(br)
    vb = sum((b - mb) ** 2 for b in br)
    if vb <= 0:
        return 1.0
    mc = _mean(cr)
    return sum((a - mc) * (b - mb) for a, b in zip(cr, br)) / vb


def _pearson(xs, ys):
    n = min(len(xs), len(ys))
    if n < 4:
        return 0.0
    xs, ys = xs[-n:], ys[-n:]
    mx, my = _mean(xs), _mean(ys)
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    dx = math.sqrt(sum((a - mx) ** 2 for a in xs))
    dy = math.sqrt(sum((b - my) ** 2 for b in ys))
    if dx <= 0 or dy <= 0:
        return 0.0
    return num / (dx * dy)


def rep(name, arr, extra=""):
    if not arr:
        print(f"  {name:52} n=0"); return
    n = len(arr)
    w = sum(1 for r in arr if r > 0)
    mid = n // 2
    h1 = _mean(arr[:mid]) * 100 if mid else 0.0
    h2 = _mean(arr[mid:]) * 100 if (n - mid) else 0.0
    m = _mean(arr) * 100
    rob = "ROBUST" if h1 > 0 and h2 > 0 else "fragile" if (h1 > 0) != (h2 > 0) else "neg"
    flag = "  <<< +EV" if m > 0 and rob == "ROBUST" else ""
    print(f"  {name:52} n={n:>4} win={w/n*100:>3.0f}%  mean={m:>+6.2f}%  "
          f"OOS {h1:>+5.2f}/{h2:>+5.2f} {rob}{extra}{flag}")


# ─── data loading (shared) ──────────────────────────────────────────────────
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
        if len(bars) >= 80:
            # Store as dict keyed by ymd → full bar
            data[c] = {_ymd(b["t"]): b for b in bars}
    return data


# ═══════════════════════════════════════════════════════════════════════════
# (a) SPECTRAL / FOURIER CYCLE
# ═══════════════════════════════════════════════════════════════════════════
# Pure-Python DFT (no numpy) — O(n²) but n≤256, fine.

def _dft_power(xs):
    """Return (frequencies, power_spectrum) for a real signal xs.
    Frequencies in cycles/day from 1/n to 0.5."""
    n = len(xs)
    if n < 8:
        return [], []
    # Remove mean
    m = _mean(xs)
    xs = [x - m for x in xs]
    powers = []
    freqs = []
    # Only compute positive frequencies up to Nyquist
    for k in range(1, n // 2 + 1):
        re = sum(xs[t] * math.cos(2 * math.pi * k * t / n) for t in range(n))
        im = sum(xs[t] * math.sin(2 * math.pi * k * t / n) for t in range(n))
        powers.append(re * re + im * im)
        freqs.append(k / n)
    return freqs, powers


def _dominant_cycle_period(xs, min_period=3, max_period=60):
    """Return dominant cycle period (days) from DFT power spectrum."""
    freqs, powers = _dft_power(xs)
    if not powers:
        return None
    # Filter to valid period range
    filtered = [(p, f) for p, f in zip(powers, freqs)
                if f > 0 and (1.0 / f) >= min_period and (1.0 / f) <= max_period]
    if not filtered:
        return None
    best_power, best_freq = max(filtered, key=lambda x: x[0])
    return 1.0 / best_freq   # period in days


def _cycle_phase(xs, period):
    """Estimate current phase of dominant cycle via simple sine-fit (lookahead-safe, uses xs only).
    Returns phase in [0, 2π]. Phase ~ 0/2π = trough (buy); phase ~ π = peak (sell)."""
    n = len(xs)
    if n < int(period) or period <= 0:
        return None
    # Fit: project signal onto sin/cos at dominant frequency
    f = 1.0 / period
    m = _mean(xs)
    xs_dm = [x - m for x in xs]
    re = sum(xs_dm[t] * math.cos(2 * math.pi * f * t) for t in range(n))
    im = sum(xs_dm[t] * math.sin(2 * math.pi * f * t) for t in range(n))
    # Phase of current cycle position (last sample)
    phase_at_0 = math.atan2(-im, re)  # phase such that signal ~ A*cos(2πft + phase_at_0)
    # Phase at t=n-1 (most recent day)
    phase_now = (2 * math.pi * f * (n - 1) + phase_at_0) % (2 * math.pi)
    return phase_now


def run_spectral(data):
    """
    Strategy: estimate BTC dominant cycle at time t (trailing SPEC_WIN bars).
    If phase is in 'buy zone' (near trough) → long top-K momentum names.
    If phase in 'sell zone' (near peak) → short bottom-K momentum names.
    Market-neutral version: trade L-S but only during low-phase windows.
    """
    SPEC_WIN = 60   # trailing bars for cycle estimation
    BUY_ZONE = (0.0, math.pi * 0.5)     # phase 0..π/2 = coming off trough
    SELL_ZONE = (math.pi * 0.75, math.pi * 1.5)  # phase 3π/4..3π/2 = coming off peak
    LB = 7          # momentum lookback (same as xs-momentum baseline)

    btc_data = data.get("BTC")
    if btc_data is None:
        return [], [], []

    all_days = sorted({d for cd in data.values() for d in cd})
    burn_in = max(SPEC_WIN + 5, LB + 5)

    ls_rets = []     # vanilla L-S (no cycle filter — baseline)
    ls_cycle_buy = []   # L-S filtered to cycle buy-zone
    ls_cycle_sell = []  # short-only triggered in sell-zone
    cycle_periods = []

    for t in range(burn_in, len(all_days) - HOLD - 1):
        d = all_days[t]
        d_lb = all_days[t - LB]
        d_entry = all_days[t + 1]
        d_exit = all_days[min(t + 1 + HOLD, len(all_days) - 1)]

        # BTC cycle estimation (strictly from bars ≤ t)
        btc_closes = [btc_data[all_days[i]]["c"]
                      for i in range(max(0, t - SPEC_WIN), t + 1)
                      if all_days[i] in btc_data and btc_data[all_days[i]]["c"] > 0]
        btc_rets = _daily_rets(btc_closes)
        if len(btc_rets) < 20:
            continue

        period = _dominant_cycle_period(btc_rets)
        phase = _cycle_phase(btc_rets, period) if period else None
        if period:
            cycle_periods.append(period)

        # Rank coins by trailing LB-day return (momentum signal)
        ranked = []
        for coin, cd in data.items():
            if d not in cd or d_lb not in cd or d_entry not in cd or d_exit not in cd:
                continue
            r = cd[d]["c"] / cd[d_lb]["c"] - 1.0 if cd[d_lb]["c"] > 0 else 0.0
            ranked.append((coin, r))

        if len(ranked) < 2 * K + 4:
            continue

        ranked.sort(key=lambda x: x[1], reverse=True)
        longs = [c for c, _ in ranked[:K]]
        shorts = [c for c, _ in ranked[-K:]]

        def fwd(coin):
            o = data[coin][d_entry]["o"]
            c = data[coin][d_exit]["c"]
            return (c - o) / o if o > 0 else 0.0

        lr = _mean([fwd(c) for c in longs])
        sr = _mean([fwd(c) for c in shorts])
        ls = (lr - sr) - 2 * COST
        ls_rets.append(ls)

        if phase is not None:
            if BUY_ZONE[0] <= phase <= BUY_ZONE[1]:
                ls_cycle_buy.append(ls)
            if SELL_ZONE[0] <= phase <= SELL_ZONE[1]:
                ls_cycle_sell.append(ls)

    avg_period = _mean(cycle_periods) if cycle_periods else float("nan")
    return ls_rets, ls_cycle_buy, ls_cycle_sell, avg_period, cycle_periods


# ═══════════════════════════════════════════════════════════════════════════
# (b) DFA / HURST AS CROSS-SECTIONAL FACTOR
# ═══════════════════════════════════════════════════════════════════════════

def _dfa_hurst(xs, min_scale=4, max_scale=None, num_scales=8):
    """
    Detrended Fluctuation Analysis → Hurst exponent.
    xs: list of returns (not prices).
    Returns H ∈ (0,1). H > 0.5 = persistent, H < 0.5 = anti-persistent/mean-reverting.
    Lookahead-safe: xs must be trailing data only.
    """
    n = len(xs)
    if n < 16:
        return None
    if max_scale is None:
        max_scale = n // 4

    # Cumulative sum (profile)
    m = _mean(xs)
    profile = []
    cum = 0.0
    for x in xs:
        cum += (x - m)
        profile.append(cum)

    # Generate log-spaced scales
    log_min = math.log(min_scale)
    log_max = math.log(max_scale)
    if log_max <= log_min:
        return None
    scales = []
    for i in range(num_scales):
        s = int(round(math.exp(log_min + i * (log_max - log_min) / (num_scales - 1))))
        if s >= min_scale and s not in scales:
            scales.append(s)
    scales = sorted(set(scales))
    if len(scales) < 4:
        return None

    log_scales = []
    log_flucts = []

    for s in scales:
        if s < 2 or s > len(profile) // 2:
            continue
        num_segs = len(profile) // s
        if num_segs < 2:
            continue
        rms_vals = []
        for seg in range(num_segs):
            seg_data = profile[seg * s: (seg + 1) * s]
            # Detrend: fit linear trend and remove
            t_vals = list(range(len(seg_data)))
            n_s = len(seg_data)
            mt = _mean(t_vals)
            ms = _mean(seg_data)
            denom = sum((t - mt) ** 2 for t in t_vals)
            if denom == 0:
                slope = 0.0
            else:
                slope = sum((t - mt) * (y - ms) for t, y in zip(t_vals, seg_data)) / denom
            trend = [ms + slope * (t - mt) for t in t_vals]
            resid = [y - tr for y, tr in zip(seg_data, trend)]
            ms2 = _mean([r ** 2 for r in resid])
            rms_vals.append(math.sqrt(ms2) if ms2 > 0 else 0.0)
        if not rms_vals:
            continue
        f_s = _mean(rms_vals)
        if f_s > 0:
            log_scales.append(math.log(s))
            log_flucts.append(math.log(f_s))

    if len(log_scales) < 4:
        return None

    # OLS regression log(F) ~ H * log(s) + const
    n_pts = len(log_scales)
    mx = _mean(log_scales)
    my = _mean(log_flucts)
    denom = sum((x - mx) ** 2 for x in log_scales)
    if denom <= 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(log_scales, log_flucts)) / denom
    # Clamp to reasonable range
    return max(0.05, min(0.95, slope))


def run_hurst_factor(data, higher_is_long=True, dfa_win=60):
    """
    Each rebalance: compute trailing DFA-Hurst for each coin (window dfa_win days).
    Rank cross-sectionally. L-S: long high-H / short low-H (persistent vs anti-persistent).
    Beta-neutralize via within-beta-tercile method.
    Returns (raw_ls_rets, bn_tercile_rets, scores_all)
    """
    btc_data = data.get("BTC")
    if btc_data is None:
        return [], [], []

    all_days = sorted({d for cd in data.values() for d in cd})
    burn_in = dfa_win + 10

    raw_ls = []
    tercile_ls = []
    h_scores_all = []

    for t in range(burn_in, len(all_days) - HOLD - 1):
        d = all_days[t]
        d_entry = all_days[t + 1]
        d_exit = all_days[min(t + 1 + HOLD, len(all_days) - 1)]
        win_days = all_days[max(0, t - dfa_win): t + 1]

        # BTC trailing returns for beta estimation
        btc_closes = [btc_data[all_days[i]]["c"]
                      for i in range(max(0, t - BETA_WIN), t + 1)
                      if all_days[i] in btc_data]
        btc_rets = _daily_rets(btc_closes)

        ranked = []
        for coin, cd in data.items():
            if d_entry not in cd or d_exit not in cd:
                continue
            # Trailing returns for DFA
            coin_closes = [cd[all_days[i]]["c"]
                           for i in range(max(0, t - dfa_win), t + 1)
                           if all_days[i] in cd and cd[all_days[i]]["c"] > 0]
            coin_rets = _daily_rets(coin_closes)
            if len(coin_rets) < 20:
                continue
            H = _dfa_hurst(coin_rets)
            if H is None:
                continue
            # Beta vs BTC
            n_b = min(len(coin_rets), len(btc_rets))
            beta = _ols_beta(coin_rets[-n_b:], btc_rets[-n_b:]) if n_b >= 8 else 1.0
            ranked.append((coin, H, beta))
            h_scores_all.append(H)

        if len(ranked) < 2 * K + 4:
            continue

        ranked.sort(key=lambda x: x[1], reverse=higher_is_long)
        longs = ranked[:K]
        shorts = ranked[-K:]

        def fwd(coin):
            o = data[coin][d_entry]["o"]
            c = data[coin][d_exit]["c"]
            return (c - o) / o if o > 0 else 0.0

        lr = _mean([fwd(c) for c, _, _ in longs])
        sr = _mean([fwd(c) for c, _, _ in shorts])
        raw_ls.append((lr - sr) - 2 * COST)

        # Within-beta-tercile neutralization
        ranked_by_beta = sorted(ranked, key=lambda x: x[2])
        n_per = len(ranked_by_beta) // 3
        if n_per >= 3:
            t_spreads = []
            for ti in range(3):
                start = ti * n_per
                end = start + n_per if ti < 2 else len(ranked_by_beta)
                tercile = ranked_by_beta[start:end]
                tercile.sort(key=lambda x: x[1], reverse=higher_is_long)
                k_t = max(1, len(tercile) // 3)
                longs_t = [c for c, _, _ in tercile[:k_t]]
                shorts_t = [c for c, _, _ in tercile[-k_t:]]
                if longs_t and shorts_t:
                    lr_t = _mean([fwd(c) for c in longs_t])
                    sr_t = _mean([fwd(c) for c in shorts_t])
                    t_spreads.append((lr_t - sr_t) - 2 * COST)
            if t_spreads:
                tercile_ls.append(_mean(t_spreads))

    return raw_ls, tercile_ls, h_scores_all


# ═══════════════════════════════════════════════════════════════════════════
# (c) SECTOR RELATIVE-STRENGTH
# ═══════════════════════════════════════════════════════════════════════════

def _correlation_matrix(coin_list, data, days):
    """Compute pairwise Pearson correlation matrix from trailing daily returns.
    Returns dict[(coin_i, coin_j)] = corr. Only upper triangle + diagonal stored."""
    # Get return series per coin aligned to days
    ret_series = {}
    for coin in coin_list:
        cd = data[coin]
        closes = [cd[d]["c"] for d in days if d in cd and cd[d]["c"] > 0]
        rets = _daily_rets(closes)
        ret_series[coin] = rets

    # Build correlation matrix
    corr = {}
    coins = list(ret_series.keys())
    for i, ci in enumerate(coins):
        for j, cj in enumerate(coins):
            if j < i:
                corr[(ci, cj)] = corr.get((cj, ci), 0.0)
            elif i == j:
                corr[(ci, cj)] = 1.0
            else:
                r = _pearson(ret_series[ci], ret_series[cj])
                corr[(ci, cj)] = r
                corr[(cj, ci)] = r
    return corr, coins


def _average_linkage_cluster(coins, corr, n_clusters=5):
    """
    Average-linkage hierarchical agglomerative clustering on (1 - corr) distance.
    Returns list of cluster assignments: {coin: cluster_id}.
    Lookahead-safe: uses only the passed corr matrix.
    """
    # Initialize: each coin is its own cluster
    clusters = {i: [c] for i, c in enumerate(coins)}
    assignment = {c: i for i, c in enumerate(coins)}

    # Distance between clusters = average (1-corr) across all pairs
    def cluster_dist(c1_coins, c2_coins):
        dists = []
        for a in c1_coins:
            for b in c2_coins:
                if a != b:
                    dists.append(1.0 - corr.get((a, b), 0.0))
        return _mean(dists) if dists else 1.0

    n = len(coins)
    n_merges = n - n_clusters
    if n_merges < 0:
        # Not enough coins — all in one cluster
        return {c: 0 for c in coins}

    for _ in range(n_merges):
        cluster_ids = list(clusters.keys())
        if len(cluster_ids) <= n_clusters:
            break
        # Find closest pair of clusters
        best_dist = float("inf")
        best_pair = None
        for ii in range(len(cluster_ids)):
            for jj in range(ii + 1, len(cluster_ids)):
                ci, cj = cluster_ids[ii], cluster_ids[jj]
                d = cluster_dist(clusters[ci], clusters[cj])
                if d < best_dist:
                    best_dist = d
                    best_pair = (ci, cj)
        if best_pair is None:
            break
        ci, cj = best_pair
        # Merge cj into ci
        merged = clusters[ci] + clusters[cj]
        new_id = max(clusters.keys()) + 1
        for c in merged:
            assignment[c] = new_id
        clusters[new_id] = merged
        del clusters[ci]
        del clusters[cj]

    # Re-label cluster IDs to 0..n_clusters-1
    unique_ids = sorted(set(assignment.values()))
    remap = {old: new for new, old in enumerate(unique_ids)}
    return {c: remap[assignment[c]] for c in coins}


def run_sector_rs(data, n_clusters=5, corr_win=40, lb=7):
    """
    Sector-relative-strength cross-sectional L-S.
    At each rebalance:
      1. Estimate trailing corr matrix (corr_win days before t).
      2. Cluster coins (average-linkage, n_clusters clusters) — lookahead-safe.
      3. Score each coin = (coin return LB days) - (median return of its cluster LB days).
      4. Cross-sectional L-S on within-cluster-relative score, top-K long / bottom-K short.
    Baseline comparison: plain momentum (same LB, same hold) on same days.
    """
    all_days = sorted({d for cd in data.values() for d in cd})
    burn_in = max(corr_win + lb + 5, 70)

    sector_ls = []      # sector-relative-strength L-S
    plain_ls = []       # plain momentum L-S (same universe, same days — fair comparison)

    for t in range(burn_in, len(all_days) - HOLD - 1):
        d = all_days[t]
        d_lb = all_days[t - lb]
        d_entry = all_days[t + 1]
        d_exit = all_days[min(t + 1 + HOLD, len(all_days) - 1)]

        # Coins with required bars
        valid_coins = [c for c, cd in data.items()
                       if d in cd and d_lb in cd and d_entry in cd and d_exit in cd
                       and cd[d_lb]["c"] > 0]
        if len(valid_coins) < 2 * K + 4:
            continue

        # ── Lookahead-safe cluster estimation (corr_win days ≤ t) ──
        corr_days = all_days[max(0, t - corr_win): t + 1]
        if len(corr_days) < 10:
            continue

        corr, corr_coins = _correlation_matrix(valid_coins, data, corr_days)
        assignment = _average_linkage_cluster(corr_coins, corr, n_clusters=n_clusters)

        # ── Coin LB-day return ──
        coin_ret = {}
        for c in valid_coins:
            coin_ret[c] = data[c][d]["c"] / data[c][d_lb]["c"] - 1.0

        # ── Cluster medians (from same LB window, not future) ──
        cluster_coins = {}
        for c in valid_coins:
            cl = assignment.get(c, 0)
            cluster_coins.setdefault(cl, []).append(c)

        cluster_median = {}
        for cl, members in cluster_coins.items():
            rets_cl = sorted(coin_ret[c] for c in members)
            mid = len(rets_cl) // 2
            cluster_median[cl] = rets_cl[mid] if rets_cl else 0.0

        # ── Sector-relative score ──
        sector_score = {}
        for c in valid_coins:
            cl = assignment.get(c, 0)
            sector_score[c] = coin_ret[c] - cluster_median[cl]

        # ── Forward return ──
        def fwd(coin):
            o = data[coin][d_entry]["o"]
            c_ = data[coin][d_exit]["c"]
            return (c_ - o) / o if o > 0 else 0.0

        # ── Sector-RS L-S ──
        ranked_sector = sorted(valid_coins, key=lambda c: sector_score[c], reverse=True)
        longs_s = ranked_sector[:K]
        shorts_s = ranked_sector[-K:]
        lr_s = _mean([fwd(c) for c in longs_s])
        sr_s = _mean([fwd(c) for c in shorts_s])
        sector_ls.append((lr_s - sr_s) - 2 * COST)

        # ── Plain momentum L-S (same coins, same rebalance day, fair) ──
        ranked_plain = sorted(valid_coins, key=lambda c: coin_ret[c], reverse=True)
        longs_p = ranked_plain[:K]
        shorts_p = ranked_plain[-K:]
        lr_p = _mean([fwd(c) for c in longs_p])
        sr_p = _mean([fwd(c) for c in shorts_p])
        plain_ls.append((lr_p - sr_p) - 2 * COST)

    return sector_ls, plain_ls


# ─── corr to momentum / vol-dispersion streams ─────────────────────────────
def _stream_corr(s1, s2, label1="A", label2="B"):
    """Pearson corr between two equal-length daily-rebalance streams."""
    n = min(len(s1), len(s2))
    if n < 5:
        return float("nan")
    r = _pearson(s1[-n:], s2[-n:])
    return r


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 72)
    print("  Z2 FINAL CANDLE WAVE: Spectral / DFA-Hurst / Sector-RS")
    print(f"  K={K}/leg | hold={HOLD}d | cost={COST*1e4:.0f}bps/name | BT_CACHE_ONLY=1")
    print("=" * 72)

    data = load()
    n_coins = len(data)
    print(f"  Universe: {n_coins} coins loaded")
    all_days = sorted({d for cd in data.values() for d in cd})
    print(f"  Date range: {all_days[0]} → {all_days[-1]}  ({len(all_days)} bars)\n")

    # ─── (a) SPECTRAL / FOURIER CYCLE ────────────────────────────────────
    print("━" * 72)
    print("(a) SPECTRAL / FOURIER CYCLE — BTC dominant-cycle phase timing")
    print("━" * 72)

    ls_base, ls_buy, ls_sell, avg_period, all_periods = run_spectral(data)

    if avg_period and not math.isnan(avg_period):
        period_med = sorted(all_periods)[len(all_periods)//2] if all_periods else float("nan")
        print(f"  BTC dominant cycle: mean={avg_period:.1f}d, median={period_med:.1f}d "
              f"(across {len(all_periods)} rebalance estimates)")
        period_counts = {}
        for p in all_periods:
            bucket = int(round(p / 5.0)) * 5
            period_counts[bucket] = period_counts.get(bucket, 0) + 1
        top_buckets = sorted(period_counts.items(), key=lambda x: -x[1])[:5]
        print(f"  Top cycle lengths: {top_buckets}")
    else:
        print("  BTC cycle: insufficient data")

    print()
    rep("Baseline L-S (plain momentum, all rebalances)", ls_base)
    rep("Cycle-filtered: buy-zone entries only [0, π/2]", ls_buy,
        extra=f"  (n={len(ls_buy)} of {len(ls_base)})")
    rep("Cycle-filtered: sell-zone entries only [3π/4, 3π/2]", ls_sell,
        extra=f"  (n={len(ls_sell)} of {len(ls_base)})")

    # Check if cycle filter CONCENTRATES the baseline into buy-zone
    if ls_buy and ls_base:
        buy_frac = len(ls_buy) / len(ls_base)
        buy_lift = _mean(ls_buy) / _mean(ls_base) if _mean(ls_base) != 0 else float("nan")
        print(f"\n  Cycle filter coverage: {buy_frac*100:.0f}% of rebalances in buy-zone")
        if not math.isnan(buy_lift):
            print(f"  Buy-zone lift vs baseline: {buy_lift:.2f}× (1.0 = no lift)")

    print("\n  VERDICT (a):", end=" ")
    ls_buy_mean = _mean(ls_buy) * 100 if ls_buy else float("nan")
    ls_base_mean = _mean(ls_base) * 100 if ls_base else float("nan")
    if (ls_buy and _mean(ls_buy) > _mean(ls_base) * 1.1
            and len(ls_buy) > len(ls_base) * 0.2):
        print("POTENTIAL SIGNAL — cycle buy-zone lifts L-S meaningfully. Check OOS.")
    else:
        print("REFUTED — cycle phase does not concentrate momentum returns. "
              "BTC periodicity is noise at this resolution.")

    # ─── (b) DFA / HURST CROSS-SECTIONAL FACTOR ──────────────────────────
    print()
    print("━" * 72)
    print("(b) DFA/HURST as CROSS-SECTIONAL FACTOR (beta-neutral, distinct from regime-switch)")
    print("━" * 72)

    for dfa_win, label in [(40, "DFA-40d"), (60, "DFA-60d"), (80, "DFA-80d")]:
        print(f"\n  --- {label} trailing window ---")
        # Long persistent (H>0.5) / short anti-persistent (H<0.5)
        raw_hi, bn_hi, scores = run_hurst_factor(data, higher_is_long=True, dfa_win=dfa_win)
        rep(f"  Long-HIGH-H (persistent) raw L-S", raw_hi)
        rep(f"  Long-HIGH-H within-β-tercile BN ", bn_hi)

        # Reverse: long anti-persistent / short persistent (mean-reversion bet)
        raw_lo, bn_lo, _ = run_hurst_factor(data, higher_is_long=False, dfa_win=dfa_win)
        rep(f"  Long-LOW-H (anti-persist) raw L-S", raw_lo)
        rep(f"  Long-LOW-H within-β-tercile BN  ", bn_lo)

        if scores:
            avg_H = _mean(scores)
            med_H = sorted(scores)[len(scores)//2]
            sd_H = _stdev(scores)
            print(f"  Hurst distribution: mean={avg_H:.3f} median={med_H:.3f} std={sd_H:.3f}")
            frac_persist = sum(1 for h in scores if h > 0.5) / len(scores)
            print(f"  Fraction H>0.5 (persistent): {frac_persist*100:.0f}%")

    # Correlation to momentum
    _, plain_ls_ref = run_sector_rs(data, n_clusters=5, corr_win=40, lb=7)
    raw_hi_40, bn_hi_40, _ = run_hurst_factor(data, higher_is_long=True, dfa_win=40)
    corr_hurst_mom = _stream_corr(bn_hi_40, plain_ls_ref[:len(bn_hi_40)])
    print(f"\n  Corr(Hurst-BN, momentum): {corr_hurst_mom:+.3f}")

    print("\n  VERDICT (b):", end=" ")
    h_raw_mean = _mean(raw_hi_40) * 100 if raw_hi_40 else float("nan")
    h_bn_mean = _mean(bn_hi_40) * 100 if bn_hi_40 else float("nan")
    h_bn_mid = len(bn_hi_40) // 2
    h_bn_h1 = _mean(bn_hi_40[:h_bn_mid]) * 100 if h_bn_mid else 0.0
    h_bn_h2 = _mean(bn_hi_40[h_bn_mid:]) * 100 if (len(bn_hi_40) - h_bn_mid) else 0.0
    bn_robust = h_bn_h1 > 0 and h_bn_h2 > 0
    if not math.isnan(h_bn_mean) and h_bn_mean > 0.1 and bn_robust:
        print(f"POTENTIAL ALPHA — BN mean={h_bn_mean:+.2f}% OOS {h_bn_h1:+.2f}/{h_bn_h2:+.2f}")
    else:
        print(f"REFUTED — Hurst cross-sectional factor not +EV after beta-neutralization. "
              f"(BN={h_bn_mean:+.2f}% OOS {h_bn_h1:+.2f}/{h_bn_h2:+.2f})")

    # ─── (c) SECTOR RELATIVE-STRENGTH ─────────────────────────────────────
    print()
    print("━" * 72)
    print("(c) SECTOR RELATIVE-STRENGTH — clustering via return-correlation")
    print("━" * 72)

    configs = [
        (3, 40, 7,  "K_cl=3 corr_win=40 lb=7"),
        (5, 40, 7,  "K_cl=5 corr_win=40 lb=7"),
        (7, 40, 7,  "K_cl=7 corr_win=40 lb=7"),
        (5, 60, 7,  "K_cl=5 corr_win=60 lb=7"),
        (5, 40, 14, "K_cl=5 corr_win=40 lb=14"),
    ]
    best_sector_mean = -999.0
    best_sector_arr = []
    best_plain_arr = []
    best_label = ""

    for n_cl, corr_win, lb, label in configs:
        sector_ls, plain_ls = run_sector_rs(data, n_clusters=n_cl, corr_win=corr_win, lb=lb)
        print(f"\n  Config: {label}")
        rep("  Sector-relative L-S", sector_ls)
        rep("  Plain momentum L-S (same days)", plain_ls)

        if sector_ls and _mean(sector_ls) * 100 > best_sector_mean:
            best_sector_mean = _mean(sector_ls) * 100
            best_sector_arr = sector_ls
            best_plain_arr = plain_ls
            best_label = label

    # Corr to momentum (plain)
    corr_sector_mom = _stream_corr(best_sector_arr, best_plain_arr)
    print(f"\n  Best config: {best_label}")
    print(f"  Corr(sector-RS, plain momentum): {corr_sector_mom:+.3f}")

    # Beats baseline bar?
    BASELINE_MEAN = 2.32   # +2.32%/rebal plain xs-momentum from ALPHA-PLAN.md
    sector_mean = _mean(best_sector_arr) * 100 if best_sector_arr else float("nan")
    plain_mean_here = _mean(best_plain_arr) * 100 if best_plain_arr else float("nan")
    mid_s = len(best_sector_arr) // 2
    s_h1 = _mean(best_sector_arr[:mid_s]) * 100 if mid_s else 0.0
    s_h2 = _mean(best_sector_arr[mid_s:]) * 100 if (len(best_sector_arr) - mid_s) else 0.0
    s_robust = s_h1 > 0 and s_h2 > 0

    print(f"\n  Sector-RS best: {sector_mean:>+.2f}%  OOS {s_h1:>+.2f}/{s_h2:>+.2f}  {'ROBUST' if s_robust else 'fragile'}")
    print(f"  Plain momentum here: {plain_mean_here:>+.2f}% (baseline bar: +{BASELINE_MEAN:.2f}%)")
    print(f"  Delta vs plain: {sector_mean - plain_mean_here:>+.2f}% "
          f"({'LIFTS' if sector_mean > plain_mean_here else 'LOWERS'} momentum)")
    print(f"  Delta vs published bar: {sector_mean - BASELINE_MEAN:>+.2f}%")

    print("\n  VERDICT (c):", end=" ")
    if s_robust and sector_mean > plain_mean_here and sector_mean > BASELINE_MEAN:
        print(f"NEW EDGE — sector-RS beats plain momentum on both halves. "
              f"Mean +{sector_mean:.2f}% vs baseline +{BASELINE_MEAN:.2f}%.")
    elif s_robust and sector_mean > plain_mean_here:
        print(f"MARGINAL LIFT — sector-RS robust and lifts momentum slightly "
              f"({sector_mean:+.2f}% vs plain {plain_mean_here:+.2f}%), but below baseline bar.")
    elif s_robust:
        print(f"ROBUST BUT BELOW BAR — OOS positive but "
              f"({sector_mean:+.2f}%) does not beat plain ({plain_mean_here:+.2f}%).")
    else:
        print(f"REFUTED — sector-relative scoring does not beat plain momentum OOS. "
              f"Clustering adds noise, not signal (mean={sector_mean:+.2f}%, plain={plain_mean_here:+.2f}%).")

    # ─── OVERALL SUMMARY ─────────────────────────────────────────────────
    print()
    print("=" * 72)
    print("  FINAL SUMMARY — Z2 WAVE")
    print("=" * 72)
    print()
    print("  (a) FOURIER/WAVELET CYCLE:")
    buy_m = _mean(ls_buy)*100 if ls_buy else float("nan")
    base_m = _mean(ls_base)*100 if ls_base else float("nan")
    print(f"      Dominant BTC cycle: {avg_period:.1f}d avg | buy-zone mean={buy_m:+.2f}%  baseline={base_m:+.2f}%")
    buy_mid = len(ls_buy) // 2
    b_h1 = _mean(ls_buy[:buy_mid])*100 if buy_mid else 0.0
    b_h2 = _mean(ls_buy[buy_mid:])*100 if (len(ls_buy)-buy_mid) else 0.0
    print(f"      OOS {b_h1:+.2f}/{b_h2:+.2f}  {'ROBUST' if b_h1>0 and b_h2>0 else 'not robust'}")
    print(f"      VERDICT: {'POTENTIAL' if b_h1>0 and b_h2>0 and buy_m > base_m*1.05 else 'REFUTED (noise)'}")
    print()
    print("  (b) DFA/HURST CROSS-SECTIONAL:")
    print(f"      Long-HIGH-H BN within-tercile: mean={h_bn_mean:+.2f}%  OOS {h_bn_h1:+.2f}/{h_bn_h2:+.2f}")
    print(f"      Corr to momentum: {corr_hurst_mom:+.3f}")
    print(f"      VERDICT: {'POTENTIAL' if h_bn_mean > 0.1 and bn_robust else 'REFUTED'}")
    print()
    print("  (c) SECTOR RELATIVE-STRENGTH:")
    print(f"      Best config ({best_label}): {sector_mean:+.2f}%  OOS {s_h1:+.2f}/{s_h2:+.2f}")
    print(f"      vs plain momentum same-days: {plain_mean_here:+.2f}%  |  corr to plain: {corr_sector_mom:+.3f}")
    print(f"      VERDICT: {'NEW EDGE' if s_robust and sector_mean > plain_mean_here and sector_mean > BASELINE_MEAN else 'MARGINAL' if s_robust and sector_mean > plain_mean_here else 'REFUTED'}")
    print()
    print("  CANDLE-SPACE STATUS: Wave Z2 complete. If all 3 refuted → candle-testable space EXHAUSTED.")
    print("  Next frontier: OI/funding/liquidation data collectors (already gated pending collection).")


if __name__ == "__main__":
    main()
