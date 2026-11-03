# Funding-extreme reversion test, 2026-11-03

Script: `services/strategies/evals/funding_extreme_test.py`. Report:
`/tmp/funding-extreme/report.json`, `/tmp/funding-extreme/summary.csv`.

Old alpha-swarm research (git history, commit
`a7710a4532b8d6cc41ccaf2efd90344b60266db0`): on 40 crypto majors, a coin's
own trailing-24h funding z-score vs its own trailing-30d distribution
hitting z >= 2.0 (a crowded long) was a SHORT signal that cleared
`research/EVIDENCE_DOCTRINE.md`'s bar (net +4.6%/event at 25bps, both OOS
halves positive, null p=0.0006). The question here: does the same rule
clear the same bar on pathiel's own two live instruments, xyz:BRENTOIL and
xyz:SILVER?

## Verdict

**Refute. Do not turn this on.** `Params.funding_z_filter` ships off by
default, wired and gate-tested, but there is nothing here to turn it on
for.

- **Event study: 0 of 36 cells pass.** 2 coins x 2 sides (crowded-long
  short, crowded-short long) x 3 thresholds x 3 holds, every one checked
  against EVIDENCE_DOCTRINE.md's four gates (positive net of 25bps, both
  halves positive, beats a 2000-draw matched null at a Bonferroni-corrected
  alpha) plus a floor of 10 events. Most fail outright on sample size: a
  single instrument fires far fewer independent crowded-long events than
  D4's 40-coin panel did (D4 pooled to n=53; Brent alone gives 1 to 3). The
  single cell with the strongest-looking numbers -- silver, long side (fade
  a crowded short), z>=1.5, 1-day hold: n=20, mean net +0.43%, both halves
  positive -- still fails, because its raw p-value (0.049) is nowhere near
  the 0.0014 the 36-cell correction demands.
- **As a filter, it does nothing on the data that matters.** On Brent's
  live journal and its oil replay, the filter never once refuses a real
  entry at any threshold -- no qualifying funding spike happened to fall in
  those calendar windows. On silver's live journal it refuses dozens of
  ticks but still changes zero trades and zero final equity: every
  refusal landed on a tick that would not have filled anyway.
- **Standalone short-the-crowded-long "works" on 1 to 7 trades.** That is
  not evidence of anything; it is the same sample-size problem the event
  study already found, restated as a backtest.

## Why: the mechanism doesn't transfer to one instrument

