# W-X5 cell 3 — rank-hysteresis turnover buffer on the live book

## Hypothesis
Holding an incumbent leg until it exits the top/bottom (k+b) ranks (instead of hard top-k
membership every rebalance) cuts turnover-scaled fees with little selection cost; with
fees scaled by turnover the win should show at net50 especially.

## Exact rule (pre-registered in hypotheses/W-X5_xs_implementation.py before first run)
- Base recipe verbatim (meme-excluded pct_k14 k4 H10, W-X2 engine conventions). At each
  rebalance: incumbents keep their slot while ranked inside top/bottom (k+b); vacated
  slots go to the best-ranked non-incumbents. b in {1,2,4}; b=0 asserted IDENTICAL to the
  shared engine on synthetic AND real data (selftest + runtime assert, both green).
  Side-flips allowed (engine behavior); long/short disjointness asserted.
- Same rebalance dates as baseline → the W-X4 strict-dominance gate applies directly:
  PRIMARY = net25 EV AND Sharpe, both halves, 4/4; net50 reported (net50-only win =
  MARGINAL). Nulls diagnostic only.

## Results (n=33, same dates as baseline)
| b | gross | net25 | OOS h1/h2 | Sharpe | turnover | turnover cut | paired Δ vs b=0 (t) | books differ |
|---|---|---|---|---|---|---|---|---|
| 0 | +3.88% | +3.68% | +4.34/+3.06 | +0.636 | 0.807 | — | — | — |
| 1 | +3.85% | +3.66% | +4.16/+3.18 | +0.626 | 0.773 | −4.7% | −0.023% (−0.20) | 8/33 (11 legs) |
| **2** | +4.19% | **+4.01%** | +4.46/+3.58 | **+0.669** | 0.723 | −10.7% | **+0.325% (+1.41)** | 16/33 (23 legs) |
| 4 | +4.13% | +3.97% | +4.29/+3.67 | +0.664 | 0.659 | −18.7% | +0.285% (+1.01) | 22/33 (39 legs) |

Pre-registered gate: **b=2 passes STRICT DOMINANCE 4/4 at net25 AND at net50** (h1 EV/day
+0.0045 vs +0.0043, h1 Sh_ann 4.14 vs 4.09, h2 +0.0036 vs +0.0031, h2 3.99 vs 3.66).
b=1 fails (h1 both metrics), b=4 fails (h1 both metrics). Note the non-monotone b-sweep —
the first red flag.

## The phase check refutes generalization (post-hoc, declared as such)
Offset-0 is the flattering phase (W-X5_tranches.md). Paired net25 delta (b vs b=0) re-run
at ALL 10 phase offsets:
| b | mean over offsets | positive offsets | per-offset (%/rebal) |
|---|---|---|---|
| 1 | −0.095%/rebal | 5/10 | −0.02 +0.10 −0.07 +0.03 +0.00 +0.46 −1.17 +0.05 −0.28 −0.05 |
| **2** | **−0.125%/rebal** | **4/10** | +0.33 −0.07 −0.08 −0.13 +0.30 +0.24 **−1.37** +0.09 −0.30 −0.25 |
| 4 | +0.006%/rebal | 6/10 | +0.29 +0.09 −0.12 +0.39 +0.35 −0.31 −1.05 +0.38 +0.15 −0.11 |

b=2's +0.325%/rebal exists at offset 0 and evaporates across phases (mean NEGATIVE,
4/10 positive, worst offset −1.37%/rebal). The mechanical fee saving is real but tiny:
−10.7% turnover x 25bps = +0.021%/rebal (+0.042% at 50bps) — an order of magnitude smaller
than the phase noise of the selection difference it introduces. This is the W-Q/W-R lesson
in-house: a 1-of-3 sweep survivor measured on the flattering phase.

## VERDICT: **GATE-PASS BUT PHASE-FRAGILE → MARGINAL. DO NOT WIRE on this evidence.**
Deciding numbers: pre-registered gate 4/4 at both fee tiers, BUT phase-mean paired delta
−0.125%/rebal with 4/10 offsets positive. Dominance and significance separated: paired t
was only +1.41 even at offset 0. The honest reading: hysteresis buys a ~11-19% turnover cut
at approximately ZERO expected EV — worth having only if fees ever become a live problem
(they are 25bps RT assumed; the fee saving would need ~15x higher costs to clear the noise).

## SPEC — recorded per W-X4 convention because the pre-registered gate DID pass (b=2).
NOT recommended for wiring; recorded so the decision is reversible and pre-committed if the
operator ever wants the turnover cut (e.g. under a fee-tier regression or API-budget
pressure):
```
change          hermes_trader/agents/xs_momentum_live.py rebalance selection: keep an
                incumbent (coin,side) while its pct_k14 rank is inside top/bottom
                (k_per_leg + buffer); fill vacated slots with best-ranked non-incumbents.
config          xs_momentum.rank_buffer = 2 (hot-readable int; 0 = exact live behavior,
                asserted identical in the harness).
revert          set rank_buffer = 0 — bitwise return to today's book.
KILL (pre-committed, if ever wired)
                1. forward counterfactual A/B (buffered vs unbuffered book, deterministic
                   from candles): cumulative net25 delta < 0 after 6 rebalances → revert.
                2. expectation anchor: the honest prior for the delta is ~0%/rebal
                   (phase-mean −0.125), NOT +0.325 — grade against 0.
```

Caveats: survivor-biased cache; n=33; the 10 phase offsets overlap heavily (same tape);
funding not modeled; hysteresis can hold a decayed-but-not-terrible name indefinitely
(observed on synthetic: an incumbent inside the buffer never exits while it drifts).

## Scoreboard line
W-X5 hysteresis: MARGINAL (gate-pass, phase-fragile) — b=2 passes the pre-registered
strict-dominance gate 4/4 at net25 AND net50 (+4.01% vs +3.68%, Sh 0.669 vs 0.636, turnover
−10.7%) but the paired delta collapses across rebalance phases (mean −0.125%/rebal, 4/10
offsets positive, worst −1.37); b-sweep non-monotone (b=1, b=4 fail). Mechanical fee saving
+0.021%/rebal is 10x smaller than selection phase-noise. DO NOT WIRE; spec + revert
recorded (rank_buffer=2) in case fees ever matter. Second in-house confirmation that
offset-0 point estimates flatter (see W-X5_tranches.md).
