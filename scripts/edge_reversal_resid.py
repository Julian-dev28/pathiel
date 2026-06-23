#!/usr/bin/env python3
"""Alpha hunt — Reversal/mean-reversion cross-sectional study.

Three angles tested, each as a market-neutral long-short spread:

(A) RESIDUAL short-term reversal
    Signal: rank by BTC-neutral RESIDUAL return over 1/3/5d.
    Direction: LONG residual losers / SHORT residual winners (reversal).
    Did residualizing rescue what short-term total reversal couldn't?

(B) MEDIUM-HORIZON total-return reversal
    Signal: rank by raw total return over 30/60/90d.
    Direction: LONG losers / SHORT winners.
    (Classic DeBondt-Thaler adapted to short windows.)

(C) DISTANCE-FROM-MA cross-sectional reversion
    Signal: (close - MA_n) / MA_n for n ∈ {20, 50, 200}.
    Direction: LONG most-below-MA / SHORT most-above-MA.

For each variant:
- Cross-sectional L/S spread, K=8 per leg, market-neutral.
- Lookahead-safe: signal from close[t], enter t+1 open, exit t+1+hold close.
- Cost-aware: 10bps/leg (20bps round-trip per spread).
- OOS split: first-half vs second-half of trade stream (BOTH must be positive for ROBUST).
- Hold periods tested: 5d and 10d.
- Both directions tested where sign is ambiguous.
- Correlation to momentum stream measured at the end to flag overlap.

Methodology bar (ALPHA-PLAN.md): lookahead-safe, cost-aware (≥10bps/leg),
survivorship-free (whole liquid top-50 universe), OOS-robust (both halves mean-positive).
"""
import os, sys, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from hermes_trader.client.universe import get_universe
from _bt_candles import get as get_candles

# ── constants ────────────────────────────────────────────────────────────────
TOPN       = 50
VOL_FLOOR  = 5e6
K          = 8
COST       = 10.0 / 1e4   # 10bps per leg (20bps round-trip)
BETA_WIN   = 30            # days for OLS beta estimation (residual mode)
HOLDS      = (5, 10)       # hold periods to sweep


