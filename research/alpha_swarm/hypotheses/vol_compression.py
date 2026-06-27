"""vol_compression agent — volatility-compression breakout with stop-width sweep.

Hypothesis: when realized vol/range CONTRACTS into a coil (squeeze), the
range-expansion breakout on the next bar is directional — but only with a WIDE
stop (a tight DSL banks the squeeze and inverts the edge). Sweep stop width.

Lookahead-safe: squeeze + breakout decided on bars up to i, entry filled at i+1 open.
"""
from __future__ import annotations
import statistics
import alpha_lib as al
from alpha_lib import H, L, C, O, T

STOP_PCTS = [0.08, 0.15, 0.20, 0.25, 0.40]


def atr(bars, i, n=14):
    """ATR over the n bars ending at i (inclusive). Uses bars[i-n+1..i]."""
    if i < n:
        return None
    trs = []
    for j in range(i - n + 1, i + 1):
        h, l, pc = bars[j][H], bars[j][L], bars[j - 1][C]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    return statistics.mean(trs)


def build_trades(d, iv, lookback_n, horizon, tp_mult, regime_filter):
    """regime_filter: 'with', 'against', or None.
    Returns dict stop_pct -> list of trades [{t, ret}], plus meta counts."""
    # BTC daily regime: close vs 20d SMA
    btc = al.candles(d, "BTC", "1d")
    btc_sma = {}
    for i in range(len(btc)):
        if i >= 20:
            sma = statistics.mean(b[C] for b in btc[i - 19:i + 1])
            btc_sma[btc[i][T]] = 1 if btc[i][C] > sma else -1

    def btc_regime_at(ts_ms):
        # most recent daily bar at or before ts
        best = None
        for t, r in btc_sma.items():
            if t <= ts_ms:
                if best is None or t > best[0]:
                    best = (t, r)
        return best[1] if best else 0

    trades_by_stop = {sp: [] for sp in STOP_PCTS}
    n_signals = 0
    atr_n = 14
    dist_win = 50

    for coin in d["coins"]:
        bars = al.candles(d, coin, iv)
        if len(bars) < dist_win + atr_n + horizon + 5:
            continue
        # precompute ATR series
        atrs = [None] * len(bars)
        for i in range(len(bars)):
            atrs[i] = atr(bars, i, atr_n)

        for i in range(dist_win + atr_n, len(bars) - horizon - 2):
            cur_atr = atrs[i]
            if cur_atr is None:
                continue
            # squeeze: ATR(14) at i in bottom tercile of trailing dist_win ATRs
            window = [atrs[j] for j in range(i - dist_win, i) if atrs[j] is not None]
            if len(window) < dist_win // 2:
                continue
            window_sorted = sorted(window)
            tercile = window_sorted[len(window_sorted) // 3]
            if cur_atr > tercile:
                continue  # not compressed
            # breakout on bar i: close above N-bar prior high (long) / below low (short)
            prior_hi = max(bars[j][H] for j in range(i - lookback_n, i))
            prior_lo = min(bars[j][L] for j in range(i - lookback_n, i))
            side = None
            if bars[i][C] > prior_hi:
                side = "long"
            elif bars[i][C] < prior_lo:
                side = "short"
            if side is None:
                continue
            # entry at i+1 open
            entry_idx = i + 1
            if entry_idx + horizon >= len(bars):
                continue
            entry_px = bars[entry_idx][O]
            if entry_px <= 0:
                continue
            entry_ts = bars[entry_idx][T]

            # regime filter
            reg = btc_regime_at(entry_ts)
            aligned = (side == "long" and reg == 1) or (side == "short" and reg == -1)
            if regime_filter == "with" and not aligned:
                continue
            if regime_filter == "against" and aligned:
                continue

            n_signals += 1
            fwd = bars[entry_idx + 1:]
            tp_pct = None
            if tp_mult is not None:
                # tp set relative to a nominal stop = use 0.15 as base? Better: tp per-stop.
                pass
            # sweep stops; tp per-stop = tp_mult * stop width
            for sp in STOP_PCTS:
                tp = (tp_mult * sp) if tp_mult is not None else None
                res = al.sweep_stop(entry_px, side, fwd, [sp], horizon, tp_pct=tp)
                trades_by_stop[sp].append({"t": entry_ts, "ret": res[sp], "coin": coin, "side": side})

    return trades_by_stop, n_signals


def run_config(d, iv, lookback_n, horizon, tp_mult, regime_filter, label):
    tbs, nsig = build_trades(d, iv, lookback_n, horizon, tp_mult, regime_filter)
    print(f"\n=== {label} | iv={iv} N={lookback_n} H={horizon} tp_mult={tp_mult} regime={regime_filter} | signals={nsig} ===")
    print(f"{'stop':>6} {'n':>5} {'EV0bps':>9} {'EV12':>9} {'EV25':>9} {'EV50':>9} {'win':>6} {'h1':>9} {'h2':>9} {'verdict'}")
    rows = []
    for sp in STOP_PCTS:
        trades = tbs[sp]
        s = al.summarize(trades)
        if s.get("n", 0) == 0:
            continue
        oos = s["oos_12bps"]
        h1, h2 = oos["first_half_mean_pct"], oos["second_half_mean_pct"]
        verdict = "ROBUST" if (h1 and h2 and h1 > 0 and h2 > 0) else "flip/neg"
        print(f"{sp*100:>5.0f}% {s['n']:>5} {s['slip0']['mean_ret_pct']:>9} {s['slip12']['mean_ret_pct']:>9} "
              f"{s['slip25']['mean_ret_pct']:>9} {s['slip50']['mean_ret_pct']:>9} {s['slip12']['win_rate']:>6} "
              f"{str(h1):>9} {str(h2):>9} {verdict}")
        rows.append((sp, s, h1, h2))
    return rows


if __name__ == "__main__":
    d = al.load_dataset()

    # 1h timeframe: lookback 20, horizons 24/48/72
    for H_ in [24, 48, 72]:
        run_config(d, "1h", 20, H_, None, None, f"1h all-regime no-TP")
    # 1d timeframe: lookback 10, horizons 3/5/7
    for H_ in [3, 5, 7]:
        run_config(d, "1d", 10, H_, None, None, f"1d all-regime no-TP")

    # Regime split (1h H48, 1d H5)
    print("\n##### REGIME SPLIT #####")
    run_config(d, "1h", 20, 48, None, "with", "1h WITH btc regime")
    run_config(d, "1h", 20, 48, None, "against", "1h AGAINST btc regime")
    run_config(d, "1d", 10, 5, None, "with", "1d WITH btc regime")
    run_config(d, "1d", 10, 5, None, "against", "1d AGAINST btc regime")

    # With take-profit at 1.5x and 2x stop
    print("\n##### WITH TAKE-PROFIT #####")
    run_config(d, "1h", 20, 48, 1.5, None, "1h TP=1.5x stop")
    run_config(d, "1h", 20, 48, 2.0, None, "1h TP=2.0x stop")
    run_config(d, "1d", 10, 5, 1.5, None, "1d TP=1.5x stop")
    run_config(d, "1d", 10, 5, 2.0, None, "1d TP=2.0x stop")
