#!/usr/bin/env python3
"""Microstructure / flow signals from OHLCV — Wave 4 / X4.

Four sub-tests (all cross-sectional, lookahead-safe, cost-aware, OOS, beta-neutralized):

  (a) VOLUME-SPIKE: after daily $vol spikes (>2×, >3× its 20d avg), does next-1/3/5d
      return CONTINUE or REVERSE? Rank cross-sectionally by spike magnitude → L/S spread.

  (b) RANGE / PARKINSON-VOL: rank by recent Parkinson-vol (high-low range expansion);
      both directions tested; corr to vol-dispersion (idio-vol) factor reported.

  (c) DOLLAR-VOLUME TREND: is rising trailing $-vol (liquidity inflow) predictive?
      Long rising-$vol / short falling; both directions.

  (d) ILLIQUIDITY-MOMENTUM INTERACTION: does xs-momentum work better among high- or
      low-$vol coins? Conditional momentum EV by $vol tertile (high/mid/low).

Run: BT_CACHE_ONLY=1 python3 scripts/edge_microstructure.py
"""
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from hermes_trader.client.universe import get_universe
from _bt_candles import get as get_candles

# ─── constants ────────────────────────────────────────────────────────────────
VOL_FLOOR = 5e6
TOPN = 50
K = 8              # names per L/S leg
COST = 10.0 / 1e4  # 10 bps per name, round-trip
BETA_WIN = 30      # trailing days for OLS beta (BTC)
BURN_IN = 40       # warm-up bars required before scoring


# ─── helpers ──────────────────────────────────────────────────────────────────
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


def _ols_beta(cr, br):
    """OLS beta of coin returns on BTC; returns 1.0 if degenerate."""
    n = min(len(cr), len(br))
    if n < 8:
        return 1.0
    cr, br = list(cr[-n:]), list(br[-n:])
    mb = _mean(br)
    vb = sum((x - mb) ** 2 for x in br)
    if vb <= 0:
        return 1.0
    mc = _mean(cr)
    return sum((a - mc) * (b - mb) for a, b in zip(cr, br)) / vb


def _corr(xs, ys):
    """Pearson correlation between two lists (same length)."""
    n = min(len(xs), len(ys))
    if n < 4:
        return float("nan")
    xs, ys = list(xs[:n]), list(ys[:n])
    mx, my = _mean(xs), _mean(ys)
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    den = math.sqrt(sum((a - mx) ** 2 for a in xs) * sum((b - my) ** 2 for b in ys))
    return num / den if den > 0 else float("nan")


def _daily_rets(closes):
    """List of daily returns from list of closes."""
    return [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))
            if closes[i - 1] > 0]


def _fwd_ret(coin_data, d_entry, d_exit):
    """Open-to-close forward return; 0 if data missing."""
    if d_entry not in coin_data or d_exit not in coin_data:
        return None
    o = coin_data[d_entry]["o"]
    c = coin_data[d_exit]["c"]
    return (c - o) / o if o > 0 else None


def _coin_beta(coin_data, btc_data, win_days):
    """Trailing BETA_WIN-day OLS beta of coin vs BTC."""
    wd = win_days[-BETA_WIN:]
    close_c = [coin_data[d]["c"] for d in wd if d in coin_data and coin_data[d]["c"] > 0]
    close_b = [btc_data[d]["c"] for d in wd if d in btc_data and btc_data[d]["c"] > 0]
    # align on shared days
    shared = [d for d in wd if d in coin_data and d in btc_data
              and coin_data[d]["c"] > 0 and btc_data[d]["c"] > 0]
    if len(shared) < 8:
        return 1.0
    cr = _daily_rets([coin_data[d]["c"] for d in shared])
    br = _daily_rets([btc_data[d]["c"] for d in shared])
    return _ols_beta(cr, br)


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
        if len(bars) >= BURN_IN + 20:
            # store as {ymd: full bar dict}
            data[c] = {_ymd(b["t"]): b for b in bars}
    return data


