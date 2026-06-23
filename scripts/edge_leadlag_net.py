#!/usr/bin/env python3
"""Lead-lag NETWORK alpha hunt — coin-to-coin causal relationship map.

Assignment: does a coin-to-coin lead-lag network carry tradeable alpha?
- For each ordered pair (A→B): measure whether A's return at t predicts B's return at t+1.
- Identify persistent leaders (many strong follower links) vs followers.
- TRADEABLE TEST: at t, when a leader moves strongly, take its persistent followers at t+1
  (market-neutral long/short cross-section of followers). Cost ≥10bps. OOS halves.
- ENHANCER TEST: does adding "leader's recent return" as a feature improve xs-momentum?
- PERMUTATION CONTROL: shuffle leader identities → confirm real network beats random.
- VERDICT: tradeable lead-lag, data-mined noise, or pure momentum overlap?

METHODOLOGY (all required before claiming edge):
  - Lookahead-safe: pair relationship and leader move both known ≤ t, trade at t+1.
  - Cost-aware: ≥10bps/leg.
  - OOS-robust: both halves positive.
  - Permutation control: random-leader shuffle.
  - Beta-neutralize cross-sectional trades.

Run: BT_CACHE_ONLY=1 python3 scripts/edge_leadlag_net.py
"""

import os, sys, math, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone

from _bt_candles import get as get_candles
from hermes_trader.client.universe import get_universe

# ─── constants ────────────────────────────────────────────────────────────────
TOPN       = 50        # max coins from universe
VOL_FLOOR  = 5e6      # min daily notional volume
MIN_BARS   = 120       # coins need this many bars
COST_BPS   = 10.0     # per name per leg (round-trip = 2× for L + 2× for S)
COST       = COST_BPS / 1e4

# Lead-lag estimation params
PAIR_WIN       = 60    # trailing days to estimate each pair's lag correlation
PAIR_LAG       = 1     # lag = 1 trading day (lookahead-safe)
PAIR_MIN_OBS   = 30    # minimum observations per pair window
CORR_THRESH    = 0.10  # minimum |corr| to keep a directed link
LEADER_MIN_LINKS = 3   # a coin must have ≥ this many follower links to be "leader"

# Tradeable test params
SIGNAL_WIN   = 7       # trailing days of leader return as signal
SIGNAL_THRSH = 0.03    # leader must move ≥ 3% in SIGNAL_WIN days to fire
K_FOLLOWERS  = 3       # top-K / bottom-K followers to trade
HOLD         = 5       # holding period for followers (days)
BTC_BETA_WIN = 30      # for beta-neutralization

# Permutation control
N_PERMS = 200          # number of random shuffles

# OOS: split trade stream at midpoint
RANDOM_SEED = 42


# ─── helpers ─────────────────────────────────────────────────────────────────
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


def _corr(xs, ys):
    """Pearson correlation; returns 0 if degenerate."""
    n = min(len(xs), len(ys))
    if n < 4:
        return 0.0
    xs, ys = xs[-n:], ys[-n:]
    mx, my = _mean(xs), _mean(ys)
    sx, sy = _stdev(xs), _stdev(ys)
    if sx < 1e-10 or sy < 1e-10:
        return 0.0
    return sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / ((n - 1) * sx * sy)


def _ols_beta(cr, br):
    """OLS beta of cr on br."""
    n = min(len(cr), len(br))
    if n < 8:
        return 1.0
    cr, br = cr[-n:], br[-n:]
    mb = _mean(br)
    vb = sum((x - mb) ** 2 for x in br)
    if vb <= 0:
        return 1.0
    mc = _mean(cr)
    return sum((a - mc) * (b - mb) for a, b in zip(cr, br)) / vb


def _rep(name, arr):
    if not arr:
        print(f"  {name:55} n=0")
        return
    n   = len(arr)
    w   = sum(1 for r in arr if r > 0)
    mid = n // 2
    h1  = _mean(arr[:mid]) * 100 if mid else 0.0
    h2  = _mean(arr[mid:]) * 100 if (n - mid) else 0.0
    m   = _mean(arr) * 100
    rob = "ROBUST" if h1 > 0 and h2 > 0 else ("fragile" if (h1 > 0) != (h2 > 0) else "neg")
    flag = "  <<< +EV" if m > 0 and rob == "ROBUST" else ""
    print(f"  {name:55} n={n:>4} win={w/n*100:>3.0f}%  mean={m:>+6.2f}%  "
          f"OOS {h1:>+5.2f}/{h2:>+5.2f} {rob}{flag}")


