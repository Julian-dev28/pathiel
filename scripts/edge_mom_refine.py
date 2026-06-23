#!/usr/bin/env python3
"""Alpha hunt — neutralization / weighting refinements of the validated BTC-neutral residual momentum.

BASELINE: BTC-neutral residual xs-momentum, LB=7d, hold=10d, top-K=8 long / bottom-K=8 short,
cost 10bps/leg. Validated ~+0.6–1.3%/rebal (261d window). All tests here are held to the same bar:
lookahead-safe, cost-aware, OOS-robust (BOTH halves positive), AND the return stream correlation to
the baseline is checked (corr > 0.9 → same edge, no new alpha).

Refinements tested:
  (a) ETH-NEUTRAL:    rank on coin_return − beta_ETH × ETH_return
  (b) DUAL-NEUTRAL:   rank on 2-factor OLS residual (beta_BTC, beta_ETH via Gram-Schmidt)
  (c) VOL-WEIGHTED:   LB-return computed from VWAP (dollar-vol-weighted price) instead of close
  (d) EWMA-BETA:      BTC-neutral residual but beta estimated with exponential weights (recent-heavy)

For each: report mean/OOS vs baseline, and corr of the rebal return stream to the baseline.
Verdict: does any refinement MATERIALLY beat (>+0.3%/rebal AND OOS-robust) the simple BTC-neutral?
"""
import os, sys, math, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from hermes_trader.client.universe import get_universe
from _bt_candles import get as get_candles

# ── config (identical to validated baseline) ──────────────────────────────────
COST    = 10.0 / 1e4    # 10 bps / leg
K       = 8             # names per leg
LB      = 7             # lookback days for signal
HOLD    = 10            # holding period
BW      = 30            # beta estimation window (calendar days)
VOL_FLOOR = 5e6
TOPN    = 50
EWMA_HL = 10            # EWMA half-life for exponential beta weights


# ── data loading ─────────────────────────────────────────────────────────────

