# W-X2 cell D — xs_hold_sweep: the inherited 5d hold, swept {3,5,10,20}d

## Hypothesis
The live book's 5d hold (hot-config; committed default is 10d) was inherited, never swept.
Sweep H in {3,5,10,20} at the settled live ranking (pct_k(14), ranker_ab KEEP-PCTK), net of
turnover-scaled fees, at both k=8 (design) and k=4 (live, capital-capped).

## Exact rule (pre-registered)
Crypto top-50 (2026-07-20 rank snapshot), 401 daily bars, >=61-bar eligibility, decide bar i /
fill open[i+1] / exit open[i+1+H], non-overlapping, equal weight. Costs: bps round-trip per
replaced position × observed turnover. Null: 2000 matched random books (gross). OOS halves.

## Results
| k | H | n | gross | net25 | ann net25 | OOS h1/h2 | Sharpe | null p | turnover | $/wk @live sizing |
|---|---|---|---|---|---|---|---|---|---|---|
| 8 | 3 | 111 | +0.83% | +0.70% | +85.5% | +1.12 / +0.29 | +0.339 | 0.000 | 0.50 | +$20.15 |
| 8 | 5 | 66 | +1.23% | +1.08% | +79.1% | +1.37 / +0.80 | +0.358 | 0.000 | 0.58 | +$18.64 |
| 8 | 10 | 33 | +2.49% | +2.31% | +84.1% | +2.99 / +1.66 | +0.531 | 0.000 | 0.75 | +$19.82 |
| 8 | 20 | 16 | +4.28% | +4.07% | +74.2% | +3.20 / +4.93 | +0.575 | 0.000 | 0.84 | +$17.48 |
| 4 | 3 | 111 | +0.81% | +0.65% | +79.4% | +1.05 / +0.26 | +0.208 | 0.000 | 0.62 | +$9.36 |
| 4 | 5 | 66 | +1.27% | +1.10% | +80.4% | +1.01 / +1.19 | +0.229 | 0.000 | 0.70 | +$9.47 |
| **4** | **10** | 33 | +3.46% | **+3.25%** | **+118.8%** | **+3.64 / +2.89** | **+0.589** | 0.000 | 0.83 | **+$14.00** |
| 4 | 20 | 16 | +5.67% | +5.44% | +99.3% | +5.25 / +5.64 | +0.559 | 0.0015 | 0.89 | +$11.70 |

Every cell is +EV net25 with both OOS halves positive and null p <= 0.0015 — on 13 months of
bars this re-confirms the momentum family at every hold (the strongest single re-confirmation
of the live edge to date, n up to 111).

## VERDICT: **MARGINAL-UPGRADE — 5d is not refuted, but 10d dominates it at the live k=4.**
Deciding numbers: at k=4 (the live book), **H10 beats H5 on every metric**: per-day EV 0.325%
vs 0.220%, annualized net25 **+118.8% vs +80.4%**, Sharpe/rebalance **+0.589 vs +0.229**, OOS
+3.64/+2.89 vs +1.01/+1.19, and it needs HALF the rebalances (less fee/API/exec surface).
H3 is the worst cell at both k (h2 fades to +0.26-0.29, Sharpe lowest) — do NOT shorten.
H20 holds up but n=16 is thin and per-$ efficiency drops. At k=8 the H-ranking is flatter
(H5 fine), so this is a k=4-specific result: with only 4 names/leg, the longer hold lets the
per-name trend overcome selection noise.

**Recommendation (1-line config change, zero new code): restore `hold_days: 10` in the hot
config — which is the COMMITTED default that the hot config drifted away from.** Expected
+$4.5/wk over H5 at current $50-leg sizing (+$14.00 vs +$9.47). Grade the first 3 forward
rebalances against +3.25%/rebal net25 before trusting it further. Note hold_days also sets the
dsl_exit_override hard-timeout (e248c13) — at H10 the disaster stop stays 20%, timeout 10d.

## Cell E — stack settled winners (pre-registered gate: only if priors say they help)
**Vacuous by prior.** A6 vol_managed: negative Sharpe lift in all 4 configs (REFUTED).
W-A5 rank/inv-vol weighting: equal-weight net-Sharpe-optimal (REFUTED). ranker_ab: pct_k
already the settled ranker. There is no settled winner to stack onto cell A or D — the
correct combo is the null combo. (The live `vol_managed.enabled=true` hot-config flag
contradicts A6; it is currently inert — scalar 1.0, history too short — but per A6 it should
be OFF before it accumulates enough history to start scaling.)

Caveats: survivor-biased top-50; 25bps RT assumed (crypto main-dex realistic); funding not
modeled (roughly symmetric on a market-neutral book; D1/D2 showed funding adds no lift here).