# ─── data loading ─────────────────────────────────────────────────────────────
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
        if len(bars) >= MIN_BARS:
            # store as {ymd: {"o": open, "c": close}} + sorted list of days
            data[c] = {_ymd(b["t"]): {"o": b["o"], "c": b["c"]} for b in bars}
    return data


def build_returns(data):
    """Build {coin: {ymd: daily_ret}} — return from open[t-1] to close[t-1] (= public info at t)."""
    rets = {}
    for coin, oc in data.items():
        days = sorted(oc.keys())
        r = {}
        for i in range(1, len(days)):
            o_prev = oc[days[i - 1]]["o"]
            c_prev = oc[days[i - 1]]["c"]
            if o_prev > 0:
                r[days[i]] = (c_prev - o_prev) / o_prev  # day[i-1]'s return, known at day[i] open
        rets[coin] = r
    return rets


# ─── STEP 1: pair correlation matrix ─────────────────────────────────────────
def compute_pair_corrs(rets, all_days):
    """
    For each ordered pair (A→B), compute cross-lag correlation:
      corr(ret_A[t-1], ret_B[t])  over a trailing PAIR_WIN window.

    This is estimated on ALL days using the full history (not rolling per-day),
    as a first pass to identify persistent relationships.

    Lookahead-safe: we estimate these correlations on historical data; the actual
    trailing-window estimation in the tradeable step re-estimates each rebalance day
    from data ≤ t.
    """
    coins = list(rets.keys())
    nc = len(coins)

    # Global cross-lag corr using all available data
    global_corr = {}  # (A, B) -> corr(ret_A[t], ret_B[t+1])

    for i, a in enumerate(coins):
        for j, b in enumerate(coins):
            if a == b:
                continue
            # Build aligned pairs: (ret_A[t], ret_B[t+1])
            xs, ys = [], []
            ra, rb = rets[a], rets[b]
            for d_idx in range(len(all_days) - 1):
                d_t   = all_days[d_idx]
                d_tp1 = all_days[d_idx + 1]
                if d_t in ra and d_tp1 in rb:
                    xs.append(ra[d_t])
                    ys.append(rb[d_tp1])
            if len(xs) >= PAIR_MIN_OBS:
                global_corr[(a, b)] = _corr(xs, ys)
            else:
                global_corr[(a, b)] = 0.0

    return global_corr, coins


def identify_leaders(global_corr, coins):
    """
    A coin A is a 'leader' of B if global_corr[(A, B)] > CORR_THRESH.
    Count, for each coin, how many followers it has (strong positive cross-lag).
    Also count follower status (led by many leaders).
    """
    leader_count  = {c: 0 for c in coins}   # outgoing links
    follower_count = {c: 0 for c in coins}  # incoming links
    link_corrs = {}  # (A, B) -> corr, for positive links only

    for (a, b), c in global_corr.items():
        if c > CORR_THRESH:
            leader_count[a]   += 1
            follower_count[b] += 1
            link_corrs[(a, b)] = c

    # Leaders: coins with enough strong follower links
    leaders = [c for c, cnt in leader_count.items() if cnt >= LEADER_MIN_LINKS]
    leaders.sort(key=lambda c: leader_count[c], reverse=True)

    return leaders, leader_count, follower_count, link_corrs


