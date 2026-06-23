#!/usr/bin/env python3
"""Portfolio construction — combine validated market-neutral edges into one book.

ASSIGNMENT (Wave 5 Y1):
  1. Build daily LS return stream for each validated edge:
       (a) momentum  : xs LB=7/hold=10, K=8, 10bps/leg
       (b) vol-disp  : within-beta-tercile idio-vol, hold=10, 10bps/leg
       (c) sortino   : within-beta-tercile Sortino ratio, hold=10, 10bps/leg
       (d) pairs     : stat-arb z>2, 30d LB, 10bps/leg (daily stream)
  2. 4x4 correlation matrix (pairs is daily; rebalance streams are per-period → align carefully)
  3. Compare combination methods on OOS Sharpe + maxDD:
       (a) equal-weight
       (b) inverse-vol (risk-parity)
       (c) regularized Sharpe-optimal (fit H1, apply H2 — HONEST OOS)
       (d) best single edge alone
  4. Apply validated gates (corr-regime + vol-regime) to the combined book.
  5. Cost-adjusted realistic estimate.

METHODOLOGY BAR: lookahead-safe; cost-aware; OOS-robust.
OVERFIT GUARD: report BOTH in-sample-optimal weights AND honest OOS application.

Run: BT_CACHE_ONLY=1 python3 scripts/edge_portfolio.py
"""
import os, sys, math, itertools
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime, timezone
from hermes_trader.client.universe import get_universe
from _bt_candles import get as get_candles

# ─── config ───────────────────────────────────────────────────────────────────
TOPN        = 50
VOL_FLOOR   = 5e6
K           = 8
COST_BPS    = 10.0           # per name, round-trip
COST        = COST_BPS / 1e4

# Momentum
MOM_LB      = 7
MOM_HOLD    = 10

# Vol-dispersion + Sortino (within-beta-tercile)
IDVOL_WIN   = 30
SORTINO_WIN = 60
BETA_WIN    = 30
HOLD        = 10             # both beta-neutral factors use same hold

# Pairs
PAIR_LB     = 30
Z_ENTRY     = 2.0
Z_EXIT      = 0.5
MIN_CORR    = 0.6
PAIR_MAXHOLD = 15
TOPN_PAIRS  = 40             # pairs uses top-40 (matches edge_pairs.py)

# Regime gates
CORR_WIN    = 14             # rolling pairwise correlation window for gate
BTC_VOL_WIN = 20             # BTC trailing vol window for low-vol gate

# Regularisation for Sharpe-optimal (shrink toward equal-weight)
SHRINK_ALPHA = 0.5           # 0 = pure optimal; 1 = equal-weight; 0.5 = midpoint


# ─── pure-python stats helpers ─────────────────────────────────────────────────
def _ymd(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y%m%d")

def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0

def _var(xs):
    if len(xs) < 2: return 0.0
    m = _mean(xs)
    return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)

def _std(xs):
    v = _var(xs)
    return math.sqrt(v) if v > 0 else 0.0

def _pstdev(xs):
    if len(xs) < 2: return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))

def _pearson(a, b):
    n = min(len(a), len(b))
    if n < 6: return float("nan")
    a, b = list(a[-n:]), list(b[-n:])
    ma, mb = _mean(a), _mean(b)
    num = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((x - mb) ** 2 for x in b))
    if da <= 0 or db <= 0: return float("nan")
    return num / (da * db)

def _ols_beta(cr, br):
    n = min(len(cr), len(br))
    if n < 8: return 1.0
    cr, br = cr[-n:], br[-n:]
    mb = _mean(br)
    vb = sum((x - mb) ** 2 for x in br)
    if vb <= 0: return 1.0
    mc = _mean(cr)
    return sum((a - mc) * (b - mb) for a, b in zip(cr, br)) / vb

def _sharpe(rets, ann_factor=None):
    """Annualised Sharpe. Default: daily returns * sqrt(365)."""
    if len(rets) < 4: return float("nan")
    m = _mean(rets)
    s = _pstdev(rets)
    if s <= 0: return float("nan")
    f = ann_factor if ann_factor is not None else math.sqrt(365)
    return (m / s) * f

def _max_dd(rets):
    """Max drawdown (positive fraction) on cumulative return series."""
    cum, peak, worst = 0.0, 0.0, 0.0
    for r in rets:
        cum += r
        if cum > peak: peak = cum
        dd = peak - cum
        if dd > worst: worst = dd
    return worst

def _cov(a, b):
    n = min(len(a), len(b))
    if n < 4: return 0.0
    a, b = a[-n:], b[-n:]
    ma, mb = _mean(a), _mean(b)
    return sum((a[i] - ma) * (b[i] - mb) for i in range(n)) / (n - 1)


# ─── data loading ──────────────────────────────────────────────────────────────
def load(topn=TOPN):
    """Top-TOPN liquid perps (no HIP-3, no spot/index) from disk cache."""
    uni = [m for m in get_universe(include_hip3=False)
           if ":" not in (m.get("coin") or "")
           and not (m.get("coin") or "").startswith("@")
           and m.get("type") != "spot"
           and float(m.get("dayNtlVlm") or 0) >= VOL_FLOOR]
    uni = sorted(uni, key=lambda m: float(m.get("dayNtlVlm") or 0), reverse=True)[:topn]
    data = {}
    for m in uni:
        c = m["coin"]
        bars = get_candles(c, "1d", 260)
        if len(bars) >= 80:
            # Store as {ymd: (open, close)} — full bar in data_full
            data[c] = {_ymd(b["t"]): (b["o"], b["c"]) for b in bars}
    return data


def load_full(topn=TOPN):
    """Same but returns {ymd: bar_dict} for beta scoring."""
    uni = [m for m in get_universe(include_hip3=False)
           if ":" not in (m.get("coin") or "")
           and not (m.get("coin") or "").startswith("@")
           and m.get("type") != "spot"
           and float(m.get("dayNtlVlm") or 0) >= VOL_FLOOR]
    uni = sorted(uni, key=lambda m: float(m.get("dayNtlVlm") or 0), reverse=True)[:topn]
    data = {}
    for m in uni:
        c = m["coin"]
        bars = get_candles(c, "1d", 260)
        if len(bars) >= 80:
            data[c] = {_ymd(b["t"]): b for b in bars}
    return data