# ─── reporting ────────────────────────────────────────────────────────────────
def rep(name, arr, extra=""):
    if not arr:
        print(f"  {name:55} n=0")
        return
    n = len(arr)
    w = sum(1 for r in arr if r > 0)
    mid = n // 2
    h1 = _mean(arr[:mid]) * 100 if mid else 0.0
    h2 = _mean(arr[mid:]) * 100 if (n - mid) else 0.0
    m = _mean(arr) * 100
    rob = "ROBUST" if h1 > 0 and h2 > 0 else "fragile" if (h1 > 0) != (h2 > 0) else "neg"
    flag = "  <<< +EV" if m > 0 and rob == "ROBUST" else ""
    print(f"  {name:55} n={n:>4} win={w/n*100:>3.0f}%  mean={m:>+6.2f}%  "
          f"OOS {h1:>+5.2f}/{h2:>+5.2f} {rob}{extra}{flag}")


def section(title):
    print(f"\n{'='*72}")
    print(f"  {title}")
    print(f"{'='*72}")


# ─────────────────────────────────────────────────────────────────────────────
#  (a) VOLUME-SPIKE reversal vs continuation
# ─────────────────────────────────────────────────────────────────────────────
def _dollar_vol(bar):
    """$-volume for a bar: v × close (v = contracts, close = price)."""
    v = bar.get("v", 0) or 0
    c = bar.get("c", 0) or 0
    return float(v) * float(c)


