# W-X2 cell B — xs_sector_buckets: within-bucket xs + bucket rotation on top-50

## Hypothesis
Sector structure on the top-50 crypto universe adds EV over the flat all-universe xs book:
(i) within-bucket L/S (esp. MEME-only, AI-only), (ii) bucket-momentum rotation.
**Prior: C14 sector_rotation REFUTED** (39-coin universe, 188 bars: rotation ≈0 with OOS
sign-flips; intra-sector < all-universe in 4/4 cells). This cell is the targeted extension on
top-50 with 13 months of bars, judged against that prior.

## Exact rule (pre-registered)
- Universe: top-50 main-dex perps by dayNtlVlm (2026-07-20 snapshot; weekend-quiet tape, only
  16 names >$5M that day — declared amendment: rank-based top-50, ranks 30-50 thin). 401 daily
  bars for seasoned names; >=61-bar eligibility. 66 rebalances, H=5.
- SECTOR_MAP hand-declared in hypotheses/W-X2_xs_widening.py BEFORE running (6 buckets:
  L1/DEFI/MEME/AI/INFRA/EXCH). Unmapped new listings excluded and printed: ACE, SUSHI, GRAM,
  AZTEC (4/50).
- (i) Within-bucket: buckets with >=6 members run 7d-raw-momentum L/S k=2, H=5.
- (ii) Rotation: long ALL coins of the strongest bucket / short ALL of the weakest by trailing
  7d equal-weight bucket return (buckets with >=4 members), H=5.
- Baseline: flat all-universe raw7 k8 H5 + a same-k control (raw7 k2 H5) for the concentration
  confound. Costs/null/OOS as in the shared W-X2 engine.

## Results
| book | n | gross | net25 | OOS h1/h2 | Sharpe | null p | $/wk |
|---|---|---|---|---|---|---|---|
| baseline all-universe raw7 k8 | 66 | +0.78% | +0.61% | +1.01 / +0.22 | +0.199 | 0.002 | +$10.56 |
| control all-universe raw7 k2 | 66 | +0.72% | +0.53% | +1.44 / −0.39 | +0.084 | 0.084 | +$2.26 |
| within-L1 raw7 k2 (18 coins) | 66 | +1.67% | **+1.50%** | +2.51 / +0.49 | +0.238 | 0.000 | +$6.44 |
| within-DEFI raw7 k2 (12) | 66 | +0.43% | +0.26% | +1.27 / −0.75 | +0.073 | 0.156 | +$1.13 |
| within-MEME raw7 k2 (8) | 66 | −0.08% | −0.22% | +0.27 / −0.71 | −0.055 | 0.56 | −$0.96 |
| within-AI (4 members) | — | — | — | — | — | — | BLOCKED (<6) |
| bucket-rotation 7d | 66 | −0.01% | −0.16% | −1.31 / +0.98 | −0.046 | — | −$1.41 |

## VERDICT: **REFUTED** (headline confirms C14) — with one MARGINAL side-finding.
Deciding numbers: **bucket rotation −0.16% net25 with an OOS sign-flip** (−1.31/+0.98) —
dead, exactly as C14 found. **MEME-only −0.22% (p=0.56)** — no meme-specific momentum book.
AI-only BLOCKED (only 4 AI names survive in today's top-50). DEFI is noise (p=0.16, h2 −0.75).
Sector structure adds nothing; the all-universe book stays the reference.

**Side-finding (MARGINAL, do not wire without its own cell): within-L1 +1.50% net25, p=0.000,
both halves positive, beats the same-k control (+0.53%, h2 −0.39) — not the concentration
confound.** This contradicts C14's "intra-sector always weaker" on this longer window, but
(a) it is the best of 4 bucket cells (selection), (b) "L1" here = the 18 most liquid majors,
so the more parsimonious read is **momentum among liquid majors is cleaner than among the
thin tail**, not sector alpha. If pursued: pre-register a dedicated "liquid-majors-only xs"
cell (top-20 by volume, no sector language) and test against the top-50 book.

Caveats: survivor-biased universe (today's top-50 over 13 months back-history), hand-declared
sector map (32% of the L1 label is judgment), weekend-quiet volume snapshot for universe pick.
