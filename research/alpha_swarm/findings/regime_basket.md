# regime_basket — BTC-regime-conditioned cross-sectional L/S basket

## Hypothesis
A daily market-neutral long/short basket whose DIRECTION is flipped by BTC regime
("long winners in up-tape, long losers in down-tape") beats a static unconditional
momentum book. Owner framing: "a random basket of longs one day and shorts another
based on regime."

## Rule tested (lookahead-safe)
- Universe: 38 of 40 coins with full 1d history (BTC used only for regime; LIT/MON short-history handled per-day).
- Each day t: rank the cross-section by trailing **k-day close return** (k∈{3,5,10}), decided at CLOSE of day t.
- BTC regime at close of t: `sma20` (BTC close vs its 20d SMA) OR `ret7` (sign of BTC trailing-7d return).
- Build a market-neutral book: long top-m / short bottom-m (m∈{4,6,8}), **FILL at OPEN of t+1**, hold H days (H∈{1,3,5}), **EXIT at OPEN of t+1+H**.
- One trade = one coin-leg, side-signed (long=+ret, short=−ret). Equal-weight and vol-scaled (1/realized-10d-vol, mean-1 weights).
- Variants: **baseline** (unconditional momentum, ignores regime) · **B regime-flip** (up→long winners, down→REVERSE: long losers/short winners) · **C dispersion gate** (only top-tercile cross-sectional stdev days) · **long-top-only** (non-neutral).
- 216 configs swept. Cost sweep 0/6/12/25/50 bps and first/second-half OOS via `alpha_lib.summarize()`.

## Headline results (EV = mean % per book-leg)

Matched head-to-head, equal-weight, regime=sma20:

| config | BASELINE EV@12bps (H1/H2) | B-FLIP EV@12bps (H1/H2) |
|---|---|---|
| k5 H3 m6  | **+0.553** (0.97/0.13) | −0.116 (0.07/−0.31) |
| k5 H5 m6  | **+0.752** (1.38/0.11) | −0.192 (−0.11/−0.28) |
| k10 H3 m6 | **+0.635** (1.08/0.18) | −0.377 (−0.30/−0.45) |
| k10 H5 m6 | **+1.177** (1.86/0.49) | −0.422 (−0.41/−0.43) |

regime=ret7 gives the same baseline (it ignores regime, by construction — confirms wiring) and B-flip stays negative every cell.

Non-overlapping check (stride=5, independent books, k10 H5):
- m4: baseline **+1.12%** @12bps (H1 +2.13 / H2 +0.04); B-flip **−0.92%** (H1 −1.67 / H2 −0.11)
- m6: baseline **+1.38%** @12bps (H1 +1.91 / H2 +0.81, survives to +1.25% @25bps); B-flip **−1.08%** (both halves negative)

Dispersion gate (variant C) on the winning baseline: collapses to EV +0.04% @12bps and **flips H2 negative** (H1 +0.28 / H2 −0.20). Gate destroys the edge — REFUTED as an enhancement.

Long-top-only book: EV +0.34% @12bps but **sign-flips across halves** (H1 −0.25 / H2 +0.94) → not robust (it's just net-long beta, not an edge).

## VERDICT

**REFUTED — regime conditioning.** The deciding number: B-flip is **negative EV in every one of the 8 matched cells** (−0.04% to −0.42% @12bps) and negative in BOTH OOS halves at the strong params. Conditioning basket direction on BTC regime does not beat the static book; it actively destroys the edge. The dispersion gate (variant C) also refuted (flips H2 negative). Long-only refuted (sign-flip = beta not alpha).

**What survives is the thing it was supposed to beat:** the UNCONDITIONAL cross-sectional momentum book. Best robust config = **k=10 trailing-day rank, long top-4..6 / short bottom-4..6, equal-weight, hold H=5 days**: +1.0–1.4% per book-leg @12bps, +EV both OOS halves, survives 25bps. Vol-scaling slightly weaker than equal-weight (H2 thins to ~0). This just re-confirms the existing live `xs_momentum` keeper (see MEMORY: cross-sectional momentum edge) — it is NOT a new edge.

## Why regime-flip fails (mechanism)
In this 301-day survivor sample the cross-section is momentum-persistent in BOTH regimes — down-tape losers keep losing, they don't mean-revert to winners. So flipping to "long losers in down-regime" buys the worst coins right before they continue down. The static momentum book already captures the only stable signal; adding a BTC-regime switch just injects timing noise and inverts the leg in exactly the periods momentum was still working.

## Caveats / biggest risks
- **Survivorship**: today's-liquid 38-coin set. The baseline +EV is an UPPER BOUND; dead coins (which momentum would have shorted then they delisted) are absent. The REFUTATION of regime-flip is the safer-direction conclusion and not threatened by survivorship.
- H=5 daily-rebalanced books overlap (autocorrelated legs inflate n/sharpe); the stride=5 independent-book check confirms the sign and magnitude, so the conclusion is not an artifact of overlap.
- Edge lives at the 5-day hold; the H=1 book is near-zero after costs (+0.14% @12bps, dead by 25bps) — this is a weekly-rotation edge, not a daily one.

## Files
- script: `scratchpad/regime_basket.py` (`python regime_basket.py sweep` / `detail`)