D4's edge is cross-sectional: 40 coins, pooled, to reach n=53 independent
z>=2.0 events. A single instrument's own funding rarely spikes extreme
enough, enough times, for the same statistical argument to carry over.
That matches a refutation already in this codebase's own history
(`research/EVIDENCE_DOCTRINE.md`'s refuted list, `W-FND1`: "funding
z-score battery -- 8 cells, majors, 208d. Zero survivors. Funding on
majors is asymmetric: +2sigma fires 20x, -2sigma fires 219x"). Brent shows
the same asymmetry, more extreme: over 244 days, z>=+2.0 fires 19 hours
(3 independent events); z<=-2.0 fires 429 hours (11 events). The crowded-
long signal this rule needs essentially never happens on Brent.

## 1. Funding facts

| Coin | Window | Hours | Long cost/day | Long pays how often |
|---|---|---|---|---|
| xyz:BRENTOIL | 2026-03-04 to 2026-11-03 (244.3d) | 5,863 | **-0.062%/day** (longs were PAID) | 59.3% of hours |
| xyz:SILVER | 2025-12-26 to 2026-11-03 (312.9d) | 7,510 | **+0.027%/day** (longs pay) | 90.5% of hours |

C5 (`C5_no_long_edge_in_xyz.md`) found xyz tokenized-equity funding
structurally charges longs. **That holds for silver** (small positive
cost) but **does not hold for Brent** over this window: Brent's average
funding rate is net negative, so on average a Brent long has been paid to
hold, not charged. The direction flips hour to hour (41% of Brent hours
still have a positive rate), but the mean over the whole 8-month window is
negative.

How often the z-score (trailing-24h funding vs trailing-30d distribution)
crosses each threshold -- hours it holds, and collapsed into independent
events (one per spike, not one per hour it lasts):

| Coin | Threshold | z >= +T (crowded long) | z <= -T (crowded short) |
|---|---|---|---|
| Brent | 1.5 | 34 hrs / **2 events** | 623 hrs / 15 events |
| Brent | 2.0 | 19 hrs / **3 events** | 429 hrs / 11 events |
| Brent | 2.5 | 1 hr / **1 event** | 329 hrs / 9 events |
| Silver | 1.5 | 502 hrs / **26 events** | 295 hrs / 29 events |
| Silver | 2.0 | 286 hrs / **16 events** | 129 hrs / 11 events |
| Silver | 2.5 | 174 hrs / **9 events** | 80 hrs / 6 events |

The short signal (bold column) is the one D4 validated. Brent gives 1-3
events over 8 months; silver gives 9-26. Neither is close to D4's own
n=53.

## 2. Event study

Forward close-to-close return after each event, side-adjusted (short
profits when price falls) and netted of a flat 25bps round trip, against
2,000 random-timestamp draws of the same size and side, split into
chronological halves. Candle history on this HIP-3 dex only goes back to
2026-04-09 (shorter than funding retention -- see Caveats), so the event
study's window is 2026-04-09 to 2026-11-03 for both coins, a subset of the
funding window above.

Short side (crowded long -> fade short), 5-day hold, the row D4 itself led
with:

| Coin | T | n | mean net | half 1 | half 2 | p | passes? |
|---|---|---|---|---|---|---|---|
| Brent | 2.0 | 3 | -1.78% | +2.17% | -9.69% | 0.591 | no (n<10) |
| Silver | 2.0 | 10 | -1.34% | -1.41% | -1.27% | 0.897 | no |

Full grid (36 cells, `/tmp/funding-extreme/summary.csv`): **0 pass**. The
best-looking cell by raw p-value, long side (fade a crowded short):

| Coin | Side | T | Hold | n | mean net | half 1 | half 2 | p | Bonferroni alpha | passes? |
|---|---|---|---|---|---|---|---|---|---|---|
| Silver | long | 1.5 | 1d | 20 | +0.43% | +0.69% | +0.18% | 0.049 | 0.0014 | **no** |

This is the one cell with enough events, a positive net return, and both
halves agreeing in sign -- and it still fails, by more than 30x on the
corrected significance bar. Nothing else in the grid gets that close.

## 3. As a filter on the live desk

`Params.funding_z_filter` (new field, additive, default `None`): refuses a
new long while z >= X, a new short while z <= -X. Tested at X = 1.5 / 2.0
/ 2.5 against `live_brent_params()` / `live_silver_params()` (the 15% risk
cap included).

| Dataset | Theses | Live final / trades | z>=1.5 final / trades / refused | z>=2.0 | z>=2.5 |
|---|---|---|---|---|---|
| Brent live journal (2026-10-16 to 11-03) | 25 | $285.31 / 14t | same / same / **0** | same | same |
| Brent replay (Aug 31 - Sep 29) | 30 | $57.78 / 18t | same / same / **0** | same | same |
| Brent replay-30b | 0 | -- | no theses found, skipped | -- | -- |
| Silver live journal (2026-10-21 to 11-03) | 12 | $84.50 / 1t | same / same / 104 refused, 0 changed | same / 36 refused | same / 32 refused |

On both real-trading datasets that matter (the journal and its direct
replay), the filter is a no-op: either it never finds a qualifying funding
spike in that calendar window (Brent, every threshold), or it refuses
plenty of ticks but none of them were ever going to fill anyway (silver).
`replay-30b`'s `theses/` directory is empty (checked directly: 0 files),
so it reports nothing to test -- not a bug, there is nothing there.

The always-long / always-short / daily-sign controls (full ~7-8 month
history, hourly ticks -- a diagnostic benchmark, not a live rule) do move
under the filter, both directions depending on dataset and threshold
(e.g. Brent always-short: $0.35 to $1.13-$1.47 final; silver daily-sign:
$131.81 to $123.82-$166.28). These are included for completeness, not as
a basis for the verdict: an always-long/always-short series is not
something the desk trades.

## 4. Standalone: short-the-crowded-long, as a reference

A direction series that shorts purely off z >= T (no thesis, no
structure), run through each coin's own live stop/target/fee mechanics at
a $100 reference slice (not the desk's real allocation):

| Coin | T | Trades | Win rate | Total R | Final ($100 start) |
|---|---|---|---|---|---|
| Brent | 1.5 | 4 | 50% | +12.74R | $263.53 |
| Brent | 2.0 | 4 | 50% | +12.71R | $263.27 |
| Brent | 2.5 | 0 | -- | 0 | $100.00 (never fired) |
| Silver | 1.5 | 7 | 57% | +5.84R | $183.12 |
| Silver | 2.0 | 1 | 100% | +2.61R | $135.55 |
| Silver | 2.5 | 1 | 100% | +2.61R | $135.55 |

These numbers look good and mean nothing: 1 to 7 trades is a coin flip's
worth of sample. This is exactly the sample-size problem section 2 found,
restated as a P&L curve. Reported as the requested reference, not as
evidence of an edge.

## Caveats

- **Candle history is shorter than funding history on this HIP-3 dex.**
  `candleSnapshot` for both xyz:BRENTOIL and xyz:SILVER starts at
  2026-04-09 13:00Z no matter how far back it's asked (checked directly:
  a request for 30 days earlier returns one bar, the venue's own first
  one), while `fundingHistory` goes back further (Brent to 2026-03-04,
  silver to 2025-12-26). Section 1's funding facts use the full funding
  window; sections 2 and 4 (which need prices) are bounded to the shorter
  candle-covered window.
- **Single-instrument event studies are underpowered by construction.**
  This is the headline finding, not a footnote: D4 needed a 40-coin panel
  to reach n=53. Rerunning the exact same check on one coin at a time
  will usually fail on sample size alone, whatever the true effect is. A
  negative result here does not re-refute D4 on majors; it says the
  mechanism needs breadth this account doesn't have on two instruments.
- **The null is a same-side random-timestamp draw**, matching
  EVIDENCE_DOCTRINE item 3 ("same coin, same holding rule, random entry
  times"), not a cross-sectional null like D4's. A single coin has no
  cross-section to draw from.
- **The standalone test's exit is the live desk's own stop/target rule**,
  not D4's fixed h-day hold -- a deliberate, documented difference (see
  the module's own docstring), because "with fees, as a reference" reads
  most naturally as "through the mechanics this desk actually trades
  with."

## Reproduce

```sh
python -m services.strategies.evals.funding_extreme_test
```

Gate tests: `services/strategies/tests/test_funding_signal.py` (the pure
z-score/event/filter helpers and the sim hook, synthetic, no network),
`services/strategies/tests/test_funding_extreme_eval.py` (the event-study
math, the matched null, the doctrine verdict gates).
