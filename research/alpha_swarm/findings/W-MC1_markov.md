# W-MC1 — first-order Markov chain on discretised returns

**Question (operator-requested 2026-07-23):** does a Markov chain strategy — state =
discretised return today, trade the sign of the state-conditional forward return — have
a tradeable edge?

**Method (PIT, OOS-by-construction, `hypotheses/W-MC1_markov.py`):** dataset.json cached
OHLCV, 40 crypto coins. Per coin: r_t = c_t/c_{t-1}−1. TRAIN = first 60% of each coin's
bars — fit quantile state breakpoints AND per-state mean forward return mu(s); freeze
both. TEST = last 40% — label each day by the frozen breakpoints, take side =
sign(mu(s) − grand_train_mean), enter close_t / exit close_{t+1}. Fees 0/25/50 bps RT.
Null = 2000x label permutation within TEST (shuffle state vector vs forward-return
vector), one-sided p on net25. Cells: {2,3,5} states × {1d, 1h}. Seed 20260723.

## Results — 0 / 6 cells tradeable

| cell | n_test | gross | net25 | test h1/h2 net25 | perm p | verdict |
|---|---|---|---|---|---|---|
| 1d 2-state | 4700 | +0.020% | −0.230% | −0.248 / −0.213 | 0.572 | REFUTED |
| 1d 3-state | 4700 | +0.002% | −0.248% | −0.288 / −0.209 | 0.695 | REFUTED |
| 1d 5-state | 4700 | −0.052% | −0.302% | −0.323 / −0.281 | 0.182 | REFUTED |
| 1h 2-state | 31960 | −0.042% | −0.292% | −0.282 / −0.303 | 0.0005 | REFUTED |
| 1h 3-state | 31960 | −0.041% | −0.291% | −0.287 / −0.295 | 0.0005 | REFUTED |
| 1h 5-state | 31960 | −0.036% | −0.286% | −0.275 / −0.297 | 0.0005 | REFUTED |

## Reading

1. **The transition matrix is uniform — the process is memoryless.** Every row of every
   estimated P(next|state) sits within a few points of 1/n_states (1d 3-state: rows
   [.36,.31,.34] / [.35,.35,.30] / [.31,.34,.35]). There is no first-order Markov
   structure to exploit: tomorrow's state does not depend on today's.

2. **Gross edge is ~zero out of sample.** The TRAIN mu(s) dispersion (e.g. 1d 3-state
   high-state +0.19% vs mid −0.64%) does NOT carry to TEST — gross net25 EV in TEST is
   +0.002% (3-state) to −0.05% (5-state). The in-sample dispersion was noise fit.

3. **Where structure is statistically real (1h, p=0.0005) it is 10x smaller than fees
   and mean-reverting.** With n=31,960 the permutation p is tiny — but the observed EV
   is NEGATIVE: the detectable 1h micro-structure is ~0.02–0.03%/bar of weak mean
   reversion, and a daily/hourly Markov trader pays 25bps every bar to harvest it. The
   significance is significance of a LOSER. Net −0.29%/trade = the fee, locked in each bar.

## VERDICT: **REFUTED** (all 6 cells, per the locked gate)

Deciding numbers: best gross EV +0.020% (1d 2-state), best net25 −0.230%, no cell
positive net of fees, no cell with both TEST halves positive, uniform transition
matrices. First-order Markov chains on returns have no tradeable edge on this universe —
a clean, independent confirmation of the standing "price entries have no edge / candle
space saturated" prior on a model class not previously tested here. Higher-order chains
(state = last k returns) are not worth pursuing: a memoryless 1st-order process cannot
hide exploitable k-th-order structure at n we have, and turnover fees only worsen.
**Recorder/live: NO-GO. Zero capital.** Artifacts: `hypotheses/W-MC1_results.json`.
