#!/usr/bin/env python3
"""Alpha hunt — CROSS-ASSET REGIME / TIMING GATES for momentum + vol-dispersion books.

Tests three timing gates on top of the validated LS books:

  (a) BTC-DOMINANCE / alt-season:
      Trailing relative strength of BTC vs equal-weight alt basket.
      Hypothesis: cross-sectional momentum & vol-dispersion pay MORE in alt-season
      (alts outperforming), less in BTC-season (BTC leads, alts follow passively).

  (b) ETH/BTC ratio regime:
      ETH/BTC trailing trend (>0 = rising = risk-on).
      Hypothesis: rising ETH/BTC signals broad risk-on appetite → better for momentum.

  (c) CORRELATION-REGIME gate:
      Rolling average pairwise Pearson correlation across the universe.
      Hypothesis: HIGH correlation → less cross-sectional dispersion → weaker LS books.
      Go flat (or smaller) when correlation is above the rolling median.

Each gate is tested CONDITIONING the two underlying books:
  - Momentum  : xs long-short spread (LB=7d, hold=10d, K=8, cost=10bps/leg)
  - Vol-disp  : within-beta-tercile long-HIGH / short-LOW idio-vol (hold=10d, cost=10bps/leg)

OOS split: chronological first-half vs second-half of each conditioned stream.
Verdict per gate: robustly improves Sharpe / cuts maxDD BOTH halves vs unconditioned?

HIGH BAR: R3 refuted Hurst/autocorr regime switching — don't qualify a gate unless BOTH
OOS halves positive AND clearly improve on the unconditioned Sharpe.

Run: BT_CACHE_ONLY=1 python3 scripts/edge_regime_timing.py
"""
import os, sys, math, statistics

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime, timezone
from hermes_trader.client.universe import get_universe
from _bt_candles import get as get_candles

# ─── config ───────────────────────────────────────────────────────────────────
TOPN        = 50
VOL_FLOOR   = 5e6
K           = 8          # names per leg (both books)
COST_BPS    = 10.0       # per name, round-trip
MOM_LB      = 7          # momentum look-back (validated)
MOM_HOLD    = 10         # momentum hold
VDISP_HOLD  = 10         # vol-dispersion hold
IDVOL_WIN   = 30         # trailing window for idio-vol signal
BETA_WIN    = 30         # trailing window for BTC-beta estimation
DOM_WIN     = 14         # trailing window for BTC dominance RS
ETHBTC_WIN  = 14         # trailing window for ETH/BTC trend
CORR_WIN    = 14         # rolling window for pairwise-correlation signal
# ─────────────────────────────────────────────────────────────────────────────


def _ymd(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y%m%d")


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _pstdev(xs):
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def _pearson(a, b):
    n = len(a)
    if n < 4:
        return 0.0
    ma, mb = _mean(a), _mean(b)
    num = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((x - mb) ** 2 for x in b))
    if da <= 0 or db <= 0:
        return 0.0
    return num / (da * db)


def _ols_beta(cr, br):
    """OLS beta of coin returns on BTC returns. Returns 1.0 if degenerate."""
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


# ─── Data loading ────────────────────────────────────────────────────────────

def load():
    """Load daily candles from disk cache. Returns dict: coin -> {ymd: (open, close)}."""
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
            data[c] = {_ymd(b["t"]): (b["o"], b["c"]) for b in bars}
    return data


# ─── Timing-signal builders ─────────────────────────────────────────────────

