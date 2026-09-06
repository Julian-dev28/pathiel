# C5 — there is no long edge in the xyz universe

**Date:** 2026-09-06
**Script:** `research/regime_2026_09/C5_long_side.py`
**Verdict:** REJECT all three long hypotheses. Ship nothing. The book stays short-only.

## Why it was asked

The operator asked for a profitable long strategy so the account could "swing
trade profitable both ways". First a fact, before any research: **every live
book is short-only.** `xs_reversal`, `news_surge_short`, `news_surge_multi`,
`unlock_short`, `thin_short_relax` — checked 2026-09-06, none of them emits a
long. The only long exposure in the system is the discretionary AI runner
(`runner_entry_gate.allow_shorts = false`), which has no measured edge behind it.

So the question was not "which long book do we turn on". It was whether a long
edge exists here at all.

## What was tested

Three hypotheses, all declared before the run, Bonferroni α = 0.025, minimum
300 observations, clustered bootstrap on entry timestamp, both time halves must
agree in sign. Same panel and same guards the short side had to clear.

| | hypothesis | n | EV/trade | win | p | halves | |
|---|---|---:|---:|---:|---:|---|---|
| **L1** | long BOTTOM decile (mirror of the live short) | 2564 | **+0.042%** | 50% | 0.420 | −0.40 / +0.55 | fail |
| **L2** | long TOP decile (momentum continuation) | 2562 | **−1.908%** | 35% | 1.000 | −2.28 / −1.48 | fail |
| **L3** | long anything liquid, no signal at all | 23997 | **−0.727%** | 43% | 1.000 | −0.94 / −0.49 | fail |
| — | short top decile (`xs_reversal`, for reference) | 2562 | **+1.408%** | 50% | 0.0000 | +1.78 / +0.98 | **PASS** |

L1 and L2 are cross-sectional, so both are blind to market drift by
construction — ranking within a snapshot subtracts whatever the whole universe
did. L3 exists precisely to cover that blind spot: "is there long money here at
all" is a different question from "is there a long signal", and answering only
the second would have left the operator's question half answered.

## The finding

**L3 is the one that settles it.** An unconditional 24h long on any liquid xyz
market loses 0.727% per trade across 23,997 observations, negative in both
halves. There is no beta to harvest. The tape is not drifting up and then being
mistimed — it is not paying longs at all.

That also explains the asymmetry. If the reversal were plain mean reversion it
would pay on both tails, and L1 shows it does not (+0.042%, p=0.42, halves
flipping sign — a flat line). The short side is collecting something a long
structurally cannot: **funding carry**. On tokenized equity perps funding runs
positive, so the short *receives* it and the long *pays* it. The same price move
nets out differently for the two sides.

The arithmetic is exact and worth keeping, because it is the whole argument:

```
short top decile  = +1.408%
long  top decile  = −1.908%
                    ───────
sum               = −0.500%  =  2 × 0.25% slippage
```

The two sides differ by nothing but round-trip cost. The long is not a separate
trade with its own edge — it is the short run backwards, paying the carry
instead of receiving it and paying the spread on top.

## What this means for the operator's request

A long book here would be a **hedge, not an edge**. It would consume slots from
a measured +1.408% book to add an unmeasured one, on an account whose binding
constraint is already capital rather than opportunity (C4: HIP-3 supplies 19×
the candidates the slots can take).

"Both ways" is the right instinct on a normal instrument. It is the wrong one
here, and the reason is structural rather than a matter of not having looked
hard enough: the venue pays shorts to hold and charges longs. Trading it
symmetrically means paying that carry half the time on purpose.

**If a long is wanted anyway**, the honest routes are a different universe
(crypto has no equivalent structural short bias — though C4 rejected it on
sample size, n=106 with sign-flipping halves), or a mechanism that is not
price-directional at all. Neither is ready to ship, and neither is a reason to
wire a losing long into a working short book in the meantime.

## Reproduce

```sh
python research/regime_2026_09/C5_long_side.py
```
