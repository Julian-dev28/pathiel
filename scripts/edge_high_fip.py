#!/usr/bin/env python3
"""Alpha hunt — 52-WEEK-HIGH PROXIMITY (George-Hwang) + FROG-IN-THE-PAN (Da-Gurun-Warachka).

Two momentum-ADJACENT anomalies that are mechanically DISTINCT from trailing return:

(a) 52-WEEK-HIGH PROXIMITY (George & Hwang 2004):
    signal = close[t] / max(high[t-W..t])  — nearness to the trailing high.
    In equities: nearness to the 52wk-high predicts continuation INDEPENDENT of past return
    (anchor/reference-point psychology; resistance at the high creates a "stickiness" that breaks
    upward when crossed). Cross-sectional: LONG coins nearest their high, SHORT those farthest below.
    Window W = max available (~252d; we have ~261d). BOTH directions tested.

(b) FROG-IN-THE-PAN / Information Discreteness (Da, Gurun & Warachka 2014):
    Momentum from many small same-sign moves (smooth path) predicts continuation better than
    momentum from a few large jumps (choppy path). Intuition: continuous-positive-news is less
    salient to noise traders → less crowded → better continuation.
    ID = sign(cum_LB_return) × (pct_negative_days - pct_positive_days)
        (same sign convention as the paper: NEGATIVE ID = smooth continuous moves)
    Test ID as a gate/tilt ON cross-sectional momentum:
      - LONG top-momentum coins with SMOOTH path (negative ID among longs)
      - SHORT bottom-momentum coins with SMOOTH path (negative ID [inverted] among shorts)
    Also tested standalone: rank by ID directly (smooth-positive long / choppy-positive short).
    Lookback matches validated xs-momentum LB=7d; hold=10d.

Methodology: lookahead-safe (signal ≤ t, enter t+1 open), cost-aware (>=10bps/leg),
survivorship-free (whole liquid top-50 universe), OOS-split (both halves must be positive).

Run with: BT_CACHE_ONLY=1 python3 scripts/edge_high_fip.py
"""
import os, sys, statistics, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from hermes_trader.client.universe import get_universe
from _bt_candles import get as get_candles

# ─── constants ────────────────────────────────────────────────────────────────
VOL_FLOOR = 5e6
TOPN      = 50
K         = 8              # names per leg
HOLD      = 10             # hold period (days) — matches validated xs-momentum
COST      = 10.0 / 1e4    # 10 bps per name, round-trip
MOM_LB    = 7              # momentum lookback (matches validated config)
HIGH_WIN  = 180            # trailing-high window (days); cache=261d, 252d window leaves ~0 rebalances
                          # 180d = ~6mo trailing max, longest practical with this cache depth

