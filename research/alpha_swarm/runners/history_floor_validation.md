# history floor: 60-hourly vs 60-daily — REFUTED (keep the 60-daily floor)

Q (operator, 2026-06-29): the 60-DAILY history floor blocks fresh HIP-3 names (KIOXIA=4d) for ~2 months.
Would a 60-HOURLY-bar floor (~2.5d) safely admit them sooner without trusting garbage 5-bar TA?

Test: every xyz HIP-3 breakout-momentum LONG signal (new 24h high + green + 1.5x vol), forward 24h, net
12bps, bucketed by coin AGE-at-signal. 96 coins, hourly bars (hist_floor_validate.py). Lookahead-safe.

  age bucket     n     meanRet   OOS h1/h2      verdict
  <2.5d          54    -0.99%    -0.36/-1.62    -EV both   (garbage zone — both floors block)
  2.5-15d        476   -0.27%    -0.52/-0.02    -EV both   (the band a 60-HOURLY floor would ADMIT)
  15-30d         576   +0.52%    +0.13/+0.91    +EV both   (but not corroborated below = noise)
  30-60d         945   -0.18%    +0.13/-0.49    mixed
  60-120d        1559  +0.01%    +0.04/-0.02    mixed
  120d+          1422  +0.14%    +0.40/-0.13    mixed

VERDICT: switching to a 60-hourly floor is NOT supported. It would admit the 2.5-15d band, which is -EV
both halves (-0.27%). No coherent "older=better" edge — buckets zigzag (15-30d +0.52 then 30-60d -0.18),
i.e. noise not an age threshold. Young HIP-3 names have NO tradeable edge until well past the hourly cutoff;
even mature bands are only noisy breakeven (consistent with [[project_price_entries_no_edge]] — HIP-3 price
entries are marginal). The intuition that the floor blocks missed alpha is REFUTED: the blocked names are
-EV, not missed runners. KEEP min_history_bars=60 (daily). Could relax to ~15d with ~no EV harm, but ~no EV
benefit either (just more breakeven churn + fees) — not worth touching. Survivor universe = upper bound.