def _ymd(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y%m%d")


def load(topn=TOPN):
    """Load universe + raw candle data (close + vol per day)."""
    uni = [m for m in get_universe(include_hip3=False)
           if ":" not in (m.get("coin") or "")
           and not (m.get("coin") or "").startswith("@")
           and m.get("type") != "spot"
           and float(m.get("dayNtlVlm") or 0) >= VOL_FLOOR]
    uni = sorted(uni, key=lambda m: float(m.get("dayNtlVlm") or 0), reverse=True)[:topn]
    closes, vols, opens_ = {}, {}, {}
    for m in uni:
        c = m["coin"]
        bars = get_candles(c, "1d", 260)
        if len(bars) >= 80:
            closes[c]  = {_ymd(b["t"]): float(b["c"]) for b in bars}
            vols[c]    = {_ymd(b["t"]): float(b.get("v", 0) or 0) for b in bars}
            opens_[c]  = {_ymd(b["t"]): float(b["o"]) for b in bars}
    return closes, vols, opens_


# ── beta helpers ──────────────────────────────────────────────────────────────

def _ols_beta(cr, br):
    """Equal-weight OLS beta of coin returns on benchmark returns."""
    n = min(len(cr), len(br))
    if n < 8:
        return 1.0
    cr, br = cr[-n:], br[-n:]
    mb = statistics.mean(br)
    vb = sum((x - mb) ** 2 for x in br)
    if vb <= 0:
        return 1.0
    mc = statistics.mean(cr)
    return sum((a - mc) * (b - mb) for a, b in zip(cr, br)) / vb


def _ewma_beta(cr, br, halflife=EWMA_HL):
    """Exponentially-weighted beta: recent observations weighted more heavily."""
    n = min(len(cr), len(br))
    if n < 8:
        return 1.0
    cr, br = cr[-n:], br[-n:]
    lam = 0.5 ** (1.0 / halflife)
    # weights: most recent = 1, older = lambda, lambda^2, ...
    ws = [lam ** (n - 1 - i) for i in range(n)]
    sw  = sum(ws)
    mb  = sum(w * b for w, b in zip(ws, br)) / sw
    mc  = sum(w * c for w, c in zip(ws, cr)) / sw
    vb  = sum(w * (b - mb) ** 2 for w, b in zip(ws, br)) / sw
    if vb <= 0:
        return 1.0
    cov = sum(w * (a - mc) * (b - mb) for w, a, b in zip(ws, cr, br)) / sw
    return cov / vb


def _gram_schmidt_resid(cr, br_btc, br_eth):
    """Two-factor OLS residual via Gram-Schmidt:
    1. Regress out BTC first, get residual of coin on BTC.
    2. Regress out ETH from the BTC-residual.
    Returns (beta_btc, beta_eth, resid_series) where resid is orthogonal to both."""
    n = min(len(cr), len(br_btc), len(br_eth))
    if n < 10:
        return None, None, None
    cr, br_btc, br_eth = cr[-n:], br_btc[-n:], br_eth[-n:]
    # step 1: beta_btc
    beta_btc = _ols_beta(cr, br_btc)
    resid1 = [c - beta_btc * b for c, b in zip(cr, br_btc)]
    # step 2: also orthogonalize ETH vs BTC, then regress residual on ortho-ETH
    mb = statistics.mean(br_btc)
    beta_eth_on_btc = _ols_beta(br_eth, br_btc)
    eth_orth = [e - beta_eth_on_btc * b for e, b in zip(br_eth, br_btc)]
    beta_eth = _ols_beta(resid1, eth_orth)
    resid2 = [r - beta_eth * e for r, e in zip(resid1, eth_orth)]
    return beta_btc, beta_eth, resid2


# ── VWAP helper ───────────────────────────────────────────────────────────────

def _vwap_return(closes_c, vols_c, d_past, d_now, all_days):
    """LB-return using a VWAP-ish proxy: sum(close*vol)/sum(vol) over a window around each endpoint.
    Window = 3 days centered on the endpoint to smooth noise without lookahead bias.
    Strictly past data only: window ends AT the endpoint day."""
    def _vwap_at(d):
        idx = all_days.index(d)
        window_days = all_days[max(0, idx - 2): idx + 1]   # ≤3 days ending at d (no lookahead)
        pv = sum(closes_c[wd] * vols_c.get(wd, 0) for wd in window_days if wd in closes_c)
        v  = sum(vols_c.get(wd, 0) for wd in window_days if wd in closes_c)
        return pv / v if v > 0 else closes_c.get(d)
    vp_now  = _vwap_at(d_now)
    vp_past = _vwap_at(d_past)
    if vp_past is None or vp_now is None or vp_past <= 0:
        return None
    return vp_now / vp_past - 1.0


# ── daily return series builder ───────────────────────────────────────────────

def _daily_ret_series(closes_c, win_days):
    """Build day-to-day return series over win_days list."""
    rets = []
    for k in range(1, len(win_days)):
        d0, d1 = win_days[k - 1], win_days[k]
        if d0 in closes_c and d1 in closes_c and closes_c[d0] > 0:
            rets.append(closes_c[d1] / closes_c[d0] - 1.0)
    return rets


# ── correlation helper ────────────────────────────────────────────────────────

def _corr(a, b):
    """Pearson correlation between two lists of equal length."""
    n = min(len(a), len(b))
    if n < 10:
        return float("nan")
    a, b = a[-n:], b[-n:]
    ma, mb = statistics.mean(a), statistics.mean(b)
    sa = math.sqrt(sum((x - ma) ** 2 for x in a) / n)
    sb = math.sqrt(sum((x - mb) ** 2 for x in b) / n)
    if sa <= 0 or sb <= 0:
        return float("nan")
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (n * sa * sb)


# ── core backtest engine ──────────────────────────────────────────────────────

def run_mode(closes, vols, mode):
    """Run one neutralization mode. Returns list of per-rebal LS returns (net of 2*COST).
    mode ∈ {'btc', 'eth', 'dual', 'volwt', 'ewma'}"""
    btc_cl = closes.get("BTC")
    eth_cl = closes.get("ETH")
    if not btc_cl:
        return []
    if mode in ("eth", "dual") and not eth_cl:
        print(f"  [WARN] ETH data missing, skipping mode={mode}")
        return []

    all_days = sorted({d for cl in closes.values() for d in cl})

    # build a sorted index for VWAP window lookup
    day_idx = {d: i for i, d in enumerate(all_days)}

    out = []
    need_start = max(LB, BW)
    for t in range(need_start, len(all_days) - HOLD - 1):
        d       = all_days[t]
        d_lb    = all_days[t - LB]
        d_en    = all_days[t + 1]
        d_ex_i  = min(t + 1 + HOLD, len(all_days) - 1)
        d_ex    = all_days[d_ex_i]
        win_days = all_days[t - BW: t]      # BW days strictly before t → no lookahead

        # benchmark daily returns for this window
        btc_rets = _daily_ret_series(btc_cl, win_days)
        eth_rets = _daily_ret_series(eth_cl, win_days) if eth_cl else []

        ranked = []
        for c, cl in closes.items():
            if c in ("BTC", "ETH"):
                continue
            if not all(x in cl for x in (d, d_lb, d_en, d_ex)) or cl[d_lb] <= 0:
                continue

            coin_rets = _daily_ret_series(cl, win_days)

            if mode == "btc":
                # ── baseline: BTC-neutral residual ──────────────────────────
                if len(btc_rets) < 8 or len(coin_rets) < 8:
                    continue
                n = min(len(coin_rets), len(btc_rets))
                beta = _ols_beta(coin_rets[-n:], btc_rets[-n:])
                rc   = cl[d] / cl[d_lb] - 1
                rb   = btc_cl.get(d, 0) / btc_cl.get(d_lb, 1) - 1 if btc_cl.get(d_lb, 0) > 0 else 0
                score = rc - beta * rb

            elif mode == "eth":
                # ── (a) ETH-neutral residual ────────────────────────────────
                if not eth_cl or len(eth_rets) < 8 or len(coin_rets) < 8:
                    continue
                n = min(len(coin_rets), len(eth_rets))
                beta = _ols_beta(coin_rets[-n:], eth_rets[-n:])
                rc   = cl[d] / cl[d_lb] - 1
                re   = eth_cl.get(d, 0) / eth_cl.get(d_lb, 1) - 1 if eth_cl.get(d_lb, 0) > 0 else 0
                score = rc - beta * re

            elif mode == "dual":
                # ── (b) dual-neutral (BTC+ETH two-factor) ──────────────────
                if not eth_cl or len(btc_rets) < 10 or len(eth_rets) < 10 or len(coin_rets) < 10:
                    continue
                n = min(len(coin_rets), len(btc_rets), len(eth_rets))
                _, _, resid_series = _gram_schmidt_resid(coin_rets[-n:], btc_rets[-n:], eth_rets[-n:])
                if resid_series is None:
                    continue
                # The SIGNAL is the LB-return portion unexplained by both factors.
                # Use the same 2-factor structure on the LB return:
                beta_btc = _ols_beta(coin_rets[-n:], btc_rets[-n:])
                # ortho-ETH component
                beta_eth_on_btc = _ols_beta(eth_rets[-n:], btc_rets[-n:])
                eth_orth_lb = (eth_cl.get(d, 0) / eth_cl.get(d_lb, 1) - 1
                               if eth_cl.get(d_lb, 0) > 0 else 0)
                eth_orth_lb -= beta_eth_on_btc * (btc_cl.get(d, 0) / btc_cl.get(d_lb, 1) - 1
                                                   if btc_cl.get(d_lb, 0) > 0 else 0)
                beta_eth2 = _ols_beta(
                    [r - beta_btc * b for r, b in zip(coin_rets[-n:], btc_rets[-n:])],
                    [e - beta_eth_on_btc * b for e, b in zip(eth_rets[-n:], btc_rets[-n:])]
                )
                rc   = cl[d] / cl[d_lb] - 1
                rb   = btc_cl.get(d, 0) / btc_cl.get(d_lb, 1) - 1 if btc_cl.get(d_lb, 0) > 0 else 0
                score = rc - beta_btc * rb - beta_eth2 * eth_orth_lb

            elif mode == "volwt":
                # ── (c) VWAP-return signal ──────────────────────────────────
                # Build VWAP-based LB-return; still neutralize on BTC close (no vol data bias)
                vr = _vwap_return(cl, vols.get(c, {}), d_lb, d, all_days)
                if vr is None:
                    continue
                n = min(len(coin_rets), len(btc_rets))
                if n < 8:
                    continue
                beta = _ols_beta(coin_rets[-n:], btc_rets[-n:])
                rb   = btc_cl.get(d, 0) / btc_cl.get(d_lb, 1) - 1 if btc_cl.get(d_lb, 0) > 0 else 0
                score = vr - beta * rb

            elif mode == "ewma":
                # ── (d) EWMA-beta BTC-neutral residual ─────────────────────
                if len(btc_rets) < 8 or len(coin_rets) < 8:
                    continue
                n = min(len(coin_rets), len(btc_rets))
                beta = _ewma_beta(coin_rets[-n:], btc_rets[-n:])
                rc   = cl[d] / cl[d_lb] - 1
                rb   = btc_cl.get(d, 0) / btc_cl.get(d_lb, 1) - 1 if btc_cl.get(d_lb, 0) > 0 else 0
                score = rc - beta * rb

            else:
                raise ValueError(f"Unknown mode {mode!r}")

            ranked.append((c, score))

        if len(ranked) < 2 * K + 4:
            continue

        ranked.sort(key=lambda x: x[1], reverse=True)
        longs  = [c for c, _ in ranked[:K]]
        shorts = [c for c, _ in ranked[-K:]]

        def fwd(c):
            o = closes[c].get(d_en, 0)
            ex = closes[c].get(d_ex, 0)
            return (ex - o) / o if o > 0 else 0.0

        lr = statistics.mean(fwd(c) for c in longs)
        sr = statistics.mean(fwd(c) for c in shorts)
        out.append((lr - sr) - 2 * COST)

    return out


# ── reporting ─────────────────────────────────────────────────────────────────

def report(label, arr, baseline=None):
    """Print stats. If baseline provided, also print correlation."""
    if not arr or len(arr) < 20:
        print(f"  {label:30} n={len(arr) if arr else 0} (too thin)")
        return
    n   = len(arr)
    mid = n // 2
    h1  = statistics.mean(arr[:mid]) * 100
    h2  = statistics.mean(arr[mid:]) * 100
    q   = n // 4
    qs  = [statistics.mean(arr[i * q : (i + 1) * q if i < 3 else n]) * 100 for i in range(4)]
    rob = ("ROBUST" if h1 > 0 and h2 > 0
           else "fragile" if (h1 > 0) != (h2 > 0)
           else "neg")
    mean_pct = statistics.mean(arr) * 100
    flag = "  <<< +EV" if mean_pct > 0 and rob == "ROBUST" else ""
    neg_q = sum(1 for q_ in qs if q_ <= 0)

    corr_str = ""
    if baseline is not None and len(baseline) == len(arr):
        r = _corr(arr, baseline)
        if not math.isnan(r):
            same = " (SAME EDGE — no new alpha)" if r > 0.90 else " (distinct)" if r < 0.70 else " (overlapping)"
            corr_str = f"  corr_to_baseline={r:+.3f}{same}"

    print(f"  {label:30} n={n:>4}  mean {mean_pct:>+6.2f}%  OOS {h1:>+5.2f}/{h2:>+5.2f}  {rob}{flag}")
    print(f"  {'':30} Qs {qs[0]:>+5.2f}/{qs[1]:>+5.2f}/{qs[2]:>+5.2f}/{qs[3]:>+5.2f}  ({neg_q}/4 Q<=0){corr_str}")


def delta(label, arr, baseline):
    """Print improvement over baseline."""
    if not arr or not baseline or len(arr) < 20 or len(baseline) < 20:
        return
    delta_mean = (statistics.mean(arr) - statistics.mean(baseline)) * 100
    mid = len(arr) // 2
    mid_b = len(baseline) // 2
    dh1 = statistics.mean(arr[:mid]) * 100 - statistics.mean(baseline[:mid_b]) * 100
    dh2 = statistics.mean(arr[mid:]) * 100 - statistics.mean(baseline[mid_b:]) * 100
    mat = "MATERIAL" if delta_mean > 0.30 and dh1 > 0 and dh2 > 0 else "marginal"
    print(f"  {'delta vs baseline':30} Δmean {delta_mean:>+6.2f}%  ΔH1 {dh1:>+5.2f}/ΔH2 {dh2:>+5.2f}  [{mat}]")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"# Momentum neutralization/weighting refinements | LB={LB}d hold={HOLD}d K={K} cost={int(COST*1e4)}bps")
    print(f"# Lookahead-safe | cost-aware | OOS-robust bar | BETA_WIN={BW}d | EWMA_HL={EWMA_HL}d")
    print()

    closes, vols, _opens = load()
    print(f"# {len(closes)} coins loaded ({len([c for c in closes if c not in ('BTC','ETH')])} non-BTC/ETH)\n")

    # baseline
    print("── BASELINE: BTC-neutral residual momentum ─────────────────────────────────────")
    btc_base = run_mode(closes, vols, "btc")
    report("btc-neutral (baseline)", btc_base)
    print()

    # (a) ETH-neutral
    print("── (a) ETH-NEUTRAL residual momentum ───────────────────────────────────────────")
    eth_arr = run_mode(closes, vols, "eth")
    report("eth-neutral", eth_arr, baseline=btc_base)
    delta("eth-neutral vs baseline", eth_arr, btc_base)
    print()

    # (b) dual-neutral (BTC+ETH two-factor)
    print("── (b) DUAL-NEUTRAL (BTC+ETH two-factor) ───────────────────────────────────────")
    dual_arr = run_mode(closes, vols, "dual")
    report("dual-neutral (BTC+ETH)", dual_arr, baseline=btc_base)
    delta("dual-neutral vs baseline", dual_arr, btc_base)
    print()

    # (c) volume-weighted return signal
    print("── (c) VOLUME-WEIGHTED (VWAP-ish) return signal ───────────────────────────────")
    vw_arr = run_mode(closes, vols, "volwt")
    report("vol-weighted signal", vw_arr, baseline=btc_base)
    delta("vol-weighted vs baseline", vw_arr, btc_base)
    print()

    # (d) EWMA beta
    print("── (d) EWMA-BETA (recent-weighted) BTC-neutral ─────────────────────────────────")
    ewma_arr = run_mode(closes, vols, "ewma")
    report("ewma-beta btc-neutral", ewma_arr, baseline=btc_base)
    delta("ewma-beta vs baseline", ewma_arr, btc_base)
    print()

    # ── summary ───────────────────────────────────────────────────────────────
    print("=" * 80)
    print("VERDICT SUMMARY")
    print("=" * 80)
    results = [
        ("BTC-neutral (baseline)", btc_base),
        ("(a) ETH-neutral",        eth_arr),
        ("(b) Dual-neutral",        dual_arr),
        ("(c) Vol-weighted",        vw_arr),
        ("(d) EWMA-beta",           ewma_arr),
    ]
    base_mean = statistics.mean(btc_base) * 100 if btc_base else 0.0
    print(f"  Baseline BTC-neutral mean: {base_mean:+.2f}%/rebal\n")
    for name, arr in results[1:]:
        if not arr or len(arr) < 20:
            print(f"  {name:30} INSUFFICIENT DATA"); continue
        n, mid = len(arr), len(arr) // 2
        h1, h2 = statistics.mean(arr[:mid]) * 100, statistics.mean(arr[mid:]) * 100
        m = statistics.mean(arr) * 100
        rob = "ROBUST" if h1 > 0 and h2 > 0 else "fragile" if (h1 > 0) != (h2 > 0) else "neg"
        dm = m - base_mean
        corr_b = len(btc_base) == len(arr)
        r = _corr(arr, btc_base) if corr_b else float("nan")
        mat = "MATERIAL BEAT" if dm > 0.30 and rob == "ROBUST" else ("BEATS" if dm > 0 and rob == "ROBUST" else "NO BEAT")
        print(f"  {name:30} mean {m:>+6.2f}%  Δ{dm:>+5.2f}%  {rob:8}  corr={r:+.3f}  → {mat}")
    print()
    print("  A refinement MATERIALLY beats the baseline if: Δmean > +0.3% AND OOS-robust.")
    print("  corr > 0.90 = same edge, no new alpha; < 0.70 = genuinely distinct signal.")


if __name__ == "__main__":
    main()
