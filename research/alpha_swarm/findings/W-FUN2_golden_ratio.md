# W-FUN2 — the golden ratio (phi): Fibonacci retracement + phi-day timing

**Question (operator, 2026-07-23):** phi is at least a real TA tool — does the 0.618/0.382
Fibonacci retracement, or phi-day timing, have an edge on crypto?

**Method (`hypotheses/W-FUN2_golden_ratio.py`):** ETH/BTC/SOL daily (~278d). CELL A: trailing
20d range position pos=(close-lo)/(hi-lo); go LONG in each fib zone, forward 3d return, fee
10bps, 2000x same-coin random-time null, OOS halves. CELL B: LONG on Fibonacci days-of-month
{1,2,3,5,8,13,21} vs the rest. Plus an OOS split-half test of "fit each coin's best bucket
on H1, trade it on H2."

## Results

CELL A — the fib zones are NOT special:
| coin | 0.382 (fib) | 0.618 (fib) | best bucket | notes |
|---|---|---|---|---|
| ETH | +0.47% ROBUST p=.018 | −2.37% REFUTED | 0.382 | the one fib hit |
| BTC | −0.30% REFUTED | +0.62% MARGINAL | 0.236 | different coin, different "level" |
| SOL | −0.66% REFUTED | −3.80% REFUTED | **0.500 (NOT fib)** ROBUST p=.011 | winner isn't Fibonacci |

CELL B — phi-day timing REFUTED on all 3 (ETH −1.24%, BTC −0.61%, SOL −1.84%), each ~equal
to its own non-fib-day control. No timing edge.

OOS "play the fitted level" test (H1-best bucket → H2 EV): ETH 0.236 (+0.50%→−0.00%, FAILS),
BTC 0.618 (−0.03%→+1.15%, fake: H1 was negative), SOL 0.500 (+0.79%→+1.82%, holds). 1 fail,
1 fake, 1 hold — you cannot pick the winning per-coin level in advance.

## Reading

1. **The golden ratio is noise.** Across 15 buckets × 3 coins the "winning" level is
   different per coin, the two actual fib levels (0.382/0.618) don't consistently beat their
   non-fib neighbours, and SOL's only ROBUST bucket (0.500) isn't even Fibonacci. Scattered
   ROBUST/MARGINAL cells are exactly the multiple-comparisons hits W-FUN1/W-MC1 predict.

2. **The real thing hiding underneath is plain mean-reversion, not phi.** On EVERY coin the
   UPPER buckets (0.618/0.786 — buying near the trailing-range HIGH) were strongly negative
   (−2 to −4%), while the LOWER buckets (0.236–0.5 — buying a pullback) leaned positive. That
   monotone "pullbacks beat chasing" is a known weak effect; the fib numbers are decoration
   on top of it. It is fractions-of-a-percent, dies to fees at size, and would need its own
   pre-registered OOS validation before any capital — but it is the honest signal the phi
   framing was obscuring.

## VERDICT: **REFUTED** (golden ratio, both cells) — fib levels/days carry no edge; the
apparent hits are per-coin cherry-picks that don't survive OOS. Do not rebuild phi. The only
survivor worth a future, properly-registered look is range-position mean-reversion (buy low
in range, never near highs), which is not about the golden ratio at all. Artifacts:
`hypotheses/W-FUN2_golden_ratio.py`.