def build_signals(data, all_days):
    """Compute timing signals for each day t (lookahead-safe: uses only data ≤ t).

    Returns dict: day -> {
        'btc_dom_rising': bool|None,    # BTC trailing RS > alt basket
        'ethbtc_rising':  bool|None,    # ETH/BTC ratio trending up
        'corr_high':      bool|None,    # pairwise correlation above rolling median
    }
    """
    # Precompute daily returns for each coin indexed by day
    returns_by_day = {}   # coin -> {day: return}
    for coin, oc in data.items():
        days_sorted = sorted(oc)
        returns_by_day[coin] = {}
        for i in range(1, len(days_sorted)):
            d, prev = days_sorted[i], days_sorted[i - 1]
            c_now, c_prev = oc[d][1], oc[prev][1]
            if c_prev > 0:
                returns_by_day[coin][d] = c_now / c_prev - 1.0

    # Precompute trailing return over window W (close[t]/close[t-W] - 1)
    def trailing_ret(coin, day_idx, w):
        if day_idx < w:
            return None
        d_now = all_days[day_idx]
        d_past = all_days[day_idx - w]
        oc = data[coin]
        if d_now not in oc or d_past not in oc:
            return None
        c_now, c_past = oc[d_now][1], oc[d_past][1]
        if c_past <= 0:
            return None
        return c_now / c_past - 1.0

    signals = {}
    for t, day in enumerate(all_days):
        sig = {}

        # ── (a) BTC dominance / alt-season ──────────────────────────────────
        # btc_dom_rising = BTC trailing return > MEAN(alt coins trailing return)
        # "alt-season" ≡ BTC NOT dominating (alts outperform BTC over the window)
        btc_trail = trailing_ret("BTC", t, DOM_WIN) if "BTC" in data else None
        if btc_trail is not None:
            alt_trails = [r for c, _ in data.items()
                          if c != "BTC"
                          for r in [trailing_ret(c, t, DOM_WIN)]
                          if r is not None]
            if len(alt_trails) >= 5:
                alt_mean = _mean(alt_trails)
                sig["btc_dom_rising"] = btc_trail > alt_mean   # True = BTC-season
            else:
                sig["btc_dom_rising"] = None
        else:
            sig["btc_dom_rising"] = None

        # ── (b) ETH/BTC ratio regime ─────────────────────────────────────────
        # Rising ETH/BTC = BTC outperformed BY ETH → risk-on
        eth_trail = trailing_ret("ETH", t, ETHBTC_WIN) if "ETH" in data else None
        btc_trail_eth = trailing_ret("BTC", t, ETHBTC_WIN) if "BTC" in data else None
        if eth_trail is not None and btc_trail_eth is not None:
            # ETH/BTC ratio trend: positive means ETH outperforming BTC over window
            sig["ethbtc_rising"] = eth_trail > btc_trail_eth
        else:
            sig["ethbtc_rising"] = None

        # ── (c) Correlation-regime gate ──────────────────────────────────────
        # Rolling average pairwise Pearson correlation over CORR_WIN trading days
        # Use a fixed set of liquid coins (those with returns available for the window)
        if t >= CORR_WIN:
            window_days = all_days[t - CORR_WIN + 1: t + 1]   # up to and incl. t
            eligible = []
            for coin in data:
                rets_w = [returns_by_day[coin].get(d) for d in window_days]
                if sum(1 for r in rets_w if r is not None) >= CORR_WIN * 0.8:
                    filled = [r if r is not None else 0.0 for r in rets_w]
                    eligible.append((coin, filled))
            if len(eligible) >= 4:
                # Average all pairs (n*(n-1)/2 pairs; use first 15 for speed)
                eligible_sub = eligible[:15]
                pair_corrs = []
                for i in range(len(eligible_sub)):
                    for j in range(i + 1, len(eligible_sub)):
                        rho = _pearson(eligible_sub[i][1], eligible_sub[j][1])
                        pair_corrs.append(rho)
                sig["_avg_corr"] = _mean(pair_corrs) if pair_corrs else None
            else:
                sig["_avg_corr"] = None
        else:
            sig["_avg_corr"] = None

        signals[day] = sig

    # ── Build correlation-regime boolean against rolling median ───────────────
    # Gate = above-median correlation (computed lookahead-safe: median over days ≤ t)
    running_corrs = []
    for day in all_days:
        avg_c = signals[day].get("_avg_corr")
        if avg_c is not None:
            running_corrs.append(avg_c)
            median_so_far = statistics.median(running_corrs)
            signals[day]["corr_high"] = avg_c > median_so_far
        else:
            signals[day]["corr_high"] = None

    return signals


# ─── Momentum book ───────────────────────────────────────────────────────────

def build_momentum_stream(data, all_days):
    """Returns list of (day, ls_return) for the xs-momentum book.

    LB=MOM_LB, hold=MOM_HOLD, K=K legs, cost=COST_BPS/leg.
    Entry: t+1 open; exit: t+1+hold close. Lookahead-safe.
    """
    cost = COST_BPS / 1e4
    cost_both = 2 * cost
    stream = []
    for t in range(MOM_LB, len(all_days) - MOM_HOLD - 1):
        d = all_days[t]
        d_lb = all_days[t - MOM_LB]
        d_entry = all_days[t + 1]
        d_exit = all_days[t + 1 + MOM_HOLD] if t + 1 + MOM_HOLD < len(all_days) else all_days[-1]

        ranked = []
        for coin, oc in data.items():
            if d in oc and d_lb in oc and d_entry in oc and d_exit in oc:
                c_now, c_past = oc[d][1], oc[d_lb][1]
                if c_past > 0:
                    ranked.append((coin, c_now / c_past - 1))
        if len(ranked) < 2 * K + 4:
            continue
        ranked.sort(key=lambda x: x[1], reverse=True)
        longs  = [c for c, _ in ranked[:K]]
        shorts = [c for c, _ in ranked[-K:]]

        def fwd(coin):
            o = data[coin][d_entry][0]
            c = data[coin][d_exit][1]
            return (c - o) / o if o > 0 else 0.0

        lr = _mean([fwd(c) for c in longs])
        sr = _mean([fwd(c) for c in shorts])
        ls = (lr - sr) - cost_both
        stream.append((d, ls))
    return stream


