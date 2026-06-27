# C9 engulfing_reversal_xs — ROBUST +EV (modest, surprising) — NOT refuted

## Hypothesis
Engulfing candles carry directional info: bullish engulfing → long, bearish → short, as a
cross-sectional signed signal. (The item expected "almost certainly refuted.")

## Exact rule
Daily. At bar i (decided on close): bullish engulf = green bar whose body fully engulfs the
prior RED body (o≤prev_close, c≥prev_open); bearish = mirror. side = +long/−short. Fill i+1
open, hold horizon {1,3,5}d, stop sweep {8..40}%. Per-coin signed trade ret = side·fwd_ret
(market-neutral read = mean of signed returns). `alpha_lib.summarize` + THREE nulls via
`mc_null`: random-side, directional (enter in any bar's color), bigbar (range-expansion
same-color continuation — the strong control isolating the engulf condition).

## Results
| hz | stop | n | EV@12 | EV@25 | EV@50 | win | h1 | h2 |
|--|--|--|--|--|--|--|--|--|
| **1** | **.08** | 1476 | **+0.78%** | **+0.65%** | **+0.40%** | .511 | **+0.79** | **+0.77** |
| 1 | .25 | 1476 | +0.68% | +0.55% | +0.30% | .520 | +0.60 | +0.76 |
| 1 | .40 | 1476 | +0.68% | +0.55% | +0.30% | .520 | +0.57 | +0.78 |
| 3 | .08 | 1221 | +1.03% | +0.90% | +0.65% | .481 | +1.58 | +0.48 |
| 5 | .08 | 1009 | +1.24% | +1.11% | +0.86% | .462 | +1.75 | +0.73 |

- **All 5 hz=1 stop cells are both-halves positive** (the cleanest family; longer horizons
  have stronger h1 but weaker h2 — h1/h2 imbalance, so hz=1 is the robust operating point).

**MC nulls on the best cell (hz=1, stop=8%), obs_mean = +0.90%/trade:**
| null | null_mean | excess | z | p |
|--|--|--|--|--|
| random-side | +0.30% | **+0.60%** | 3.34 | **0.0006** |
| directional (any bar's color) | +0.29% | **+0.61%** | 3.31 | **0.0004** |
| bigbar (range-expansion continuation) | +0.04% | **+0.86%** | 4.63 | **0.0002** |

- The bigbar control is decisive: "enter in the direction of a range-expansion bar" is ~flat
  (+0.04%). The full ENGULF condition (range expansion + complete engulf of the opposite-color
  prior body) is what adds the +0.86% — so this is NOT generic candle-color or big-bar momentum.

## VERDICT
**ROBUST +EV (modest).** Deciding number: **excess +0.6% to +0.86%/trade over three
matched nulls (p ≤ 0.0006), both OOS halves positive (+0.79/+0.77), survives to +0.40% at
50bps, and the entire hz=1 stop family is positive.** It beats the strict bigbar null, so the
engulf pattern itself — not the tape, not generic momentum — carries the edge. Surprising:
the item expected a refute; it survived every gate.

**Params:** daily, bullish-engulf→long / bearish-engulf→short, fill next open, hold 1 day,
stop 8–40% (insensitive). Cross-sectional signed basket (long bullish set / short bearish set).

**Biggest risks:** (1) per-trade edge is modest (~+0.6% gross, win 51%) — transaction-cost
discipline is essential; at >50bps all-in it erodes. (2) Survivorship: signed/market-neutral
framing dampens it but positive EV is still an upper bound. (3) It's a candle pattern — prior
on these is low, so demand forward shadow confirmation before sizing up.

**Shadow proposal:** log a daily cross-sectional engulfing book (long bullish-engulf coins /
short bearish-engulf coins, 1-day hold, equal-weight), small size, forward-validate the
excess vs the bigbar null on live data.