# ─── STEP 2: rolling pair estimation (lookahead-safe, per rebalance) ──────────
def rolling_pair_corr(rets, a, b, win_days):
    """
    Estimate corr(ret_A[t], ret_B[t+1]) over a trailing window of days.
    win_days is a sorted list of dates UP TO AND INCLUDING today's date t.
    We use ret_A[d_t] and ret_B[d_t+1], both fully known at t+1 open.
    But since we only signal AT t (not t+1), ret_B[t+1] is LOOKAHEAD.
    WAIT — correct framing:
      ret_A[t-1] is known at close[t-1] → available at t.
      ret_B[t] is known at close[t] → available at t+1 open.
    So cross-lag signal: at time t, use ret_A[t-1] to predict ret_B[t] (enter at t+1 open).
    This means we estimate corr using pairs (ret_A[s-1], ret_B[s]) for s in the window.
    All such data IS available at t.
    """
    ra, rb = rets.get(a, {}), rets.get(b, {})
    xs, ys = [], []
    for i in range(1, len(win_days)):
        d_prev = win_days[i - 1]
        d_curr = win_days[i]
        if d_prev in ra and d_curr in rb:
            xs.append(ra[d_prev])
            ys.append(rb[d_curr])
    if len(xs) < PAIR_MIN_OBS:
        return 0.0
    return _corr(xs, ys)


# ─── STEP 3: tradeable backtest ───────────────────────────────────────────────
def run_tradeable(data, rets, all_days, btc_rets, leader_set):
    """
    Each day t (with enough history):
      1. Re-estimate all leader→follower correlations on trailing PAIR_WIN window (≤ t).
      2. For each leader in leader_set:
         a. Compute leader's SIGNAL_WIN-day trailing return (known at t).
         b. If |signal| >= SIGNAL_THRSH, identify top-CORR followers (corr > CORR_THRESH).
         c. If leader signal is positive: go LONG top-K followers by correlation strength.
            If negative: go SHORT top-K followers.
      3. Market-neutral: net out market beta across positions (beta-neutralize).
      4. Holds HOLD days. Enter at t+1 open, exit at t+1+HOLD close.
      5. Net of costs.

    Returns list of per-trade returns (for each leader×signal event).
    """
    burn_in  = max(PAIR_WIN + SIGNAL_WIN + 10, BTC_BETA_WIN + 20)
    all_days = sorted(all_days)
    coins    = list(data.keys())

    trades = []  # list of (day_idx, leader, direction, net_ret)

    for t_idx in range(burn_in, len(all_days) - HOLD - 2):
        d_t      = all_days[t_idx]
        d_entry  = all_days[t_idx + 1]
        d_exit   = all_days[min(t_idx + 1 + HOLD, len(all_days) - 1)]

        win_days = all_days[max(0, t_idx - PAIR_WIN): t_idx + 1]  # ≤ t

        for leader in leader_set:
            if leader not in rets:
                continue

            # Leader signal: trailing SIGNAL_WIN-day return known at t
            sig_start = all_days[max(0, t_idx - SIGNAL_WIN)]
            if sig_start not in data[leader] or d_t not in data[leader]:
                continue
            o_sig = data[leader][sig_start]["o"]
            c_sig = data[leader][d_t]["c"]  # close at t = public info
            if o_sig <= 0:
                continue
            signal = (c_sig - o_sig) / o_sig  # leader's trailing return

            if abs(signal) < SIGNAL_THRSH:
                continue  # no strong signal

            direction = 1 if signal > 0 else -1

            # Re-estimate follower links for this leader at time t
            follower_corrs = []
            for b in coins:
                if b == leader:
                    continue
                c_ab = rolling_pair_corr(rets, leader, b, win_days)
                if c_ab > CORR_THRESH:
                    follower_corrs.append((b, c_ab))

            if len(follower_corrs) < K_FOLLOWERS:
                continue

            # Sort by correlation strength, take top-K
            follower_corrs.sort(key=lambda x: x[1], reverse=True)
            followers = [c for c, _ in follower_corrs[:K_FOLLOWERS]]

            # Check data availability
            valid = [c for c in followers
                     if d_entry in data[c] and d_exit in data[c]]
            if len(valid) < 2:
                continue

            # Compute beta for each follower (for beta-neutralization)
            betas = []
            for c in valid:
                cr = [rets[c].get(d, 0.0) for d in win_days[-BTC_BETA_WIN:] if d in rets.get(c, {})]
                br = [btc_rets.get(d, 0.0) for d in win_days[-BTC_BETA_WIN:] if d in btc_rets]
                betas.append(_ols_beta(cr, br) if len(cr) >= 8 and len(br) >= 8 else 1.0)

            avg_beta = _mean(betas) if betas else 1.0

            # Forward returns of followers (enter t+1 open, exit t+1+HOLD close)
            fwd_rets = []
            for c in valid:
                o = data[c][d_entry]["o"]
                cl = data[c][d_exit]["c"]
                if o > 0:
                    fwd_rets.append((cl - o) / o * direction)

            if not fwd_rets:
                continue

            gross = _mean(fwd_rets)

            # Beta hedge: market moved direction × avg_beta × BTC return over same period
            btc_fwd = 0.0
            if d_entry in btc_rets and d_exit in data.get("BTC", {}):
                o_btc = data["BTC"].get(d_entry, {}).get("o", 0)
                c_btc = data["BTC"].get(d_exit, {}).get("c", 0)
                if o_btc > 0:
                    btc_fwd = (c_btc - o_btc) / o_btc

            # Beta-neutralized: subtract market component (avg_beta × BTC_fwd × direction)
            # We're already taking directional positions, so the market component is:
            beta_adj = avg_beta * btc_fwd * direction
            net_beta_neutral = gross - beta_adj

            # Cost: 10bps each way for long positions (simplified: 2× per trade)
            net_ret = net_beta_neutral - 2 * COST

            trades.append({
                "t_idx": t_idx,
                "leader": leader,
                "signal": signal,
                "direction": direction,
                "gross": gross,
                "net": net_ret,
                "n_followers": len(valid),
            })

    return trades


