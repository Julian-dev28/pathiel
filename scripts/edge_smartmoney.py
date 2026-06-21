#!/usr/bin/env python3
"""Smart-money copy edge. HL is transparent: leaderboard + per-address fills are public.
Hypothesis: traders who were skilled EARLY keep predicting forward returns — copy their
entries. Walk-forward to kill survivorship: rank each candidate by realized PnL in the
FIRST 60% of their fills, keep the skilled ones, then test ONLY their entries in the
LAST 40%. Forward return measured on the coin's own price over a fixed horizon
(objective, not their exit timing), signed by side, net of cost.
"""
import json
import statistics
import time
import httpx
from hermes_trader.client.hl_client import fetch_hl_candles
from hermes_trader.indicators.math import candle_val

POOL = 80           # candidate traders (top by month ROI, filtered)
MIN_ACCT = 100_000  # exclude tiny accounts
COST = 8.0 / 1e4    # liquid-coin round-trip (smart money trades the liquid book)
H = 12              # forward horizon (hours)


def leaderboard_pool():
    r = httpx.get("https://stats-data.hyperliquid.xyz/Mainnet/leaderboard",
                  headers={"User-Agent": "Mozilla/5.0"}, timeout=90)
    rows = r.json()["leaderboardRows"]
    cand = []
    for x in rows:
        try:
            av = float(x.get("accountValue") or 0)
            wp = dict(x.get("windowPerformances") or [])
            mo = wp.get("month") or {}
            roi = float(mo.get("roi") or 0); vlm = float(mo.get("vlm") or 0)
            if av >= MIN_ACCT and vlm >= 1_000_000 and roi > 0:
                cand.append((roi, x["ethAddress"]))
        except Exception:
            pass
    cand.sort(reverse=True)
    return [a for _, a in cand[:POOL]]


def fills(addr):
    for _ in range(4):                       # retry on 429/timeout
        try:
            r = httpx.post("https://api.hyperliquid.xyz/info", json={"type": "userFills", "user": addr}, timeout=20)
            if r.status_code == 200:
                return r.json() or []
        except Exception:
            pass
        time.sleep(1.5)
    return []


def main():
    pool = leaderboard_pool()
    print(f"# pool: {len(pool)} traders (month ROI>0, acct>${MIN_ACCT/1e3:.0f}k, vlm>$1M) | H={H}h | cost {COST*1e4:.0f}bps")
    test_entries = []   # (coin, time_ms, side_sign)
    skilled = 0
    for addr in pool:
        fl = fills(addr)
        if len(fl) < 40:
            continue
        fl = sorted(fl, key=lambda f: f.get("time", 0))
        split = int(len(fl) * 0.6)
        early_pnl = sum(float(f.get("closedPnl") or 0) for f in fl[:split])
        if early_pnl <= 0:                          # not skilled in-sample -> drop
            continue
        skilled += 1
        for f in fl[split:]:                        # out-of-sample entries only
            d = (f.get("dir") or "")
            if "Open Long" in d:
                test_entries.append((f["coin"], int(f["time"]), +1))
            elif "Open Short" in d:
                test_entries.append((f["coin"], int(f["time"]), -1))
    print(f"# skilled (early-PnL>0): {skilled}/{len(pool)} | OOS entries to test: {len(test_entries)}")
    if not test_entries:
        print("no entries"); return

    # per-coin candle cache (1h closes by hour bucket)
    coins = sorted({c for c, _, _ in test_entries})
    cache = {}
    for c in coins:
        try:
            cd = fetch_hl_candles(c, "1h", 1500)
            if cd:
                cache[c] = {int(x.t) // 3_600_000: candle_val(x, "c") for x in cd}
        except Exception:
            pass

    rets, longs, shorts = [], [], []
    for coin, t_ms, sgn in test_entries:
        cc = cache.get(coin)
        if not cc:
            continue
        h0 = t_ms // 3_600_000
        if h0 in cc and (h0 + H) in cc and cc[h0] > 0:
            r = (cc[h0 + H] / cc[h0] - 1) * sgn - COST   # copy return, net cost
            rets.append(r)
            (longs if sgn > 0 else shorts).append(r)
    if not rets:
        print("no measurable entries"); return
    half = len(rets) // 2
    win = sum(1 for r in rets if r > 0) / len(rets) * 100
    print(f"# COPY edge (signed forward {H}h, net cost): {statistics.mean(rets)*100:+.3f}%/trade over {len(rets)} | win {win:.0f}%")
    print(f"#   1st-half {statistics.mean(rets[:half])*100:+.3f}% | 2nd-half {statistics.mean(rets[half:])*100:+.3f}%")
    if longs:
        print(f"#   longs:  {statistics.mean(longs)*100:+.3f}%/trade ({len(longs)})")
    if shorts:
        print(f"#   shorts: {statistics.mean(shorts)*100:+.3f}%/trade ({len(shorts)})")


if __name__ == "__main__":
    main()
