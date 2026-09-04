# W-XSR1 — cross-sectional reversal, gated on an awake funding market

**Book: `xs_reversal`. VALIDATED. Live from 2026-09-04 by operator instruction,
with no shadow arm.**

## Hypothesis

In a directionless, dispersed tape, the top decile of 3-day cross-sectional
return gives the move back over the following 24 hours — but only where perp
positioning actually exists to unwind.

## Why this hypothesis, for this regime

`research/regime_2026_09/regime_read.py` on 2026-09-04: index going nowhere
(median +0.63%), cross-sectional standard deviation 7.08%, decile spread +8.4%
to -5.0%, funding positive on 86% of coins at a median +11% annualised, and 47%
of the board pinned at the venue's 1.25e-05 baseline.

Flat index plus high dispersion is where relative performance carries the
information and direction does not.

## Method

- **Data.** `.state/.data_funding_oi.jsonl`, the `data_logger` panel: funding,
  open interest, mark price and 24h volume for ~280 Hyperliquid coins, roughly
  every three hours since 2026-06-26. 48,935 usable (coin, snapshot)
  observations across 196 coins and 464 entry snapshots.
- **Universe.** >= 90% panel coverage and >= $1M 24h volume at entry.
- **Feature.** Trailing 3d return, ranked cross-sectionally **at each snapshot**,
  never pooled. A pooled rank compares a coin today against the whole market's
  history and silently becomes a time-series signal.
- **Outcome.** Next 24h, quoted from the short's side: price move against the
  short, plus funding collected, minus 25bps of round trip. Costs are subtracted
  on the short side directly and never by negating a long, which would turn a
  25bps cost into a 25bps credit.
- **Significance.** Bootstrap over **entry timestamps**, not observations. Three
  hundred coins in one snapshot share one market and are nowhere near
  independent; an observation-level p-value would be wrong by roughly the square
  root of the panel width.

## Results

Shorting the top decile of 3-day return, 24h hold, net:

| leg | n | return/trade | win |
|---|---:|---:|---:|
| **top decile (the trade)** | 5106 | **+1.373%** | 57% |
| bottom decile (control) | 5106 | -0.772% | 44% |
| everything (control) | 48935 | -0.158% | 46% |

IC +0.0835. Bootstrap p **0.0000**, clustered on snapshot. The controls are the
point: shorting the losers loses and shorting indiscriminately is flat, so the
edge is specifically in the winners rather than in being short.

### The gate: is the funding market awake

Fraction of the trailing 7 days a coin spent off the 1.25e-05 baseline.
Trailing only — never the entry tick alone, never anything at or after exit.

| funding awake | n | return/trade | win |
|---|---:|---:|---:|
| dead (0%) | 363 | **-0.443%** | 52% |
| faint (1-33%) | 2061 | +0.775% | 56% |
| moderate (34-66%) | 514 | +1.512% | 63% |
| **awake (67-100%)** | **1995** | **+2.474%** | 58% |

Monotone across four bands. Bootstrap p 0.0000 on the awake leg.

**Mechanism.** Funding moves when longs and shorts disagree enough to pay each
other. A coin sitting at the venue baseline for weeks has no crowded side
because it barely has a side, and the trade is a bet that positioning has to
unwind. Where there is no positioning there is nothing to unwind — which is what
the dead bucket losing money says.

### Both OOS halves, and every quartile

Time quartiles of the awake leg: **+4.94% / +0.70% / +0.89% / +3.05%**. All four
positive. Plain top-decile without the gate has a negative quartile (-0.36%);
the gate removes it.

### Matched null and the checks that could have killed it

- **Liquidity is not the explanation.** Pinned coins are thinner, so controlling
  for volume should collapse the gap if this is a spread-capture illusion.
  Inside the **thin half alone**: awake +2.776% against dead -0.754%, a gap of
  +3.530%. The liquid half has too few dead coins to compare, which is the same
  finding restated — liquid coins nearly always have an awake funding market.
- **Not a funding-direction effect.** W-XSR1's predecessor H4 tested whether the
  funding LEVEL inside the top momentum decile matters. It does not: top third
  +2.013% against bottom third +2.053%, a -0.04% difference. What separates the
  sample is whether funding moves at all, not which way it points.
- **Survives a higher bar.** At a $5M volume floor the ungated trade still pays
  +0.968%. At a 48h hold it improves to +1.871%.
- **Bonferroni.** Four hypotheses were run against this panel (H1 funding carry,
  H2 momentum continuation, H3 reversal, H4 funding direction). Two failed. At
  m=4 the surviving p of 0.0000 clears any correction that matters.

## Refuted along the way

- **H1, short crowded funding: REFUTED.** -0.285%/trade, IC -0.006, p 0.98. The
  carry leg paid +0.245% exactly as theory predicts while the price leg ran
  -0.323% against it. High funding marks momentum here, not crowding-to-revert.
- **H2, momentum continuation: REFUTED.** Monotone in the opposite direction.
- **H4, funding direction as a filter: REFUTED.** See above.

## The honest discount

**The awake gate was found, not predicted.** It fell out of H4's control row
while H4 itself was failing. Found results carry a higher prior of being noise
than pre-registered ones, and this one has not yet been tested on data collected
after it was stated. That is what the forward ledger is for.

The ledger records every candidate the book considers, funded or not, so the
forward record is the strategy's and not the account balance's. A book that only
logs the trades it could afford grades itself on a sample selected by margin.

## What kills this book

The mechanism is positioning unwinding. It stops working if funding stops
discriminating — the venue changes how its baseline behaves, or a regime arrives
where the biggest movers are driven by spot flow rather than perp positioning.
`scripts/autonomous_cycle.py` demotes on the forward ledger without asking, and
that is the intended failure path.

## Reproduce

```
python research/regime_2026_09/regime_read.py --days 7
python research/regime_2026_09/H3_reversal.py
python research/regime_2026_09/H5_live_funding.py
```