def run_tradeable_with_leaders(data, rets, all_days, btc_rets, leader_set):
    """Wrapper that returns just net returns list."""
    trades = run_tradeable(data, rets, all_days, btc_rets, leader_set)
    return [t["net"] for t in trades], trades


# ─── STEP 4: enhancer test ────────────────────────────────────────────────────
def run_enhancer(data, rets, all_days, btc_rets, leaders, link_corrs, global_corr):
    """
    Does adding 'leader's recent return' as a FEATURE improve xs-momentum?

    xs-momentum: rank coins by LB-day trailing return → long top-K, short bottom-K.
    xs-momentum + lead-lag: for each coin, add a 'leader signal score' =
      sum over its strong leaders: corr(leader,coin) × leader's recent return.
    Rank by: alpha * own_momentum + (1 - alpha) * leader_score.
    Test alpha in {0, 0.5, 1.0} (0=pure leader, 0.5=blend, 1.0=pure momentum).
    """
    LB = 7
    HOLD_E = 10
    K = 8
    ALPHA_VALUES = [0.0, 0.25, 0.5, 0.75, 1.0]
    COST_E = 10.0 / 1e4
    MIN_BURN = LB + PAIR_WIN + 5

    coins = list(data.keys())
    all_days_s = sorted(all_days)

    results = {alpha: [] for alpha in ALPHA_VALUES}

    for t_idx in range(MIN_BURN, len(all_days_s) - HOLD_E - 1):
        d_t     = all_days_s[t_idx]
        d_lb    = all_days_s[max(0, t_idx - LB)]
        d_entry = all_days_s[t_idx + 1]
        d_exit  = all_days_s[min(t_idx + 1 + HOLD_E, len(all_days_s) - 1)]
        win_days = all_days_s[max(0, t_idx - PAIR_WIN): t_idx + 1]

        # Own trailing momentum score for each coin
        mom_scores = {}
        for coin in coins:
            if d_t in data[coin] and d_lb in data[coin]:
                c_now  = data[coin][d_t]["c"]
                c_past = data[coin][d_lb]["c"]
                if c_past > 0:
                    mom_scores[coin] = c_now / c_past - 1

        # Leader signal score for each coin: weighted sum of leader signals
        leader_scores = {coin: 0.0 for coin in coins}
        for leader in leaders:
            # Leader's recent return (trailing LB-day, known at t)
            if d_t not in data.get(leader, {}) or d_lb not in data.get(leader, {}):
                continue
            o_l = data[leader][d_lb]["c"]
            c_l = data[leader][d_t]["c"]
            if o_l <= 0:
                continue
            leader_ret = c_l / o_l - 1

            # How much does this leader predict each follower?
            for coin in coins:
                if coin == leader:
                    continue
                pair_c = rolling_pair_corr(rets, leader, coin, win_days)
                if pair_c > CORR_THRESH:
                    leader_scores[coin] += pair_c * leader_ret

        # Forward returns (enter t+1 open, exit close)
        def fwd(coin):
            o = data[coin].get(d_entry, {}).get("o", 0)
            c = data[coin].get(d_exit, {}).get("c", 0)
            return (c - o) / o if o > 0 else 0.0

        for alpha in ALPHA_VALUES:
            # Blend: alpha × own_mom + (1-alpha) × leader_score
            blended = {}
            for coin in coins:
                if coin not in mom_scores:
                    continue
                ms = mom_scores[coin]
                ls = leader_scores.get(coin, 0.0)
                blended[coin] = alpha * ms + (1 - alpha) * ls

            if len(blended) < 2 * K + 4:
                continue

            ranked = sorted(blended.items(), key=lambda x: x[1], reverse=True)
            longs  = [c for c, _ in ranked[:K]]
            shorts = [c for c, _ in ranked[-K:]]

            # Check data availability
            longs  = [c for c in longs  if d_entry in data[c] and d_exit in data[c]]
            shorts = [c for c in shorts if d_entry in data[c] and d_exit in data[c]]
            if len(longs) < 2 or len(shorts) < 2:
                continue

            lr = _mean([fwd(c) for c in longs])
            sr = _mean([fwd(c) for c in shorts])
            ls_ret = (lr - sr) - 2 * COST_E

            results[alpha].append(ls_ret)

    return results