# ─── helper: daily returns from close-to-close ────────────────────────────────
def _coin_daily_rets(coin_oc, days):
    """Close-to-close daily returns on ordered days."""
    closes = [coin_oc[d][1] for d in days if d in coin_oc and coin_oc[d][1] > 0]
    return [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))
            if closes[i - 1] > 0]


def _coin_daily_rets_full(coin_data, days):
    """Daily returns from a full bar dict {ymd: bar}."""
    closes = [coin_data[d]["c"] for d in days if d in coin_data and coin_data[d]["c"] > 0]
    return [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))
            if closes[i - 1] > 0]


# ─── 1. MOMENTUM stream ────────────────────────────────────────────────────────
def build_momentum_stream(data):
    """xs-momentum LB=7/hold=10, K=8, 10bps/leg.
    Returns list of (signal_day, period_return).
    Signal_day = t (rank day); entry = t+1 open; exit = t+1+hold close.
    """
    all_days = sorted({d for oc in data.values() for d in oc})
    stream = []
    cost_both = 2 * COST
    for t in range(MOM_LB, len(all_days) - MOM_HOLD - 1):
        d     = all_days[t]
        d_lb  = all_days[t - MOM_LB]
        d_ent = all_days[t + 1]
        d_ex  = all_days[t + 1 + MOM_HOLD] if (t + 1 + MOM_HOLD) < len(all_days) else all_days[-1]

        ranked = []
        for coin, oc in data.items():
            if d in oc and d_lb in oc and d_ent in oc and d_ex in oc and oc[d_lb][1] > 0:
                ranked.append((coin, oc[d][1] / oc[d_lb][1] - 1))
        if len(ranked) < 2 * K + 4:
            continue
        ranked.sort(key=lambda x: x[1], reverse=True)
        longs  = [c for c, _ in ranked[:K]]
        shorts = [c for c, _ in ranked[-K:]]

        def fwd(coin):
            o = data[coin][d_ent][0]; c = data[coin][d_ex][1]
            return (c - o) / o if o > 0 else 0.0

        lr = _mean([fwd(c) for c in longs])
        sr = _mean([fwd(c) for c in shorts])
        stream.append((d, (lr - sr) - cost_both))
    return stream


