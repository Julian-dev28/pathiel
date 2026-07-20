# W-X5 cell 2 — tranche staggering: 2 half-books offset by H/2 vs the lump book

## Hypothesis
Splitting the live book (meme-excluded pct_k14 k4 H10) into 2 tranches rebalanced 5 bars
apart smooths rebalance-date risk (half the book turns over each half-period) at little or
no EV cost. Structural variance work, not signal.

## Exact rule (pre-registered in hypotheses/W-X5_xs_implementation.py before first run)
- Tranche A = the full recipe at half notional, rebalancing at start 66, 76, ...; tranche
  B identical, offset +5 (71, 81, ...). Lump comparator = A at full notional.
- Valuation on the 5-bar grid: each rebalance's H-period fwd split EXACTLY on
  entry-notional basis (sub1 = mark(i+6)/open(i+1) − 1, sub2 = fwd − sub1; identity
  asserted per leg and per book; mid-mark fallbacks counted: 0 occurred). Fees (25bps RT x
  turnover) at each tranche's own rebalance, 0.5-weighted; grid trimmed to the
  fully-deployed window (64 x 5d periods).
- WIRE gate (pre-registered): grid Sharpe higher in BOTH halves AND |EV drag| <= 10% of
  lump per-H net25 AND maxDD not worse AND expressible at current E.

## Results
| scheme | per-H net25 | grid Sharpe (h1/h2) | worst 5d | worst rebalance | maxDD |
|---|---|---|---|---|---|
| LUMP (A @ full) | **+3.770%** | **+0.440** (+0.533/+0.350) | −8.10% | −8.74% | 9.35pp |
| TRANCHE 0.5A+0.5B | +2.615% | +0.320 (+0.377/+0.260) | −6.84% | A −8.74 / B −9.01 | 8.42pp |

- Tranche A: n=33, net25 +3.68%/rebal. Tranche B (same recipe, +5 bars): n=32, **+1.46%**.
- EV drag **−1.155%/H (−30.6% relative)** — 3x over the pre-registered 10% band. Grid
  Sharpe LOSES both halves. maxDD/worst-5d improve slightly (−0.93pp / +1.26pp) — the
  smoothing is real but tiny relative to the EV cost.
- Capital: 2-tranche legs halve → strict expressibility moves to $70 (>$65 current); the
  scheme is not even expressible today at full book integrity (see cell 1 capital table).

## VERDICT: **REFUTED — KEEP LUMP** (gate 1/4: only maxDD passes).
Deciding numbers: EV drag −1.155%/H (−30.6% rel, band was 10%), grid Sharpe +0.320 vs
+0.440 with both halves losing.

## The real finding: the baseline's point estimate is PHASE-LUCKY
The B-tranche earning +1.46% vs A's +3.68% on the same recipe is not a tranching cost — it
is rebalance-phase sampling variance. Post-hoc sweep (declared as such), same recipe at
every offset:
| offset | +0 | +1 | +2 | +3 | +4 | +5 | +6 | +7 | +8 | +9 |
|---|---|---|---|---|---|---|---|---|---|---|
| net25 %/rebal | **+3.68** | +2.23 | +2.23 | +1.76 | +2.01 | +1.46 | +2.67 | +1.98 | +2.11 | +2.29 |

Offset 0 — the grid every W-X2/X3/X4/X5 crypto number is measured on — is the BEST of all
10 phases. Phase-mean = **+2.24%/rebal**; the quoted +3.68% carries ~+1.4pp of phase luck.
Two consequences, both honest:
1. The edge is real at EVERY phase (min +1.46%, all 10 positive) — nothing here refutes
   the book.
2. Forward expectations should be set off the phase-mean, not offset-0: at current sizing
   (envelope $415 gross) that is ~**+$6.5/wk**, not +$10.7/wk. The W-X4 exclusion-overlay
   delta and any future forward-grading against "+3.68%/rebal" should use ~+2.2%/rebal as
   the anchor or they will read healthy books as underperforming.

Caveats: survivor-biased cache; A/B share one 13-month sample so the 10 offsets are highly
overlapping (not independent draws — the phase spread understates true sampling error);
funding not modeled; tranche capital shortfall is as-of current $65 equity.

## Scoreboard line
W-X5 tranches: REFUTED — 2-tranche H/2 stagger costs −1.155%/H (−30.6% rel) with grid
Sharpe 0.320 vs 0.440 both halves losing; maxDD gain (9.35→8.42pp) nowhere near pays for
it; also not min-order-expressible at $65. Side-finding that matters: offset-0 is the best
of all 10 rebalance phases (+3.68 vs phase-mean +2.24%/rebal) — set forward expectations
and grading anchors at ~+2.2%/rebal (~$6.5/wk at current sizing), and treat all offset-0
point estimates in the W-X series as flattered by ~+1.4pp.