# ─── Vol-dispersion book ─────────────────────────────────────────────────────

def build_vdisp_stream(data, all_days):
    """Returns list of (day, ls_return) for the within-beta-tercile vol-dispersion book.

    Rank by idio-vol (residual vol after BTC beta), long HIGH, short LOW,
    WITHIN each beta tercile (beta-neutral construction).
    Hold=VDISP_HOLD, cost=COST_BPS/leg. Lookahead-safe.
    """
    cost = COST_BPS / 1e4
    cost_both = 2 * cost

    # Precompute daily returns by coin
    daily_rets = {}
    for coin, oc in data.items():
        days_sorted = sorted(oc)
        daily_rets[coin] = {}
        for i in range(1, len(days_sorted)):
            d, prev = days_sorted[i], days_sorted[i - 1]
            c_now, c_prev = oc[d][1], oc[prev][1]
            if c_prev > 0:
                daily_rets[coin][d] = c_now / c_prev - 1.0

    btc_rets = daily_rets.get("BTC", {})

    stream = []
    warmup = max(IDVOL_WIN, BETA_WIN) + 2

    for t in range(warmup, len(all_days) - VDISP_HOLD - 1):
        d = all_days[t]
        d_entry = all_days[t + 1]
        d_exit = all_days[t + 1 + VDISP_HOLD] if t + 1 + VDISP_HOLD < len(all_days) else all_days[-1]

        # Signal window: trailing IDVOL_WIN days ending at d (lookahead-safe)
        win_days = all_days[max(0, t - IDVOL_WIN + 1): t + 1]
        beta_days = all_days[max(0, t - BETA_WIN + 1): t + 1]

        # Build per-coin (beta, idio_vol) pairs
        factors = []
        for coin, oc in data.items():
            if d_entry not in oc or d_exit not in oc:
                continue
            cr_win  = [daily_rets[coin].get(dd) for dd in win_days]
            cr_win  = [r for r in cr_win if r is not None]
            cr_beta = [daily_rets[coin].get(dd) for dd in beta_days]
            br_beta = [btc_rets.get(dd) for dd in beta_days]
            cr_beta = [r for r in cr_beta if r is not None]
            br_beta_f = [btc_rets.get(dd, 0.0) for dd in beta_days]

            if len(cr_win) < IDVOL_WIN // 2:
                continue
            beta = _ols_beta(cr_beta, br_beta_f)

            # Idiosyncratic vol: residual std after removing beta*BTC return
            btc_win = [btc_rets.get(dd, 0.0) for dd in win_days]
            residuals = [cr_win[i] - beta * btc_win[i]
                         for i in range(min(len(cr_win), len(btc_win)))]
            if len(residuals) < 4:
                continue
            idvol = _pstdev(residuals)
            factors.append((coin, beta, idvol))

        if len(factors) < 2 * K + 4:
            continue

        # Within-beta-tercile neutralization: split into 3 beta terciles,
        # rank by idvol within each tercile, pick top-K/3 and bot-K/3 from each tercile
        factors.sort(key=lambda x: x[1])
        n_f = len(factors)
        t1, t2 = n_f // 3, 2 * n_f // 3
        terciles = [factors[:t1], factors[t1:t2], factors[t2:]]

        k_per = max(1, K // 3)   # ~2-3 from each tercile
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
            oc = data[coin]
            o = oc[d_entry][0]
            c = oc[d_exit][1]
            return (c - o) / o if o > 0 else 0.0

        lr = _mean([fwd(c) for c in longs])
        sr = _mean([fwd(c) for c in shorts])
        ls = (lr - sr) - cost_both
        stream.append((d, ls))

    return stream


# ─── Reporting ───────────────────────────────────────────────────────────────

def sharpe(rets):
    """Annualised Sharpe (daily observations, ×√365)."""
    if len(rets) < 4:
        return float("nan")
    m = _mean(rets)
    s = _pstdev(rets)
    if s <= 0:
        return float("nan")
    return (m / s) * math.sqrt(365)


def max_dd(rets):
    """Maximum drawdown (as a positive fraction)."""
    cum = 0.0
    peak = 0.0
    worst = 0.0
    for r in rets:
        cum += r
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > worst:
            worst = dd
    return worst


def rep_stream(label, rets):
    """Print stats for a return stream."""
    if not rets:
        print(f"    {label:40s}  n=0  (no observations)")
        return
    n = len(rets)
    mid = n // 2
    h1_rets = rets[:mid]
    h2_rets = rets[mid:]
    m   = _mean(rets) * 100
    m1  = _mean(h1_rets) * 100 if h1_rets else float("nan")
    m2  = _mean(h2_rets) * 100 if h2_rets else float("nan")
    sh  = sharpe(rets)
    sh1 = sharpe(h1_rets)
    sh2 = sharpe(h2_rets)
    mdd = max_dd(rets) * 100
    w   = sum(1 for r in rets if r > 0)
    robust = (m1 > 0 and m2 > 0)
    rob_tag = "ROBUST" if robust else ("fragile" if (m1 > 0) != (m2 > 0) else "neg")
    ev_tag  = "  <<< +EV" if m > 0 and robust else ""
    print(f"    {label:40s}  n={n:>4}  win={w/n*100:>3.0f}%  mean={m:>+6.2f}%  "
          f"OOS {m1:>+5.2f}/{m2:>+5.2f}  Sh={sh:>+5.2f}({sh1:>+5.2f}/{sh2:>+5.2f})  "
          f"maxDD={mdd:>5.2f}%  {rob_tag}{ev_tag}")


def gate_stats(stream, signals, gate_key, gate_true_label, gate_false_label):
    """Split a stream by a binary signal and report conditioned + unconditioned stats.

    stream: list of (day, ret)
    signals: dict of day -> {gate_key: bool|None}
    """
    all_rets = [r for _, r in stream]
    true_rets  = [r for d, r in stream
                  if signals.get(d, {}).get(gate_key) is True]
    false_rets = [r for d, r in stream
                  if signals.get(d, {}).get(gate_key) is False]
    skip_rets  = [r for d, r in stream
                  if signals.get(d, {}).get(gate_key) is None]

    print(f"      Unconditioned:          ", end="")
    rep_stream("", all_rets)
    print(f"      {gate_true_label:26s}: ", end="")
    rep_stream("", true_rets)
    print(f"      {gate_false_label:26s}: ", end="")
    rep_stream("", false_rets)
    if skip_rets:
        print(f"      No signal (skipped):    ", end="")
        rep_stream("", skip_rets)

    # Verdict: does conditioning on EITHER leg beat unconditioned Sharpe (both OOS halves)?
    base_sh = sharpe(all_rets)
    best_sh, best_label, best_rets = -999.0, None, []
    for rets, lbl in [(true_rets, gate_true_label), (false_rets, gate_false_label)]:
        if len(rets) >= 6:
            sh = sharpe(rets)
            if sh > best_sh:
                best_sh, best_label, best_rets = sh, lbl, rets
    mid = len(best_rets) // 2
    best_h1 = _mean(best_rets[:mid]) * 100 if mid else float("nan")
    best_h2 = _mean(best_rets[mid:]) * 100 if (len(best_rets) - mid) else float("nan")
    improves = best_sh > base_sh and best_h1 > 0 and best_h2 > 0

    return base_sh, best_sh, best_label, improves


def verdict(gate_name, book_name, base_sh, best_sh, best_label, improves):
    delta = best_sh - base_sh
    if improves and delta > 0.3:
        tag = "USEFUL GATE (both OOS positive, Sharpe lift)"
    elif improves and delta > 0.0:
        tag = "MARGINAL (improves but small lift)"
    else:
        tag = "NOISE — does not improve on unconditioned"
    print(f"      >>> VERDICT [{gate_name} on {book_name}]: {tag}")
    print(f"          Best leg [{best_label}] Sh={best_sh:>+.2f} vs base Sh={base_sh:>+.2f}  "
          f"(Δ={delta:>+.2f})")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 90)
    print("# REGIME / TIMING GATES FOR MOMENTUM + VOL-DISPERSION BOOKS")
    print(f"# K={K}/leg | cost {COST_BPS:.0f}bps/name | lookahead-safe | OOS chronological split")
    print("=" * 90)

    data = load()
    n_coins = len(data)
    all_days = sorted({d for oc in data.values() for d in oc})
    print(f"\n# {n_coins} coins | {len(all_days)} trading days ({all_days[0]} – {all_days[-1]})")

    required = ["BTC", "ETH"]
    missing = [c for c in required if c not in data]
    if missing:
        print(f"ERROR: {missing} not in cache — cannot build all signals. Aborting.")
        sys.exit(1)

    # ── Build timing signals ────────────────────────────────────────────────
    print("\n# Building timing signals...")
    signals = build_signals(data, all_days)

    # Signal distribution summary
    dom_vals  = [s.get("btc_dom_rising") for s in signals.values() if s.get("btc_dom_rising") is not None]
    eth_vals  = [s.get("ethbtc_rising")  for s in signals.values() if s.get("ethbtc_rising")  is not None]
    corr_vals = [s.get("corr_high")      for s in signals.values() if s.get("corr_high")      is not None]
    avg_corrs = [s.get("_avg_corr")      for s in signals.values() if s.get("_avg_corr")      is not None]

    print(f"  (a) BTC-dominance signal  : {len(dom_vals)} days  "
          f"BTC-season={sum(dom_vals)} ({sum(dom_vals)/len(dom_vals)*100:.1f}%)  "
          f"alt-season={len(dom_vals)-sum(dom_vals)} ({(len(dom_vals)-sum(dom_vals))/len(dom_vals)*100:.1f}%)")
    print(f"  (b) ETH/BTC ratio regime  : {len(eth_vals)} days  "
          f"rising={sum(eth_vals)} ({sum(eth_vals)/len(eth_vals)*100:.1f}%)  "
          f"falling={len(eth_vals)-sum(eth_vals)} ({(len(eth_vals)-sum(eth_vals))/len(eth_vals)*100:.1f}%)")
    if avg_corrs:
        print(f"  (c) Correlation regime    : {len(corr_vals)} days  "
              f"high-corr={sum(corr_vals)} ({sum(corr_vals)/len(corr_vals)*100:.1f}%)  "
              f"low-corr={len(corr_vals)-sum(corr_vals)} ({(len(corr_vals)-sum(corr_vals))/len(corr_vals)*100:.1f}%)  "
              f"avg-pair-corr={_mean(avg_corrs):>.3f}  min={min(avg_corrs):.3f}  max={max(avg_corrs):.3f}")

    # ── Build LS books ──────────────────────────────────────────────────────
    print(f"\n# Building momentum book (LB={MOM_LB}d, hold={MOM_HOLD}d)...")
    mom_stream = build_momentum_stream(data, all_days)
    print(f"  {len(mom_stream)} rebalance periods")

    print(f"# Building vol-dispersion book (hold={VDISP_HOLD}d, within-beta-tercile)...")
    vdisp_stream = build_vdisp_stream(data, all_days)
    print(f"  {len(vdisp_stream)} rebalance periods")

    # ── Gate analysis ───────────────────────────────────────────────────────
    gates = [
        ("btc_dom_rising",
         "(a) BTC-DOMINANCE / ALT-SEASON",
         "BTC-season (BTC leads)",
         "alt-season (alts lead)"),
        ("ethbtc_rising",
         "(b) ETH/BTC ratio regime",
         "ETH/BTC rising (risk-on)",
         "ETH/BTC falling (risk-off)"),
        ("corr_high",
         "(c) CORRELATION-REGIME",
         "high-corr (flat hypothesis)",
         "low-corr (run hypothesis)"),
    ]

    for book_label, book_stream in [("MOMENTUM", mom_stream), ("VOL-DISPERSION", vdisp_stream)]:
        print(f"\n{'═' * 90}")
        print(f"# BOOK: {book_label}")
        print(f"{'═' * 90}")

        for gate_key, gate_name, true_lbl, false_lbl in gates:
            print(f"\n  ─── Gate {gate_name} ───")
            b_sh, best_sh, best_lbl, improves = gate_stats(
                book_stream, signals, gate_key, true_lbl, false_lbl
            )
            verdict(gate_name, book_label, b_sh, best_sh, best_lbl, improves)

    # ── Combined low-corr + alt-season gate (stacking best hypotheses) ─────
    print(f"\n{'═' * 90}")
    print("# STACKED GATE: low-corr AND alt-season (both conditions must hold)")
    print(f"{'═' * 90}")
    for book_label, book_stream in [("MOMENTUM", mom_stream), ("VOL-DISPERSION", vdisp_stream)]:
        stack_rets = [r for d, r in book_stream
                      if signals.get(d, {}).get("corr_high") is False
                      and signals.get(d, {}).get("btc_dom_rising") is False]
        base_rets  = [r for _, r in book_stream]
        print(f"\n  {book_label}:")
        print(f"    Unconditioned       ", end="")
        rep_stream("", base_rets)
        print(f"    low-corr+alt-season ", end="")
        rep_stream("", stack_rets)
        n_total = len(book_stream)
        if n_total:
            pct = len(stack_rets) / n_total * 100
            print(f"    (stacked gate fires {len(stack_rets)}/{n_total} = {pct:.1f}% of periods)")

    # ── OOS deep-dive: per-gate Sharpe table ──────────────────────────────
    print(f"\n{'═' * 90}")
    print("# SUMMARY TABLE — unconditioned vs best-leg Sharpe (both OOS halves)")
    print(f"{'═' * 90}")
    print(f"  {'Book':<18} {'Gate':<30} {'Base Sh':>8} {'Best Sh':>8} {'Best leg':<28} {'Verdict'}")
    print(f"  {'-'*18} {'-'*30} {'-'*8} {'-'*8} {'-'*28} {'-'*20}")

    for book_label, book_stream in [("MOMENTUM", mom_stream), ("VOL-DISPERSION", vdisp_stream)]:
        base_rets = [r for _, r in book_stream]
        base_sh   = sharpe(base_rets)
        for gate_key, gate_name, true_lbl, false_lbl in gates:
            true_rets  = [r for d, r in book_stream
                          if signals.get(d, {}).get(gate_key) is True]
            false_rets = [r for d, r in book_stream
                          if signals.get(d, {}).get(gate_key) is False]
            best_sh_v, best_lbl_v = -999.0, ""
            for rets, lbl in [(true_rets, true_lbl[:26]), (false_rets, false_lbl[:26])]:
                if len(rets) >= 4:
                    sh = sharpe(rets)
                    if sh > best_sh_v:
                        best_sh_v, best_lbl_v = sh, lbl
            mid = len(true_rets) // 2 if true_rets else 0
            best_pool = (true_rets if best_lbl_v == true_lbl[:26] else false_rets)
            mp  = len(best_pool) // 2
            h1b = _mean(best_pool[:mp]) * 100 if mp else float("nan")
            h2b = _mean(best_pool[mp:]) * 100 if (len(best_pool) - mp) else float("nan")
            rob_ok = h1b > 0 and h2b > 0
            delta = best_sh_v - base_sh
            if rob_ok and delta > 0.3:
                vtag = "USEFUL"
            elif rob_ok and delta > 0:
                vtag = "marginal"
            else:
                vtag = "NOISE"
            print(f"  {book_label:<18} {gate_name[:30]:<30} {base_sh:>+8.2f} "
                  f"{best_sh_v:>+8.2f} {best_lbl_v:<28} {vtag}")

    # ── Final verdicts ─────────────────────────────────────────────────────
    print(f"\n{'═' * 90}")
    print("# FINAL VERDICTS (hold bar: BOTH OOS halves positive, Sharpe lift > 0.3)")
    print(f"{'═' * 90}")
    print("""
  (a) BTC-DOMINANCE / ALT-SEASON  — see table above
      Hypothesis: momentum/vol-disp pay more in alt-season (alts outperform BTC).

  (b) ETH/BTC RATIO REGIME         — see table above
      Hypothesis: rising ETH/BTC (risk-on) = better conditions for momentum books.

  (c) CORRELATION-REGIME           — see table above
      Hypothesis: low-corr → more cross-sectional dispersion → stronger LS books.
      This is the MOST theoretically grounded gate (R3 analogy: don't revisit without
      a new angle; look at whether correlation is informative unlike Hurst/autocorr).

  STACKED (low-corr + alt-season): combined gate — fires only when BOTH conditions
      hold, potentially higher-quality periods with fewer observations.

  Methodology reminder:
    - lookahead-safe  : all signals use data ≤ t; entry is t+1 open
    - cost-aware      : 10bps/leg (20bps/rebal both legs) already subtracted
    - OOS-robust      : both chronological halves of CONDITIONED stream must be positive
    - HIGH BAR        : must IMPROVE base Sharpe (not just be positive if base is too)
""")


if __name__ == "__main__":
    main()
