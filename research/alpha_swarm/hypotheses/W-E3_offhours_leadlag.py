#!/usr/bin/env python3
"""W-E3 — CLOSED-hours crypto -> xyz equity lead-lag.

Prior refute (findings/stock_crypto_leadlag.md) tested equity-ACTIVE bars only.
This is the complement: while the underlying is SHUT (weekday nights, weekends,
holidays) the only live price discovery for macro/risk is crypto. Does a BTC/ETH
1h (and 15m) move LEAD the xyz equity perp's NEXT bar, or is the co-move fully
contemporaneous (perp reprices instantly, nothing left to trade)?

Method:
  closed bar = 1h bar [t,t+1h) fully outside RTH (incl. weekends/holidays).
  corr tables: BTC[t] vs xyz[t] (contemp), BTC[t-1] vs xyz[t] (BTC leads),
  xyz[t-1] vs BTC[t] (xyz leads) — pooled + weekend/weekday-night split.
  Tradeable probe: during closed hours, |BTC 1h ret| >= thr {0.5%,1%} ->
  same-direction xyz at next bar open, hold {1,3}h. Basket per event hour
  (dedup), alpha_lib cost tiers, random-sign + pool nulls.
  15m variant on the 6 core names (fast reaction window).
"""
from __future__ import annotations
import random, statistics, sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import importlib
we = importlib.import_module("W-E_lib")
import alpha_lib
import mc_null

T, O, C = we.T, we.O, we.C


def is_closed_bar(t_ms: int, iv_ms: int) -> bool:
    day = date.fromtimestamp(t_ms / 1000)
    if not we.is_trading_day(day):
        return True
    o_, c_ = we.rth_utc(day)
    return t_ms + iv_ms <= o_ or t_ms >= c_


def rets_by_t(bars: list, iv_ms: int) -> dict[int, float]:
    out = {}
    idx = {int(b[T]): b for b in bars}
    for b in bars:
        t = int(b[T])
        prev = idx.get(t - iv_ms)
        if prev and float(prev[C]):
            out[t] = (float(b[C]) - float(prev[C])) / float(prev[C])
    return out


def corr(pairs):
    if len(pairs) < 30:
        return None, len(pairs)
    xs = [p[0] for p in pairs]; ys = [p[1] for p in pairs]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sx, sy = statistics.pstdev(xs), statistics.pstdev(ys)
    if sx == 0 or sy == 0:
        return None, len(pairs)
    return sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / (len(xs) * sx * sy), len(pairs)


def leadlag_table(d, iv_key, iv_ms, coins):
    btc = rets_by_t(d[iv_key]["BTC"], iv_ms)
    rows = {"contemp": [], "btc_leads1": [], "btc_leads2": [], "xyz_leads1": []}
    rows_we = {"contemp": [], "btc_leads1": []}
    rows_wn = {"contemp": [], "btc_leads1": []}
    for coin in coins:
        xr = rets_by_t(d[iv_key].get(coin, []), iv_ms)
        for t, r in xr.items():
            if not is_closed_bar(t, iv_ms):
                continue
            wknd = not we.is_trading_day(date.fromtimestamp(t / 1000))
            if t in btc:
                rows["contemp"].append((btc[t], r))
                (rows_we if wknd else rows_wn)["contemp"].append((btc[t], r))
            if t - iv_ms in btc:
                rows["btc_leads1"].append((btc[t - iv_ms], r))
                (rows_we if wknd else rows_wn)["btc_leads1"].append((btc[t - iv_ms], r))
            if t - 2 * iv_ms in btc:
                rows["btc_leads2"].append((btc[t - 2 * iv_ms], r))
            if t + iv_ms in btc:
                rows["xyz_leads1"].append((r, btc[t + iv_ms]))
    print(f"\n[{iv_key}] closed-hours lead-lag (pooled over {len(coins)} names):")
    for k, v in rows.items():
        c, n = corr(v)
        print(f"  {k:12s}: {c if c is None else format(c, '+.4f')}  (n={n})")
    for lbl, rr in (("weekend", rows_we), ("weekday-night", rows_wn)):
        c0, n0 = corr(rr["contemp"]); c1, n1 = corr(rr["btc_leads1"])
        print(f"  {lbl:14s} contemp {c0 if c0 is None else format(c0, '+.4f')} (n={n0})"
              f"  btc_leads1 {c1 if c1 is None else format(c1, '+.4f')} (n={n1})")


def random_sign_null(rets, n_iter=5000, seed=0):
    rng = random.Random(seed)
    obs_m = statistics.mean(rets)
    return sum(1 for _ in range(n_iter)
               if statistics.mean(r * (1 if rng.random() < 0.5 else -1) for r in rets) >= obs_m) / n_iter


def tradeable_probe(d, coins):
    btc_bars = d["candles_1h"]["BTC"]
    btc = rets_by_t(btc_bars, we.HOUR)
    xidx = {c: we.by_t(d["candles_1h"].get(c, [])) for c in coins}
    xret = {c: rets_by_t(d["candles_1h"].get(c, []), we.HOUR) for c in coins}
    for thr in (0.005, 0.01):
        for hold in (1, 3):
            trades = []
            pool = []
            for t, br in btc.items():
                fill_t = t + we.HOUR
                if not is_closed_bar(t, we.HOUR) or not is_closed_bar(fill_t, we.HOUR):
                    continue
                for cn in coins:
                    idx = xidx[cn]
                    entry_b = idx.get(fill_t)
                    exit_b = idx.get(fill_t + (hold - 1) * we.HOUR)
                    if not entry_b or not exit_b:
                        continue
                    r_long = (float(exit_b[C]) - float(entry_b[O])) / float(entry_b[O])
                    pool.append(r_long)
                    if abs(br) >= thr:
                        dirn = 1 if br > 0 else -1
                        trades.append({"t": fill_t, "ep": fill_t, "ret": dirn * r_long})
            basket = we.basket_by_key(trades)
            if len(basket) < 15:
                print(f"thr={thr*100:.1f}% hold={hold}h: {len(basket)} episodes — below n=15")
                continue
            s = alpha_lib.summarize(basket)
            p_sign = random_sign_null([b["ret"] for b in basket])
            print(f"\nFOLLOW-BTC closed-hours |btc1h|>={thr*100:.1f}% hold={hold}h  "
                  f"episodes={s['n']} (name-trades={len(trades)})  p_sign={p_sign:.4f}")
            print(we.fmt_summary(s))


def main():
    d = we.load()
    coins_1h = [c for c in d["coins"] if len(d["candles_1h"].get(c, [])) > 24 * 10]
    leadlag_table(d, "candles_1h", we.HOUR, coins_1h)
    core = [c for c in d["candles_15m"] if c.startswith("xyz:")]
    leadlag_table(d, "candles_15m", 900_000, core)
    print("\n── tradeable probe (1h, follow BTC next bar) ──")
    tradeable_probe(d, coins_1h)


if __name__ == "__main__":
    main()