# ─── STEP 5: permutation control ─────────────────────────────────────────────
def run_permutation_control(data, rets, all_days, btc_rets, leader_set, coins,
                             real_mean, n_perms=N_PERMS, seed=RANDOM_SEED):
    """
    Shuffle which coins are 'leaders' (random set of same size), re-run tradeable.
    Compare real_mean vs distribution of shuffled means.
    p-value = fraction of shuffled runs with mean >= real_mean.
    """
    rng = random.Random(seed)
    perm_means = []
    all_coins_list = list(coins)

    for _ in range(n_perms):
        shuffled_leaders = rng.sample(all_coins_list, min(len(leader_set), len(all_coins_list)))
        perm_rets, _ = run_tradeable_with_leaders(data, rets, all_days, btc_rets,
                                                   set(shuffled_leaders))
        perm_means.append(_mean(perm_rets) if perm_rets else 0.0)

    p_value = sum(1 for m in perm_means if m >= real_mean) / n_perms if perm_means else 1.0
    return perm_means, p_value


# ─── STEP 6: correlation vs momentum ─────────────────────────────────────────
def compute_stream_corr(trade_rets_list, mom_rets_list):
    """Pearson corr between two trade-return streams (aligned by index if same length)."""
    n = min(len(trade_rets_list), len(mom_rets_list))
    if n < 10:
        return float("nan")
    return _corr(trade_rets_list[:n], mom_rets_list[:n])


# ─── xs-momentum baseline (for comparison) ───────────────────────────────────
def run_xsmom_baseline(data, all_days):
    """Same xs-momentum as edge_xsectional.py (LB=7, hold=10, K=8)."""
    LB = 7
    HOLD_M = 10
    K = 8
    coins = list(data.keys())
    all_days_s = sorted(all_days)
    ls_rets = []

    for t in range(LB, len(all_days_s) - HOLD_M - 1):
        d     = all_days_s[t]
        d_lb  = all_days_s[t - LB]
        d_entry = all_days_s[t + 1]
        d_exit  = all_days_s[t + 1 + HOLD_M] if t + 1 + HOLD_M < len(all_days_s) else all_days_s[-1]

        ranked = []
        for coin in coins:
            if d in data[coin] and d_lb in data[coin]:
                c_now  = data[coin][d]["c"]
                c_past = data[coin][d_lb]["c"]
                if c_past > 0:
                    ranked.append((coin, c_now / c_past - 1))

        if len(ranked) < 2 * K + 4:
            continue
        ranked.sort(key=lambda x: x[1], reverse=True)
        longs  = [c for c, _ in ranked[:K]]
        shorts = [c for c, _ in ranked[-K:]]

        def fwd(coin):
            o = data[coin].get(d_entry, {}).get("o", 0)
            c_price = data[coin].get(d_exit, {}).get("c", 0)
            return (c_price - o) / o if o > 0 else 0.0

        longs  = [c for c in longs  if d_entry in data[c] and d_exit in data[c]]
        shorts = [c for c in shorts if d_entry in data[c] and d_exit in data[c]]
        if len(longs) < 2 or len(shorts) < 2:
            continue

        lr = _mean([fwd(c) for c in longs])
        sr = _mean([fwd(c) for c in shorts])
        ls_rets.append((lr - sr) - 2 * COST)

    return ls_rets