# ─── helpers ──────────────────────────────────────────────────────────────────
def _ymd(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y%m%d")

def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0

def _stdev(xs):
    if len(xs) < 2: return 0.0
    m = _mean(xs)
    v = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return math.sqrt(v) if v > 0 else 0.0

def _pearson(xs, ys):
    n = min(len(xs), len(ys))
    if n < 10: return float("nan")
    xs, ys = xs[-n:], ys[-n:]
    mx, my = _mean(xs), _mean(ys)
    sx, sy = _stdev(xs), _stdev(ys)
    if sx <= 0 or sy <= 0: return float("nan")
    return sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / ((n - 1) * sx * sy)


# ─── data loading ─────────────────────────────────────────────────────────────
def load():
    """Load top-50 liquid perps from disk cache. Returns dict[coin -> dict[ymd -> bar]]."""
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
            data[c] = {_ymd(b["t"]): b for b in bars}
    return data


# ─── signal functions (both lookahead-safe — use data up to and including day t) ────

def _high_proximity(coin_data, all_days, t, win):
    """Closeness to trailing high: close[t] / max(high[t-win..t]).
    Returns None if insufficient data."""
    d = all_days[t]
    if d not in coin_data:
        return None
    c_now = coin_data[d]["c"]
    if c_now <= 0:
        return None
    # gather highs over trailing win bars (including t)
    win_days = all_days[max(0, t - win + 1): t + 1]
    highs = [coin_data[day]["h"] for day in win_days if day in coin_data and coin_data[day]["h"] > 0]
    if len(highs) < 20:  # need a meaningful window
        return None
    trailing_max = max(highs)
    if trailing_max <= 0:
        return None
    return c_now / trailing_max  # [0,1]; 1.0 = AT the high


def _momentum_score(coin_data, all_days, t, lb):
    """Trailing lb-day return (close[t] / close[t-lb] - 1)."""
    if t < lb:
        return None
    d, d_lb = all_days[t], all_days[t - lb]
    if d not in coin_data or d_lb not in coin_data:
        return None
    c_now  = coin_data[d]["c"]
    c_past = coin_data[d_lb]["c"]
    if c_past <= 0 or c_now <= 0:
        return None
    return c_now / c_past - 1.0


def _frog_in_pan_id(coin_data, all_days, t, lb):
    """Information-discreteness score over trailing lb days.
    ID = sign(cum_ret) * (pct_negative_days - pct_positive_days)
    Negative ID = smooth continuous path in direction of cum return.
    Returns (cum_ret, id_score) or (None, None)."""
    if t < lb:
        return None, None
    win_days = all_days[max(0, t - lb): t + 1]   # up to and incl. t
    closes = [coin_data[d]["c"] for d in win_days if d in coin_data and coin_data[d]["c"] > 0]
    if len(closes) < lb // 2:
        return None, None
    # daily returns within the window
    day_rets = [closes[i] / closes[i-1] - 1.0 for i in range(1, len(closes))]
    if not day_rets:
        return None, None
    cum_ret = closes[-1] / closes[0] - 1.0
    n = len(day_rets)
    pct_up   = sum(1 for r in day_rets if r > 0) / n
    pct_down = sum(1 for r in day_rets if r < 0) / n
    sign_cum = 1.0 if cum_ret > 0 else (-1.0 if cum_ret < 0 else 0.0)
    if sign_cum == 0:
        return cum_ret, 0.0
    id_score = sign_cum * (pct_down - pct_up)
    # negative ID = smooth; positive ID = choppy
    return cum_ret, id_score


def _fwd_ret(coin_data, d_entry, d_exit):
    """Forward return entering at d_entry open, exiting at d_exit close."""
    if d_entry not in coin_data or d_exit not in coin_data:
        return None
    o = coin_data[d_entry]["o"]
    c = coin_data[d_exit]["c"]
    if o <= 0:
        return None
    return (c - o) / o


# ─── core backtest engine (generic cross-sectional long-short) ────────────────
def _run_cs(data, all_days, score_fn, higher_is_long=True, burn_in=70):
    """
    score_fn(coin, coin_data, all_days, t) -> float or None
    Returns ls_rets (long-short minus cost), lo_rets (long-only minus cost).
    """
    ls_rets, lo_rets = [], []
    for t in range(burn_in, len(all_days) - HOLD - 1):
        d_entry = all_days[t + 1]
        d_exit  = all_days[min(t + 1 + HOLD, len(all_days) - 1)]

        ranked = []
        for coin, cd in data.items():
            score = score_fn(coin, cd, all_days, t)
            if score is not None and d_entry in cd and d_exit in cd:
                ranked.append((coin, score))

        if len(ranked) < 2 * K + 4:
            continue

        ranked.sort(key=lambda x: x[1], reverse=higher_is_long)
        longs  = [c for c, _ in ranked[:K]]
        shorts = [c for c, _ in ranked[-K:]]

        def fwd(coin):
            r = _fwd_ret(data[coin], d_entry, d_exit)
            return r if r is not None else 0.0

        lr = _mean([fwd(c) for c in longs])
        sr = _mean([fwd(c) for c in shorts])
        ls_rets.append((lr - sr) - 2 * COST)
        lo_rets.append(lr - COST)

    return ls_rets, lo_rets


# ─── momentum baseline (for correlation) ──────────────────────────────────────
def run_momentum_baseline(data):
    """Plain total-return cross-sectional momentum (LB=7d, hold=10d)."""
    all_days = sorted({d for cd in data.values() for d in cd})
    def score(coin, cd, all_days, t):
        return _momentum_score(cd, all_days, t, MOM_LB)
    ls, _ = _run_cs(data, all_days, score, higher_is_long=True, burn_in=MOM_LB + 5)
    return ls, all_days


# ─── (a) 52-WEEK-HIGH PROXIMITY ───────────────────────────────────────────────
def run_52wk_high(data, all_days):
    """Cross-sectional rank by trailing-high proximity. LONG nearest, SHORT farthest."""
    def score(coin, cd, all_days, t):
        return _high_proximity(cd, all_days, t, HIGH_WIN)
    ls_near, lo_near = _run_cs(data, all_days, score, higher_is_long=True,  burn_in=HIGH_WIN)
    ls_far,  lo_far  = _run_cs(data, all_days, score, higher_is_long=False, burn_in=HIGH_WIN)
    return ls_near, lo_near, ls_far, lo_far


# ─── (b) FROG-IN-THE-PAN — standalone ID signal ───────────────────────────────
def run_fip_standalone(data, all_days):
    """Rank purely on ID: LONG smooth (negative ID, i.e. many same-sign small moves),
    SHORT choppy (positive ID). Direction: lower ID = smoother = long."""
    def score(coin, cd, all_days, t):
        _, id_score = _frog_in_pan_id(cd, all_days, t, MOM_LB)
        return id_score  # lower (more negative) = smoother path
    # lower ID = smoother = long: higher_is_long=False (we LONG lowest ID)
    ls_smooth_long, lo_smooth_long = _run_cs(data, all_days, score, higher_is_long=False,
                                             burn_in=MOM_LB + 5)
    ls_choppy_long, lo_choppy_long = _run_cs(data, all_days, score, higher_is_long=True,
                                             burn_in=MOM_LB + 5)
    return ls_smooth_long, lo_smooth_long, ls_choppy_long, lo_choppy_long


# ─── (b) FROG-IN-THE-PAN — as GATE on momentum ───────────────────────────────
def run_fip_gated(data, all_days):
    """Momentum LS but ONLY among coins with smooth paths (negative ID).
    At each rebalance, filter the ranked universe to smooth-only before selecting top/bottom K.
    Two variants:
      - strict: filter BEFORE ranking (only smooth coins eligible)
      - tilt:   rank all, but LONG top-K smoothest-among-winners, SHORT bottom-K smoothest-among-losers
    Returns (ls_gated_strict, ls_gated_tilt).
    """
    burn_in = MOM_LB + 5
    ls_strict, ls_tilt = [], []

    for t in range(burn_in, len(all_days) - HOLD - 1):
        d_entry = all_days[t + 1]
        d_exit  = all_days[min(t + 1 + HOLD, len(all_days) - 1)]

        mom_scores, id_scores = {}, {}
        for coin, cd in data.items():
            if d_entry not in cd or d_exit not in cd:
                continue
            ms = _momentum_score(cd, all_days, t, MOM_LB)
            cum_ret, id_s = _frog_in_pan_id(cd, all_days, t, MOM_LB)
            if ms is not None and id_s is not None:
                mom_scores[coin] = ms
                id_scores[coin]  = id_s

        if len(mom_scores) < 2 * K + 4:
            continue

        # ── STRICT GATE: keep only coins with negative ID (smooth path) ──────
        smooth_coins = [c for c, id_s in id_scores.items() if id_s < 0]
        strict_ranked = sorted([(c, mom_scores[c]) for c in smooth_coins],
                               key=lambda x: x[1], reverse=True)
        if len(strict_ranked) >= 2 * K + 2:
            longs_s  = [c for c, _ in strict_ranked[:K]]
            shorts_s = [c for c, _ in strict_ranked[-K:]]
            def fwd(coin):
                r = _fwd_ret(data[coin], d_entry, d_exit)
                return r if r is not None else 0.0
            lr = _mean([fwd(c) for c in longs_s])
            sr = _mean([fwd(c) for c in shorts_s])
            ls_strict.append((lr - sr) - 2 * COST)

        # ── TILT: among top-K mom longs, prefer smoothest; bottom-K, prefer smoothest ──
        all_ranked = sorted(mom_scores.items(), key=lambda x: x[1], reverse=True)
        top_half  = [c for c, _ in all_ranked[:len(all_ranked) // 2]]
        bot_half  = [c for c, _ in all_ranked[len(all_ranked) // 2:]]
        # among top-half winners, pick K with most negative ID (smoothest)
        top_smooth = sorted(top_half, key=lambda c: id_scores.get(c, 0.0))[:K]
        # among bottom-half losers, pick K with most negative ID (smoothest short — smoothest LOSERS)
        bot_smooth = sorted(bot_half, key=lambda c: id_scores.get(c, 0.0))[:K]
        if len(top_smooth) == K and len(bot_smooth) == K:
            def fwd(coin):
                r = _fwd_ret(data[coin], d_entry, d_exit)
                return r if r is not None else 0.0
            lr = _mean([fwd(c) for c in top_smooth])
            sr = _mean([fwd(c) for c in bot_smooth])
            ls_tilt.append((lr - sr) - 2 * COST)

    return ls_strict, ls_tilt


# ─── reporting ────────────────────────────────────────────────────────────────
def rep(name, arr, mom_arr=None):
    if not arr:
        print(f"  {name:44} n=0 (insufficient data)"); return
    n   = len(arr)
    w   = sum(1 for r in arr if r > 0)
    mid = n // 2
    h1  = _mean(arr[:mid]) * 100 if mid else 0.0
    h2  = _mean(arr[mid:]) * 100 if (n - mid) else 0.0
    mn  = _mean(arr) * 100
    rob = "ROBUST" if h1 > 0 and h2 > 0 else ("fragile" if (h1 > 0) != (h2 > 0) else "neg")
    flag = "  <<< +EV VALIDATED" if mn > 0 and rob == "ROBUST" else ""
    corr_str = ""
    if mom_arr is not None and len(arr) >= 10:
        c = _pearson(arr, mom_arr)
        if not math.isnan(c):
            corr_str = f"  corr_mom={c:+.2f}"
    print(f"  {name:44} n={n:>4} win {w/n*100:>3.0f}%  mean {mn:>+6.2f}%  "
          f"OOS {h1:>+5.2f}/{h2:>+5.2f}  {rob}{corr_str}{flag}")


# ─── main ─────────────────────────────────────────────────────────────────────
def main():
    print("=" * 80)
    print("# EDGE HUNT: 52-WEEK-HIGH PROXIMITY (George-Hwang) + FROG-IN-THE-PAN (Da-Gurun-Warachka)")
    print(f"# universe: top{TOPN} liquid crypto perps (no HIP-3, no spot/index)")
    print(f"# K={K}/leg  hold={HOLD}d  cost={COST*1e4:.0f}bps/name  lookahead-safe  OOS-split")
    print("=" * 80)

    data = load()
    print(f"\n# {len(data)} coins loaded\n")

    if not data:
        print("ERROR: no data loaded — is BT_CACHE_ONLY=1 and cache pre-warmed?")
        sys.exit(1)

    # ── baseline momentum stream (for correlation) ─────────────────────────────
    print("# Computing baseline momentum stream (LB=7d, hold=10d) for correlation…")
    mom_ls, all_days = run_momentum_baseline(data)
    print(f"  Momentum baseline: n={len(mom_ls)}, mean={_mean(mom_ls)*100:+.2f}%  "
          f"(should match ~+1.27% from ALPHA-PLAN validated config)\n")

    # ══════════════════════════════════════════════════════════════════════════
    print("─" * 80)
    print("# (a) 52-WEEK-HIGH PROXIMITY  (George & Hwang 2004)")
    print(f"#     Signal: close[t] / max(high[t-W..t])  — nearness to trailing {HIGH_WIN}d max"
          f" (cache=261d; 252d window → 0 rebalances, using {HIGH_WIN}d)")
    print("#     Hypothesis: anchor psychology → coins near their peak continue upward;")
    print("#     coins far below their high face psychological resistance to recovery.")
    print("#     Cross-sectional: LONG nearest-to-high, SHORT farthest-below-high.")
    print("#     Distinction from momentum: both a strong-momentum coin and a recent")
    print("#     consolidator can be near their high; the HIGH is the anchor, not the trend slope.")
    print("─" * 80)

    ls_near, lo_near, ls_far, lo_far = run_52wk_high(data, all_days)

    print(f"\n  [a1] LONG nearest / SHORT farthest (George-Hwang direction):")
    rep("  52wk-high NEAR long / FAR short", ls_near, mom_ls)

    print(f"\n  [a2] LONG farthest / SHORT nearest (reversal / mean-reversion direction):")
    rep("  52wk-high FAR long / NEAR short",  ls_far,  mom_ls)

    print(f"\n  [long-only cuts — diagnostic only, not the claimed edge]")
    print(f"  {'  long-only (nearest)':44} n={len(lo_near):>4} mean {_mean(lo_near)*100:>+6.2f}%")
    print(f"  {'  long-only (farthest)':44} n={len(lo_far)*100:>4} mean {_mean(lo_far)*100:>+6.2f}%"
          if lo_far else f"  {'  long-only (farthest)':44} n=0")

    # ══════════════════════════════════════════════════════════════════════════
    print()
    print("─" * 80)
    print("# (b) FROG-IN-THE-PAN / INFORMATION DISCRETENESS  (Da, Gurun & Warachka 2014)")
    print(f"#     ID = sign(cum_return_LB{MOM_LB}) × (pct_down_days - pct_up_days)")
    print("#     Negative ID = smooth / continuous path (many small same-sign moves).")
    print("#     Positive ID = choppy / jump-driven (few big moves).")
    print("#     Hypothesis: smooth-positive momentum is less salient to noise traders,")
    print("#     so it's less crowded → better continuation (under-reaction to drip news).")
    print("#     Note: BOTH legs (long AND short) tested in smooth-path direction.")
    print("─" * 80)

    print(f"\n  [b1] Standalone ID signal — rank purely on path smoothness:")
    ls_smooth, lo_smooth, ls_choppy, lo_choppy = run_fip_standalone(data, all_days)
    rep("  SMOOTH path long / CHOPPY short (FIP dir)",  ls_smooth, mom_ls)
    rep("  CHOPPY path long / SMOOTH short (reversal)", ls_choppy, mom_ls)

    print(f"\n  [b2] FIP as GATE/TILT on cross-sectional momentum:")
    print(f"       strict = restrict universe to smooth-path coins before picking top/bottom K")
    print(f"       tilt   = rank all by momentum, then among top-half longs/bot-half shorts pick K smoothest")
    ls_strict, ls_tilt = run_fip_gated(data, all_days)
    rep("  Momentum STRICT-gated (smooth-only coins)", ls_strict, mom_ls)
    rep("  Momentum TILT (smooth subset of top/bot)",  ls_tilt,   mom_ls)

    # ══════════════════════════════════════════════════════════════════════════
    print()
    print("═" * 80)
    print("# VERDICT SUMMARY")
    print("═" * 80)
    all_results = [
        ("(a) 52wk-high NEAR long  [George-Hwang dir]", ls_near,   mom_ls),
        ("(a) 52wk-high FAR long   [reversal dir]",     ls_far,    mom_ls),
        ("(b) FIP smooth long      [standalone]",        ls_smooth, mom_ls),
        ("(b) FIP choppy long      [reversal]",          ls_choppy, mom_ls),
        ("(b) FIP gate strict      [mom-gated]",         ls_strict, mom_ls),
        ("(b) FIP tilt             [mom-tilt]",          ls_tilt,   mom_ls),
    ]
    for label, arr, marr in all_results:
        if not arr:
            verdict = "SKIP (n=0)"
        else:
            mid = len(arr) // 2
            h1 = _mean(arr[:mid]); h2 = _mean(arr[mid:])
            mn = _mean(arr)
            corr = _pearson(arr, marr)
            corr_str = f"  corr_mom={corr:+.2f}" if not math.isnan(corr) else ""
            if mn > 0 and h1 > 0 and h2 > 0:
                verdict = f"VALIDATED +EV   mean={mn*100:>+6.2f}%  OOS {h1*100:>+5.2f}/{h2*100:>+5.2f}{corr_str}"
            elif mn > 0:
                verdict = f"fragile         mean={mn*100:>+6.2f}%  OOS {h1*100:>+5.2f}/{h2*100:>+5.2f}{corr_str}"
            else:
                verdict = f"REFUTED         mean={mn*100:>+6.2f}%{corr_str}"
        print(f"  {label:44}  {verdict}")

    print()
    print("# CORRELATION MATRIX (diagnostic):")
    pairs = [(n, arr) for n, arr, _ in all_results if arr]
    pairs.insert(0, ("momentum_baseline", mom_ls))
    for i, (n1, a1) in enumerate(pairs):
        for j, (n2, a2) in enumerate(pairs):
            if j <= i: continue
            c = _pearson(a1, a2)
            if not math.isnan(c):
                print(f"  corr({n1:30} , {n2:30}) = {c:+.3f}")

    print()
    print("# HOW TO WIRE IF VALIDATED:")
    print("#  (a) 52wk-high: add _high_proximity() as an alternative signal to rank_universe()")
    print("#      in agents/xs_momentum.py; gate = same vol-regime filter already wired.")
    print("#  (b) FIP-gate: add _frog_in_pan_id() to xs_momentum.py, filter strict-smooth")
    print("#      within the top/bottom-K selection loop. Hot-readable via config flag.")


if __name__ == "__main__":
    main()
