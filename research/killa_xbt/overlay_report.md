# Range-location overlay on the live books (Lane K2, 2026-07-11)

Question (SPEC Part 4E, the one open item after K1 refuted all standalone
families): does a range-structure gate improve the entries our EXISTING books
already take? The falsifiable claim: "PASSing mid-range entries (state in
RANGE family, price_location 0.35-0.65) improves the books."

Data: `research/killa_xbt/overlay_results.json`.
Code: `scratchpad/overlay_run.py` (session scratchpad; imports K1's frozen
`hermes_trader/indicators/range_structure.py` and
`scripts/backtest_range_structure.py` unmodified).

## Method

- Shadow-ledger jsonl is too thin to grade: extreme_fade holds 56 rows, all
  from 2026-07-09/10 (no forward bars to grade them against); engulf_short and
  funding_spike_short keep no episode ledger locally. So entries were
  reconstructed from the exact live rules over the full 208d cache
  (40 coins, 1h -> daily, 2025-12-13 to 2026-07-09):
  - extreme_fade: last completed daily ret <= -12% -> LONG, 3d hold, 20% stop
  - engulf_short: bearish full-body engulf (body ratio >= 1.0) -> SHORT, 1d hold, 20% stop
  - funding_spike_short: trailing-24h funding z >= 2.0 vs own 30d daily sums
    -> SHORT, 5d hold, 15% stop (funding history covers 2026-03-29 to 2026-06-27,
    so this book only exists in H2)
- Live discipline: decision at daily close, entry next open, one position per
  coin (non-overlapping holds), pessimistic intra-bar stops, net of 25 bps +
  realized hourly funding.
- Each entry tagged with the 4h `RangeStructure` snapshot at the decision
  instant (last completed 4h bar, same closing moment - no lookahead).
- Gate comparison: kept vs passed mean net return; permutation test, 2000
  label shuffles, seed 20260711. Halves split at the pooled episode-time
  midpoint (2026-04-16).

## Per-book bucket table (n / mean net EV25 / win rate)

extreme_fade (LONG, n=100, book mean +3.02%, win 66%):

| location bucket | n | EV25 | win% |
|---|---|---|---|
| 0.00-0.20 | 75 | +4.06% | 72% |
| 0.20-0.35 | 9 | +1.29% | 56% |
| 0.35-0.65 | 13 | +2.04% | 46% |
| 0.65-0.80 | 1 | -20.28% | 0% |
| state RANGE-family | 1 | +2.27% | - |
| mid-range gate | fired 0 / 100 | - | - |

engulf_short (SHORT, n=533, book mean +0.03%, win 55%):

| location bucket | n | EV25 | win% |
|---|---|---|---|
| 0.00-0.20 | 194 | +0.09% | 55% |
| 0.20-0.35 | 118 | -0.26% | 51% |
| 0.35-0.65 | 164 | -0.03% | 57% |
| 0.65-0.80 | 40 | -1.01% | 45% |
| 0.80-1.00 | 5 | +2.21% | 80% |
| mid-range gate | fired 15 / 533 | passed -0.44% vs kept +0.04% | p=0.35 |

funding_spike_short (SHORT, n=26, book mean +1.55%, win 62%):

| location bucket | n | EV25 | win% |
|---|---|---|---|
| 0.00-0.20 | 1 | -14.97% | 0% |
| 0.20-0.35 | 2 | +10.38% | 100% |
| 0.35-0.65 | 7 | -1.02% | 57% |
| 0.65-0.80 | 4 | +3.88% | 75% |
| 0.80-1.00 | 12 | +2.18% | 58% |
| mid-range gate | fired 2 / 26 | passed -4.87% vs kept +2.09% | p=0.19 |

Pooled (n=659, mean +0.54%): gate fires 17 (2.6%); passed -0.96% vs kept
+0.58%, delta +1.54%/gated-episode, permutation p=0.181 (strict state==RANGE
variant: 14 fired, delta +1.98%, p=0.136).

