# C6 round_number_magnet — REFUTED (no stable edge at round levels)

## Hypothesis
Price rejects at / is magnetized toward psychological round levels (power-of-ten grid).

## Exact rule
Per-coin grid g = 10^floor(log10(close))/10 (round thousands for BTC, tens for SOL, dollars
sub-$10), levels = integer multiples of g. Rejection-short: high pierces a level from below
(high≥L) but open<L and close<L → short. Rejection-long: low pierces from above, closes back
above → long. Decide i close, fill i+1 open. 1d (hz 2/3/5) and 1h (hz 6/12/24), stop sweep.
`alpha_lib.summarize` + mc_null on any robust cell.

## Results
**1d — top by EV@25bps:**
| side | hz | stop | n | EV@12 | EV@25 | EV@50 | win | h1 | h2 |
|--|--|--|--|--|--|--|--|--|--|
| short | 5 | .08 | 1836 | 0.79% | 0.66% | 0.41% | .46 | **+2.00** | **−0.42** |
| short | 5 | .25 | 1836 | 0.63% | 0.50% | 0.25% | .55 | +2.14 | −0.88 |

**1h — top by EV@25bps:** all NEGATIVE (best long hz6: EV@25 −0.22%, h2 −0.23).

- **No robust-both-halves cell on either timeframe.**
- The 1d short cells' positive raw EV is the −44%-tape short tailwind concentrated in the
  first half; every cell **sign-flips** (h1 ≈ +2.0, h2 negative). Not a magnet, a regime artifact.
- 1h is uniformly fee-dominated negative.

## VERDICT
**REFUTED.** Deciding number: no robust cell; the only positive-EV cells (1d shorts)
**sign-flip across halves** (h1 +2.0 / h2 −0.42 to −0.88) and that positivity is the
short-beta tape, not the round level. 1h pierce-rejection is negative after costs. No MC null
needed — nothing passed the OOS gate. Confirms the high refute prior on the round-number story.
