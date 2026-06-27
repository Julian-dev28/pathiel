# B6 adx_gated_momentum

## Hypothesis
Only taking trend/momentum entries when ADX>threshold (genuine trend present) cuts the dud rate
and improves EV on the live momentum entries.

## Exact rule
- Per coin, daily. Directional momentum: long if 14d return>0 else short.
- Gate: take entry only if Wilder ADX(14)[i] > thr (computed lookahead-safe, bars up to i).
- Fill i+1 open, fwd=candles[i+1:], 25% stop, 5d horizon. dud = trade with negative realized return.
- Sweep thr {0,20,25,30}. OOS+slippage via summarize.

## Results (@12bps)
| ADXthr | n | EV% | win | dud% | sharpe | h1 | h2 | OOS |
|--|--|--|--|--|--|--|--|--|
| 0 (ungated) | 9969 | 0.374 | 0.519 | 47.4 | 0.033 | 1.18 | -0.44 | sign-flip |
| 20 | 7467 | 0.450 | 0.522 | 47.1 | 0.039 | 1.36 | -0.47 | sign-flip |
| **25** | 5697 | **0.536** | 0.519 | 47.4 | 0.045 | 0.91 | 0.16 | ROBUST both |
| 30 | 4215 | 0.365 | 0.512 | 48.2 | 0.030 | 0.61 | 0.12 | ROBUST both |

## VERDICT: MARGINAL
Deciding numbers: ADX>25 lifts EV **+0.162%** (0.374->0.536) and converts ungated momentum (sign-flip
noise, h2 -0.44) into an OOS-robust series (h1 0.91, h2 **+0.16**). BUT the claimed mechanism is wrong:
the **dud rate does not move** (47.4% -> 47.4%). The benefit is regime selection (momentum's sign-flip
is filtered out, not its loss rate), and the second-half EV (+0.16%) is thin. Directional momentum in
a -44% tape carries a small short tailwind (no random baseline run here). Net: ADX>25 is a usable
robustness gate, not a dud-cutter; deploy claim should be "stabilizes momentum EV," not "fewer duds."
