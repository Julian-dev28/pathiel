# C12 entropy_predictability_filter — INCONCLUSIVE (right direction, not significant)

## Hypothesis
Only take a Tier-1 signal on LOW permutation-entropy (predictable/structured) coins → cuts
duds. Meta-filter on the live `extreme_fade` (−12%/1d crash → long, 20% stop, 3d).

## Exact rule
Base: daily close-to-close < −12% → long, fill i+1 open, 20% stop, 3d. Filter: at decision
bar, permutation entropy (m=3, delay=1) of the coin's last 30 daily returns (lookahead-safe).
Split base trades by PE median + terciles; compare EV/win/OOS-halves; permutation-test the
low-vs-high EV gap (shuffle PE labels, 10k iters).

## Results
| subset | n | EV@12 | EV@25 | win | h1 | h2 |
|--|--|--|--|--|--|--|
| BASE (all) | 177 | +4.65% | +4.52% | .627 | +6.48 | +2.80 |
| LOW-PE (≤med) | 90 | **+5.72%** | +5.59% | .656 | +8.09 | **+3.25** |
| HIGH-PE (>med) | 87 | +3.55% | +3.42% | .598 | +5.89 | **−2.61** |
| LOW-PE tercile | 60 | +5.98% | +5.85% | .617 | +8.83 | +2.93 |
| HIGH-PE tercile | 60 | +3.37% | +3.24% | .617 | +5.59 | −1.07 |

- Direction is right and **monotone** across terciles: low-PE EV +5.98 → high-PE +3.37. The
  low-PE subset keeps both OOS halves positive; the high-PE subset's second half goes NEGATIVE
  (h2 −2.6), i.e. the filter strips the OOS-fragile crash-fades.
- **BUT the permutation test fails:** low-vs-high EV gap = +2.18%/trade, **p_one_sided = 0.22**.
  At n=177 with the high variance of deep-crash bounces, a gap this size occurs ~1-in-5 by
  chance.

## VERDICT
**INCONCLUSIVE.** Deciding number: the EV separation is **+2.18%/trade but p=0.22** — not
significant. The filter is directionally correct (low-PE crash-fades are better and OOS-robust;
high-PE ones have a negative second half) and the tercile monotonicity is encouraging, but the
sample (n=177, only ~90 per side) is too small to confirm the dud-cut is real rather than the
natural EV-variance of the base edge. Do NOT deploy as a filter yet. Re-test when more crash
events accrue (or bolt onto a higher-n base like the engulfing book) — if the monotone gap
holds with p<0.05 it becomes a useful size/dud filter on the fade book.
