# Range-structure validation report (KillaXBT methodology, quantitative half)

Lane K2, 2026-07-11. SPEC: `research/killa_xbt/SPEC.md` Parts 3-7, 9-10 (quant).
Code: `hermes_trader/indicators/range_structure.py` (research-only),
`scripts/backtest_range_structure.py`, `tests/test_range_structure.py` (26 gate
tests, all green, 0.12s). Results: `research/killa_xbt/validation_results.json`.

## Conclusion

**Outcome A: REJECT.** No range-structure feature family survives out-of-sample.
All four families (range-location fade, deviation-rejection, acceptance-breakout,
combined) pass the train stage, fail validate, and fail the untouched test period.
The spec's key falsifiable claim ("state==RANGE and price_location 0.35-0.65 means
PASS improves outcomes"), tested directly on 659 reconstructed entries of our live
books, is **not confirmed**: directionally right (passed entries -0.96% vs kept
+0.58% net) but p=0.18, and the gate fires on only 2.6% of real entries, so it is
near-inert on the books we actually trade. Do not wire anything, not even shadow
fields.

## Data and design

- 40 Hyperliquid coins, 1h candles 2025-12-13 to 2026-07-09 (208d, `hourly_ext.json`
  from W-H0, validated against dataset.json), aggregated to 4h decision bars
  (hole-safe aggregation, incomplete groups dropped).
- Daily-majors replication: BTC/ETH/SOL 1d, 2020-08 to 2026-07 (2152 bars,
  `daily_majors.json`), split in untouched halves. Addresses one-cycle dependence.
- Funding: realized hourly rates (`research/alpha_swarm/funding.json`, 2026-03-29
  to 2026-06-27), per-coin median fill elsewhere.
- Costs: 25 bps round trip + funding, all results net; 2x-cost stress reported.
- Fills: decision at 4h close, entry at NEXT bar open. Pessimistic intra-bar:
  stops assumed hit before any favourable excursion.
- Episodes: per-coin non-overlapping holds. Nulls: matched same-coin,
  same-direction, same-hold random-time Monte Carlo (2000 reps on validate/test).
- Walk-forward: train to 2026-03-15, validate to 2026-05-15, untouched test after.
  Grids pre-registered (spec 4C thresholds), tuned on train only, frozen.
  Bonferroni context: grid sizes 12/36/9/9 reported per family.
- Lookahead protections (enforced by tests): boundary window excludes the current
  bar, ATR taken from the prior bar (a poke cannot inflate its own threshold),
  future bars provably do not change past snapshots, accumulation/redistribution
  labels are trailing-only candidates, thresholds never touched validate/test.

## Walk-forward verdict table (frozen params, net of 25 bps + funding)

| family | frozen cell | train n / mean / p | validate n / mean / p | test n / mean / p | verdict |
|---|---|---|---|---|---|
| loc_fade (range-location filter) | h18, loc<=0.20 long / >=0.80 short | 112 / +1.92% / 0.008 | 103 / +0.46% / 0.162 | 102 / **-2.61%** / 0.985 | REFUTED |
| dev_reject (deviation-rejection fade) | h6, 0.50 ATR poke, any state | 67 / +1.57% / 0.004 | 50 / +0.13% / 0.163 | 26 / +0.15% / 0.406 | NO EDGE |
| acceptance_bo (k-closes breakout) | h18, k=1 | 865 / +0.40% / 0.681 | 772 / **-0.59%** / 0.997 | 650 / -0.23% / 0.986 | REFUTED |
| combined (range state + location + deviation) | h12, 0.25 ATR | 17 / +2.32% / 0.086 | 10 / -0.19% / 0.394 | 7 / -1.61% / 0.731 | REFUTED |

Robustness on the frozen cells: validate drop-top-3 flips loc_fade to -0.14% and
dev_reject to -0.67% (the "edge" is a few lucky trades); 2x costs kill everything;
held-out-coins test split is negative for every family (loc_fade -3.38%/ep).
Win rates on test: loc_fade 30.4%, dev_reject 46.2%, acceptance 45.5%, combined 14.3%.

Baselines for context (net, hold 6x4h): unconditional long -0.38/-0.07/-0.34% per
period; EMA-trend long and short both negative everywhere. The tape is a bear
regime where nothing passive earns; the families do not beat their matched nulls.

## Daily-majors replication (BTC/ETH/SOL, 2020-2026, halves split)