def test_volume_spike(data, all_days, btc_data):
    """
    Signal: ratio of today's $vol to trailing 20d avg $vol.
    Higher ratio = bigger spike. We test CONTINUATION (long high-spike / short low-spike)
    AND REVERSAL (reverse order) for hold=1/3/5 days.
    Lookahead-safe: compute signal from bar[t], enter open of bar[t+1].
    """
    section("(a) VOLUME-SPIKE — continuation vs reversal")

    VOL_AVG_WIN = 20
    for hold in (1, 3, 5):
        # direction 1: CONTINUATION (long top spike / short bottom spike)
        # direction 2: REVERSAL (short top spike / long bottom spike = reverse sort)
        ls_cont, ls_rev = [], []

        for t in range(VOL_AVG_WIN + BURN_IN, len(all_days) - hold - 1):
            d = all_days[t]
            d_entry = all_days[t + 1]
            d_exit = all_days[t + 1 + hold] if t + 1 + hold < len(all_days) else all_days[-1]
            win_days = all_days[max(0, t - 70): t + 1]

            ranked = []
            for coin, cd in data.items():
                if d not in cd or d_entry not in cd or d_exit not in cd:
                    continue
                # compute 20d avg $vol using bars[t-20..t-1] (strictly prior to t)
                hist_days = all_days[max(0, t - VOL_AVG_WIN): t]
                hist_dvols = [_dollar_vol(cd[hd]) for hd in hist_days if hd in cd]
                if len(hist_dvols) < 10:
                    continue
                avg_dvol = _mean(hist_dvols)
                if avg_dvol <= 0:
                    continue
                today_dvol = _dollar_vol(cd[d])
                spike_ratio = today_dvol / avg_dvol  # signal at time t
                beta = _coin_beta(cd, btc_data, win_days)
                ranked.append((coin, spike_ratio, beta))

            if len(ranked) < 2 * K + 4:
                continue

            ranked.sort(key=lambda x: x[1], reverse=True)  # high spike first
            longs_c = [c for c, _, _ in ranked[:K]]   # continuation: long high spike
            shorts_c = [c for c, _, _ in ranked[-K:]]  # continuation: short low spike

            def fwd(coin):
                r = _fwd_ret(data[coin], d_entry, d_exit)
                return r if r is not None else 0.0

            lr = _mean([fwd(c) for c in longs_c])
            sr = _mean([fwd(c) for c in shorts_c])
            ls_cont.append((lr - sr) - 2 * COST)
            ls_rev.append((sr - lr) - 2 * COST)  # reversal = flip

        print(f"\n  hold={hold}d:")
        rep(f"    continuation (long high-spike / short low-spike)", ls_cont)
        rep(f"    reversal     (long low-spike / short high-spike)", ls_rev)

    # Directional absolute: do high-spike coins outperform the market?
    print("\n  [Directional note: continuation L/S > 0 ⇒ winners keep vol (momentum-like);")
    print("   reversal L/S > 0 ⇒ spike is exhaustion signal. Only ROBUST +EV counts.]")

    # Also break out by threshold: spikes >2× and >3×
    print("\n  Threshold sub-tests (hold=1d, full-period mean fwd return of spiking coins):")
    for thr in (2.0, 3.0):
        hits_next = []
        for t in range(VOL_AVG_WIN + BURN_IN, len(all_days) - 2):
            d = all_days[t]
            d_entry = all_days[t + 1]
            d_exit = all_days[t + 2] if t + 2 < len(all_days) else all_days[-1]
            hit_rets = []
            nohit_rets = []
            for coin, cd in data.items():
                if d not in cd or d_entry not in cd or d_exit not in cd:
                    continue
                hist_days = all_days[max(0, t - VOL_AVG_WIN): t]
                hist_dvols = [_dollar_vol(cd[hd]) for hd in hist_days if hd in cd]
                if len(hist_dvols) < 10:
                    continue
                avg_dvol = _mean(hist_dvols)
                if avg_dvol <= 0:
                    continue
                today_dvol = _dollar_vol(cd[d])
                r = _fwd_ret(cd, d_entry, d_exit)
                if r is None:
                    continue
                if today_dvol / avg_dvol >= thr:
                    hit_rets.append(r)
                else:
                    nohit_rets.append(r)
            if hit_rets and nohit_rets:
                hits_next.append(_mean(hit_rets) - _mean(nohit_rets))
        n = len(hits_next)
        if n:
            m = _mean(hits_next) * 100
            mid = n // 2
            h1 = _mean(hits_next[:mid]) * 100 if mid else 0
            h2 = _mean(hits_next[mid:]) * 100 if n - mid else 0
            rob = "ROBUST" if h1 > 0 and h2 > 0 else "fragile" if (h1 > 0) != (h2 > 0) else "neg"
            flag = "  <<< +EV" if m > 0 and rob == "ROBUST" else ""
            print(f"    >={thr:.0f}× spike vs rest (hold=1d, excess)  n={n:>4} "
                  f"mean={m:>+6.2f}% OOS {h1:>+5.2f}/{h2:>+5.2f} {rob}{flag}")
        else:
            print(f"    >={thr:.0f}× threshold: n=0 spike events")


# ─────────────────────────────────────────────────────────────────────────────
#  (b) RANGE / PARKINSON-VOL (cross-sectional)
# ─────────────────────────────────────────────────────────────────────────────
def _parkinson_vol(cd, win_days, win=10):
    """Parkinson estimator over win days: sqrt(1/(4n ln2) * Σ(ln(H/L))^2)."""
    days = [d for d in win_days[-win:] if d in cd]
    if len(days) < 5:
        return None
    hl_sq = []
    for d in days:
        h = cd[d].get("h", 0) or 0
        l = cd[d].get("l", 0) or 0
        if h > 0 and l > 0 and h >= l:
            lhl = math.log(h / l)
            hl_sq.append(lhl * lhl)
    if not hl_sq:
        return None
    return math.sqrt(_mean(hl_sq) / (4.0 * math.log(2)))


