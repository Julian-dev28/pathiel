"""Orthogonality check: is the D5 premium-fade short a NEW trigger, or does it fire
on the same coin-days as the live rally_exhaustion / crash_continue_div_short books?

Decision metric = event-set overlap. If most premium-fade events coincide (same coin,
within +/-2 days) with an existing-book event, it's redundant. If most are distinct,
it's new capacity worth a shadow book.

Live book triggers (from the live modules):
  rally_exhaustion:          2d return >= +12%  AND BTC-down (close < 20d-ago close)
  crash_continue_div_short:  2d return <= -8%   AND BTC-up   (close > 20d-ago close)
  premium_fade (D5):         trailing-24h mean premium z >= +2.0 vs own 30d daily-premium dist
"""
from __future__ import annotations
import statistics
import alpha_lib as A
import funding_lib as F

DAY_MS = 86_400_000
d = A.load_dataset()
fd = F.load_funding()
coins = [c for c in d["coins"] if c != "BTC"]

# BTC daily regime series: map day-floor -> up?(close > close 20 bars ago)
btc = A.candles(d, "BTC", "1d")
btc_close = {b[A.T] // DAY_MS: b[A.C] for b in btc}
btc_days = sorted(btc_close)
def btc_up_on(day):
    if day not in btc_close:
        # nearest prior
        prior = [x for x in btc_days if x <= day]
        if not prior: return None
        day = prior[-1]
    idx = btc_days.index(day)
    if idx < 20: return None
    return btc_close[day] > btc_close[btc_days[idx - 20]]

def daily_premium_series(coin):
    """day-floor -> mean hourly premium that day."""
    buckets = {}
    for t, rate, prem in F.rows(fd, coin):
        buckets.setdefault(t // DAY_MS, []).append(prem)
    return {day: statistics.mean(v) for day, v in buckets.items() if v}

rally_ev, crash_ev, prem_ev = set(), set(), set()

for coin in coins:
    bars = A.candles(d, coin, "1d")
    if len(bars) < 35:
        continue
    closes = {b[A.T] // DAY_MS: b[A.C] for b in bars}
    days = sorted(closes)
    # price-based events (rally_exhaustion / crash_continue), lookahead-safe: decided on completed bar
    for i in range(2, len(days)):
        day = days[i]
        c0, c2 = closes[days[i - 2]], closes[day]
        if c0 <= 0: continue
        ret2 = c2 / c0 - 1.0
        up = btc_up_on(day)
        if up is None: continue
        if ret2 >= 0.12 and up is False:
            rally_ev.add((coin, day))
        if ret2 <= -0.08 and up is True:
            crash_ev.add((coin, day))
    # premium-z events (D5)
    dp = daily_premium_series(coin)
    pdays = sorted(dp)
    for i in range(30, len(pdays)):
        day = pdays[i]
        hist = [dp[pdays[j]] for j in range(i - 30, i)]
        mu, sd = statistics.mean(hist), statistics.pstdev(hist)
        if sd <= 0: continue
        z = (dp[day] - mu) / sd
        if z >= 2.0:
            prem_ev.add((coin, day))

def overlaps(ev, others, tol_days=2):
    """fraction of `ev` events that have an `others` event for the same coin within tol days."""
    hit = 0
    for coin, day in ev:
        for c2, d2 in others:
            if c2 == coin and abs(d2 - day) <= tol_days:
                hit += 1
                break
    return hit, len(ev)

existing = rally_ev | crash_ev
hit, n = overlaps(prem_ev, existing)
hit_rally, _ = overlaps(prem_ev, rally_ev)
hit_crash, _ = overlaps(prem_ev, crash_ev)

print(f"event counts: premium_fade={len(prem_ev)}  rally_exhaustion={len(rally_ev)}  crash_continue={len(crash_ev)}")
print(f"premium events overlapping ANY existing-book event (+/-2d, same coin): {hit}/{n} = {100*hit/max(n,1):.0f}%")
print(f"  ... overlapping rally_exhaustion: {hit_rally}/{n} = {100*hit_rally/max(n,1):.0f}%")
print(f"  ... overlapping crash_continue:   {hit_crash}/{n} = {100*hit_crash/max(n,1):.0f}%")
print(f"DISTINCT premium events (new coin-days the existing books do NOT trade): {n-hit}/{n} = {100*(n-hit)/max(n,1):.0f}%")

# regime split of premium events: where do they fire vs the existing books' regime gates?
up_ev = sum(1 for c, day in prem_ev if btc_up_on(day) is True)
dn_ev = sum(1 for c, day in prem_ev if btc_up_on(day) is False)
print(f"premium events by BTC regime: up={up_ev}  down={dn_ev}  "
      f"(rally_exhaustion only fires in DOWN, crash_continue only in UP)")