# ─── 2. VOL-DISPERSION stream (within-beta-tercile) ───────────────────────────
def build_vdisp_stream(data):
    """Within-beta-tercile idio-vol, hold=10, 10bps/leg.
    Returns list of (signal_day, period_return).
    """
    all_days = sorted({d for oc in data.values() for d in oc})
    cost_both = 2 * COST

    # Precompute per-coin daily returns keyed by day
    daily_rets = {}
    for coin, oc in data.items():
        days_s = sorted(oc)
        daily_rets[coin] = {}
        for i in range(1, len(days_s)):
            d, prev = days_s[i], days_s[i - 1]
            c_now, c_prev = oc[d][1], oc[prev][1]
            if c_prev > 0:
                daily_rets[coin][d] = c_now / c_prev - 1.0

    btc_rets = daily_rets.get("BTC", {})
    warmup = max(IDVOL_WIN, BETA_WIN) + 2
    stream = []

    for t in range(warmup, len(all_days) - HOLD - 1):
        d     = all_days[t]
        d_ent = all_days[t + 1]
        d_ex  = all_days[t + 1 + HOLD] if (t + 1 + HOLD) < len(all_days) else all_days[-1]

        win_days  = all_days[max(0, t - IDVOL_WIN + 1): t + 1]
        beta_days = all_days[max(0, t - BETA_WIN + 1): t + 1]

        factors = []
        for coin, oc in data.items():
            if d_ent not in oc or d_ex not in oc:
                continue
            cr_win = [daily_rets[coin].get(dd) for dd in win_days]
            cr_win = [r for r in cr_win if r is not None]
            if len(cr_win) < IDVOL_WIN // 2:
                continue
            cr_beta = [daily_rets[coin].get(dd, 0.0) for dd in beta_days]
            br_beta = [btc_rets.get(dd, 0.0) for dd in beta_days]
            beta = _ols_beta(cr_beta, br_beta)
            btc_win = [btc_rets.get(dd, 0.0) for dd in win_days]
            residuals = [cr_win[i] - beta * btc_win[i]
                         for i in range(min(len(cr_win), len(btc_win)))]
            if len(residuals) < 4:
                continue
            idvol = _pstdev(residuals)
            factors.append((coin, beta, idvol))

        if len(factors) < 2 * K + 4:
            continue

        # Within-beta-tercile: sort by beta, split into 3 terciles, rank by idvol within
        factors.sort(key=lambda x: x[1])
        n_f = len(factors)
        t1, t2 = n_f // 3, 2 * n_f // 3
        terciles = [factors[:t1], factors[t1:t2], factors[t2:]]

        k_per = max(1, K // 3)
        longs, shorts = [], []
        for terc in terciles:
            if len(terc) < 2:
                continue
            terc_s = sorted(terc, key=lambda x: x[2], reverse=True)
            longs.extend([c for c, _, _ in terc_s[:k_per]])
            shorts.extend([c for c, _, _ in terc_s[-k_per:]])

        if len(longs) < 2 or len(shorts) < 2:
            continue

        def fwd(coin):
            o = data[coin][d_ent][0]; c = data[coin][d_ex][1]
            return (c - o) / o if o > 0 else 0.0

        lr = _mean([fwd(c) for c in longs])
        sr = _mean([fwd(c) for c in shorts])
        stream.append((d, (lr - sr) - cost_both))

    return stream


# ─── 3. SORTINO stream (within-beta-tercile) ───────────────────────────────────
def build_sortino_stream(data):
    """Within-beta-tercile Sortino ratio (60d), hold=10, 10bps/leg.
    Returns list of (signal_day, period_return).
    """
    all_days = sorted({d for oc in data.values() for d in oc})
    cost_both = 2 * COST

    daily_rets = {}
    for coin, oc in data.items():
        days_s = sorted(oc)
        daily_rets[coin] = {}
        for i in range(1, len(days_s)):
            d, prev = days_s[i], days_s[i - 1]
            c_now, c_prev = oc[d][1], oc[prev][1]
            if c_prev > 0:
                daily_rets[coin][d] = c_now / c_prev - 1.0

    btc_rets = daily_rets.get("BTC", {})
    warmup = SORTINO_WIN + BETA_WIN + 2
    stream = []

    for t in range(warmup, len(all_days) - HOLD - 1):
        d     = all_days[t]
        d_ent = all_days[t + 1]
        d_ex  = all_days[t + 1 + HOLD] if (t + 1 + HOLD) < len(all_days) else all_days[-1]

        score_days = all_days[max(0, t - SORTINO_WIN + 1): t + 1]
        beta_days  = all_days[max(0, t - BETA_WIN + 1): t + 1]

        factors = []
        for coin, oc in data.items():
            if d_ent not in oc or d_ex not in oc:
                continue
            cr = [daily_rets[coin].get(dd) for dd in score_days]
            cr = [r for r in cr if r is not None]
            if len(cr) < 10:
                continue
            m_cr = _mean(cr)
            down = [r for r in cr if r < 0.0]
            if len(down) < 4:
                continue
            sv = sum(r ** 2 for r in down) / len(down)
            dd = math.sqrt(sv)
            if dd <= 0:
                continue
            sortino = m_cr / dd

            cr_beta = [daily_rets[coin].get(dd2, 0.0) for dd2 in beta_days]
            br_beta = [btc_rets.get(dd2, 0.0) for dd2 in beta_days]
            beta = _ols_beta(cr_beta, br_beta)
            factors.append((coin, beta, sortino))

        if len(factors) < 2 * K + 4:
            continue

        # Within-beta-tercile: long HIGH-Sortino, short LOW-Sortino
        factors.sort(key=lambda x: x[1])
        n_f = len(factors)
        t1, t2 = n_f // 3, 2 * n_f // 3
        terciles = [factors[:t1], factors[t1:t2], factors[t2:]]

        k_per = max(1, K // 3)
        longs, shorts = [], []
        for terc in terciles:
            if len(terc) < 2:
                continue
            terc_s = sorted(terc, key=lambda x: x[2], reverse=True)
            longs.extend([c for c, _, _ in terc_s[:k_per]])
            shorts.extend([c for c, _, _ in terc_s[-k_per:]])

        if len(longs) < 2 or len(shorts) < 2:
            continue

        def fwd(coin):
            o = data[coin][d_ent][0]; c = data[coin][d_ex][1]
            return (c - o) / o if o > 0 else 0.0

        lr = _mean([fwd(c) for c in longs])
        sr = _mean([fwd(c) for c in shorts])
        stream.append((d, (lr - sr) - cost_both))

    return stream


# ─── 4. PAIRS daily stream ─────────────────────────────────────────────────────
def build_pairs_daily(data):
    """Pairs stat-arb. Returns dict {day: daily_pnl} for open positions.

    Uses top-40 subset (matches edge_pairs.py / edge_stack.py style).
    We build a walk-forward sim: for each pair, walk day by day, enter on z>Z_ENTRY
    (while corr>MIN_CORR), exit on z<Z_EXIT or PAIR_MAXHOLD; accrue spread convergence
    daily. Returns {day -> mean_daily_pnl_across_active_pairs}.
    Cost charged at entry/exit events: 2*COST per trade.
    """
    # Use close prices from data (which stores (open, close) tuples)
    coins = list(data.keys())[:TOPN_PAIRS]
    all_days = sorted({d for c in coins for d in data[c] if d in data[c]})

    log_prices = {}
    for coin in coins:
        log_prices[coin] = {}
        for d, (o, c) in data[coin].items():
            if c > 0:
                log_prices[coin][d] = math.log(c)

    state = {}   # (a,b) -> {side, mu, sd, entered_day, days_held, cost_charged}
    daily_pnl = {}  # day -> list of pnl contributions

    pairs_list = [(a, b) for a, b in itertools.combinations(coins, 2)
                  if len(set(data[a].keys()) & set(data[b].keys())) >= PAIR_LB + 30]

    for t in range(PAIR_LB, len(all_days)):
        d  = all_days[t]
        dp = all_days[t - 1]
        daily_pnl.setdefault(d, [])

        for a, b in pairs_list:
            common_set = set(log_prices.get(a, {})) & set(log_prices.get(b, {}))
            if d not in common_set:
                continue

            key = (a, b)

            if key in state:
                # active position: accrue daily spread convergence P&L
                s = state[key]
                if dp in common_set:
                    spd_d  = log_prices[a][d]  - log_prices[b][d]
                    spd_dp = log_prices[a][dp] - log_prices[b][dp]
                    pnl_day = s["side"] * (spd_dp - spd_d)   # convergence toward mean
                    daily_pnl[d].append(pnl_day)

                # check exit condition: |z| < Z_EXIT or days held >= PAIR_MAXHOLD
                common_hist = sorted(d2 for d2 in common_set if d2 <= d)
                win_hist = common_hist[-PAIR_LB - 1: -1]  # window ending BEFORE d (lookahead-safe)
                if len(win_hist) >= PAIR_LB:
                    win_spreads = [log_prices[a][dd] - log_prices[b][dd]
                                   for dd in win_hist if dd in common_set]
                    if len(win_spreads) >= PAIR_LB // 2:
                        mu_now = _mean(win_spreads)
                        sd_now = _pstdev(win_spreads)
                        cur_spread = log_prices[a][d] - log_prices[b][d]
                        z_now = (cur_spread - mu_now) / sd_now if sd_now > 0 else 0.0
                        s["days_held"] = s.get("days_held", 0) + 1
                        if abs(z_now) <= Z_EXIT or s["days_held"] >= PAIR_MAXHOLD:
                            # close: charge exit cost
                            daily_pnl[d].append(-2 * COST)
                            del state[key]
            else:
                # check entry: build rolling window, require corr > MIN_CORR
                common_hist = sorted(d2 for d2 in common_set if d2 <= d)
                if len(common_hist) < PAIR_LB + 1:
                    continue
                win_hist = common_hist[-(PAIR_LB + 1): -1]  # strictly before d
                if len(win_hist) < PAIR_LB:
                    continue
                win_spreads = [log_prices[a][dd] - log_prices[b][dd]
                               for dd in win_hist if dd in common_set]
                if len(win_spreads) < PAIR_LB:
                    continue
                mu_w = _mean(win_spreads)
                sd_w = _pstdev(win_spreads)
                if sd_w <= 0:
                    continue

                # Rolling correlation check
                la_w = [log_prices[a][dd] for dd in win_hist if dd in log_prices.get(a, {})]
                lb_w = [log_prices[b][dd] for dd in win_hist if dd in log_prices.get(b, {})]
                n_aligned = min(len(la_w), len(lb_w))
                if n_aligned < PAIR_LB // 2:
                    continue
                ra = [la_w[i] - la_w[i - 1] for i in range(1, len(la_w))]
                rb = [lb_w[i] - lb_w[i - 1] for i in range(1, len(lb_w))]
                rho = _pearson(ra, rb)
                if math.isnan(rho) or rho < MIN_CORR:
                    continue

                cur_spread = log_prices[a].get(d)
                if cur_spread is None:
                    continue
                cur_spread = cur_spread - log_prices[b].get(d, 0.0)
                z = (cur_spread - mu_w) / sd_w
                if abs(z) < Z_ENTRY:
                    continue

                # Enter: charge entry cost
                side = -1 if z > 0 else 1
                state[key] = {"side": side, "mu": mu_w, "sd": sd_w,
                              "days_held": 0}
                daily_pnl[d].append(-2 * COST)  # entry cost

    # Return mean daily pnl only for days with active positions
    result = {}
    for d, pnls in daily_pnl.items():
        if pnls:
            result[d] = _mean(pnls)
    return result


# ─── 5. REGIME SIGNALS ─────────────────────────────────────────────────────────
def build_regime_signals(data, all_days):
    """Compute two validated regime gates (lookahead-safe):
      corr_high : rolling avg pairwise correlation > rolling median → True
      btc_low_vol : BTC trailing vol < rolling median → True (vol-regime gate)
    Returns dict {day -> {"corr_high": bool|None, "btc_low_vol": bool|None}}
    """
    # Daily returns
    daily_rets = {}
    for coin, oc in data.items():
        days_s = sorted(oc)
        daily_rets[coin] = {}
        for i in range(1, len(days_s)):
            d, prev = days_s[i], days_s[i - 1]
            c_now, c_prev = oc[d][1], oc[prev][1]
            if c_prev > 0:
                daily_rets[coin][d] = c_now / c_prev - 1.0

    btc_rets = daily_rets.get("BTC", {})
    signals = {}

    running_corrs = []
    running_btcvols = []

    for t, day in enumerate(all_days):
        sig = {}

        # ── correlation-regime ────────────────────────────────────────────────
        if t >= CORR_WIN:
            window_days = all_days[t - CORR_WIN + 1: t + 1]
            eligible = []
            for coin in data:
                rets_w = [daily_rets[coin].get(dd) for dd in window_days]
                if sum(1 for r in rets_w if r is not None) >= CORR_WIN * 0.8:
                    filled = [r if r is not None else 0.0 for r in rets_w]
                    eligible.append((coin, filled))
            if len(eligible) >= 4:
                sub = eligible[:15]
                pair_corrs = [_pearson(sub[i][1], sub[j][1])
                              for i in range(len(sub))
                              for j in range(i + 1, len(sub))
                              if not math.isnan(_pearson(sub[i][1], sub[j][1]))]
                avg_c = _mean(pair_corrs) if pair_corrs else None
            else:
                avg_c = None

            if avg_c is not None:
                running_corrs.append(avg_c)
                # Median so far (lookahead-safe)
                sorted_corrs = sorted(running_corrs)
                n_c = len(sorted_corrs)
                median_c = (sorted_corrs[n_c // 2 - 1] + sorted_corrs[n_c // 2]) / 2 if n_c % 2 == 0 else sorted_corrs[n_c // 2]
                sig["corr_high"] = avg_c > median_c
            else:
                sig["corr_high"] = None
        else:
            sig["corr_high"] = None

        # ── BTC vol-regime ────────────────────────────────────────────────────
        if t >= BTC_VOL_WIN:
            win_days = all_days[t - BTC_VOL_WIN + 1: t + 1]
            btc_w = [btc_rets.get(dd) for dd in win_days]
            btc_w = [r for r in btc_w if r is not None]
            if len(btc_w) >= BTC_VOL_WIN // 2:
                btc_vol = _pstdev(btc_w)
                running_btcvols.append(btc_vol)
                sorted_vols = sorted(running_btcvols)
                n_v = len(sorted_vols)
                median_v = (sorted_vols[n_v // 2 - 1] + sorted_vols[n_v // 2]) / 2 if n_v % 2 == 0 else sorted_vols[n_v // 2]
                sig["btc_low_vol"] = btc_vol < median_v
            else:
                sig["btc_low_vol"] = None
        else:
            sig["btc_low_vol"] = None

        signals[day] = sig

    return signals


# ─── 6. Align streams to a common daily grid ──────────────────────────────────
def align_to_daily(streams_dict, all_days):
    """
    Rebalance streams emit one return per HOLD-day period. To correlate them, we
    spread each period's return across the HOLD days it covers — each day in the
    hold window gets period_return / HOLD (a daily-equivalent mark-to-market).
    Pairs already returns a daily dict.

    Returns {name -> {day: daily_ret}} and a sorted list of common days.
    """
    daily = {}
    for name, stream in streams_dict.items():
        if isinstance(stream, dict):
            # already daily
            daily[name] = stream
        else:
            # (signal_day, period_ret) list — spread over HOLD days starting t+1
            series_dict = {}
            for i, (sig_day, period_ret) in enumerate(stream):
                # find signal_day index in all_days
                try:
                    t_idx = all_days.index(sig_day)
                except ValueError:
                    continue
                daily_eq = period_ret / HOLD
                for k in range(1, HOLD + 1):
                    if t_idx + k < len(all_days):
                        carry_day = all_days[t_idx + k]
                        # If multiple rebalances overlap (hold > rebal_freq) take mean
                        series_dict.setdefault(carry_day, []).append(daily_eq)
            daily[name] = {d: _mean(vs) for d, vs in series_dict.items()}

    common = sorted(set.intersection(*[set(d.keys()) for d in daily.values()]))
    return daily, common


# ─── 7. Correlation matrix ────────────────────────────────────────────────────
def corr_matrix(daily, common, names):
    """4x4 Pearson correlation matrix on aligned daily series."""
    series = {n: [daily[n][d] for d in common] for n in names}
    mat = {}
    for a in names:
        mat[a] = {}
        for b in names:
            mat[a][b] = _pearson(series[a], series[b])
    return mat, series


# ─── 8. Combination methods ───────────────────────────────────────────────────
def _combine(series_map, weights):
    """Return combined daily series given {name: [daily_ret]} and {name: weight}."""
    names = list(weights.keys())
    n = min(len(series_map[nm]) for nm in names)
    combined = []
    for i in range(n):
        combined.append(sum(weights[nm] * series_map[nm][i] for nm in names))
    return combined


def _portfolio_stats(series, label=""):
    if not series or len(series) < 4:
        return {"label": label, "n": 0, "mean": float("nan"),
                "sharpe": float("nan"), "maxdd": float("nan"),
                "h1_mean": float("nan"), "h2_mean": float("nan")}
    n = len(series)
    mid = n // 2
    h1 = series[:mid]; h2 = series[mid:]
    return {
        "label":   label,
        "n":       n,
        "mean":    _mean(series) * 100,
        "sharpe":  _sharpe(series),
        "maxdd":   _max_dd(series) * 100,
        "h1_mean": _mean(h1) * 100,
        "h2_mean": _mean(h2) * 100,
        "h1_sh":   _sharpe(h1),
        "h2_sh":   _sharpe(h2),
    }


def equal_weight(series_map, common):
    """Equal-weight combination (1/N each edge)."""
    names = list(series_map.keys())
    w = {n: 1.0 / len(names) for n in names}
    return _combine({n: [series_map[n][d] for d in common] for n in names}, w)


def inverse_vol_weight(series_map, common):
    """Inverse-volatility (risk-parity) weights."""
    series = {n: [series_map[n][d] for d in common] for n in series_map}
    vols = {n: _pstdev(series[n]) for n in series}
    inv = {n: 1.0 / vols[n] if vols[n] > 0 else 0.0 for n in series}
    total = sum(inv.values())
    w = {n: inv[n] / total if total > 0 else 1.0 / len(series) for n in series}
    return _combine(series, w), w


def regularized_sharpe_optimal(series_map, common):
    """
    Sharpe-optimal (max-Sharpe) weights fit on H1 only, then applied to H2.
    Regularized: shrink toward equal-weight by SHRINK_ALPHA.
    Returns: (h2_combined_series, weights_from_h1, h1_insample_stats, h2_oos_stats)

    Simple max-Sharpe via grid search over 4-edge weights.
    We sweep weights on a coarse grid (each 0.0 to 1.0 in 0.2 steps)
    and pick the max-Sharpe combination on H1, then apply to H2.
    """
    names = list(series_map.keys())
    N = len(names)
    n = len(common)
    mid = n // 2
    h1_days = common[:mid]
    h2_days = common[mid:]
    h1_series = {nm: [series_map[nm][d] for d in h1_days] for nm in names}
    h2_series = {nm: [series_map[nm][d] for d in h2_days] for nm in names}

    # Grid search for max-Sharpe on H1
    step = 0.2
    weight_grid = [round(i * step, 2) for i in range(int(1.0 / step) + 1)]
    best_sh = -999.0
    best_raw_w = None

    for combo in itertools.product(weight_grid, repeat=N):
        if abs(sum(combo)) < 1e-9:
            continue
        total_w = sum(combo)
        w = {names[i]: combo[i] / total_w for i in range(N)}
        combined = _combine(h1_series, w)
        sh = _sharpe(combined)
        if not math.isnan(sh) and sh > best_sh:
            best_sh = sh
            best_raw_w = {names[i]: combo[i] / total_w for i in range(N)}

    if best_raw_w is None:
        ew = 1.0 / N
        best_raw_w = {n: ew for n in names}

    # Regularize: shrink toward equal-weight
    ew = 1.0 / N
    reg_w = {nm: SHRINK_ALPHA * ew + (1 - SHRINK_ALPHA) * best_raw_w[nm]
             for nm in names}
    # Re-normalize
    total_r = sum(reg_w.values())
    reg_w = {nm: reg_w[nm] / total_r for nm in names}

    # In-sample (H1) performance with regularized weights
    h1_combined = _combine(h1_series, reg_w)
    h1_stats = _portfolio_stats(h1_combined, "H1 in-sample (reg-optimal on H1)")

    # OOS (H2) application of H1-derived weights — THE HONEST NUMBER
    h2_combined = _combine(h2_series, reg_w)
    h2_stats = _portfolio_stats(h2_combined, "H2 OOS (reg-optimal from H1)")

    # Also full-sample with reg weights
    full_combined = _combine({nm: [series_map[nm][d] for d in common] for nm in names}, reg_w)

    return full_combined, reg_w, best_raw_w, h1_stats, h2_stats


# ─── 9. Apply gates to combined stream ────────────────────────────────────────
def apply_gates(stream_days, series, signals, gate_combo):
    """
    Filter a combined stream by regime gates.
    gate_combo: one of "corr_only", "vol_only", "both"
    Returns filtered series + the gate's inclusion rate.
    """
    filtered = []
    total = 0
    kept = 0
    for i, d in enumerate(stream_days):
        if i >= len(series):
            break
        sig = signals.get(d, {})
        total += 1
        if gate_combo == "corr_only":
            # Corr-regime: mom UP in low-corr, vol-disp UP in high-corr → mixed;
            # for the COMBINED book, run only when at least one regime is favorable
            # (don't require both simultaneously — that would over-filter)
            corr_h = sig.get("corr_high")
            active = corr_h is not None  # always run — corr just sizes
            # For gate test: include all (corr as sizing handled separately)
            # Actually test: does restricting to low-corr periods help the combined book?
            if corr_h is False or corr_h is None:  # low-corr or unknown
                filtered.append(series[i])
                kept += 1
        elif gate_combo == "vol_only":
            low_vol = sig.get("btc_low_vol")
            if low_vol is True:
                filtered.append(series[i])
                kept += 1
        elif gate_combo == "both":
            corr_h = sig.get("corr_high")
            low_vol = sig.get("btc_low_vol")
            if (corr_h is False or corr_h is None) and low_vol is True:
                filtered.append(series[i])
                kept += 1
        else:
            filtered.append(series[i])
            kept += 1

    pct = 100.0 * kept / total if total > 0 else 0.0
    return filtered, pct


# ─── 10. Reporting helpers ────────────────────────────────────────────────────
def _stat_row(label, stats, extra=""):
    n   = stats.get("n", 0)
    m   = stats.get("mean", float("nan"))
    sh  = stats.get("sharpe", float("nan"))
    mdd = stats.get("maxdd", float("nan"))
    h1m = stats.get("h1_mean", float("nan"))
    h2m = stats.get("h2_mean", float("nan"))
    h1s = stats.get("h1_sh", float("nan"))
    h2s = stats.get("h2_sh", float("nan"))
    oos_ok = (not math.isnan(h1m)) and (not math.isnan(h2m)) and h1m > 0 and h2m > 0
    oos_tag = "ROBUST" if oos_ok else ("fragile" if (not math.isnan(h1m) and not math.isnan(h2m) and (h1m > 0) != (h2m > 0)) else "neg")
    print(f"  {label:<38} n={n:>4}  mean={m:>+6.3f}%  Sh={sh:>+5.2f}  maxDD={mdd:>5.2f}%  "
          f"H1={h1m:>+5.2f}%/Sh={h1s:>+4.2f}  H2={h2m:>+5.2f}%/Sh={h2s:>+4.2f}  {oos_tag}{extra}")


def print_corr_matrix(mat, names):
    header = f"  {'':25}" + "".join(f"{n:>12}" for n in names)
    print(header)
    for a in names:
        row = f"  {a:<25}"
        for b in names:
            v = mat[a][b]
            row += f"  {v:>+9.3f} " if not math.isnan(v) else f"  {'n/a':>9} "
        print(row)


# ─── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    sep = "=" * 90
    print(sep)
    print("  PORTFOLIO CONSTRUCTION — 4-EDGE COMBINATION (Wave 5 Y1)")
    print(f"  Edges: momentum (LB={MOM_LB}/hold={MOM_HOLD}) | vol-disp (WT, hold={HOLD}) | "
          f"Sortino (WT, hold={HOLD}) | pairs (z>{Z_ENTRY})")
    print(f"  Cost: {COST_BPS:.0f}bps/leg | BT_CACHE_ONLY | OOS = chronological H1/H2 split")
    print(f"  Overfit guard: Sharpe-optimal fit on H1 only, applied to H2; shrink={SHRINK_ALPHA}")
    print(sep)

    # ── Load data ──────────────────────────────────────────────────────────────
    print("\n# Loading candles (cache-only)...", flush=True)
    data = load()
    print(f"  {len(data)} coins loaded")

    if "BTC" not in data:
        print("ERROR: BTC not in cache. Aborting.")
        sys.exit(1)

    all_days = sorted({d for oc in data.values() for d in oc})
    print(f"  {len(all_days)} trading days ({all_days[0]} – {all_days[-1]})")

    # ── Build edge streams ─────────────────────────────────────────────────────
    print("\n# Building edge return streams...", flush=True)

    print("  (a) momentum...", flush=True)
    mom_stream = build_momentum_stream(data)
    print(f"      {len(mom_stream)} rebalance periods  "
          f"mean={_mean([r for _, r in mom_stream])*100:+.2f}%")

    print("  (b) vol-dispersion (within-beta-tercile)...", flush=True)
    vdisp_stream = build_vdisp_stream(data)
    print(f"      {len(vdisp_stream)} rebalance periods  "
          f"mean={_mean([r for _, r in vdisp_stream])*100:+.2f}%")

    print("  (c) sortino (within-beta-tercile)...", flush=True)
    sort_stream = build_sortino_stream(data)
    print(f"      {len(sort_stream)} rebalance periods  "
          f"mean={_mean([r for _, r in sort_stream])*100:+.2f}%")

    print("  (d) pairs (daily walk-forward)...", flush=True)
    pairs_daily_dict = build_pairs_daily(data)
    n_pair_days = len(pairs_daily_dict)
    pair_rets = list(pairs_daily_dict.values())
    print(f"      {n_pair_days} days with active positions  "
          f"mean={_mean(pair_rets)*100:+.3f}%/day")

    # ── Align all to daily grid ────────────────────────────────────────────────
    print("\n# Aligning to common daily grid...", flush=True)
    streams_in = {
        "momentum":  mom_stream,
        "vol-disp":  vdisp_stream,
        "sortino":   sort_stream,
        "pairs":     pairs_daily_dict,
    }
    daily_map, common_days = align_to_daily(streams_in, all_days)
    print(f"  {len(common_days)} common days ({common_days[0] if common_days else 'n/a'} – "
          f"{common_days[-1] if common_days else 'n/a'})")

    if len(common_days) < 30:
        print("  WARNING: too few common days to compute portfolio stats reliably.")

    names = ["momentum", "vol-disp", "sortino", "pairs"]

    # ── 4x4 Correlation matrix ─────────────────────────────────────────────────
    print(f"\n{'─' * 90}")
    print("# 4x4 EDGE CORRELATION MATRIX (daily-equivalent returns, aligned)")
    print(f"{'─' * 90}")
    # series_list: {name: [val, ...]} indexed by position in common_days (for combine ops)
    # daily_map:   {name: {day: val}}  (for gate lookups)
    mat, series_list = corr_matrix(daily_map, common_days, names)
    print_corr_matrix(mat, names)

    # Interpret key pairs
    print()
    print(f"  Key pairs:")
    key_pairs = [
        ("momentum", "vol-disp",  "mom vs vol-disp (expect ~+0.40 per ALPHA-PLAN)"),
        ("momentum", "sortino",   "mom vs sortino  (expect ~+0.07 per ALPHA-PLAN)"),
        ("vol-disp", "sortino",   "vol-disp vs sortino  (expect ~+0.37 per ALPHA-PLAN)"),
        ("momentum", "pairs",     "mom vs pairs    (expect ~0.00, validated orthogonal)"),
        ("vol-disp", "pairs",     "vol-disp vs pairs"),
        ("sortino",  "pairs",     "sortino vs pairs"),
    ]
    for a, b, desc in key_pairs:
        v = mat[a][b]
        print(f"    {desc:<55} r = {v:>+.3f}")

    # series_list: {name: [val, ...]} in common_days order (positional, for combos)
    # daily_map:   {name: {day: val}} (day-keyed, for gate lookups by day)
    # Build the positional series dict from daily_map + common_days
    series_pos = {nm: [daily_map[nm][d] for d in common_days] for nm in names}

    # ── Individual edge stats ──────────────────────────────────────────────────
    print(f"\n{'─' * 90}")
    print("# INDIVIDUAL EDGE STATS (daily-equivalent, common window)")
    print(f"{'─' * 90}")
    for nm in names:
        ser = series_pos[nm]
        st  = _portfolio_stats(ser, nm)
        _stat_row(nm, st)

    # ── Combination methods ────────────────────────────────────────────────────
    print(f"\n{'─' * 90}")
    print("# COMBINATION METHODS — OOS COMPARISON")
    print(f"{'─' * 90}")
    print("  (All results on the COMMON aligned daily window unless noted)")
    print()

    # (a) Equal-weight
    ew_series = equal_weight(daily_map, common_days)
    ew_st = _portfolio_stats(ew_series, "equal-weight (1/4 each)")
    print("  (a) Equal-weight:")
    _stat_row("  equal-weight 1/4 each", ew_st)

    # (b) Inverse-vol / risk-parity
    iv_series, iv_weights = inverse_vol_weight(daily_map, common_days)
    iv_st = _portfolio_stats(iv_series, "inverse-vol (risk-parity)")
    print()
    print("  (b) Inverse-vol (risk-parity):")
    print(f"      Weights: " + ", ".join(f"{nm}={iv_weights[nm]:.3f}" for nm in names))
    _stat_row("  inverse-vol / risk-parity", iv_st)

    # (c) Regularized Sharpe-optimal (HONEST OOS)
    print()
    print("  (c) Regularized Sharpe-optimal (fit H1, apply H2):")
    full_reg, reg_w, raw_w, h1_st, h2_st = regularized_sharpe_optimal(daily_map, common_days)
    full_reg_st = _portfolio_stats(full_reg, "reg-sharpe-opt (full)")
    print(f"      Raw optimal weights (H1, PRE-shrink):  "
          + ", ".join(f"{nm}={raw_w[nm]:.3f}" for nm in names))
    print(f"      Regularized weights (shrink={SHRINK_ALPHA}):        "
          + ", ".join(f"{nm}={reg_w[nm]:.3f}" for nm in names))
    print(f"      H1 in-sample  : n={h1_st['n']}  mean={h1_st['mean']:>+6.3f}%  Sh={h1_st['sharpe']:>+5.2f}  maxDD={h1_st['maxdd']:>5.2f}%")
    print(f"      H2 HONEST OOS : n={h2_st['n']}  mean={h2_st['mean']:>+6.3f}%  Sh={h2_st['sharpe']:>+5.2f}  maxDD={h2_st['maxdd']:>5.2f}%")
    _stat_row("  reg-sharpe-opt (full)", full_reg_st, "  (see H1/H2 above for honest OOS)")

    # (d) Best single edge alone
    print()
    print("  (d) Best single edge (full window):")
    single_stats = {}
    for nm in names:
        ser = series_pos[nm]
        st  = _portfolio_stats(ser, nm)
        single_stats[nm] = st
        _stat_row(f"  {nm}", st)

    best_single = max(names, key=lambda nm: single_stats[nm]["sharpe"]
                      if not math.isnan(single_stats[nm]["sharpe"]) else -999.0)
    print(f"\n      Best single edge: {best_single} (Sh={single_stats[best_single]['sharpe']:>+.2f})")

    # ── Summary comparison table ───────────────────────────────────────────────
    print(f"\n{'─' * 90}")
    print("# COMBINATION COMPARISON TABLE")
    print(f"{'─' * 90}")
    print(f"  {'Method':<38} {'n':>5}  {'mean%':>7}  {'Sharpe':>7}  {'maxDD%':>7}  "
          f"{'H1%':>7}  {'H2%':>7}  OOS")
    print(f"  {'':-<90}")

    all_combos = [
        ("equal-weight (1/4)", ew_st),
        ("inverse-vol / risk-parity", iv_st),
        ("reg-Sharpe-opt (full)", full_reg_st),
    ]
    for nm in names:
        all_combos.append((f"single: {nm}", single_stats[nm]))

    for label, st in all_combos:
        n   = st["n"]
        m   = st.get("mean", float("nan"))
        sh  = st.get("sharpe", float("nan"))
        mdd = st.get("maxdd", float("nan"))
        h1m = st.get("h1_mean", float("nan"))
        h2m = st.get("h2_mean", float("nan"))
        oos_ok = (not math.isnan(h1m)) and (not math.isnan(h2m)) and h1m > 0 and h2m > 0
        oos_tag = "ROBUST" if oos_ok else "fragile"
        print(f"  {label:<38} {n:>5}  {m:>+6.3f}%  {sh:>+6.2f}  {mdd:>6.2f}%  "
              f"{h1m:>+6.3f}%  {h2m:>+6.3f}%  {oos_tag}")

    # H2-only honest comparison (most important)
    print(f"\n  *** HONEST OOS (H2 only) for reg-Sharpe-opt: "
          f"mean={h2_st['mean']:>+.3f}%  Sh={h2_st['sharpe']:>+.2f}  "
          f"maxDD={h2_st['maxdd']:>+.2f}%")
    print(f"  *** Vs best single edge ({best_single}) H2:  "
          f"mean={single_stats[best_single]['h2_mean']:>+.3f}%  "
          f"Sh={single_stats[best_single]['h2_sh']:>+.2f}")

    # ── Gate analysis ──────────────────────────────────────────────────────────
    print(f"\n{'─' * 90}")
    print("# REGIME GATE ANALYSIS — gated vs ungated combination")
    print(f"{'─' * 90}")

    print("  Building regime signals...", flush=True)
    signals = build_regime_signals(data, all_days)

    # Use equal-weight as the reference combination for gating
    gate_ref_series = ew_series
    gate_ref_days   = common_days

    def gate_test(label, gate_combo):
        filt, pct = apply_gates(gate_ref_days, gate_ref_series, signals, gate_combo)
        if len(filt) < 10:
            print(f"  {label:<35}  too few obs ({len(filt)})")
            return
        st = _portfolio_stats(filt, label)
        _stat_row(f"  {label}", st, f"  (in {pct:.0f}% of periods)")

    print(f"\n  Ungated combined (reference = equal-weight):")
    _stat_row("  ungated equal-weight", ew_st)
    print()
    print(f"  Gated versions (applied to equal-weight combined book):")
    gate_test("low-corr-only gate", "corr_only")
    gate_test("low-BTC-vol gate", "vol_only")
    gate_test("both gates (AND)", "both")

    # Also gate momentum-only for comparison (validated gate in ALPHA-PLAN)
    print(f"\n  Momentum-alone gating (validated in ALPHA-PLAN, for reference):")
    for gate_lbl, gate_combo in [("mom low-vol gate", "vol_only"),
                                   ("mom low-corr gate", "corr_only")]:
        filt_mom = [daily_map["momentum"][d]
                    for d in common_days
                    if (gate_combo == "vol_only" and signals.get(d, {}).get("btc_low_vol") is True)
                    or (gate_combo == "corr_only" and signals.get(d, {}).get("corr_high") is False)]
        if len(filt_mom) < 10:
            continue
        st_m = _portfolio_stats(filt_mom, gate_lbl)
        pct_m = 100.0 * len(filt_mom) / len(common_days)
        _stat_row(f"  {gate_lbl}", st_m, f"  (in {pct_m:.0f}% of periods)")

    # ── Final assessment ───────────────────────────────────────────────────────
    print(f"\n{'=' * 90}")
    print("# PORTFOLIO ASSESSMENT & RECOMMENDED LIVE ALLOCATION")
    print(f"{'=' * 90}")

    # Compute key summary numbers
    ew_sh   = ew_st["sharpe"]
    ew_h2sh = ew_st.get("h2_sh", float("nan"))
    _all_sh = [ew_sh, iv_st["sharpe"], full_reg_st["sharpe"]] + \
              [single_stats[nm]["sharpe"] for nm in names
               if not math.isnan(single_stats[nm]["sharpe"])]
    best_sh_all = max(_all_sh) if _all_sh else float("nan")
    mom_sh  = single_stats["momentum"]["sharpe"]
    vd_sh   = single_stats["vol-disp"]["sharpe"]
    so_sh   = single_stats["sortino"]["sharpe"]
    pa_sh   = single_stats["pairs"]["sharpe"]

    corr_mom_vd = mat["momentum"]["vol-disp"]
    corr_vd_so  = mat["vol-disp"]["sortino"]
    corr_mom_so = mat["momentum"]["sortino"]
    corr_mom_pa = mat["momentum"]["pairs"]

    print(f"""
  CORRELATION SUMMARY (key findings):
    mom / vol-disp  : {corr_mom_vd:>+.3f}  {'HIGH overlap — same crash regime' if abs(corr_mom_vd) > 0.3 else 'LOW — genuinely diversifying'}
    vol-disp / Sortino : {corr_vd_so:>+.3f}  {'HIGH overlap — suspected same factor family' if abs(corr_vd_so) > 0.3 else 'MODERATE — partial overlap'}
    mom / Sortino   : {corr_mom_so:>+.3f}  {'LOW — independent' if abs(corr_mom_so) < 0.25 else 'MODERATE overlap'}
    mom / pairs     : {corr_mom_pa:>+.3f}  {'ORTHOGONAL — confirmed independent' if abs(corr_mom_pa) < 0.15 else 'some correlation'}

  INDIVIDUAL EDGE SHARPES:
    momentum  : {mom_sh:>+.2f}
    vol-disp  : {vd_sh:>+.2f}
    sortino   : {so_sh:>+.2f}
    pairs     : {pa_sh:>+.2f}

  COMBINATION SHARPES:
    equal-weight      : {ew_sh:>+.2f}  (H2 OOS: {ew_st.get('h2_sh', float('nan')):>+.2f})
    inverse-vol       : {iv_st['sharpe']:>+.2f}  (H2 OOS: {iv_st.get('h2_sh', float('nan')):>+.2f})
    reg-Sharpe-opt    : {full_reg_st['sharpe']:>+.2f}  (H2 HONEST OOS: {h2_st['sharpe']:>+.2f})

  HONEST ASSESSMENT:
    Does diversifying materially beat momentum-alone?
      equal-weight vs momentum: Sharpe {ew_sh:>+.2f} vs {mom_sh:>+.2f}  {'IMPROVEMENT' if ew_sh > mom_sh + 0.1 else 'MARGINAL' if ew_sh > mom_sh else 'NO — momentum dominates'}
      H2 OOS eq-wt vs mom H2:   {ew_st.get('h2_sh', float('nan')):>+.2f} vs {single_stats['momentum'].get('h2_sh', float('nan')):>+.2f}

  RECOMMENDED LIVE ALLOCATION:
    Primary book     : MOMENTUM (xs LB=7/hold=10, K=8)  → up to 60-70% of gross
    Secondary book   : PAIRS stat-arb (z>2, 30d LB)     → 20-30% of gross
                       [lowest correlation to momentum: {corr_mom_pa:>+.3f}, adds diversification]
    Tertiary (SMALL) : VOL-DISP or SORTINO (NOT both — r={corr_vd_so:>+.3f} overlap)
                       → 10-20% of gross, SHADOW-validated first, bear-regime caveat
    Gates:
      corr-regime gate : SIZE MOMENTUM UP in low-corr; SIZE VOL-DISP UP in high-corr
                         (from ALPHA-PLAN: Sh lifts 4.95→8.36 for mom in low-corr)
      vol-regime gate  : SIZE MOMENTUM UP in low-vol BTC (+3.45% vs +0.54% high-vol)
                         (secondary to corr-regime; DON'T apply both simultaneously — over-filters)

  CAVEATS (DO NOT IGNORE):
    1. SINGLE REGIME WINDOW: all backtests cover one ~6mo bull/choppy period (Oct 2025–Jun 2026).
       No crash/bear data. Vol-disp and Sortino carry unquantified bear-bleed risk.
    2. OVERFIT RISK: Sharpe-optimal weights overfit notoriously even with shrinkage.
       The reg-Sharpe-opt H2 honest OOS is the NUMBER TO TRUST, not full-sample.
    3. VOL-DISP + SORTINO OVERLAP (r≈+0.37): these are not two independent edges.
       Deploy ONLY ONE of them. Sortino is more regime-stable (per ALPHA-PLAN V2 result).
       Unless the portfolio test here shows vol-disp clearly dominates, prefer Sortino.
    4. PAIRS is a daily event-driven stream, not a daily book — pairing count and liquidity
       are thin at the $60 live account scale. At <$200, pairs adds little dollar alpha.
    5. CAPITAL CONSTRAINT: at $60 main (stranded capital issue), ONE momentum book saturates
       main perp margin. Fund main (xyz→main transfer, operator-only) before running the stack.
    6. SIZING-GAP: rebalancer _analysis doesn't pass external_alpha_notional to executor
       (noted in ALPHA-PLAN). Fix this BEFORE any live-flip to avoid 6x oversizing.
""")

    print(f"{'=' * 90}")
    print("# DONE")
    print(f"{'=' * 90}")


if __name__ == "__main__":
    main()
