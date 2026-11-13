# Rule fusion sweep, 2026-11-13

**Verdict: NONE. No fusion of two or more rules passes.** 8,747 combinations
of the eight optional rules were run on the live oil journal (71 theses,
10-18 to 2026-11-13 02:00Z), twice: once on the baseline every earlier test
used, once on that baseline plus the live 20/15 leverage fit, which the
simulator had been leaving out. On the first baseline exactly one combo
clears the old bar and all four guards, and it is a single rule: the entry
delay at 180 minutes (`delay180`, +$237.40 vs +$142.82, ROI 130% vs 78%,
DD 17.7% vs 28.9%, WTI +$123.72). On the fitted baseline four pass:
`delay180` again (+$187.40 vs +$139.03) and three delay+half-off pairs
that fail on the first baseline, so they are not stable. Every other
top-10 row is `delay180` plus a passenger rule that changes nothing
(marginal gain $0.00). The rules do not stack: the trend veto blocks every
trade the delay blocks and more; the cap, the reclaim and the one-entry
rule each remove at most one trade the delay already removes; the profit
lock, the half-off and the resting edge change exits and cancel against
nearly everything.

**The delay stays OFF.** It passes the guards on both baselines and its
plateau is wide (90 to 300 minutes all beat both baselines), but: it is the
end of the range the attribution tested, not the tested value (120 failed
drop-best-day, +0.01R left); it was the single survivor of 8,747 tries;
it changes trades in only 2 of the 4 weeks (it never fires in October);
all of its edge is skipping stops on minutes-old theses during the 11-04
and 11-09/10 slides; and on the real fills both November winners were
bought inside 2h of a rewrite. Gut call: not yet. The exact switch, if the
operator wants it anyway, is at the end. Nothing in the live config
changed.

Data as of 2026-11-13T02:00Z candles. Every number is from
`services/strategies/evals/rule_fusion_test.py`; reproduce commands at the
end.

## Corrected 2026-11-13: leverage fit

