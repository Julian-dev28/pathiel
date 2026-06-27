# B10 garch_jump_detection

## Hypothesis
Classifying a daily bar as a JUMP via a vol-scaled threshold (|ret| > z·trailing-vol, not a fixed
%) isolates a tradeable post-jump drift or reversion, separable by up/down direction.

## Exact rule
- Per coin, daily. z = ret_i / pstdev(prior 20d returns, excl. bar i). JUMP if |z|>=3, FRESH
  (prior bar not itself a jump). up/down by sign. 132 up, 150 down events.
- 4 quadrants: {up,down} x {continue, fade}. Fill i+1 open, fwd=candles[i+1:]. Stop-width SWEEP
  {8,15,20,25,40}%, horizon 5; pick stop maximizing OOS-robust EV. Excess over matched random-side baseline.

## Results (@12bps, best stop per quadrant)
| quadrant | stop | n | EV% | win | h1 | h2 | excess | OOS |
|--|--|--|--|--|--|--|--|--|
| **up_fade (short)** | 25% | 132 | **+1.286** | 0.652 | 2.37 | 0.17 | **+2.56** | ROBUST both |
| down_fade (long) | 25% | 150 | +1.294 | 0.560 | 3.96 | -1.75 | +1.16 | sign-flip |
| up_continue (long) | 20% | 132 | -1.567 | 0.326 | -1.49 | -1.65 | -0.46 | negative |
| down_continue (short) | 40% | 150 | -1.626 | 0.433 | -3.98 | 1.06 | -2.46 | sign-flip |

## VERDICT: MARGINAL (confirmatory, no new alpha)
Deciding number: only **up_fade** (short after a vol-scaled up-jump) is OOS-robust — EV +1.286%, win
65%, **excess +2.56%** over random, both halves >0 (though h2 thin at +0.17%). This re-derives the
live `rally_exhaustion` / pump-fade edge through a vol-scaled trigger instead of a fixed +% threshold;
it does NOT add a new edge. The down-jump fade (the long side) is NOT robust here (h2 -1.75) — fixed
-12% crash_fade in extreme_surface was robust, so vol-scaling the down trigger HURTS that edge.
Continuation is negative both directions. Net: jump-classification offers no novel alpha; keep the
fixed-threshold extreme_fade/rally_exhaustion. Survivor-biased (deep up-jumps that died are absent).