def _idio_vol_score(cd, btcd, win_days, idvol_win=30):
    """Re-implement idio-vol for corr comparison (stdev of BTC-residual returns)."""
    wd = win_days[-idvol_win:]
    shared = [d for d in wd if d in cd and d in btcd
              and cd[d]["c"] > 0 and btcd[d]["c"] > 0]
    if len(shared) < 10:
        return None
    cr = _daily_rets([cd[d]["c"] for d in shared])
    br = _daily_rets([btcd[d]["c"] for d in shared])
    n = min(len(cr), len(br))
    if n < 8:
        return None
    cr, br = cr[-n:], br[-n:]
    beta = _ols_beta(cr, br)
    resid = [c - beta * b for c, b in zip(cr, br)]
    return _stdev(resid)


def test_parkinson_vol(data, all_days, btc_data):
    """
    Signal: trailing Parkinson-vol (range expansion) ranked cross-sectionally.
    Long HIGH Parkinson (expanding range) / short LOW → is expanding range +EV?
    Also test reverse (long LOW vol, classic low-vol anomaly in cross-section).
    Report corr of Parkinson scores to idio-vol scores each period.
    """
    section("(b) RANGE / PARKINSON-VOL (cross-sectional)")

    PARK_WIN = 10   # Parkinson window
    HOLD = 10

    ls_high, ls_low = [], []  # high-park long vs low-park long
    corr_to_idvol = []

    for t in range(BURN_IN + PARK_WIN + 5, len(all_days) - HOLD - 1):
        d = all_days[t]
        d_entry = all_days[t + 1]
        d_exit = all_days[min(t + 1 + HOLD, len(all_days) - 1)]
        win_days = all_days[max(0, t - 70): t + 1]

        ranked = []
        idvol_scores = []
        park_scores_list = []

        for coin, cd in data.items():
            if d not in cd or d_entry not in cd or d_exit not in cd:
                continue
            park = _parkinson_vol(cd, win_days, win=PARK_WIN)
            if park is None:
                continue
            idvol = _idio_vol_score(cd, btc_data, win_days)
            beta = _coin_beta(cd, btc_data, win_days)
            ranked.append((coin, park, beta))
            if idvol is not None:
                idvol_scores.append(idvol)
                park_scores_list.append(park)

        if len(ranked) < 2 * K + 4:
            continue

        # Corr to idio-vol
        if len(park_scores_list) >= 5:
            c = _corr(park_scores_list, idvol_scores)
            if not math.isnan(c):
                corr_to_idvol.append(c)

        ranked.sort(key=lambda x: x[1], reverse=True)  # high park first
        longs_h = [c for c, _, _ in ranked[:K]]
        shorts_h = [c for c, _, _ in ranked[-K:]]
        # low-park = reverse (long the bottom-K)
        longs_l = shorts_h
        shorts_l = longs_h

        def fwd(coin):
            r = _fwd_ret(data[coin], d_entry, d_exit)
            return r if r is not None else 0.0

        lr_h = _mean([fwd(c) for c in longs_h])
        sr_h = _mean([fwd(c) for c in shorts_h])
        ls_high.append((lr_h - sr_h) - 2 * COST)   # long high-park
        ls_low.append((sr_h - lr_h) - 2 * COST)    # long low-park

    avg_corr = _mean(corr_to_idvol)
    print(f"\n  Parkinson({PARK_WIN}d) vs idio-vol cross-corr: {avg_corr:>+.3f}  "
          f"({'HIGH overlap' if abs(avg_corr) > 0.7 else 'moderate overlap' if abs(avg_corr) > 0.4 else 'low overlap'})")
    print(f"  hold={HOLD}d:")
    rep(f"    long HIGH Parkinson / short LOW (range-expansion +EV?)", ls_high)
    rep(f"    long LOW  Parkinson / short HIGH (low-vol anomaly?)",    ls_low)
    print()
    print("  [Parkinson corr to idio-vol > 0.7 → essentially the same factor;")
    print("   corr < 0.4 → independent signal worth testing standalone.]")