The gap in section 5 is fixed. `live_params.live_brent_params` now carries
`entry_fit_leverage` read off `next_trade.leverage_fit_for(BRENT)` (20/15,
the same candidates `services/strategies/api.py` hands the live tick and
`scripts/pathiel-mcp-server.py` `handle_place_bracket` fits the order at),
so every eval that builds "the live rule" through it sizes a wide-stop
entry the way the desk does: at 15x instead of refusing it at 20x. The
sweep's default baseline is the fitted one; `--no-lev-fit` re-runs the old
unfitted baseline for comparison. Both were re-run on the same candles
(to 2026-11-13 04:30Z, 2.5 hours past the first run's 02:00Z), 71 theses,
$182.09 start, 6% risk:

| row | was (unfitted, 02:00Z) | unfitted, 04:30Z | **fitted, 04:30Z (the live rule)** | verdict was -> now |
|---|---|---|---|---|
| baseline net / ROI / DD | +142.82 / 78.4% / 28.9% | +142.82 / 78.4% / 28.9% | **+142.82 / 78.4% / 27.7%** | - |
| baseline last third / WTI | +27.24 / -24.42 | +27.24 / -22.90 | **-1.11 / -42.92** | - |
| delay120 | +187.38, +44.56 over | +187.38, +44.56 over (fails drop-best-day) | **+144.34, +1.52 over** (fails the old bar: last third -0.22) | fail -> fail |
| delay180 | +237.40, DD 17.7%, WTI +123.72 | +237.40, DD 17.7%, WTI +123.72 | **+187.40, DD 20.6%, WTI +41.45, +44.58 over** | PASS -> PASS |
| trend120 | +91.31, -51.51 | +91.31, -51.51 | **+134.06, -8.76** | fail -> fail |
| reclaim24h | +112.42 (section 5) | +91.49, -51.33 | **+115.88, -26.94** | fail -> fail |
| cap2 | +125.89 (section 5) | +143.07, +0.25 | **+129.52, -13.30** | fail -> fail |
| one_entry1 | +142.82 (no-op) | +142.82, +0.00 | **+160.74, +17.92** (fails old bar, WTI -69.49) | fail -> fail |
| half3R | +118.86 | +118.86, -23.96 | **+142.43, -0.39** | fail -> fail |
| best fusion by net | delay180+edge0.15 +215.15 (fitted) | delay180+lock2R +237.40 (passenger, marginal fail) | **delay180+edge0.15 +215.15**, loses 2 of 4 weeks | fail -> fail |
| passing combos | 1 (delay180) | 1 (delay180) | **4: delay180, delay180+half3R +199.83, delay120+half3R +154.95, delay90+half3R +154.71** | - |
| funnel: beat / old bar / dbd / weeks / plateau / marginal / all | 592 / 69 / 870 / 342 / 123 / 17 / 4 (fitted) | 678 / 246 / 144 / 216 / 269 / 6 / 1 | **572 / 69 / 870 / 342 / 122 / 16 / 4** | - |

The two baselines close at the same cent (+$142.82) on different trades:
the fitted run holds the 11-10 10:00Z long at 88.65 open at the data end
(marked at 93.35, +$58.06 unrealized) where the unfitted run's 88.33 entry
filled its 93.81 target on 11-12 (+$80.74); final equity 324.906 vs
324.909. The harness's `fit_diagnostic.identical` now compares trade lists,
not the rounded net, so it reads DIFFERS.

**delay180 under the fit: PASS, every bar and guard.** Old bar: net
+$187.40 vs +$142.82, DD 20.6% vs 27.7%, neighbour delay120 +$144.34 above
the baseline, last third +$23.77 vs -$1.11, WTI +$41.45. Drop-best-day:
best day 11-12 +$58.56, rest +$42.87 and +2.45R (not a fluke). Weeks:
2 won / 0 lost / 2 flat, leave-one-week-out minimum +$28.55. Plateau: yes.
Marginal: +$44.58 from its one rule. The three delay+half3R pairs pass on
the fitted baseline only (on the unfitted one delay180+half3R fails
marginal, the 90/120 pairs fail drop-best-day and weeks): still not a rule.
The verdict above stands: NONE for fusions, delay180 the single survivor,
**the delay stays OFF** for the reasons listed (end of the tested range,
1 of 8,747, two of four weeks, November only). What changed is the
attribution's delay120: it no longer clears its own bar on the live rule
(TRADE_ATTRIBUTION_2026-11-13.md, corrected).

The delay plateau on the corrected data (`delay_plateau.py` in the eval
scratch dir reproduces it from the same contexts; net $ / DD / trades /
WTI $):

| delay min | unfitted | fitted (live) |
|---|---|---|
| off | +142.82 / 28.9% / 16 / -22.90 | +142.82 / 27.7% / 16 / -42.92 |
| 90 | +187.41 / 27.5% / 14 / +28.97 | +144.10 / 25.6% / 16 / +2.89 |
| 120 | +187.38 / 27.5% / 14 / +42.71 | +144.34 / 25.6% / 16 / +7.25 |
| 150 | +211.69 / 22.7% / 13 / +73.15 | +164.77 / 25.5% / 15 / +4.46 |
| 180 | +237.40 / 17.7% / 12 / +123.72 | +187.40 / 20.6% / 14 / +41.45 |
| 210 | +264.91 / 15.5% / 11 / +138.53 | +211.63 / 15.4% / 13 / +52.28 |
| 240 | +222.68 / 15.5% / 10 / +138.53 | +174.42 / 15.5% / 12 / +52.23 |
| 300 | +201.14 / 15.5% / 10 / +83.88 | +220.41 / 13.1% / 11 / +75.46 |
| 360 | +136.74 / 15.5% / 8 / +83.88 | +152.76 / 10.2% / 9 / +54.22 |

Gate tests: `tests/test_live_params.py` fails the day `live_brent_params`
and `next_trade.leverage_fit_for(BRENT)` disagree (the expected value is
read off next_trade, never typed); `test_rule_fusion.py`,
`test_trend_gate.py`, `test_stop_reclaim.py` pin that their baselines
carry it. No gate test pinned a dollar figure from the unfitted baseline
(the suite went 4985 -> 4992 with the new tests and nothing else moved);
the pinned numbers lived in these docs and are corrected here.

## 1. What was fused

Each axis is an existing `desk_tick_sim.Params` switch that was tested on
its own: off, the tested value, one neighbour. The full factorial is
4 x 3 x 3 x 3 x 3 x 3 x 3 x 3 = 8,748 combos including the baseline.

| axis | Params | off / tested / neighbour | earlier test |
|---|---|---|---|
| delay | `entry_delay_min` | off / 120 / 90, 180 | TRADE_ATTRIBUTION_2026-11-13 (passed the bar, FLUKE on one day) |
| trend | `trend_gate_span` | off / 120 / 144 | TREND_GATE_TEST_2026-11-13 (failed) |
| reclaim | `reclaim_after_stops=2`, `reclaim_window_h` | off / 24h / 12h | STOP_RECLAIM_TEST_2026-11-10 (failed) |
| cap | `daily_stop_cap` | off / 2 / 1 | STOP_CAP_TEST_2026-11-02 (failed) |
| edge | `entry_mode="edge"`, `atr_rest_frac` | off / 0.25 / 0.15 | TRADE_ATTRIBUTION_2026-11-13 (failed, -$93.94) |
| one_entry | `max_stop_reentries`, `reentry_needs_1h_reclaim` | off / 1 / 2 | FINAL_STRATEGY_TEST_2026-11-04 `entries_reclaim` (rejected) |
| lock | `lock_trigger_r`, `lock_r=0` | off / 1R / 2R | PROFIT_LOCK_TEST_2026-11-01 (0 of 32) |
| half | `partial_frac=0.5`, `partial_r` | off / 3R / 4R | FINAL_STRATEGY_TEST_2026-11-04 half-off (optional, 3/4) |

Left out: the zone gate (`entry_zone_frac`) changes no trade on any
dataset (the 2R gate implies it); the funding filter never fires on the
journal. The leverage fit (`entry_fit_leverage=(20,15)`) is live since
11-04 but not in `live_params()`, so the sim baseline every test has used
omits it. It is run here as a second baseline, not as an axis (section 5).

Baseline: `trend_gate_test.base_params` (market entry off the 24h range,
0.75 stop, 0.5% offset target, 2R, 20x, no entry after a break, weekend
flat, 6% risk cap, size slippage on), $182.09 start so the numbers compare
with the earlier docs. ROI % is net over that start. The "$ at $213.80"
and "$/30d" columns are the same combo re-run from today's equity
(`account_overview`, read only). WTI is xyz:CL with the journal's sides.

## 2. The bar and the guards

Old bar (TRADE_ATTRIBUTION's five legs): net beats the baseline; max DD no
worse; every neighbour (one axis moved to its adjacent ON level) beats
the baseline; last third (its own run from the start equity) not negative
and not below the baseline's; WTI not negative.

- a. **Drop-best-day**: the $ delta over the baseline by exit day, best
  day removed, must stay positive, AND the review's R fluke rule
  (`one_day_flips`: the other days must add at least 0.25R) must not
  fire.
- b. **Weeks**: the $ delta per 7-day week of the window (4 weeks, by
  exit) is positive in more weeks than it is negative, and leaving any
  one week out keeps the total positive. The strict reading (positive in
  more than half of all 4 weeks) is also reported; no combo meets it,
  because nothing changes an October trade.
- c. **Plateau**: the neighbour leg above.
- d. **Complexity**: every rule in the combo adds value on its own:
  net(combo) minus net(combo without that rule) is positive. A combo
  carrying a $0 rule is not minimal.
- e. **Multiple testing**: the funnel below says how many combos survive
  each leg. With 8,747 tries, a lone survivor is a 1-in-8,747 event by
  construction, not evidence.

Best of the best = the smallest combo (fewest rules) that passes all of
it, ties by net.

## 3. Results on the baseline every earlier test used

| combo | rules | trades | wins | net $ | ROI % | max DD | last third $ | WTI $ | $ at $213.8 | $/30d | old bar | drop-best-day rest $ | weeks won/lost/flat | plateau | marginal | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 0 | 16 | 8 | +142.82 | +78.4% | 28.9% | +27.24 | -24.42 | +167.69 | +194.15 | - | - | - | - | - | - |
| delay120 | 1 | 14 | 8 | +187.38 | +102.9% | 27.5% | +55.95 | +42.71 | +220.01 | +254.73 | yes | +10.83 no | 2/0/2 yes | yes | yes | fail |
| trend120 | 1 | 7 | 5 | +91.31 | +50.1% | 7.2% | +0.00 | +29.26 | +107.21 | +124.13 | no | -86.89 no | 1/2/1 no | no | no | fail |
| reclaim24h | 1 | 15 | 7 | +91.49 | +50.2% | 32.9% | -5.83 | -6.96 | +107.42 | +124.37 | no | -56.91 no | 0/2/2 no | yes | no | fail |
| cap2 | 1 | 16 | 8 | +143.07 | +78.6% | 27.5% | +27.40 | +21.06 | +167.98 | +194.49 | no | +0.03 no | 2/0/2 yes | no | yes | fail |
| edge0.25 | 1 | 18 | 5 | +44.06 | +24.2% | 41.9% | -1.05 | +19.95 | +51.73 | +59.89 | no | -162.45 no | 0/4/0 no | no | no | fail |
| one_entry1 | 1 | 16 | 8 | +142.82 | +78.4% | 28.9% | +27.24 | -18.17 | +167.69 | +194.15 | no | +0.00 no | 0/0/4 no | no | no | fail |
| lock1R | 1 | 18 | 7 | +105.56 | +58.0% | 28.1% | +24.43 | -2.20 | +123.94 | +143.50 | no | -40.72 no | 0/3/1 no | no | no | fail |
| half3R | 1 | 16 | 8 | +118.86 | +65.3% | 23.8% | +11.80 | -10.90 | +139.56 | +161.58 | no | -24.35 no | 0/2/2 no | no | no | fail |
| **delay180** | 1 | 12 | 8 | +237.40 | +130.4% | 17.7% | +88.17 | +123.72 | +278.74 | +322.72 | yes | +60.85 yes | 2/0/2 yes | yes | yes | **PASS** |
| delay180+lock2R | 2 | 12 | 8 | +237.40 | +130.4% | 17.7% | +88.17 | +123.72 | +278.74 | +322.72 | yes | +60.85 yes | 2/0/2 yes | yes | no | fail |
| delay180+one_entry1 | 2 | 12 | 8 | +237.40 | +130.4% | 17.7% | +88.17 | +92.45 | +278.74 | +322.72 | yes | +60.85 yes | 2/0/2 yes | yes | no | fail |
| delay180+one_entry1+lock2R | 3 | 12 | 8 | +237.40 | +130.4% | 17.7% | +88.17 | +92.45 | +278.74 | +322.72 | yes | +60.85 yes | 2/0/2 yes | yes | no | fail |
| delay180+one_entry2 | 2 | 12 | 8 | +237.40 | +130.4% | 17.7% | +88.17 | +92.45 | +278.74 | +322.72 | yes | +60.85 yes | 2/0/2 yes | yes | no | fail |
| delay180+one_entry2+lock2R | 3 | 12 | 8 | +237.40 | +130.4% | 17.7% | +88.17 | +92.45 | +278.74 | +322.72 | yes | +60.85 yes | 2/0/2 yes | yes | no | fail |
| delay180+cap2 | 2 | 12 | 8 | +237.40 | +130.4% | 17.7% | +88.17 | +123.72 | +278.74 | +322.72 | yes | +60.85 yes | 2/0/2 yes | yes | no | fail |
| delay180+cap2+lock2R | 3 | 12 | 8 | +237.40 | +130.4% | 17.7% | +88.17 | +123.72 | +278.74 | +322.72 | yes | +60.85 yes | 2/0/2 yes | yes | no | fail |
| delay180+cap2+one_entry1 | 3 | 12 | 8 | +237.40 | +130.4% | 17.7% | +88.17 | +92.45 | +278.74 | +322.72 | yes | +60.85 yes | 2/0/2 yes | yes | no | fail |
| delay180+cap2+one_entry1+lock2R | 4 | 12 | 8 | +237.40 | +130.4% | 17.7% | +88.17 | +92.45 | +278.74 | +322.72 | yes | +60.85 yes | 2/0/2 yes | yes | no | fail |

The top 10 by net are all the same 12 trades: `delay180` with rules bolted
on that never fire once the delay has skipped the November entries
(the WTI column moves because the one-entry rule does fire on WTI). The
complexity guard removes all nine.

Funnel (guard e):

| leg | combos surviving |
|---|---|
| tried | 8747 |
| net above the baseline | 678 |
| old bar (net, DD, plateau, last third, WTI) | 246 |
| a. drop-best-day | 144 |
| b. weeks | 216 |
| c. plateau | 269 |
| d. every rule adds value | 6 |
| all of it | 1 |

The six that pass "every rule adds value" are the three delays alone,
`cap2` (+$0.25 from one trade's sizing, fails plateau and drop-best-day),
and two delay pairs that fail elsewhere. Only `delay180` passes all legs.

What `delay180` changes against the baseline (exit-day deltas +2.23R
11-04, -0.03R 11-06, +1.14R 11-09, +1.12R 11-10, 0R 11-12; dollars
+33.73 / +6.58 / +14.71 / +16.05 / +23.51, the later dollars compounding
on the earlier saved stops):

| run | entry | px | exit | R | net $ |
|---|---|---|---|---|---|
| baseline only | 2026-11-04 14:00Z | 100.63 | 11-04 15:45Z stop | -1.13 | -17.46 |
| baseline only | 2026-11-04 16:00Z | 99.243 | 11-04 16:30Z stop | -1.10 | -16.27 |
| baseline only | 2026-11-04 17:30Z | 94.178 | 11-06 21:00Z weekend | +3.42 | +46.70 |
| baseline only | 2026-11-09 07:45Z | 95.602 | 11-09 11:15Z stop | -1.14 | -18.29 |
| baseline only | 2026-11-09 13:00Z | 93.127 | 11-09 14:15Z stop | -1.12 | -17.10 |
| baseline only | 2026-11-10 06:45Z | 89.78 | 11-10 08:45Z stop | -1.12 | -16.05 |
| delay180 only | 2026-11-05 01:45Z | 93.678 | 11-06 21:00Z weekend | +3.39 | +53.28 |
| delay180 only | 2026-11-09 11:00Z | 95.575 | 11-09 11:15Z stop | -1.12 | -20.68 |
| both (sized bigger) | 2026-11-10 13:45Z | 88.333 | 11-12 19:30Z target | +6.00 | +80.74 -> +104.25 |

Six stops become two; the bounce is still bought. Against `delay120`
(TRADE_ATTRIBUTION): 120 skipped the two 11-04 stops and moved the 11-09
and 11-10 stops two hours later for the same loss; 180 skips three of
those four as well, which is why its drop-best-day rest is +2.23R where
120's was +0.01R. Both weeks it wins are November's slide; October is
untouched (weeks 0 and 1 flat).

## 4. Overlap: which rules block the same trades

Pairs at their tested values. "Removes" = baseline trades (by entry time)
the single rule drops; "both remove" = the overlap. Interaction =
net(A+B) - net(A) - net(B) + net(baseline). Labels: adds up (pair above
both singles, interaction not negative), overlap (shared removals,
interaction negative), cancel (pair below both singles), mixed.

| A | B | net A | net B | net A+B | A removes | B removes | both remove | interaction $ | label |
|---|---|---|---|---|---|---|---|---|---|
| delay120 | trend120 | +187.38 | +91.31 | +91.31 | 6 | 9 | 6 | -44.56 | overlap |
| delay120 | reclaim24h | +187.38 | +91.49 | +187.38 | 6 | 1 | 1 | +51.33 | mixed |
| delay120 | cap2 | +187.38 | +143.07 | +187.38 | 6 | 1 | 1 | -0.25 | overlap |
| delay120 | edge0.25 | +187.38 | +44.06 | +132.96 | 6 | 15 | 6 | +44.34 | mixed |
| delay120 | one_entry1 | +187.38 | +142.82 | +187.38 | 6 | 0 | 0 | +0.00 | mixed |
| delay120 | lock1R | +187.38 | +105.56 | +169.22 | 6 | 0 | 0 | +19.10 | mixed |
| delay120 | half3R | +187.38 | +118.86 | +160.38 | 6 | 0 | 0 | -3.04 | mixed |
| trend120 | reclaim24h | +91.31 | +91.49 | +91.31 | 9 | 1 | 1 | +51.33 | mixed |
| trend120 | cap2 | +91.31 | +143.07 | +91.31 | 9 | 1 | 1 | -0.25 | overlap |
| trend120 | edge0.25 | +91.31 | +44.06 | +53.22 | 9 | 15 | 9 | +60.67 | mixed |
| trend120 | one_entry1 | +91.31 | +142.82 | +91.31 | 9 | 0 | 0 | +0.00 | mixed |
| trend120 | lock1R | +91.31 | +105.56 | +63.25 | 9 | 0 | 0 | +9.20 | cancel |
| trend120 | half3R | +91.31 | +118.86 | +91.31 | 9 | 0 | 0 | +23.96 | mixed |
| reclaim24h | cap2 | +91.49 | +143.07 | +91.49 | 1 | 1 | 1 | -0.25 | overlap |
| reclaim24h | edge0.25 | +91.49 | +44.06 | +67.39 | 1 | 15 | 1 | +74.66 | mixed |
| reclaim24h | one_entry1 | +91.49 | +142.82 | +91.49 | 1 | 0 | 0 | +0.00 | mixed |
| reclaim24h | lock1R | +91.49 | +105.56 | +61.59 | 1 | 0 | 0 | +7.36 | cancel |
| reclaim24h | half3R | +91.49 | +118.86 | +74.16 | 1 | 0 | 0 | +6.63 | cancel |
| cap2 | edge0.25 | +143.07 | +44.06 | +4.82 | 1 | 15 | 1 | -39.49 | cancel |
| cap2 | one_entry1 | +143.07 | +142.82 | +143.07 | 1 | 0 | 0 | +0.00 | mixed |
| cap2 | lock1R | +143.07 | +105.56 | +107.53 | 1 | 0 | 0 | +1.72 | mixed |
| cap2 | half3R | +143.07 | +118.86 | +119.31 | 1 | 0 | 0 | +0.20 | mixed |
| edge0.25 | one_entry1 | +44.06 | +142.82 | +57.37 | 15 | 0 | 0 | +13.31 | mixed |
| edge0.25 | lock1R | +44.06 | +105.56 | -46.49 | 15 | 0 | 0 | -53.29 | cancel |
| edge0.25 | half3R | +44.06 | +118.86 | +23.37 | 15 | 0 | 0 | +3.27 | cancel |
| one_entry1 | lock1R | +142.82 | +105.56 | +105.56 | 0 | 0 | 0 | +0.00 | mixed |
| one_entry1 | half3R | +142.82 | +118.86 | +118.86 | 0 | 0 | 0 | +0.00 | mixed |
| lock1R | half3R | +105.56 | +118.86 | +84.54 | 0 | 0 | 0 | +2.94 | cancel |

How to read it:

- **The entry gates are nested, not additive.** The trend veto removes all
  six trades the delay removes plus the 11-04 and 11-10 winners; delay +
  trend = trend alone. The cap and the reclaim each remove one trade (the
  11-04 16:00Z second stop, the 11-09 13:00Z second stop) that the delay
  also removes; delay + cap = delay, delay + reclaim = delay. Nothing
  "adds up" on this baseline: no pair beats both of its singles.
- **The exit rules cancel.** The profit lock, the half-off and the
  resting edge do not remove entries (0 removals for lock, half,
  one_entry); they change how the common trades exit, and on these trades
  that costs money with nearly every partner (edge + lock -$46.49).
- **The one-entry rule is a no-op on Brent** (0 trades removed, net
  identical to the baseline); its 13 "reentry_wait" refusals fall on
  ticks that would have been refused anyway.

## 5. The same sweep on the live leverage fit

*(Was the diagnostic run; since the 2026-11-13 correction above this IS the
default baseline. The numbers below are the 02:00Z run, kept as history.)*

`live_params()` did not carry `entry_fit_leverage=(20,15)` although the
desk has traded it since 11-04 (OIL_STRATEGY.md, FINAL_STRATEGY_TEST).
With it the baseline takes entries the 20x gate refused, at 15x (10-20
15:00Z, a +2.77R target; 11-03 14:00Z, a stop that replaces the 11-04
14:00Z one), enters the 11-04 and 11-10 bottoms at different ticks
(94.51 and 88.65 instead of 94.18 and 88.33), and still holds the 11-10
long at the data end (its target untouched, marked to market in the
equity). The baseline is
+$139.03 instead of +$142.82, DD 27.7%, last third -$3.21, WTI -$43.86.
`--lev-fit` re-runs the whole grid on it.

| combo | rules | trades | wins | net $ | ROI % | max DD | last third $ | WTI $ | $ at $213.8 | $/30d | old bar | drop-best-day rest $ | weeks won/lost/flat | plateau | marginal | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline (fit) | 0 | 16 | 8 | +139.03 | +76.4% | 27.7% | -3.21 | -43.86 | +163.25 | +189.01 | - | - | - | - | - | - |
| delay120 | 1 | 16 | 9 | +144.34 | +79.3% | 25.6% | -0.22 | +7.25 | +169.47 | +196.21 | no | +6.62 yes | 2/0/2 yes | yes | yes | fail |
| trend120 | 1 | 8 | 6 | +134.06 | +73.6% | 7.2% | +0.00 | +37.66 | +157.41 | +182.25 | no | +9.26 yes | 2/1/1 no | no | no | fail |
| reclaim24h | 1 | 16 | 8 | +112.42 | +61.7% | 31.9% | -18.04 | -35.53 | +131.99 | +152.82 | no | -25.43 no | 1/1/2 no | yes | no | fail |
| cap2 | 1 | 16 | 8 | +125.89 | +69.1% | 28.8% | -10.54 | -13.77 | +147.81 | +171.13 | no | -12.56 no | 1/1/2 no | no | no | fail |
| edge0.25 | 1 | 18 | 5 | +44.06 | +24.2% | 41.9% | -1.05 | +19.95 | +51.73 | +59.89 | no | -107.75 no | 0/4/0 no | no | no | fail |
| one_entry1 | 1 | 16 | 8 | +156.75 | +86.1% | 29.8% | +6.65 | -70.26 | +184.05 | +213.09 | no | -3.09 no | 1/1/2 no | yes | yes | fail |
| lock1R | 1 | 21 | 8 | +118.98 | +65.3% | 29.2% | +5.56 | -54.45 | +139.70 | +161.74 | no | -41.80 no | 1/2/1 no | no | no | fail |
| half3R | 1 | 16 | 8 | +140.50 | +77.2% | 24.5% | -2.40 | -26.24 | +164.97 | +191.00 | no | -1.00 no | 1/1/2 no | yes | yes | fail |
| **delay180** | 1 | 14 | 9 | +187.40 | +102.9% | 20.6% | +23.77 | +41.45 | +220.03 | +254.75 | yes | +42.87 yes | 2/0/2 yes | yes | yes | **PASS** |
| delay180+half3R | 2 | 14 | 9 | +199.83 | +109.7% | 17.3% | +30.69 | +39.75 | - | - | yes | +56.02 yes | 2/0/2 yes | yes | yes | PASS here, fails on the other baseline |
| delay120+half3R | 2 | 16 | 9 | +154.95 | +85.1% | 22.4% | +5.69 | +11.69 | - | - | yes | +17.93 yes | 2/0/2 yes | yes | yes | PASS here, fails on the other baseline |
| delay90+half3R | 2 | 16 | 9 | +154.71 | +85.0% | 22.5% | +5.55 | +23.26 | - | - | yes | +17.72 yes | 2/0/2 yes | yes | yes | PASS here, fails on the other baseline |
| delay180+edge0.15 (top by net) | 2 | 7 | 5 | +215.15 | +118.2% | 17.1% | +71.48 | +224.35 | +252.61 | +292.47 | yes | +17.19 yes | 2/2/0 no | yes | yes | fail |

Funnel on the fitted baseline: 8747 tried; 592 above the baseline; old bar
69; drop-best-day 870; weeks 342; plateau 123; marginal 17; all 4.

- `delay120`, the attribution's tested value, is worth +$5.31 here, not
  +$44.56. The fitted baseline's 11-03 14:00Z stop becomes, with the
  delay, a 11-04 12:30Z entry at 101.53 that stops the same way: under
  the fit the delay moves that loss instead of removing it, and the two
  11-04 stops it skipped on the first baseline are not both there to skip.
- `delay180` holds: +$48.37 over the fitted baseline, rest +$42.87 /
  +2.45R after dropping its best day (11-12: its 88.90 entry fills the
  target at +3.31R where the fitted baseline's 88.65 entry is still open
  at the data end; the baseline's unrealized gain is in its equity, the
  exit is the difference).
- The three delay+half3R pairs pass here only. On the first baseline
  half3R costs $30.66 next to delay180 (+$206.74 vs +$237.40) and the
  pairs fail weeks and drop-best-day. A rule whose sign flips with the
  baseline's sizing detail is not a rule.
- `delay180+edge0.15` is the top row by net (+$215.15 on 7 trades, WTI
  +$224.35) and loses two of its four weeks. The resting limit at 0.15 ATR
  misses the 11-10 bottom on Brent.

The delay plateau, both baselines (net $ / WTI $):

| delay min | unfitted net | unfitted DD | unfitted WTI | fitted net | fitted DD | fitted WTI |
|---|---|---|---|---|---|---|
| off | +142.82 | 28.9% | -24.42 | +139.03 | 27.7% | -43.86 |
| 90 | +187.41 | 27.5% | +28.97 | +144.10 | 25.6% | +2.89 |
| 120 | +187.38 | 27.5% | +42.71 | +144.34 | 25.6% | +7.25 |
| 150 | +211.69 | 22.7% | +73.15 | +164.77 | 25.5% | +4.46 |
| 180 | +237.40 | 17.7% | +123.72 | +187.40 | 20.6% | +41.45 |
| 210 | +264.91 | 15.5% | +138.53 | +211.63 | 15.4% | +52.28 |
| 240 | +222.68 | 15.5% | +138.53 | +174.42 | 15.5% | +52.23 |
| 300 | +201.14 | 15.5% | +83.88 | +220.41 | 13.1% | +75.46 |
| 360 | +136.74 | 15.5% | +83.88 | +152.76 | 10.2% | +54.22 |

Every value from 90 to 300 beats both baselines; the money rises with the
delay up to 210 and is bumpy after. That is the shape of "take fewer of
November's entries", with the trade count falling 16 -> 11 -> 8. It is a
real plateau and it is one regime.

## 6. Why NONE, and what would change it

1. No two-rule combo passes on both baselines. The entry gates nest
   inside the delay; the exit rules lose money on these trades.
2. The one survivor is a single rule at the far end of its tested range,
   picked by the sweep, 1 of 8,747. The attribution tested 120 and called
   it a fluke on its own rule; 180 passes that rule because it also skips
   the 11-09 and 11-10 stops, which are the same kind of day (a thesis
   written into a slide, bought at once, stopped within hours).
3. It never changes an October trade. Two of four weeks carry it.
4. On the real fills the two November winners (11-04 17:32Z +$58.36,
   11-12 03:58Z +$30.01) were both bought inside 2h of a rewrite. The sim
   says the delayed entry still fills (11-05 01:45Z, 11-10 13:45Z); the
   ledger cannot say that.
5. ~~The baseline every test has used is not the live rule: the 20/15
   leverage fit is live and the sim leaves it out, and that alone moves
   `delay120` from +$44.56 to +$5.31. Follow-up (separate change, touches
   every eval's golden numbers): put `entry_fit_leverage=(20,15)` into
   `live_params.live_brent_params` and re-pin.~~ Done 2026-11-13 (the
   correction at the top): `live_brent_params` reads the fit off
   `next_trade.leverage_fit_for`; on the corrected data `delay120` is
   +$1.52 over the live rule and fails its bar.

What would make the delay a yes: a second November-style week where the
live desk takes a fresh-thesis entry that stops inside three hours while
price later holds, on a thesis that was written into the move. Re-run
`rule_fusion_test` (both baselines) after it; if `delay180` still passes
every guard and the weeks column reads 3/0 or better, that is the switch.

The switch, if the operator wants it now (whole oil desk; the 10% sleeve
cannot exist beside the 90% desk on one coin, TRADE_ATTRIBUTION section
7): `config {"oil_entry_delay_min": 180}`, then reconnect the pathiel MCP
server and restart the price watcher so both read the setting. `config
{"oil_entry_delay_min": 0}` turns it off. At today's $213.80 the sim's
window says +$278.74 vs +$167.69 unfitted (+$322.72 vs +$194.15 per 30
days), +$220.03 vs +$163.25 fitted. Nothing was changed.

## Reproduce

    # the sweep on the live rule (20/15 leverage fit in the baseline, the default
    # since 2026-11-13; exit 0 = something passes every guard)
    PATHIEL_STATE_DIR=<pathia>/.state .venv/bin/python -m services.strategies.evals.rule_fusion_test \
        --oil-theses-dir <pathia>/.state/strategies/oil/theses --cache-dir <fresh dir> --out-dir <dir> --equity 213.80

    # the same grid on the pre-2026-11-13 unfitted baseline (fixed 20x), for comparison
    PATHIEL_STATE_DIR=<pathia>/.state .venv/bin/python -m services.strategies.evals.rule_fusion_test \
        --oil-theses-dir <pathia>/.state/strategies/oil/theses --cache-dir <same dir> --out-dir <dir2> --equity 213.80 --no-lev-fit

    # gate tests
    .venv/bin/python -m pytest -q services/strategies/tests/test_rule_fusion.py

Outputs: `<out>/report.json` (baseline, singles, top 10, funnel, overlap,
the trade lists of every reported row, the rows re-run on the other
baseline under `fit_diagnostic`),
`<out>/combos.csv` (every combo with every guard), `<out>/progress.log`.
Both runs took about 150 s on 8 processes (26,241 simulations each).
