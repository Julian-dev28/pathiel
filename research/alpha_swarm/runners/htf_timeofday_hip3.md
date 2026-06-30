# Fixed-time daily LONG on HIP-3 tokenized stocks (operator 22:00 UTC idea)

Test of the operator's hypothesis: open a LONG at a fixed UTC hour each day on
xyz:* tokenized stocks/commodities (especially xyz:SPCX = SpaceX), betting on a
post-US-close daily drift. 22:00 UTC sits ~1h after the US cash close (21:00 UTC).

Scripts: `runners/build_hip3_1h.py` (fetch), `runners/analyze_hip3_tod.py` (test).
Data: `scratchpad/hip3_1h.json` — 1h candles, fetched 2026-06-30.

## VERDICT (one line)

NO. There is no tradeable fixed-time daily long on HIP-3 stocks at 22:00 UTC or
the 20-23 US-close window. Pooled across 67 xyz stocks the US-close window is among
the WORST entry hours for the holds that matter (24h, 16h) — negative excess in
BOTH out-of-sample halves — and NET return is negative at every hour of the day
after even 12bps cost. SPCX alone is too short (44 days, ~31 trades/hour) and too
noisy to support any single-coin claim, and 22:00 is not its standout hour. The
post-close-drift premise fails at the root: these perps trade 24/7, so there is no
real session "close" to drift away from.

## Data / sample (be honest)

- 97 xyz coins in the universe; 80 returned candles, 17 empty (unlisted/dead:
  VIX, WHEAT, URANIUM, NIFTY, KRW, etc.).
- Only **22 coins have >=180 days** of 1h history (NVDA, TSLA, MSTR, MSFT, META,
  PLTR, INTC, ORCL, NFLX, XYZ100, SILVER, MU, SNDK, JPY...). Most are weeks old.
- **xyz:SPCX = 44 days / 1050 bars only.** Per-hour SPCX sample is n=30-44. That is
  noise-grade for a single-coin time-of-day claim.
- Pooled tables use 67 coins with >=30 days and >=480 valid trades. Survivor caveat
  applies (universe is current-listed only; deads excluded).

## Session structure (kills the premise)

The README claims "HIP-3 equity perps only trade during US equity hours" — that is
**outdated**. The xyz perps trade **24/7, including weekends**, with real (lower)
off-hours volume. Bar coverage is ~equal across all 24 hours and all 7 weekdays.
Volume peaks 13:00-15:00 UTC (US cash session) and is lowest 02:00-05:00 UTC, but
never zero. So a "post-close" long is just a long into a market that never closes —
there is no overnight gap to capture. (Main tables filter to weekday entries anyway;
including weekends does not change the conclusion.)

## Hour-of-day EV, POOLED (67 xyz stocks, weekday-only, 12bps net cost)

`excess` = return minus each coin's OWN grand-mean (controls for coin drift = the
null). `net` = actual tradeable long return after cost. `OOS` = sign agreement across
the two date halves. A real edge needs positive excess, BOTH+, and positive net.

24h HOLD (the natural "daily" hold):

| Window | best hours | US-close 20-23 |
|---|---|---|
| excess | 00-09 UTC all BOTH+, peak ~+8.5bps (H00) | H20-23 all **negative**, both halves neg (~-4 to -5bps) |
| net (tradeable) | best is H00 = **-0.7bps** (still <0) | ~-13 to -14bps |
| 22:00 specifically | — | excess **-4.5bps**, net -13.7bps, OOS h0 -3.7 / h1 -5.3 (both neg) |

Every hour's NET return is negative — the xyz basket net-drifted down over the
window, so a long-only fixed-time entry loses at every hour. The "best excess" hours
(overnight 00-09 UTC) are the LEAST bad, not profitable.

16h HOLD (22:00 entry -> 14:00 US-open exit, the operator's exact thesis):
H22 is the **single worst hour**: excess -14.2bps, net -22.5bps, both halves negative
(h0 -1.5 / h1 -27.1). The "hold post-close to US open" trade is the worst on the board.

4h HOLD: H19-H21 are the only US-evening hours with positive excess BOTH+ (H20 +4.8,
H21 +3.2) but H22 is mixed and H23 is strongly negative (-10.6, both-). Net is still
negative even at the best hour (H19 net -1.0bps). So 20:00-21:00 is mildly better than
random, 22:00 is not, and none clears cost.

1h HOLD: H20/H21 marginally positive excess (BOTH+) but net -8.5 / -9.3bps. Dead.

## US-close window, cost sensitivity (pooled, weekday-only)

| hour | 12bps net | 25bps net | excess | OOS halves |
|---|---|---|---|---|
| H20 | -14.4bps | -27.4bps | -5.2 | -4.1 / -6.3 (both neg) |
| H21 | -13.1bps | -26.1bps | -3.9 | -3.2 / -4.6 (both neg) |
| H22 | -13.7bps | -26.7bps | -4.5 | -3.7 / -5.3 (both neg) |
| H23 | -13.4bps | -26.4bps | -4.2 | -4.5 / -3.9 (both neg) |

Negative excess in both halves at 12bps; collapses further at 25bps (HIP-3 is thin).

## xyz:SPCX specifically (SpaceX, n=30-44/hour)

- 24h hold: the two OOS halves are violently split (e.g. H22 h0 -54bps / h1 +43bps) —
  SPCX had a drift/regime flip between halves, every hour reads "mixed". No stable
  time-of-day effect. The operator's 22:00 is not a standout (24h excess -5.8bps weekday).
- 4h / 16h hold: a 20:00-21:00 cluster shows positive net excess (4h: H20/H21 ~+44bps
  net, BOTH+), but n=31 per hour — noise, and again 22:00 is weaker/mixed, 23:00 negative.
- Verdict for SPCX: too short and too small-sample to trade. If anything tempting, it is
  20:00-21:00 (not 22:00), on ~6 weeks of data — not actionable.

## Why 22:00 fails

The perp is 24/7, so the operator's "post-close pop" has no gap to feed on. The only
non-random structure is a weak market-neutral tilt (xyz basket tends to firm overnight
00-09 UTC and bleed through the US session 12-21 UTC), but that is (a) a long/short
relative tilt, not the requested fixed-time LONG, and (b) net-negative on the long leg
after cost. The requested trade — fixed LONG at 22:00 UTC — is negative-excess and
negative-net across every hold, OOS-consistent in being bad. Refuted.

## If anything is worth a follow-up (not this idea)

The overnight-firm / US-session-bleed tilt is the only signal with consistent sign
(BOTH+ excess at 00-09 for 24h hold). It is market-neutral-shaped, not a fixed-time
long, and would need a long-overnight / short-US-session relative book to test —
separate hypothesis, and still has to clear cost which it currently does not.
