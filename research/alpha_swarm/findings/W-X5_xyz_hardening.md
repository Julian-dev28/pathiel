# W-X5 cell 4 — xs_xyz_equities hardening: is the live config on the efficient frontier?

## Hypothesis
The new live xyz book (W-X2 cell A ROBUST: resid7/k5/H5 vs xyz:XYZ100) was wired at its
primary spec without a neighborhood sweep. Sensitivity: hold {3,5,10} x k {3,5} x benchmark
{xyz:XYZ100, equal-weight-universe-mean} on the same cached xyz data — does a neighbor
strictly dominate the live cell, or does the live config stand?

## Exact rule (pre-registered in hypotheses/W-X5_xs_implementation.py before first run)
- Data/engine: `W-X2_cache_daily.json` xyz world (87 markets, ~281-day grid), W-X2 engine,
  equities-only (NON_EQUITY_XYZ excluded), >=61 bars + 30d mean notional >= $250k, score =
  7d residual momentum (OLS beta, 30d window), start=66, 2000-draw matched nulls.
- EW_UNIV benchmark = synthetic equal-weight index of ALL declared xyz equity names
  (pre-eligibility): consecutive-grid-day members = names with closes on both days, daily
  ret = mean member ret, >=5 members else flat, base 100, injected as a virtual coin.
  (Plain cross-sectional demeaning is rank-invariant — beta-weighted index residual is the
  distinct variant.) Selftest: identical-drift world → index ret exact, residual ~0.
- Frontier gate (pre-registered): a neighbor replaces the live spec ONLY if it beats the
  live cell on per-day net25 EV AND annualized Sharpe in BOTH halves (4/4) AND its own
  null p < 0.05. Proposal-only (W-X4 bar), never an automatic change.

## Results (per-rebalance per-leg signed EV; live cell bolded)
| bench | k | H | n | gross | net25 | ann | OOS h1/h2 | Sharpe | null p | turn | $/wk @$76.8-leg |
|---|---|---|---|---|---|---|---|---|---|---|---|
| XYZ100 | 3 | 3 | 60 | +0.41% | +0.27% | +32.6% | +0.60/−0.06 | +0.097 | 0.0545 | 0.56 | +$2.88 |
| XYZ100 | 3 | 5 | 35 | +1.21% | +1.04% | +76.2% | +1.04/+1.04 | +0.323 | 0.0035 | 0.68 | +$6.73 |
| XYZ100 | 3 | 10 | 17 | +1.45% | +1.24% | +45.4% | +1.10/+1.37 | +0.268 | 0.042 | 0.80 | +$4.01 |
| XYZ100 | 5 | 3 | 58 | +0.33% | +0.20% | +24.7% | +0.23/+0.17 | +0.090 | 0.0625 | 0.49 | +$3.64 |
| **XYZ100** | **5** | **5** | 34 | +0.81% | **+0.65%** | +47.2% | +0.18/+1.12 | +0.217 | **0.0055** | 0.64 | +$6.95 |
| XYZ100 | 5 | 10 | 17 | +1.50% | +1.31% | +48.0% | +0.15/+2.35 | +0.308 | 0.02 | 0.76 | +$7.07 |
| EW_UNIV | 3 | 3 | 60 | +0.28% | +0.14% | +17.1% | +0.54/−0.26 | +0.055 | 0.1375 | 0.57 | +$1.51 |
| EW_UNIV | 3 | 5 | 35 | +1.04% | +0.87% | +63.5% | +0.69/+1.04 | +0.261 | 0.0105 | 0.69 | +$5.62 |
| EW_UNIV | 3 | 10 | 17 | +1.51% | +1.30% | +47.4% | +0.53/+1.98 | +0.263 | 0.0355 | 0.82 | +$4.19 |
| EW_UNIV | 5 | 3 | 58 | +0.28% | +0.16% | +19.2% | +0.38/−0.06 | +0.074 | 0.105 | 0.49 | +$2.82 |
| EW_UNIV | 5 | 5 | 34 | +0.87% | +0.71% | +51.8% | +0.33/+1.09 | +0.241 | 0.004 | 0.65 | +$7.63 |
| EW_UNIV | 5 | 10 | 17 | +1.62% | +1.43% | +52.2% | −0.14/+2.82 | +0.329 | 0.014 | 0.75 | +$7.69 |

Frontier gate: **0/11 neighbors dominate the live cell 4/4.** Closest calls:
- XYZ100 k3/H5 (+1.04%, p=0.0035, flat halves +1.04/+1.04, Sharpe 0.323): beats live on h1
  but LOSES h2 EV/day (+0.208 vs +0.224%/day) — fails.
- The H10 column (up to +1.43%/rebal) is h1-weak everywhere (+0.15, −0.14 h1 at k5) —
  fails, and n=17.

Phase-mean per-day net25 (post-hoc, declared; offsets 0..H-1):
| | k3/H3 | k3/H5 | k3/H10 | k5/H3 | k5/H5 | k5/H10 |
|---|---|---|---|---|---|---|
| XYZ100 | +0.034 | +0.048 | +0.043 | +0.052 | **+0.066** | +0.056 |
| EW_UNIV | −0.005 | +0.048 | +0.084 | +0.011 | +0.060 | +0.098 |

The live cell has the best phase-mean per-day EV in its benchmark column AND the best
phase-MIN of any positive cell (−0.08% worst offset; the H10 cells swing to −1.1..−2.2%
at bad offsets). EW_UNIV k5/H10's higher phase-mean (+0.098%/day) comes with h1 NEGATIVE
at offset 0, phase-min −1.06, and n=17 — a watch item, not a change.

## VERDICT: **LIVE CONFIG STANDS — resid7/k5/H5/XYZ100 is on the efficient frontier.**
Deciding numbers: 0/11 neighbors pass the pre-registered 4/4 + p<0.05 gate; the live cell
is simultaneously the most phase-stable positive cell (phase-min −0.08%) and top of its
column on phase-mean per-day EV. The benchmark choice (XYZ100 vs EW index) moves nothing
materially at H5 (+0.65 vs +0.71, both p<0.006) — the edge is not benchmark-sensitive,
which is itself a robustness point. No spec-change proposal.

Watch item (no action): if the book survives its W-X2-A kill gates past ~12 rebalances,
re-run this sweep with the then-longer tape — the H10 column's h2 strength (+2.35/+2.82)
is the semis-trend persistence story from W-X2-A and n will have doubled by then.

Caveats: young universe (~6 months usable), survivorship mild but real; thin books; the
W-X2-A concentration caveat stands (EV carried by the semis-dispersion theme; no-semis
ablation collapsed to +0.08%); 24/7 bars include stale weekend prices; funding not modeled.

## Scoreboard line
W-X5 xyz_hardening: LIVE CONFIG STANDS — 0/11 of hold{3,5,10} x k{3,5} x bench{XYZ100,
EW-index} dominate resid7/k5/H5/XYZ100 (4/4 gate + p<0.05); live cell is the most
phase-stable positive cell (phase-min −0.08%/rebal, phase-mean +0.066%/day best in
column); benchmark choice immaterial at H5 (robustness point). EW k5/H10 (+0.098%/day
phase-mean but h1 −0.14, n=17) = watch item for the 12-rebalance re-sweep. No change.
