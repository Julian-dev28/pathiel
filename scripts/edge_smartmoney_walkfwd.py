#!/usr/bin/env python3
"""Smart-money ZERO-OVERLAP walk-forward — kills survivorship two ways:
  1. Pool selected by ACCOUNT VALUE (size), not recent performance -> no forward bias
     in who we look at.
  2. "Skilled" = positive realized PnL strictly BEFORE a cutoff T; we then test only
     their OPEN entries strictly AFTER T. Selection and test never overlap.
If their post-T entries still predict forward returns, the copy edge is real.
"""
import statistics
import time
import httpx
from hermes_trader.client.hl_client import fetch_hl_candles
from hermes_trader.indicators.math import candle_val

POOL = 120
SPLIT_DAYS = 14
MIN_ACCT = 50_000
COST = 8.0 / 1e4
H = 12


def _post(body, retries=4):
    for _ in range(retries):
        try:
            r = httpx.post("https://api.hyperliquid.xyz/info", json=body, timeout=15)
            if r.status_code == 200:
                return r.json() or []
        except Exception:
            pass
        time.sleep(1.2)
    return []


def main():
    rows = httpx.get("https://stats-data.hyperliquid.xyz/Mainnet/leaderboard",
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=90).json()["leaderboardRows"]
    # pool by ACCOUNT VALUE (size), not performance
    pool = []
    for x in rows:
        try:
            av = float(x.get("accountValue") or 0)
            wp = dict(x.get("windowPerformances") or [])
            vlm = float((wp.get("month") or {}).get("vlm") or 0)
            if av >= MIN_ACCT and vlm >= 1_000_000:
                pool.append((av, x["ethAddress"]))
        except Exception:
            pass
    pool.sort(reverse=True)
    addrs = [a for _, a in pool[:POOL]]
    T = int((time.time() - SPLIT_DAYS * 86400) * 1000)
    print(f"# pool {len(addrs)} by account-size | cutoff T = now-{SPLIT_DAYS}d | H={H}h | cost {COST*1e4:.0f}bps")

    skilled, entries = 0, []
    for addr in addrs:
        fl = _post({"type": "userFills", "user": addr})
        if len(fl) < 40:
            continue
        pre = [f for f in fl if int(f.get("time", 0)) < T]
        post = [f for f in fl if int(f.get("time", 0)) >= T]
        if len(pre) < 20 or len(post) < 5:
            continue
        if sum(float(f.get("closedPnl") or 0) for f in pre) <= 0:   # not skilled BEFORE T
            continue
        skilled += 1
        for f in post:
            d = (f.get("dir") or "")
            if "Open Long" in d:
                entries.append((f["coin"], int(f["time"]), +1))
            elif "Open Short" in d:
                entries.append((f["coin"], int(f["time"]), -1))
    print(f"# skilled-before-T: {skilled}/{len(addrs)} | post-T entries to test: {len(entries)}")
    if not entries:
        print("no entries"); return

    cache = {}
    for c in sorted({c for c, _, _ in entries}):
        try:
            cd = fetch_hl_candles(c, "1h", 800)
            if cd:
                cache[c] = {int(x.t) // 3_600_000: candle_val(x, "c") for x in cd}
        except Exception:
            pass

    rets = []
    for coin, t_ms, sgn in entries:
        cc = cache.get(coin)
        if not cc:
            continue
        h0 = t_ms // 3_600_000
        if h0 in cc and (h0 + H) in cc and cc[h0] > 0:
            rets.append((cc[h0 + H] / cc[h0] - 1) * sgn - COST)
    if not rets:
        print("no measurable entries"); return
    half = len(rets) // 2
    win = sum(1 for r in rets if r > 0) / len(rets) * 100
    print(f"# CLEAN copy edge (post-T only, signed {H}h, net cost): "
          f"{statistics.mean(rets)*100:+.3f}%/trade over {len(rets)} | win {win:.0f}%")
    print(f"#   post-T 1st-half {statistics.mean(rets[:half])*100:+.3f}% | 2nd-half {statistics.mean(rets[half:])*100:+.3f}%")


if __name__ == "__main__":
    main()
