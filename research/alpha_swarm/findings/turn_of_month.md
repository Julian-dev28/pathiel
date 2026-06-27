# B14 turn_of_month

## Hypothesis
A turn-of-month / first-N-days effect (institutional flows) makes the market outperform around the
month boundary.

## Exact rule
- day-of-month (UTC) of the realizing bar. ToM window = days {28,29,30,31,1,2,3}.
- Compare ToM market mean vs rest; long-market on ToM days, OOS both halves via summarize.
- Multiple-comparison null: 2000 random contiguous 7-day-of-month windows, empirical p = frac >= ToM.

## Results
- ToM mean -0.096% (n=60) vs rest-of-month -0.155% (n=240) — both negative (-44% tape).
- ToM long-market: EV12 **-0.216%**, win 0.517, h1 -0.064 / h2 -0.379 -> SIGN-FLIP.
- Null: **1024/2000** random windows beat ToM -> empirical **p=0.512** (the ToM window is at the
  median of random windows).

## VERDICT: REFUTED
Deciding number: empirical **p=0.512** — the turn-of-month window is indistinguishable from a random
day-of-month window, and the ToM long-market trade is itself -EV (-0.22%) with a sign-flip across
halves. No tradeable calendar effect; the small ToM/rest gap is the kind of noise the multiple-
comparison gate is designed to kill (same outcome as seasonality's hard gate). 9-month sample is also
too short for a monthly-frequency claim (~9-10 ToM windows).