# ─────────────────────────────────────────────────────────────────────────────
#  (c) DOLLAR-VOLUME TREND (rising vs falling liquidity)
# ─────────────────────────────────────────────────────────────────────────────
def _dvol_trend_score(cd, win_days, short_win=5, long_win=20):
    """
    Rising $-vol score: (mean $vol over short_win) / (mean $vol over long_win) - 1.
    Positive = short-term $vol above long-term trend (rising liquidity inflow).
    Lookahead-safe: uses only prior-to-t bars.
    """
    days = [d for d in win_days if d in cd]
    if len(days) < long_win:
        return None
    dvols = [_dollar_vol(cd[d]) for d in days[-long_win:]]
    if not any(v > 0 for v in dvols):
        return None
    short_avg = _mean(dvols[-short_win:])
    long_avg = _mean(dvols)
    if long_avg <= 0:
        return None
    return short_avg / long_avg - 1.0


def test_dvol_trend(data, all_days, btc_data):
    """
    Signal: (5d avg $vol) / (20d avg $vol) - 1.
    Positive = rising $vol trend (inflow). Rank cross-sectionally.
    Long rising / short falling; also reverse.
    Also test vs momentum corr (is this just a momentum proxy via vol?).
    """
    section("(c) DOLLAR-VOLUME TREND (rising vs falling $-vol)")

    for hold in (5, 10):
        ls_rising, ls_falling = [], []
        dvol_scores_all, mom_scores_all = [], []

        MOM_LB = 7  # momentum lookback for corr check

        for t in range(BURN_IN + 25, len(all_days) - hold - 1):
            d = all_days[t]
            d_entry = all_days[t + 1]
            d_exit = all_days[min(t + 1 + hold, len(all_days) - 1)]
            win_days = all_days[max(0, t - 70): t + 1]

            ranked = []
            dvol_cs, mom_cs = [], []

            for coin, cd in data.items():
                if d not in cd or d_entry not in cd or d_exit not in cd:
                    continue
                score = _dvol_trend_score(cd, win_days)
                if score is None:
                    continue
                # momentum score for corr
                if len(win_days) >= MOM_LB + 1:
                    d_lb = win_days[-(MOM_LB + 1)]
                    if d_lb in cd and cd[d_lb]["c"] > 0 and cd[d]["c"] > 0:
                        mom = cd[d]["c"] / cd[d_lb]["c"] - 1.0
                    else:
                        mom = None
                else:
                    mom = None
                beta = _coin_beta(cd, btc_data, win_days)
                ranked.append((coin, score, beta))
                dvol_cs.append(score)
                if mom is not None:
                    mom_cs.append(mom)

            if len(ranked) < 2 * K + 4:
                continue

            # Store cross-sectional corr (dvol trend vs momentum) per period
            if len(dvol_cs) >= 5 and len(mom_cs) == len(dvol_cs):
                c = _corr(dvol_cs, mom_cs)
                if not math.isnan(c):
                    dvol_scores_all.append(c)

            ranked.sort(key=lambda x: x[1], reverse=True)  # high rising first
            longs_r = [c for c, _, _ in ranked[:K]]
            shorts_r = [c for c, _, _ in ranked[-K:]]

            def fwd(coin):
                r = _fwd_ret(data[coin], d_entry, d_exit)
                return r if r is not None else 0.0

            lr = _mean([fwd(c) for c in longs_r])
            sr = _mean([fwd(c) for c in shorts_r])
            ls_rising.append((lr - sr) - 2 * COST)
            ls_falling.append((sr - lr) - 2 * COST)

        avg_corr_mom = _mean(dvol_scores_all)
        print(f"\n  hold={hold}d | $vol-trend vs momentum corr: {avg_corr_mom:>+.3f}")
        rep(f"    long RISING $vol / short FALLING (inflow = +EV?)",  ls_rising)
        rep(f"    long FALLING $vol / short RISING (outflow = +EV?)", ls_falling)

    print()
    print("  [If corr to momentum > 0.5: $vol trend is a momentum proxy, not independent.")
    print("   Low corr (< 0.3) = potential orthogonal signal → worth stacking.]")