## Stability across halves (pooled gate)

| half | n | gate fired | passed mean | kept mean | gate helps? | p |
|---|---|---|---|---|---|---|
| H1 (to 2026-04-16) | 309 | 5 | +1.90% | +0.85% | NO (-1.06%) | 0.63 |
| H2 (after) | 350 | 12 | -2.15% | +0.34% | yes (+2.49%) | 0.10 |

The sign flips across halves. Whatever the pooled point estimate suggests, it
is not a stable effect.

## K1 inversion check

K1's descriptive panel found unconditional RANGE-state 0.00-0.20 longs were the
WORST bucket (-1.11%/24h: "cheap in range keeps falling"). Inside the books:

- extreme_fade (longs): 0.00-0.20 is the BEST bucket (+4.06%, n=75, win 72%;
  RANGE-state subset n=0). The panel inversion does NOT carry into the book -
  conditioning on a -12% crash day flips low-bucket drift from negative to
  positive. Location adds nothing the crash signal doesn't already dominate.
- engulf_short (shorts): RANGE-state 0.00-0.20 short entries earn +1.54%
  (n=19). This is CONSISTENT with the panel (negative long-drift at range-low
  = profitable shorts), not an inversion.
- funding_spike_short: n=1 in that cell (-14.97%), no inference.

## Verification of K1's headline numbers

- Re-ran K1's frozen loc_fade cell (h18, 0.20/0.80) on the untouched test
  period from a fresh engine build: n=102, mean -2.6072%, win 30.4%, t=-3.73 -
  byte-identical to `validation_results.json`. Reproduction PASSED.
- Read-through of validation_results.json confirms the coordinator's summary:
  loc_fade train +1.92% p=0.008 / test -2.61% p=0.985; dev_reject train +1.57%
  p=0.004, validate +0.13% p=0.163, test +0.15% p=0.406; acceptance_bo validate
  -0.59% p=0.997, test -0.23% p=0.986. One nuance, not a discrepancy:
  acceptance_bo's TRAIN mean was positive (+0.40%) but never significant
  (p=0.68); "never positive" is accurate for validate/test.
- Flagged, not silent: before the scope-change message arrived this session had
  (a) added a now-removed `--stage overlay` to K1's backtest script (reverted
  byte-equivalent, 661 lines, still parses; overlay logic moved to
  `scratchpad/overlay_run.py`), (b) re-run `--stage daily` because
  `daily_majors.json` was a 0-byte file that crashes the stage - the refetch
  reproduced every daily number exactly (fixed seed) and left a valid 382KB
  cache, and (c) authored `validation_report.md` (it did not pre-date this
  session; its numbers are K1's, read from validation_results.json).

## Answer

**No live book earns a range-location gate recorder.** The mid-range PASS gate
fires on 2.6% of real entries, never touches the strongest book (crash-fade
entries structurally arrive at range-bottom or in TREND_DOWN/BREAKOUT_DOWN
states), flips sign across time halves, and its pooled effect (p=0.14-0.18) is
indistinguishable from label noise. The location buckets inside each book
either contradict the doctrine (extreme_fade earns MOST at range-bottom) or
carry n too small to act on (funding_spike n=2, engulf 15/533). Outcome A for
the overlay as well: no recorder, no shadow fields.

## Repro

```
.venv/bin/python /private/tmp/claude-501/-Users-julian-dev-Documents-code-hermes-trader/f77b77de-96c2-4bf2-a574-1fd5aeebb7f2/scratchpad/overlay_run.py
# writes research/killa_xbt/overlay_results.json
```
(The runner lives in the session scratchpad per lane write scope. /tmp is
volatile: if this analysis ever needs a re-run after cleanup, the Method
section above plus the frozen K1 modules specify it completely - entry rules,
exit structure, gate definition, permutation test, and seed 20260711.)
