"""HIP-3 liquidity-floor EV test. READ-ONLY, cache-only.
Tests momentum/breakout longs on HIP-3 coins per band, gross + net of band slippage,
slippage-mult sweep {0.5,1.0,1.5}, OOS both halves. Min 60 1d-bars to be usable.
"""
import statistics
import liquidity_lib as L

d = L.load()
T, O, H, Lo, C, V = 0, 1, 2, 3, 4, 5
BANDS = ['0.1-0.7M', '0.7-2M', '2-5M', '5-20M', '20-50M', '50M+']
MIN_BARS = 60


def sma(xs):
    return sum(xs) / len(xs) if xs else 0.0


def breakout_trades(cd, lookback=20, vol_mult=1.5, hold=5, trail=0.08):
    """New `lookback`-bar high + volume burst -> long. Fill at next bar open.
    Exit: trailing stop `trail` OR after `hold` bars, whichever first. Lookahead-safe."""
    trades = []
    n = len(cd)
    i = lookback
    while i < n - 1:
        window_h = max(float(cd[j][H]) for j in range(i - lookback, i))
        avgv = sma([float(cd[j][V]) for j in range(i - lookback, i)])
        close_i = float(cd[i][C]); vol_i = float(cd[i][V])
        if close_i > window_h and avgv > 0 and vol_i > vol_mult * avgv:
            entry = float(cd[i + 1][O])
            if entry <= 0:
                i += 1; continue
            peak = entry; exit_px = None
            end = min(i + 1 + hold, n)
            for k in range(i + 1, end):
                hk = float(cd[k][H]); lk = float(cd[k][Lo]); ck = float(cd[k][C])
                peak = max(peak, hk)
                if lk <= peak * (1 - trail):
                    exit_px = peak * (1 - trail); break
                exit_px = ck
            if exit_px is None:
                i += 1; continue
            trades.append({'t': cd[i + 1][T], 'ret': L.pct(entry, exit_px)})
            i = end  # no overlap
        else:
            i += 1
    return trades


def trend_trades(cd, fast=10, slow=30, trail=0.10, max_hold=20):
    """Trailing momentum: enter long when close > SMA(slow) and SMA(fast)>SMA(slow) (cross up).
    Fill next bar open. Exit on trailing stop or close<SMA(slow) or max_hold. Lookahead-safe."""
    trades = []
    n = len(cd)
    if n < slow + 2:
        return trades
    in_pos = False; i = slow
    while i < n - 1:
        closes = [float(cd[j][C]) for j in range(0, i + 1)]
        sf = sma(closes[-fast:]); ss = sma(closes[-slow:])
        sf_p = sma([float(cd[j][C]) for j in range(0, i)][-fast:])
        ss_p = sma([float(cd[j][C]) for j in range(0, i)][-slow:])
        crossed_up = sf > ss and not (sf_p > ss_p)
        if crossed_up and closes[-1] > ss:
            entry = float(cd[i + 1][O])
            if entry <= 0:
                i += 1; continue
            peak = entry; exit_px = None
            end = min(i + 1 + max_hold, n)
            for k in range(i + 1, end):
                hk = float(cd[k][H]); lk = float(cd[k][Lo]); ck = float(cd[k][C])
                peak = max(peak, hk)
                ss_k = sma([float(cd[j][C]) for j in range(0, k + 1)][-slow:])
                if lk <= peak * (1 - trail):
                    exit_px = peak * (1 - trail); break
                if ck < ss_k:
                    exit_px = ck; break
                exit_px = ck
            if exit_px is None:
                i += 1; continue
            trades.append({'t': cd[i + 1][T], 'ret': L.pct(entry, exit_px)})
            i = end
        else:
            i += 1
    return trades


def report(name, fn):
    print(f'\n===== STRATEGY: {name} =====')
    print(f'{"band":10s} {"coins":5s} {"nTrd":5s} {"gross%":7s} '
          f'{"net0.5x":8s} {"net1.0x":8s} {"net1.5x":8s} {"win":5s} {"h1":7s} {"h2":7s}')
    for b in BANDS:
        hip = [c for c in L.coins_in_band(d, b, hip3_only=True)
               if len(L.candles(d, c, '1d')) >= MIN_BARS]
        all_tr = []
        for c in hip:
            cd = L.candles(d, c, '1d')
            slip = L.band_slippage_bps(L.vol(d, c)) / 10000.0  # this coin's band spread (1x)
            for tr in fn(cd):
                tr['coin'] = c; tr['slip1x'] = slip
                all_tr.append(tr)
        if not all_tr:
            print(f'{b:10s} {len(hip):5d} {0:5d}  (no trades)')
            continue
        all_tr.sort(key=lambda x: x['t'])
        rets = [t['ret'] for t in all_tr]
        gross = 100 * statistics.mean(rets)
        def net_ev(mult):
            net = [t['ret'] - t['slip1x'] * mult for t in all_tr]
            return 100 * statistics.mean(net)
        # OOS halves at 1.0x
        net1 = [t['ret'] - t['slip1x'] for t in all_tr]
        half = len(net1) // 2
        h1 = 100 * statistics.mean(net1[:half]) if half else 0
        h2 = 100 * statistics.mean(net1[half:]) if len(net1) - half else 0
        win = sum(1 for x in net1 if x > 0) / len(net1)
        print(f'{b:10s} {len(hip):5d} {len(all_tr):5d} {gross:7.3f} '
              f'{net_ev(0.5):8.3f} {net_ev(1.0):8.3f} {net_ev(1.5):8.3f} '
              f'{win:5.2f} {h1:7.3f} {h2:7.3f}')


report('20-bar breakout + vol burst (hold5, trail8%)', breakout_trades)
report('trend-cross trailing momentum (10/30, trail10%)', trend_trades)