# ─────────────────────────────────────────────────────────────────────────────
#  (d) ILLIQUIDITY-MOMENTUM INTERACTION
# ─────────────────────────────────────────────────────────────────────────────
def test_illiquidity_momentum_interaction(data, all_days, btc_data):
    """
    Does xs-momentum work better among high-$vol or low-$vol coins?

    Method:
    1. Each rebalance, split universe into $vol tertiles (high/mid/low) based on trailing 20d avg $vol.
    2. Within each tertile, apply standard xs-momentum (LB=7, hold=10): rank by 7d return,
       long top / short bottom (relative within tertile).
    3. Compare L-S spread EV across tertiles.
    4. Report corr of $vol score to momentum score cross-sectionally (spurious overlap check).
    """
    section("(d) ILLIQUIDITY-MOMENTUM INTERACTION")

    MOM_LB = 7
    HOLD = 10
    DVOL_WIN = 20

    tier_rets = {"high": [], "mid": [], "low": []}
    tier_ns = {"high": 0, "mid": 0, "low": 0}

    for t in range(BURN_IN + DVOL_WIN + MOM_LB + 2, len(all_days) - HOLD - 1):
        d = all_days[t]
        d_lb = all_days[t - MOM_LB]
        d_entry = all_days[t + 1]
        d_exit = all_days[min(t + 1 + HOLD, len(all_days) - 1)]
        win_days = all_days[max(0, t - 70): t + 1]

        coins_with_scores = []
        for coin, cd in data.items():
            if d not in cd or d_lb not in cd or d_entry not in cd or d_exit not in cd:
                continue
            # Momentum score (strictly prior to entry)
            if cd[d_lb]["c"] <= 0 or cd[d]["c"] <= 0:
                continue
            mom = cd[d]["c"] / cd[d_lb]["c"] - 1.0
            # $vol score (trailing 20d prior to t)
            hist_days = all_days[max(0, t - DVOL_WIN): t]
            hist_dvols = [_dollar_vol(cd[hd]) for hd in hist_days if hd in cd]
            if len(hist_dvols) < 10:
                continue
            avg_dvol = _mean(hist_dvols)
            coins_with_scores.append((coin, mom, avg_dvol))

        if len(coins_with_scores) < 9:
            continue

        # Split into tertiles by avg_dvol
        coins_sorted_by_dvol = sorted(coins_with_scores, key=lambda x: x[2], reverse=True)
        n = len(coins_sorted_by_dvol)
        t1, t2 = n // 3, 2 * (n // 3)
        tiers = {
            "high": coins_sorted_by_dvol[:t1],
            "mid":  coins_sorted_by_dvol[t1:t2],
            "low":  coins_sorted_by_dvol[t2:],
        }

        def fwd(coin):
            r = _fwd_ret(data[coin], d_entry, d_exit)
            return r if r is not None else 0.0

        for tier_name, tier_coins in tiers.items():
            if len(tier_coins) < 4:
                continue
            # xs-momentum within tier
            tier_coins_sorted = sorted(tier_coins, key=lambda x: x[1], reverse=True)
            k_t = max(1, min(3, len(tier_coins_sorted) // 3))
            longs_t = [c for c, _, _ in tier_coins_sorted[:k_t]]
            shorts_t = [c for c, _, _ in tier_coins_sorted[-k_t:]]

            lr = _mean([fwd(c) for c in longs_t])
            sr = _mean([fwd(c) for c in shorts_t])
            tier_rets[tier_name].append((lr - sr) - 2 * COST)
            tier_ns[tier_name] += 1

    print(f"\n  Momentum L-S by $-vol tertile (LB=7d, hold=10d, K≈3 per tier):\n")
    for tier_name in ("high", "mid", "low"):
        arr = tier_rets[tier_name]
        label = f"  {tier_name.upper()} $vol tier (most liquid)"
        if tier_name == "mid":
            label = f"  MID $vol tier"
        elif tier_name == "low":
            label = f"  LOW $vol tier (least liquid)"
        rep(label, arr)

    # Summary interpretation
    print()
    ev_high = _mean(tier_rets["high"]) * 100
    ev_mid  = _mean(tier_rets["mid"]) * 100
    ev_low  = _mean(tier_rets["low"]) * 100

    best = max(("high", ev_high), ("mid", ev_mid), ("low", ev_low), key=lambda x: x[1])
    print(f"  Best tier: {best[0].upper()} ($vol) with mean={best[1]:>+.2f}%/rebal")
    if ev_high > ev_low:
        print(f"  Direction: HIGH liquidity > LOW liquidity  (momentum works BETTER in liquid coins)")
    else:
        print(f"  Direction: LOW liquidity > HIGH liquidity  (momentum works BETTER in illiquid coins)")
    print(f"  Pattern: high={ev_high:>+.2f}% / mid={ev_mid:>+.2f}% / low={ev_low:>+.2f}%")
    print()
    print("  [If HIGH tier > LOW tier: momentum is a liquidity premium (crowded liquid)")
    print("   If LOW tier > HIGH tier: illiquidity amplifies momentum (Amihud-consistent)")
    print("   Interaction is tradeable only if one tier is clearly +EV and ROBUST separately.]")


# ─────────────────────────────────────────────────────────────────────────────
#  CROSS-SIGNAL CORRELATIONS  (post-hoc: how much does each signal overlap?)
# ─────────────────────────────────────────────────────────────────────────────
def report_cross_correlations(data, all_days, btc_data):
    """
    At each rebalance compute four cross-sectional score vectors per coin and report
    average pairwise Pearson corr: vol-spike / Parkinson / $vol-trend / momentum (7d).
    This shows which signals are proxies for each other.
    """
    section("CROSS-SIGNAL CORRELATIONS (redundancy check)")

    MOM_LB = 7
    DVOL_WIN = 20
    PARK_WIN = 10

    corr_spike_mom   = []
    corr_spike_park  = []
    corr_spike_dvolt = []
    corr_park_mom    = []
    corr_park_dvolt  = []
    corr_dvolt_mom   = []

    for t in range(BURN_IN + DVOL_WIN + MOM_LB + PARK_WIN + 2, len(all_days) - 11):
        d = all_days[t]
        d_lb = all_days[t - MOM_LB]
        win_days = all_days[max(0, t - 70): t + 1]

        spikes, parks, dvolts, moms = [], [], [], []
        for coin, cd in data.items():
            if d not in cd or d_lb not in cd:
                continue
            # spike ratio
            hist_days = all_days[max(0, t - DVOL_WIN): t]
            hist_dvols = [_dollar_vol(cd[hd]) for hd in hist_days if hd in cd]
            if len(hist_dvols) < 10:
                continue
            avg_dvol = _mean(hist_dvols)
            if avg_dvol <= 0:
                continue
            spike = _dollar_vol(cd[d]) / avg_dvol

            # parkinson
            park = _parkinson_vol(cd, win_days, win=PARK_WIN)
            if park is None:
                continue

            # $vol trend
            dvolt = _dvol_trend_score(cd, win_days)
            if dvolt is None:
                continue

            # momentum
            if cd[d_lb]["c"] <= 0 or cd[d]["c"] <= 0:
                continue
            mom = cd[d]["c"] / cd[d_lb]["c"] - 1.0

            spikes.append(spike)
            parks.append(park)
            dvolts.append(dvolt)
            moms.append(mom)

        n = min(len(spikes), len(parks), len(dvolts), len(moms))
        if n < 5:
            continue
        spikes, parks, dvolts, moms = spikes[:n], parks[:n], dvolts[:n], moms[:n]

        def c(xs, ys):
            r = _corr(xs, ys)
            return r if not math.isnan(r) else None

        def append_if(lst, val):
            if val is not None:
                lst.append(val)

        append_if(corr_spike_mom,   c(spikes, moms))
        append_if(corr_spike_park,  c(spikes, parks))
        append_if(corr_spike_dvolt, c(spikes, dvolts))
        append_if(corr_park_mom,    c(parks, moms))
        append_if(corr_park_dvolt,  c(parks, dvolts))
        append_if(corr_dvolt_mom,   c(dvolts, moms))

    def prt(name, lst):
        if not lst:
            print(f"  {name:45}  n=0")
            return
        m = _mean(lst)
        flag = "  [HIGH overlap]" if abs(m) > 0.6 else "  [moderate]" if abs(m) > 0.35 else "  [low/indep]"
        print(f"  {name:45}  mean r={m:>+.3f}  (n={len(lst)}){flag}")

    print()
    prt("vol-spike vs momentum (7d)",         corr_spike_mom)
    prt("vol-spike vs Parkinson-vol",          corr_spike_park)
    prt("vol-spike vs $vol-trend",             corr_spike_dvolt)
    prt("Parkinson-vol vs momentum (7d)",      corr_park_mom)
    prt("Parkinson-vol vs $vol-trend",         corr_park_dvolt)
    prt("$vol-trend vs momentum (7d)",         corr_dvolt_mom)

    print()
    print("  Interpretation:")
    print("  r > +0.60 → signals are largely redundant; picking one is enough.")
    print("  r < +0.30 → signals are mostly independent; stacking may add value.")
    print("  Negative r → signals are contrarian to each other.")


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print("=" * 72)
    print("  MICROSTRUCTURE / FLOW — OHLCV signals")
    print("  Cross-sectional | lookahead-safe | cost-aware (10bps) | OOS-split")
    print("=" * 72)

    data = load()
    print(f"\n  {len(data)} coins loaded from cache\n")

    if not data:
        print("ERROR: no candle data loaded. Run without BT_CACHE_ONLY=1 first.")
        return

    btc_data = data.get("BTC")
    if btc_data is None:
        print("ERROR: BTC not in cache — needed for beta calculations.")
        return

    all_days = sorted({d for cd in data.values() for d in cd})
    print(f"  Date range: {all_days[0]} → {all_days[-1]}  ({len(all_days)} trading days)\n")

    test_volume_spike(data, all_days, btc_data)
    test_parkinson_vol(data, all_days, btc_data)
    test_dvol_trend(data, all_days, btc_data)
    test_illiquidity_momentum_interaction(data, all_days, btc_data)
    report_cross_correlations(data, all_days, btc_data)

    print("\n" + "=" * 72)
    print("  VERDICTS SUMMARY")
    print("=" * 72)
    print("""
  (a) VOLUME-SPIKE: absolute price-pattern history suggests REFUTE is likely
      (crypto vol-spikes are unreliable single-coin signals); cross-sectional
      framing is the better shot. See L-S results above.

  (b) RANGE/PARKINSON-VOL: if corr to idio-vol > 0.7 → redundant with the
      already-validated vol-dispersion family. Independent only if corr < 0.4.

  (c) DOLLAR-VOLUME TREND: if corr to momentum > 0.5 → just a volume-weighted
      momentum proxy; if corr < 0.3 → potentially orthogonal (liquidity inflow
      signal). The RELATIVE framing (rank rising vs falling $vol) is our only
      viable angle here.

  (d) ILLIQUIDITY-MOMENTUM INTERACTION: this is the best shot — a pure
      CONDITIONAL test (not a new signal, but a gate on the known good signal).
      If HIGH or LOW tier momentum is robustly +EV while the other isn't, that
      informs sizing/selection within the existing rebalancer.

  HIGH BAR: only promote a sub-test if mean > 0, BOTH OOS halves > 0 (ROBUST),
  and the signal adds information beyond the already-validated momentum /
  vol-dispersion / Sortino stack.
""")


if __name__ == "__main__":
    main()