No family is significant in either half (all null p >= 0.21). dev_reject is
strongly negative in the first half (-7 to -12%/ep). acceptance_bo is +1.4-1.7%
first half but p ~ 0.43-0.59 (bull-half beta, not selection) and +0.23% p=0.72
second half. The structures do not generalize across cycles either.

## Descriptive panel (does location predict anything at all?)

Forward net long-drift by location bucket, hold 6x4h, all states: -0.23 / -0.49 /
-0.33 / -0.14 / -0.06% for buckets 0-0.2 / 0.2-0.35 / 0.35-0.65 / 0.65-0.8 /
0.8-1.0. Within RANGE state: -1.11 / -0.18 / -0.49 / +0.24 / -0.08%. Mid-range is
not distinctly dead money; range-bottom is the worst bucket (in a bear tape,
"cheap in range" keeps falling). Absolute-move size is also flat across buckets
(3.1-4.4% gross), so mid-range is not even quieter. Hold 18x4h shows the same
non-pattern.

## Live-book overlay: the mid-range PASS claim, tested where it matters

(Full detail: `research/killa_xbt/overlay_report.md` and `overlay_results.json`.)

Reconstructed the live/validated books' historical daily entries from their exact
live rules (extreme_fade: daily ret <= -12% long, 3d hold, 20% stop; engulf_short:
bearish full-body engulf short, 1d hold; funding_spike_short: funding z >= 2.0
short, 5d hold, 15% stop), simulated with live exits + costs, tagged each entry
with the 4h range snapshot at the decision instant.

| book | n | mean net | gate fired | passed mean | kept mean | gate delta | perm p |
|---|---|---|---|---|---|---|---|
| extreme_fade | 100 | +3.0% | **0** (never) | - | - | 0 | - |
| engulf_short | 533 | +0.03% | 15 (2.8%) | -0.44% | +0.04% | +0.48% | 0.345 |
| funding_spike_short | 26 | +1.55% | 2 | -4.87% | +2.09% | +6.96% | 0.188 |
| pooled | 659 | +0.54% | 17 (2.6%) | -0.96% | +0.58% | +1.54% | 0.181 |

Answer to the claim: **not confirmed.** Three reasons. (1) Coverage: crash-fade
entries structurally never occur mid-range (they land at range-bottom or in
TREND_DOWN/BREAKOUT_DOWN states), so the gate cannot touch the strongest book.
(2) Significance: the pooled effect is the right sign but p=0.18 (strict RANGE
variant p=0.14) on 17 gated episodes; indistinguishable from label noise.
(3) The by-state breakdown contradicts the doctrine where n is largest:
funding_spike shorts earn MOST in TREND_UP (+3.5%, n=16), and engulf shorts in
strict RANGE state are positive (+0.35%, n=46). MAE/MFE are symmetric across
buckets (walkforward test cells: MAE -7 to -9% vs MFE +5 to +7%, no
location-dependent asymmetry).

## Limitations

- 4h-frame results cover one 208d bear regime (mitigated by the 6y daily-majors
  replication, which also fails).
- Funding history covers 90 of 208 days; median fill elsewhere (small rates,
  second-order vs the 25 bps).
- funding_spike overlay n=26; its gate cells are n=2. Nothing hinges on them.
- Reconstruction ignores live capital/margin gates, so overlay n exceeds what the
  books actually took; that biases toward MORE gate opportunities, and the gate
  still does nothing.
- Survivor universe (today's 40 liquid coins), same caveat as every cache study.

## Recommendation (SPEC Part 9)

**Outcome A - reject.** No config flag, no shadow fields, no live wiring.
`range_structure.py` stays as a tested research-only feature library (it is the
first lookahead-safe range/deviation/acceptance implementation in the repo and is
reusable for future overlays), enforced non-live by
`test_not_imported_by_live_modules`. This also independently re-confirms two
standing doctrines: breakouts (now including the untested k-closes acceptance
variant) are matched-null dead here, and failed-breakout fading only ever had
support in the rally_exhaustion form, not as a generic range-boundary trade.

## Repro

```
.venv/bin/python -m pytest tests/test_range_structure.py -q          # 26 pass
.venv/bin/python scripts/backtest_range_structure.py --stage all     # panel/baselines/walkforward
.venv/bin/python scripts/backtest_range_structure.py --stage daily   # majors replication (network)
# live-book overlay: see research/killa_xbt/overlay_report.md (repro command inside)
```