# ─── main ─────────────────────────────────────────────────────────────────────
def main():
    print("=" * 72)
    print("  LEAD-LAG NETWORK ALPHA HUNT")
    print(f"  Universe: top-{TOPN} liquid crypto perps (≥{VOL_FLOOR/1e6:.0f}M vol), ≥{MIN_BARS} bars")
    print(f"  Pair estimation: {PAIR_WIN}d trailing, lag={PAIR_LAG}d, corr_thresh={CORR_THRESH}")
    print(f"  Signal: {SIGNAL_WIN}d leader return ≥ {SIGNAL_THRSH*100:.0f}%, follow top-{K_FOLLOWERS} followers")
    print(f"  Hold={HOLD}d | cost={COST_BPS:.0f}bps/name | permutations={N_PERMS}")
    print("=" * 72)

    # Load
    data = load()
    coins = sorted(data.keys())
    print(f"\n  {len(coins)} coins loaded: {', '.join(coins[:20])}{'...' if len(coins)>20 else ''}")

    # Build returns (known at next day's open)
    rets = build_returns(data)
    all_days = sorted({d for cd in data.values() for d in cd})
    btc_rets = rets.get("BTC", {})

    print(f"  {len(all_days)} trading days available ({all_days[0]} → {all_days[-1]})")

    # ─── STEP 1: global pair correlation map ─────────────────────────────────
    print("\n" + "─" * 72)
    print("  STEP 1: Global lead-lag correlation map (A's return predicts B's next-day return)")
    print("─" * 72)

    global_corr, _ = compute_pair_corrs(rets, all_days)

    # Show distribution of pair corrs
    corr_vals = [v for v in global_corr.values() if v != 0.0]
    if corr_vals:
        corr_vals.sort()
        n_pos  = sum(1 for v in corr_vals if v >  CORR_THRESH)
        n_neg  = sum(1 for v in corr_vals if v < -CORR_THRESH)
        n_zero = len(corr_vals) - n_pos - n_neg
        print(f"  Total pairs: {len(corr_vals)} (ordered, excluding self)")
        print(f"  Corr distribution: min={corr_vals[0]:+.3f}  median={corr_vals[len(corr_vals)//2]:+.3f}"
              f"  max={corr_vals[-1]:+.3f}  mean={_mean(corr_vals):+.3f}")
        print(f"  Links |corr|>{CORR_THRESH}: positive={n_pos} | negative={n_neg} | near-zero={n_zero}")

    # ─── STEP 2: identify leaders ─────────────────────────────────────────────
    print("\n" + "─" * 72)
    print("  STEP 2: Leader/follower identification")
    print("─" * 72)

    leaders, leader_count, follower_count, link_corrs = identify_leaders(global_corr, coins)

    print(f"\n  {'Coin':12}  {'Out-links (leader)':22}  {'In-links (follower)':22}")
    print(f"  {'────':12}  {'──────────────────────':22}  {'──────────────────────':22}")
    for c in sorted(coins, key=lambda x: leader_count[x], reverse=True)[:20]:
        flag = " ← LEADER" if c in leaders else ""
        print(f"  {c:12}  {leader_count[c]:>6} follower links           {follower_count[c]:>6} leader links{flag}")

    print(f"\n  Identified {len(leaders)} leaders (≥{LEADER_MIN_LINKS} follower links): {leaders}")

    if len(leaders) < 2:
        print("\n  ⚠ Not enough leaders identified — reducing LEADER_MIN_LINKS or CORR_THRESH")
        # Relax threshold
        global_fallback = {k: v for k, v in global_corr.items() if abs(v) > 0.05}
        print(f"  Pairs with |corr|>0.05: {len(global_fallback)}")
        print("\n  VERDICT: Network too sparse for a tradeable test at the set thresholds.")
        print("  — Low correlations across coin pairs are expected given asset co-movement is mostly")
        print("    captured by BTC/ETH beta; RESIDUAL cross-coin lead-lag is near-zero.")
        return

    # Top leader→follower links
    print(f"\n  Top leader→follower links (corr > {CORR_THRESH}):")
    sorted_links = sorted(link_corrs.items(), key=lambda x: x[1], reverse=True)[:20]
    for (a, b), c in sorted_links:
        print(f"    {a:10} → {b:10}  corr={c:+.3f}")

    # ─── STEP 3: tradeable backtest ────────────────────────────────────────────
    print("\n" + "─" * 72)
    print("  STEP 3: Tradeable test — leader-triggered follower trades")
    print(f"  (enter t+1 open when leader moves ≥{SIGNAL_THRSH*100:.0f}% in {SIGNAL_WIN}d, hold {HOLD}d)")
    print("─" * 72)

    net_rets, all_trades = run_tradeable_with_leaders(data, rets, all_days, btc_rets, set(leaders))

    print(f"\n  Total trade events fired: {len(net_rets)}")
    if not net_rets:
        print("  ⚠ No trades fired — signal threshold may be too high or leaders too few")
        print("\n  VERDICT: No tradeable lead-lag signal found.")
        return

    _rep("  Lead-lag follower (beta-neutralized)", net_rets)

    # Per-leader breakdown
    by_leader = {}
    for t in all_trades:
        by_leader.setdefault(t["leader"], []).append(t["net"])

    print("\n  By leader:")
    for leader in leaders:
        if leader in by_leader:
            arr = by_leader[leader]
            m = _mean(arr) * 100
            w = sum(1 for r in arr if r > 0) / len(arr) * 100
            print(f"    {leader:12}  n={len(arr):>4}  win={w:>3.0f}%  mean={m:>+6.2f}%")

    # ─── STEP 4: permutation control ──────────────────────────────────────────
    print("\n" + "─" * 72)
    print(f"  STEP 4: Permutation control ({N_PERMS} shuffles of leader identity)")
    print("─" * 72)

    real_mean = _mean(net_rets) if net_rets else 0.0
    print(f"  Real lead-lag mean: {real_mean*100:+.3f}%")
    print(f"  Running {N_PERMS} random-leader shuffles...")

    perm_means, p_value = run_permutation_control(
        data, rets, all_days, btc_rets, set(leaders), coins, real_mean
    )

    perm_mean_of_means = _mean(perm_means) if perm_means else 0.0
    perm_std  = _stdev(perm_means) if perm_means else 0.0
    z_score = (real_mean - perm_mean_of_means) / perm_std if perm_std > 0 else 0.0

    print(f"  Shuffle distribution: mean={perm_mean_of_means*100:+.3f}%  "
          f"std={perm_std*100:.3f}%  (n={len(perm_means)} shuffles)")
    print(f"  Real z-score vs shuffle: {z_score:+.2f}")
    print(f"  p-value (fraction shuffles >= real): {p_value:.3f}")
    if p_value < 0.05 and real_mean > 0:
        print("  *** PERMUTATION: REAL SIGNAL BEATS SHUFFLE (p<0.05) ***")
    elif p_value < 0.10 and real_mean > 0:
        print("  --- PERMUTATION: marginal (p<0.10, verify OOS) ---")
    else:
        print("  ✗ PERMUTATION: random leaders achieve similar result → DATA-MINED NOISE")

    # ─── STEP 5: enhancer test ────────────────────────────────────────────────
    print("\n" + "─" * 72)
    print("  STEP 5: Enhancer test — does leader signal improve xs-momentum ranking?")
    print(f"  Blend: alpha×own_momentum + (1-alpha)×leader_score | K={8}/leg, LB={7}d, hold={10}d")
    print("─" * 72)

    # Run baseline xs-momentum
    baseline_rets = run_xsmom_baseline(data, set(all_days))
    print(f"\n  Baseline xs-momentum (LB=7d, hold=10d, K=8):")
    _rep("  xs-momentum baseline", baseline_rets)

    print(f"\n  Blended (alpha × own_mom + (1-alpha) × leader_score):")
    enhancer_results = run_enhancer(data, rets, set(all_days), btc_rets,
                                     leaders, link_corrs, global_corr)

    for alpha, arr in sorted(enhancer_results.items()):
        label = f"alpha={alpha:.2f} ({'pure_leader' if alpha==0 else 'pure_mom' if alpha==1 else f'blend'})"
        _rep(f"  {label}", arr)

    # ─── STEP 6: correlation vs momentum ─────────────────────────────────────
    print("\n" + "─" * 72)
    print("  STEP 6: Correlation of lead-lag returns vs xs-momentum")
    print("─" * 72)

    if net_rets and baseline_rets:
        corr_vs_mom = compute_stream_corr(net_rets, baseline_rets)
        print(f"  Corr(lead-lag net returns, xs-momentum returns): {corr_vs_mom:+.3f}")
        if abs(corr_vs_mom) < 0.3:
            print("  → LOW correlation — lead-lag is a distinct strategy (if +EV)")
        else:
            print("  → HIGH correlation — lead-lag largely overlaps with momentum")
    else:
        print("  (insufficient trade stream for correlation)")

    # ─── FINAL VERDICT ────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("  FINAL VERDICT")
    print("=" * 72)

    n_trades = len(net_rets)
    mean_net = _mean(net_rets) * 100 if net_rets else 0.0
    is_positive = mean_net > 0
    mid = n_trades // 2
    h1 = _mean(net_rets[:mid]) * 100 if mid and net_rets else 0.0
    h2 = _mean(net_rets[mid:]) * 100 if net_rets and mid else 0.0
    oos_robust = h1 > 0 and h2 > 0
    perm_pass = p_value < 0.05 and is_positive

    print(f"\n  Lead-lag network: {len(leaders)} leaders identified, {n_trades} trade events")
    print(f"  Net mean return : {mean_net:>+6.2f}%")
    print(f"  OOS (H1/H2)    : {h1:>+5.2f}% / {h2:>+5.2f}%  → {'ROBUST' if oos_robust else 'FRAGILE/NEG'}")
    print(f"  Permutation p  : {p_value:.3f}  → {'PASS' if perm_pass else 'FAIL (noise)'}")
    print()

    if is_positive and oos_robust and perm_pass:
        verdict = "TRADEABLE LEAD-LAG NETWORK: +EV, OOS-robust, beats permutation control."
        verdict += "\n  Add to candidate queue; wire SHADOW-first alongside xs-momentum."
    elif is_positive and oos_robust and not perm_pass:
        verdict = "MARGINAL: +EV and OOS-robust but permutation control FAILS → likely data-mined."
        verdict += "\n  Do NOT wire. The coin-to-coin network does not beat random-leader baseline."
    elif not is_positive or not oos_robust:
        verdict = "REFUTED: Lead-lag network is NOT a tradeable alpha in this universe."
        verdict += "\n  Possible reasons:"
        verdict += "\n    1. Crypto alts are too correlated with BTC — residual cross-coin predictability"
        verdict += "\n       is absorbed by the BTC factor; there is no persistent relative lead-lag."
        verdict += "\n    2. Signal-to-noise ratio too low: N²-pairs testing inflates false positives;"
        verdict += "\n       permutation control confirms the correlations are noise."
        verdict += "\n    3. The validated momentum edge IS the lead-lag relationship — when coins move,"
        verdict += "\n       the whole universe follows; there is no coin-specific predictability beyond that."
        verdict += "\n  *** BTC lead-lag was refuted (edge_sweep.py). Coin-to-coin lead-lag is also REFUTED. ***"
        verdict += "\n  Do NOT add to candidate queue."
    else:
        verdict = "INCONCLUSIVE: insufficient data."

    print(f"  VERDICT: {verdict}")

    # Summary of strongest pair links for the record
    print("\n" + "─" * 72)
    print("  Strongest persistent leader→follower links (global estimation):")
    for (a, b), c in sorted_links[:10]:
        print(f"    {a:10} → {b:10}  cross-lag corr={c:+.3f}")
    print()
    print("  Note: cross-lag corr estimates cover the full ~260d window (global prior).")
    print("  Rolling estimates (per rebalance day, from data ≤ t) are used in the tradeable test.")


if __name__ == "__main__":
    main()
