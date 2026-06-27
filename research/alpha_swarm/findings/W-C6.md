# W-C6 engulf_1h — REFUTED (daily-only; 1h is noise)

## Hypothesis
The engulf edge also exists on 1h candles (≫ samples → cleaner p), worth the extra fees.

## Rule
Same signed engulf, 1h candles, fill i+1 open. Short-leg + symmetric, horizon {1,4,12,24}
bars, stop {0.08,0.40}. Report net-of-25bps + OOS halves; MC excess only on any net-+ cell.

## Results (representative)
| leg | hz | n | EV@0 | EV@25 | win | h1 | h2 |
|--|--|--|--|--|--|--|--|
| SHORT | 1 | 5845 | +0.001 | −0.249 | .436 | −0.16 | −0.08 |
| SHORT | 4 | 5115 | −0.038 | −0.288 | .457 | −0.24 | −0.08 |
| SHORT | 24 | 2225 | −0.254 | −0.504 | .484 | −0.79 | +0.04 |
| SYM | 1 | 10696 | −0.004 | −0.254 | .426 | −0.12 | −0.13 |

- **Every cell is ~0 gross and strongly negative net**, win-rate <0.5, OOS both halves
  negative. No net-positive cell existed, so no MC excess was even computed.
- The huge n does NOT rescue it — there is simply no edge to find at 1h. The effect requires
  the daily bar.

## VERDICT
**REFUTED on 1h.** Deciding number: best 1h cell (short, hz=1) is **EV@0 = +0.001%**
(zero) and **−0.25% net of 25bps**, both OOS halves negative. The engulf edge is
**daily-only**; do NOT wire a 1h variant. (Reassuringly, this rules out a generic
microstructure artifact — the signal lives specifically on the daily timeframe.)
