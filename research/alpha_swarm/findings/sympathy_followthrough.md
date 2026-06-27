# C15 sympathy_followthrough — REFUTED (no sector-specific follow-through; just market beta)

## Hypothesis
When a sector LEADER makes a big move, the laggards follow next bar (tradeable sympathy).

## Exact rule
1h. Leader per sector = highest-dayNtlVlm coin (L1→BTC, MEME→PUMP, DEFI→AAVE, AI→WLD,
INFRA→JTO). Event: leader 1h |return| > G at bar t. Enter sector laggards in the leader's
direction at t+1 open, hold H. **KEY CONTROL (paired):** same-side, same-time OUT-OF-SECTOR
coins — isolates sector sympathy from the market-wide move that drives most big leader bars.
Reported both the paired (laggard − outsector) diff and the raw laggard signed return, OOS
halves + slippage. G∈{1,2,3}%, H∈{1,3,6}h.

## Results
| G | H | PAIRED (lag−outsector) EV@12 | paired h1/h2 | RAW laggard EV@12 |
|--|--|--|--|--|
| 1% | 1 | **−0.12%** | −0.11 / −0.14 | −0.21% |
| 2% | 3 | **−0.18%** | −0.18 / −0.17 | −0.33% |
| 3% | 1 | **−0.16%** | −0.19 / −0.14 | −0.46% |
| 3% | 6 | **−0.16%** | −0.15 / −0.16 | −0.47% |

- **Paired diff is negative in all 9 cells** (−0.12% to −0.20%), both halves negative. Sector
  laggards do NOT outperform same-side, same-time out-of-sector coins after a leader move —
  they marginally UNDERperform. There is no sector-specific sympathy.
- **Raw laggard-follow is a net loser** (−0.21% to −0.50%): the move has already happened; by
  the time you chase the laggard you get mean-reversion + fees.
- Effect strengthens (more negative) with larger G — the bigger the leader move, the worse the
  laggard-chase, the opposite of the hypothesis.

## VERDICT
**REFUTED.** Deciding number: **paired (laggard − out-of-sector) EV is −0.12% to −0.20% in
all 9 cells, both OOS halves negative.** Whatever co-movement follows a big leader bar is
plain market beta — out-of-sector coins capture it equally — and entering the laggard after
the move is a net loser. No tradeable sympathy effect. (Consistent with C14: sector tags
carry no exploitable structure here.)