def _ymd(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y%m%d")


# ── data load ────────────────────────────────────────────────────────────────
def load():
    """Load the standard top-50 liquid perp universe (no HIP-3, no spot, no @/@index)."""
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
            # store both close-keyed dict (for fast lookup) and raw list (for MA)
            data[c] = {
                "oc":   {_ymd(b["t"]): (b["o"], b["c"]) for b in bars},
                "cl":   {_ymd(b["t"]): b["c"] for b in bars},
                "days": [_ymd(b["t"]) for b in bars],  # ordered
                "bars": bars,
            }
    return data


def _all_days(data):
    return sorted({d for v in data.values() for d in v["cl"]})


# ── helpers ───────────────────────────────────────────────────────────────────
def _fwd(coin_data, d_entry, d_exit):
    """Forward return: enter at t+1 open, exit at t+1+h close."""
    oc = coin_data["oc"]
    if d_entry not in oc or d_exit not in oc:
        return None
    o = oc[d_entry][0]
    c = oc[d_exit][1]
    return (c - o) / o if o > 0 else None


def _beta(cr, br):
    """OLS beta of coin daily returns on benchmark daily returns."""
    n = min(len(cr), len(br))
    if n < 8:
        return 1.0
    cr, br = cr[-n:], br[-n:]
    mb = sum(br) / n
    vb = sum((x - mb) ** 2 for x in br)
    if vb <= 0:
        return 1.0
    mc = sum(cr) / n
    return sum((a - mc) * (b - mb) for a, b in zip(cr, br)) / vb


def _daily_rets_window(cl_dict, days_window):
    """Daily returns over a specific ordered list of days."""
    rets = []
    for i in range(1, len(days_window)):
        d0, d1 = days_window[i-1], days_window[i]
        if d0 in cl_dict and d1 in cl_dict and cl_dict[d0] > 0:
            rets.append(cl_dict[d1] / cl_dict[d0] - 1)
    return rets


def _ma(cl_dict, all_days, t_idx, n):
    """Simple moving average of closes over the n bars up to and including all_days[t_idx]."""
    window = [all_days[i] for i in range(max(0, t_idx - n + 1), t_idx + 1)
              if all_days[i] in cl_dict]
    if len(window) < n // 2:  # need at least half the window
        return None
    vals = [cl_dict[d] for d in window]
    return sum(vals) / len(vals)


# ── report ────────────────────────────────────────────────────────────────────
def rep(name, arr, momentum_stream=None):
    if not arr:
        print(f"  {name:50s} n=0")
        return
    n   = len(arr)
    w   = sum(1 for r in arr if r > 0)
    mid = n // 2
    h1  = statistics.mean(arr[:mid]) * 100 if mid else 0.0
    h2  = statistics.mean(arr[mid:]) * 100 if (n - mid) else 0.0
    mn  = statistics.mean(arr) * 100
    rob = "ROBUST" if h1 > 0 and h2 > 0 else ("fragile" if (h1 > 0) != (h2 > 0) else "neg")
    ev  = "  <<< +EV" if mn > 0 and rob == "ROBUST" else ""

    # correlation to momentum stream
    corr_str = ""
    if momentum_stream and len(momentum_stream) == len(arr):
        mx, my = statistics.mean(momentum_stream), statistics.mean(arr)
        num = sum((a - mx) * (b - my) for a, b in zip(momentum_stream, arr))
        denom = (sum((a - mx)**2 for a in momentum_stream) *
                 sum((b - my)**2 for b in arr)) ** 0.5
        corr = num / denom if denom > 0 else 0.0
        corr_str = f"  corr_mom={corr:+.2f}"
        if abs(corr) > 0.5:
            corr_str += " *** LIKELY MOMENTUM DISGUISE ***"

    print(f"  {name:50s} n={n:>4} win {w/n*100:>3.0f}%  mean {mn:>+6.2f}%  "
          f"OOS {h1:>+5.2f}/{h2:>+5.2f}  {rob}{ev}{corr_str}")


# ── (A) RESIDUAL SHORT-TERM REVERSAL ─────────────────────────────────────────
def run_residual_reversal(data, lb, hold):
    """
    Signal: residual return (BTC-neutral) over `lb` days.
    Reversal: LONG residual losers (low score) / SHORT residual winners (high score).
    Momentum (opposite): LONG winners / SHORT losers.
    Returns (reversal_stream, momentum_stream) — we report both directions.
    """
    all_days = _all_days(data)
    btc      = data.get("BTC", {}).get("cl", {})
    if not btc:
        return [], []

    btc_bars_days = data["BTC"]["days"]  # ordered list

    rev_rets = []
    mom_rets = []
    min_t    = max(lb, BETA_WIN) + 1

    for t in range(min_t, len(all_days) - hold - 1):
        d      = all_days[t]
        d_lb   = all_days[t - lb]
        d_en   = all_days[t + 1]
        d_ex   = all_days[min(t + 1 + hold, len(all_days) - 1)]

        # BTC daily returns for beta window
        beta_days = [all_days[i] for i in range(t - BETA_WIN, t)]
        br_btc = _daily_rets_window(btc, beta_days)

        ranked = []
        for coin, v in data.items():
            cl = v["cl"]
            if not all(d in cl for d in (d, d_lb, d_en)) or cl[d_lb] <= 0:
                continue
            if _fwd(v, d_en, d_ex) is None:
                continue
            # residual score
            cr = _daily_rets_window(cl, beta_days)
            if len(cr) < 8 or len(br_btc) < 8:
                continue
            beta = _beta(cr, br_btc)
            rc   = cl[d] / cl[d_lb] - 1
            rb   = (btc[d] / btc[d_lb] - 1) if d in btc and d_lb in btc and btc[d_lb] > 0 else 0.0
            score = rc - beta * rb
            ranked.append((coin, score))

        if len(ranked) < 2 * K + 4:
            continue

        ranked.sort(key=lambda x: x[1], reverse=True)
        winners = [c for c, _ in ranked[:K]]   # high residual = momentum winners
        losers  = [c for c, _ in ranked[-K:]]  # low residual  = reversal candidates

        def fwd(coin):
            return _fwd(data[coin], d_en, d_ex) or 0.0

        lr = statistics.mean(fwd(c) for c in losers)   # reversal long leg
        wr = statistics.mean(fwd(c) for c in winners)  # reversal short leg

        # reversal L/S: long losers, short winners → expect lr - wr < 0 for momentum,
        # > 0 for genuine reversal
        rev_rets.append((lr - wr) - 2 * COST)
        # momentum L/S (opposite sign): for correlation reference
        mom_rets.append((wr - lr) - 2 * COST)

    return rev_rets, mom_rets


# ── (B) MEDIUM-HORIZON TOTAL-RETURN REVERSAL ────────────────────────────────
def run_medium_reversal(data, lb, hold):
    """
    Signal: raw total return over `lb` days (30/60/90).
    Reversal: LONG losers / SHORT winners.
    Returns (reversal_stream, momentum_stream).
    """
    all_days = _all_days(data)
    rev_rets = []
    mom_rets = []

    for t in range(lb + 1, len(all_days) - hold - 1):
        d    = all_days[t]
        d_lb = all_days[t - lb]
        d_en = all_days[t + 1]
        d_ex = all_days[min(t + 1 + hold, len(all_days) - 1)]

        ranked = []
        for coin, v in data.items():
            cl = v["cl"]
            if not all(d_ in cl for d_ in (d, d_lb, d_en)) or cl[d_lb] <= 0:
                continue
            if _fwd(v, d_en, d_ex) is None:
                continue
            score = cl[d] / cl[d_lb] - 1
            ranked.append((coin, score))

        if len(ranked) < 2 * K + 4:
            continue

        ranked.sort(key=lambda x: x[1], reverse=True)
        winners = [c for c, _ in ranked[:K]]
        losers  = [c for c, _ in ranked[-K:]]

        def fwd(coin):
            return _fwd(data[coin], d_en, d_ex) or 0.0

        lr = statistics.mean(fwd(c) for c in losers)
        wr = statistics.mean(fwd(c) for c in winners)

        rev_rets.append((lr - wr) - 2 * COST)
        mom_rets.append((wr - lr) - 2 * COST)

    return rev_rets, mom_rets


# ── (C) DISTANCE-FROM-MA REVERSAL ────────────────────────────────────────────
def run_ma_reversal(data, ma_n, hold):
    """
    Signal: (close - MA_n) / MA_n.
    Reversal: LONG most-below-MA / SHORT most-above-MA.
    Returns (reversal_stream, continuation_stream).
    """
    all_days = _all_days(data)
    rev_rets  = []
    cont_rets = []

    for t in range(ma_n + 1, len(all_days) - hold - 1):
        d    = all_days[t]
        d_en = all_days[t + 1]
        d_ex = all_days[min(t + 1 + hold, len(all_days) - 1)]

        ranked = []
        for coin, v in data.items():
            cl = v["cl"]
            if d not in cl or d_en not in cl:
                continue
            if _fwd(v, d_en, d_ex) is None:
                continue
            ma = _ma(cl, all_days, t, ma_n)
            if ma is None or ma <= 0:
                continue
            deviation = (cl[d] - ma) / ma   # + means above MA, - means below
            ranked.append((coin, deviation))

        if len(ranked) < 2 * K + 4:
            continue

        ranked.sort(key=lambda x: x[1], reverse=True)
        above_ma = [c for c, _ in ranked[:K]]   # most above MA → reversal short
        below_ma = [c for c, _ in ranked[-K:]]  # most below MA → reversal long

        def fwd(coin):
            return _fwd(data[coin], d_en, d_ex) or 0.0

        bl = statistics.mean(fwd(c) for c in below_ma)  # long below-MA
        al = statistics.mean(fwd(c) for c in above_ma)  # short above-MA

        rev_rets.append((bl - al) - 2 * COST)   # reversal: expect reversion
        cont_rets.append((al - bl) - 2 * COST)  # continuation: expect persistence

    return rev_rets, cont_rets


# ── MOMENTUM REFERENCE STREAM (LB=7 hold=5, the validated core) ──────────────
def run_momentum_ref(data):
    """Reproduce the validated xs-momentum stream for correlation reference."""
    all_days = _all_days(data)
    LB, HOLD = 7, 5
    rets = []
    for t in range(LB + 1, len(all_days) - HOLD - 1):
        d    = all_days[t]
        d_lb = all_days[t - LB]
        d_en = all_days[t + 1]
        d_ex = all_days[min(t + 1 + HOLD, len(all_days) - 1)]

        ranked = []
        for coin, v in data.items():
            cl = v["cl"]
            if not all(d_ in cl for d_ in (d, d_lb, d_en)) or cl[d_lb] <= 0:
                continue
            if _fwd(v, d_en, d_ex) is None:
                continue
            ranked.append((coin, cl[d] / cl[d_lb] - 1))

        if len(ranked) < 2 * K + 4:
            continue

        ranked.sort(key=lambda x: x[1], reverse=True)
        longs  = [c for c, _ in ranked[:K]]
        shorts = [c for c, _ in ranked[-K:]]

        def fwd(coin):
            return _fwd(data[coin], d_en, d_ex) or 0.0

        lr = statistics.mean(fwd(c) for c in longs)
        sr = statistics.mean(fwd(c) for c in shorts)
        rets.append((lr - sr) - 2 * COST)
    return rets


# ── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    print("=" * 90)
    print("REVERSAL / MEAN-REVERSION EDGE STUDY")
    print("Top-50 liquid crypto perps | K=8/leg | 10bps/leg | lookahead-safe | OOS split")
    print("=" * 90)

    data = load()
    print(f"\nUniverse loaded: {len(data)} coins\n")

    # Build momentum reference stream for correlation tests
    mom_ref = run_momentum_ref(data)
    print(f"Momentum reference (LB=7/hold=5): n={len(mom_ref)}, "
          f"mean={statistics.mean(mom_ref)*100:+.2f}% [reference only]\n")

    # ─────────────────────────────────────────────────────────────────────────
    print("─" * 90)
    print("(A) RESIDUAL SHORT-TERM REVERSAL  [signal: BTC-neutral residual, short lookback]")
    print("    LONG residual losers / SHORT residual winners")
    print("    NOTE: momentum direction = opposite sign (long winners/short losers)")
    print("─" * 90)

    for lb in (1, 3, 5):
        for hold in HOLDS:
            rev, mom = run_residual_reversal(data, lb, hold)
            if not rev:
                print(f"  LB={lb}d hold={hold}d — insufficient data")
                continue

            # For correlation, align lengths with momentum reference
            # (streams are independent periods; use same-length suffix for rough corr)
            # We compare the MOMENTUM direction here to the mom_ref
            n_corr = min(len(mom), len(mom_ref))
            mom_aligned = mom[-n_corr:] if n_corr else []
            ref_aligned = mom_ref[-n_corr:] if n_corr else []

            corr_val = None
            if n_corr > 10:
                mx, my = statistics.mean(ref_aligned), statistics.mean(mom_aligned)
                num  = sum((a - mx)*(b - my) for a, b in zip(ref_aligned, mom_aligned))
                dxd  = (sum((a-mx)**2 for a in ref_aligned) *
                        sum((b-my)**2 for b in mom_aligned)) ** 0.5
                corr_val = num / dxd if dxd > 0 else 0.0

            def _rep_dir(label, arr, sign_flip=False):
                """Report one direction of the same stream."""
                if sign_flip:
                    arr = [-x for x in arr]
                n   = len(arr)
                w   = sum(1 for r in arr if r > 0)
                mid = n // 2
                h1  = statistics.mean(arr[:mid]) * 100 if mid else 0.0
                h2  = statistics.mean(arr[mid:]) * 100 if (n-mid) else 0.0
                mn  = statistics.mean(arr) * 100
                rob = ("ROBUST" if h1 > 0 and h2 > 0
                       else ("fragile" if (h1 > 0) != (h2 > 0) else "neg"))
                ev  = "  <<< +EV" if mn > 0 and rob == "ROBUST" else ""
                print(f"  LB={lb}d hold={hold}d [{label}]  "
                      f"n={n:>4} win {w/n*100:>3.0f}%  mean {mn:>+6.2f}%  "
                      f"OOS {h1:>+5.2f}/{h2:>+5.2f}  {rob}{ev}")

            _rep_dir("reversal: long losers / short winners", rev)
            _rep_dir("momentum: long winners / short losers", mom)

            if corr_val is not None:
                tag = "*** LIKELY MOMENTUM DISGUISE ***" if abs(corr_val) > 0.6 else ""
                print(f"    corr(momentum_direction, mom_ref_LB7): {corr_val:+.3f} {tag}")
        print()

    # ─────────────────────────────────────────────────────────────────────────
    print("─" * 90)
    print("(B) MEDIUM-HORIZON TOTAL-RETURN REVERSAL  [lookback 30/60/90d]")
    print("    LONG losers / SHORT winners")
    print("─" * 90)

    for lb in (30, 60, 90):
        for hold in HOLDS:
            rev, mom = run_medium_reversal(data, lb, hold)
            if not rev:
                print(f"  LB={lb}d hold={hold}d — insufficient data")
                continue

            n_corr = min(len(rev), len(mom_ref))
            rev_aligned = rev[-n_corr:]
            ref_aligned = mom_ref[-n_corr:]

            corr_val = None
            if n_corr > 10:
                mx, my = statistics.mean(ref_aligned), statistics.mean(rev_aligned)
                num  = sum((a - mx)*(b - my) for a, b in zip(ref_aligned, rev_aligned))
                dxd  = (sum((a-mx)**2 for a in ref_aligned) *
                        sum((b-my)**2 for b in rev_aligned)) ** 0.5
                corr_val = num / dxd if dxd > 0 else 0.0

            n   = len(rev)
            w   = sum(1 for r in rev if r > 0)
            mid = n // 2
            h1  = statistics.mean(rev[:mid]) * 100 if mid else 0.0
            h2  = statistics.mean(rev[mid:]) * 100 if (n-mid) else 0.0
            mn  = statistics.mean(rev) * 100
            rob = ("ROBUST" if h1 > 0 and h2 > 0
                   else ("fragile" if (h1 > 0) != (h2 > 0) else "neg"))
            ev  = "  <<< +EV" if mn > 0 and rob == "ROBUST" else ""
            print(f"  LB={lb}d hold={hold}d [reversal]  "
                  f"n={n:>4} win {w/n*100:>3.0f}%  mean {mn:>+6.2f}%  "
                  f"OOS {h1:>+5.2f}/{h2:>+5.2f}  {rob}{ev}")

            # also report momentum direction for symmetry
            n2  = len(mom)
            w2  = sum(1 for r in mom if r > 0)
            h1m = statistics.mean(mom[:n2//2]) * 100 if n2//2 else 0.0
            h2m = statistics.mean(mom[n2//2:]) * 100 if (n2-n2//2) else 0.0
            mn2 = statistics.mean(mom) * 100
            rob2 = ("ROBUST" if h1m > 0 and h2m > 0
                    else ("fragile" if (h1m > 0) != (h2m > 0) else "neg"))
            ev2 = "  <<< +EV" if mn2 > 0 and rob2 == "ROBUST" else ""
            print(f"  LB={lb}d hold={hold}d [momentum]  "
                  f"n={n2:>4} win {w2/n2*100:>3.0f}%  mean {mn2:>+6.2f}%  "
                  f"OOS {h1m:>+5.2f}/{h2m:>+5.2f}  {rob2}{ev2}")

            if corr_val is not None:
                tag = "*** LIKELY MOMENTUM DISGUISE ***" if abs(corr_val) > 0.6 else ""
                print(f"    corr(reversal_stream, mom_ref_LB7): {corr_val:+.3f} {tag}")
        print()

    # ─────────────────────────────────────────────────────────────────────────
    print("─" * 90)
    print("(C) DISTANCE-FROM-MA CROSS-SECTIONAL REVERSION  [MA 20/50/200]")
    print("    LONG most-below-MA / SHORT most-above-MA")
    print("─" * 90)

    for ma_n in (20, 50, 200):
        for hold in HOLDS:
            rev, cont = run_ma_reversal(data, ma_n, hold)
            if not rev:
                print(f"  MA={ma_n} hold={hold}d — insufficient data")
                continue

            n_corr = min(len(rev), len(mom_ref))
            rev_aligned = rev[-n_corr:]
            ref_aligned = mom_ref[-n_corr:]

            corr_val = None
            if n_corr > 10:
                mx, my = statistics.mean(ref_aligned), statistics.mean(rev_aligned)
                num  = sum((a - mx)*(b - my) for a, b in zip(ref_aligned, rev_aligned))
                dxd  = (sum((a-mx)**2 for a in ref_aligned) *
                        sum((b-my)**2 for b in rev_aligned)) ** 0.5
                corr_val = num / dxd if dxd > 0 else 0.0

            for label, arr in [("reversion (long below / short above)", rev),
                                ("continuation (long above / short below)", cont)]:
                n   = len(arr)
                w   = sum(1 for r in arr if r > 0)
                mid = n // 2
                h1  = statistics.mean(arr[:mid]) * 100 if mid else 0.0
                h2  = statistics.mean(arr[mid:]) * 100 if (n-mid) else 0.0
                mn  = statistics.mean(arr) * 100
                rob = ("ROBUST" if h1 > 0 and h2 > 0
                       else ("fragile" if (h1 > 0) != (h2 > 0) else "neg"))
                ev  = "  <<< +EV" if mn > 0 and rob == "ROBUST" else ""
                print(f"  MA={ma_n:>3} hold={hold}d [{label}]  "
                      f"n={n:>4} win {w/n*100:>3.0f}%  mean {mn:>+6.2f}%  "
                      f"OOS {h1:>+5.2f}/{h2:>+5.2f}  {rob}{ev}")

            if corr_val is not None:
                tag = "*** LIKELY MOMENTUM DISGUISE ***" if abs(corr_val) > 0.6 else ""
                print(f"    corr(reversion_stream, mom_ref_LB7): {corr_val:+.3f} {tag}")
        print()

    # ─────────────────────────────────────────────────────────────────────────
    print("=" * 90)
    print("VERDICT SUMMARY")
    print("=" * 90)
    print("""
Methodology bar (ALL required for ROBUST verdict):
  (1) mean net% > 0 AFTER 10bps/leg cost
  (2) OOS h1 > 0 AND h2 > 0 (both halves of the trade stream)
  (3) lookahead-safe (signal from close[t], enter t+1 open)
  (4) cost-aware (10bps/leg = 20bps round-trip on the spread)
  (5) survivorship-free (whole top-50 liquid universe)

OVERLAP FLAG: reversal direction with |corr_to_momentum| > 0.6 is likely MOMENTUM IN DISGUISE
  (residualizing → NEGATIVE corr to BTC stream, but can still be positive corr to the L/S spread).
""")


if __name__ == "__main__":
    main()
