# Thesis-range test, 2026-11-16

**Verdict: no mechanical edge found, nothing adopted. The operator's exact cycle loses on the search set (-$44.29, 47 trades, 13% wins), on the Brent holdout (-$13.87 with the levels frozen, -$17.33 with the levels following each rewrite) and on WTI (-$29.62 and -$96.92). The best of 71,524 variants made +$111.44 on the search set and -$8.00 on the holdout, -$45.45 on WTI 5m, and sits at the 40th percentile of its own random-level baseline (needs the 95th). No condition filter is positive on search, holdout and WTI. Two things survive as leads, not rules: the short AT the thesis high (positive per trade on all four sets, with resting orders) and the account's own longs bought BELOW the thesis entry (+$79.79 on 7).** Research only. No live config, order or rule changed.

Operator, 2026-11-16: "find the edge that makes the mechanical version hold up, it's actually working you know, I always been using the desk's thesis". The cycle: (1) short toward the current thesis entry; (2) stop 5% of the position's margin at 20x (0.25% of price); if stopped, long 20% of equity as margin to the thesis high, the stop ratcheted up; (3) at the high, short back to the entry; (4) at the entry, long again to the target; and keep cycling whether or not the thesis changes. Variant A: the levels frozen at the cycle's start. Variant B: the levels follow each rewrite.

Data: Brent 5m candles 2026-10-26 11:45Z to 2026-11-16 06:45Z (20.8 days), WTI 5m (19.3 days), Brent and WTI 15m from 2026-10-18 04:15Z (29.1 days), the journal as it stood at 07:00Z (78 theses, 74 long), $182.09 start, measured fees 0.90 / 0.31 bps. Every number is from `services/strategies/evals/thesis_range_test.py`, run `/tmp/thesis-range/cycle/`.

## The split, fixed before the search

| set | what | window | days |
|---|---|---|---|
| SEARCH | Brent 5m, the older 58 theses | 10-26 11:45Z to 11-11 03:50Z | 15.7 |
| HOLDOUT | Brent 5m, the newest 20 theses (25%) | 11-11 03:50Z to 11-16 06:45Z | 5.1 |
| HOLDOUT | all of WTI, 5m and 15m | 10-28 / 10-18 to 11-16 | 19.3 / 29.1 |

`holdout_split` reads the thesis timestamps and nothing else. Every pick (the rule, the filter ranking) reads `brent_5m_search` only; the holdout walks start after the pick is written to the log. A rule HOLDS only if it has 10+ trades and both halves up on the search set, makes money on the Brent holdout, on WTI 5m and on WTI 15m, and beats the 95th percentile of its random-level baseline on the holdout data.

## The operator's exact cycle (the primary rule)

`O_mkt0.75_s20_p0.25_f20_be50_tick_cycleA` and `..._cycleB`: a market short at a desk tick with the close 0.75% over the thesis entry (the trigger that reproduced the night of 11-16), 20% margin, stop +0.25%, target the thesis entry; stopped: market long on the next desk tick, 20% margin, to the thesis target, stop -0.25%, break-even once half the way is covered; at the target: short back to the entry; at the entry (from that short or from the first one): long to the target; and on, until a stop breaks the range.

| set | variant | n | win % | net $ | max DD | first short (n / wins / $) | flip long | short at the high | long at the entry | random levels (median, p95) |
|---|---|---|---|---|---|---|---|---|---|---|
| SEARCH Brent 5m | A = B | 47 | 12.8% | -44.29 | 24.7% | 23 / 4 / -17.89 | 19 / 2 / -16.31 | 1 / 0 / -2.00 | 4 / 0 / -8.08 | -45.58, -21.17 |
| HOLDOUT Brent 5m | A frozen | 19 | 15.8% | -13.87 | 14.7% | 8 / 0 / -17.19 | 8 / 2 / -4.54 | 2 / 1 / +10.17 | 1 / 0 / -2.31 | -18.63, +1.27 |
| | B follows | 19 | 15.8% | -17.33 | 14.2% | 8 / 0 / -16.92 | 8 / 2 / -4.36 | 2 / 1 / +6.22 | 1 / 0 / -2.26 | -18.69, +12.18 |
| HOLDOUT WTI 5m | A frozen | 58 | 19.0% | -29.62 | 25.8% | 28 / 6 / -8.34 | 22 / 5 / -4.68 | 2 / 0 / -3.88 | 6 / 0 / -12.73 | -50.98, -20.64 |
| | B follows | 57 | 19.3% | -27.78 | 25.4% | 28 / 6 / -8.55 | 22 / 5 / -4.73 | 2 / 0 / -3.92 | 5 / 0 / -10.57 | -50.13, -17.58 |
| HOLDOUT WTI 15m | A = B | 79 | 6.3% | -96.92 | 53.4% | 40 / 3 / -43.32 | 35 / 2 / -47.11 | 1 / 0 / -1.21 | 3 / 0 / -5.28 | -82.42, -70.11 |

Search halves -$9.76 / -$34.53. Holdout data pooled (Brent holdout + WTI 5m): A -$43.49, B -$45.11, against random-level 95th percentiles of -$26.66 and -$25.32. Repo adoption bar beside the live book: **FAIL** for both (Brent 5m -$16.41, Brent 15m -$43.48, WTI 15m -$28.48, drawdown worse than live on three of four).

- **A and B are the same trades almost everywhere.** The cycle rarely gets past its second leg, so it rarely lives long enough to meet a rewrite: 84 flip longs led to 6 shorts at the thesis high. Where they differ (the holdout week), frozen levels did $3.46 better.
- **The long at the entry, leg 4, is 0 wins of 14** across the four sets on this fill: a market long at the thesis entry with a 0.25% stop, after a short has just pushed price down to it.
- The cycle against the same spec stopping after the flip (no legs 3 and 4): search -$35.77 against -$44.29. The extra legs cost $8.52 on search, $0.02 on the Brent holdout, $3.66 on WTI 5m and $4.18 on WTI 15m.

The spec under the other ratchets, fills and stops, cycle A (n | win % | net $):

| change from the spec | search | Brent holdout | WTI 5m | WTI 15m |
|---|---|---|---|---|
| none (the spec) | 47, 12.8%, -44.29 | 19, 15.8%, -13.87 | 58, 19.0%, -29.62 | 79, 6.3%, -96.92 |
| no ratchet / break-even at 75% | 47, 12.8%, -44.29 | 19, 15.8%, -13.87 | 58, 19.0%, -31.15 | 79, 6.3%, -96.92 |
| trail 1 ATR | 50, 14.0%, -52.96 | 19, 21.1%, -9.64 | 76, 18.4%, -51.31 | 81, 8.6%, -95.94 |
| trail 2 ATR | 50, 12.0%, -60.24 | 19, 15.8%, -13.87 | 68, 20.6%, -37.38 | 79, 6.3%, -95.96 |
| stop 0.36% (the night's) | 48, 16.7%, -45.75 | 12, 16.7%, -18.80 | 56, 21.4%, -46.67 | 77, 15.6%, -94.10 |
| stop 0.375% (7.5% of margin) | 48, 16.7%, -48.88 | 12, 16.7%, -19.70 | 56, 25.0%, -40.48 | 75, 16.0%, -94.44 |
| stop 0.5% (10% of margin) | 43, 27.9%, -21.56 | 17, 17.6%, -28.40 | 54, 31.5%, -37.12 | 67, 25.4%, -76.83 |
| stop over the 4h swing | 32, 34.4%, -31.33 | 14, 21.4%, -30.25 | 29, 31.0%, -31.24 | 47, 36.2%, -38.79 |
| **flip and cycle legs by RESTING orders** | 40, 22.5%, +12.93 | 13, 38.5%, +31.48 | 56, 21.4%, -18.14 | 62, 12.9%, -48.94 |
| resting, stop 0.36% | 38, 23.7%, -9.78 | 13, 38.5%, +21.36 | 47, 29.8%, +13.73 | 65, 24.6%, -27.96 |
| resting, stop 0.5% | 38, 34.2%, +12.11 | 13, 38.5%, +9.13 | 51, 31.4%, -11.51 | 55, 34.5%, +3.87 |
| resting, swing stop | 31, 35.5%, +3.85 | 13, 38.5%, +9.82 | 40, 27.5%, -37.64 | 49, 34.7%, -5.56 |

- **No ratchet and no stop width rescues the spec on the desk's own fill.** With a 0.25% stop the long is out before a ratchet can act; the break-even rows equal the no-ratchet row on Brent to the cent.
- **Resting orders are the one change that moves the sign on Brent**: a buy-stop at the short's stop, a sell limit at the thesis high, a buy limit at the thesis entry. +$12.93 on search, +$31.48 on the Brent holdout, and it beats its random-level baseline on the holdout data (+$13.34 pooled against a 95th percentile of +$1.40). It still does not hold: the first search half is -$1.67, WTI loses $18.14 and $48.94, the repo bar is FAIL, and counting the whole flip bar against the new leg turns it to -$12.95 on search, -$8.17 on the Brent holdout and -$46.58 on WTI 5m. A 0.25% stop on a leg filled inside a bar whose median range is 0.17% lives or dies on that assumption.

## The best rule on the search set, and what it did after

`O_mkt0.75_s20_swing_f30_trail2.0_resting_cycleA`: the spec's short at 20% margin with its stop over the 4h swing high instead of +0.25%, resting orders for the flip (30% margin) and the cycle legs, stop trailed 2 ATR, cycle A, no filter.

| set | n | win % | net $ | max DD | avg R | flip-bar worst case | random levels: median, p95 | percentile |
|---|---|---|---|---|---|---|---|---|
| SEARCH Brent 5m | 34 | 50.0% | +111.44 | 10.4% | +0.19 | +43.89 | +9.64, +96.33 | 97.5 |
| HOLDOUT Brent 5m | 17 | 29.4% | **-8.00** | 18.6% | -0.45 | -8.37 | -25.06, +12.71 | 76 |
| HOLDOUT WTI 5m | 52 | 30.8% | **-45.45** | 32.3% | -0.50 | -45.45 | -15.77, +49.19 | 19.5 |
| HOLDOUT WTI 15m | 59 | 39.0% | +8.74 | 21.5% | +0.10 | -1.16 | -51.43, +6.16 | 95.5 |
| holdout data pooled (Brent 5m holdout + WTI 5m) | 69 | | -53.45 | | | | -39.71, +54.77 | 40.5 |

Search halves +$21.29 / +$90.16. On search its first short won 12 of 17 for +$93.56 (a wide stop in a market falling from 110 to 90); on the holdout the same leg won 1 of 7 for -$19.47. It fails three legs (Brent holdout, WTI 5m, random levels) and the repo bar (WTI drawdown 60% against 49%). **Not a candidate.**

The journal is live: a thesis written at 08:14Z moved the split by a day in an earlier run. `--journal-asof` pins it.

## Best of N against the field

71,524 variants; 34,034 have 10+ trades on the search set, 12,190 of those are above zero, 4,853 pass the selection gate.

| | search net | Brent holdout | WTI 5m | WTI 15m |
|---|---|---|---|---|
| the pick | +111.44 | -8.00 (34th percentile of the field) | -45.45 (8th) | +8.74 (89th) |
| median variant | -6.23 | -4.66 | -16.62 | -20.84 |
| share above zero | 36% | 28.3% | 18.5% | 18.9% |
| the search's top 50: median, share above zero | | -1.07, 48% | +9.32, 54% | +28.09, 74% |
| rank correlation, search net vs this | | 0.17 | 0.49 | 0.47 |

- On the clean holdout (later days, same market) the search order carries almost nothing: 0.17. Being the best of 71,524 bought a below-median outcome.
- The search's winners do carry to WTI (0.49). WTI is another instrument on the SAME days as the search set, so this is mostly the same moves seen twice, not a second sample. It says the result is not a Brent quirk; it does not say it persists.

## Random-level baseline

Each rule against itself with the levels moved: for the ladder, every thesis's whole range is displaced by a uniform draw of up to one width either way; for the range rule, each thesis's limit is drawn uniformly inside the range. 200 seeded draws, everything else held.

| rule | search: rule vs median / p95 | holdout pool: rule vs median / p95 | percentile | beats p95 |
|---|---|---|---|---|
| operator cycle A | -44.29 vs -45.58 / -21.17 | -43.49 vs -68.64 / -26.66 | 87 | no |
| operator cycle B | -44.29 vs -45.45 / -21.65 | -45.11 vs -68.06 / -25.32 | 81.5 | no |
| cycle A, resting orders | +12.93 vs -33.30 / +13.18 | +13.34 vs -51.31 / +1.40 | 96 | yes |
| the pick | +111.44 vs +9.64 / +96.33 | -53.45 vs -39.71 / +54.77 | 40.5 | no |
| best range rule (`LS_b0.1...+not_post_close`) | +40.49 vs +12.77 / +35.06 | -26.56 vs -9.91 / +28.14 | 21 | no |
| baseline long: buy the thesis entry, thesis stop and target | -21.27 vs -4.29 / +18.12 | -0.34 vs +4.86 / +29.87 | 36.5 | no |
| sell the thesis target, stop +0.36%, back to the entry | +15.32 vs +0.57 / +17.45 | +11.43 vs -6.21 / +13.33 | 93.5 | no |

The thesis levels are better than displaced ones for the operator's cycle (it sits above its random median on every pooled row), and on the desk's fill that only means it loses less. The one row that clears the 95th percentile is the resting-order cycle, which fails on WTI and on the whole-bar reading above.

## Which condition is the edge

None. Dollars per trade, pooled over the 7,200 unfiltered ladder variants and the same variants under each filter:

| filter on the first short | search | Brent holdout | WTI 5m | WTI 15m |
|---|---|---|---|---|
| none | -0.17 | -0.22 | -0.42 | -0.51 |
| thesis under 2h old | +0.15 | -0.71 | -0.51 | -0.33 |
| thesis 2 to 8h old | -0.39 | -0.13 | -0.31 | -0.37 |
| thesis 8h+ old | -0.35 | -2.24 | -1.15 | -0.85 |
| range under 4 ATR wide | +0.18 | +0.05 | -0.09 | -0.15 |
| range 4+ ATR wide | -0.47 | -0.71 | -0.98 | -1.04 |
| Asia session | -0.65 | +0.17 | -0.48 | -0.70 |
| London session | -0.11 | -0.77 | +0.26 | -0.14 |
| New York session | -0.05 | -1.17 | -0.70 | -0.89 |

No filter is positive on all four. A narrow range (under 4 ATR) is the closest: positive on both Brent sets by cents, still negative on WTI. The two that point the same way everywhere are negatives: a stale thesis (8h+) and a wide range (4+ ATR) are worse than no filter on all four. That is a reason to skip, not an edge.

The range rule's 19 filters (146 carriers) tell the same story. Ranked on search: New York, 8h+ old, entry near the price when written. On the Brent holdout New York is -$0.71 a trade, New York with near-the-price is -$1.67, and every combination is negative on WTI. The post-close rewrite, confidence, the EIA blackout and the 1h / 4h trend do not separate winners on all four sets either (the 1h uptrend is the closest: +0.82, +0.15, +0.04, -0.20).

What does matter is the leg and the order type, not a condition. Win % and dollars per trade, pooled over the unfiltered ladder variants:

| leg | fill | search | Brent holdout | WTI 5m | WTI 15m |
|---|---|---|---|---|---|
| 1. first short toward the entry | resting flip grid | 29.0%, +0.06 | 7.3%, -1.27 | 25.4%, -0.22 | 24.4%, -0.28 |
| | next-tick grid | 27.4%, -0.03 | 6.7%, -1.30 | 25.9%, -0.19 | 23.0%, -0.31 |
| 2. flip long to the high | resting buy-stop | 38.9%, +0.71 | 50.9%, +0.84 | 33.5%, -0.72 | 32.5%, -0.33 |
| | next-tick market | 18.1%, -1.37 | 46.9%, +0.35 | 23.1%, -1.42 | 16.5%, -1.76 |
| 3. short at the thesis high | resting sell limit | 49.9%, **+3.70** | 18.6%, **+0.37** | 38.6%, **+2.54** | 38.6%, **+2.19** |
| | next-tick market | 44.6%, +2.14 | 43.1%, +1.66 | 20.6%, +0.09 | 5.6%, -1.71 |
| 4. long at the thesis entry | resting buy limit | 12.4%, -1.72 | 48.1%, +4.31 | 29.8%, +0.50 | 25.3%, +0.32 |
| | next-tick market | 1.9%, -3.17 | 28.7%, +2.09 | 16.9%, -1.52 | 16.6%, -1.00 |

- **Leg 1, the short toward the entry, is the leak.** It is about flat on search and loses on the other three, with 7% wins in the holdout week, and it is the leg the cycle opens first every time.
- **Leg 3, the short at the thesis high with a sell limit resting there, is the only leg positive on all four sets.** It is the same trade as "sell the thesis target" in the baseline table (+$11.43 on 6 holdout trades, 93.5th percentile). It is rare (5 to 10% of the grid's trades), pooled over variants that share the same few fills, and wins 19% in the holdout week. Label: LEANING, not an edge.
- Leg 2 works on Brent only with the resting buy-stop (+$0.71, +$0.84) and loses on WTI; by market order it loses $1.37 to $1.76 a trade outside the holdout week.
- Leg 4 with a resting buy limit is positive on three of four sets and loses on search, where Brent fell from 110 to 90.
- Axis means over the grid (search, Brent holdout, WTI 5m, WTI 15m): the resting sell limit at mid-range is the only entry with a positive mean everywhere (+$4.93, +$6.16, +$1.35, +$2.11; 44 to 73% of its variants up); the market entry is -$12.59, +$0.46, -$24.24, -$36.24. Resting fills +$7.09, -$1.32, -$5.45, -$4.60 against -$13.82, -$3.56, -$21.40, -$34.63 for next-tick. Cycle A -$4.25, +$2.25, -$9.43, -$18.40; cycle B -$5.53, -$3.54, -$15.56, -$20.05; no cycle -$4.14, -$3.91, -$15.90, -$21.57. The swing stop is the least bad stop (-$2.14, -$0.74, -$8.41, -$3.98 against -$8.02, -$0.43, -$13.69, -$22.25 for 0.25%).

## The account's own trades against the thesis range

17 closed Brent trips in the review ledger (the ledger does not separate hand trades from desk-tick trades; the two shorts and the last long are known to be by hand). Position 0 = the thesis entry, 1 = the thesis target.

| where it entered | n | wins | net $ | bps of notional | label |
|---|---|---|---|---|---|
| long, below the thesis entry (under -0.1) | 7 | 4 | +79.79 | +46.4 | LEANING + |
| long, at the thesis entry (within 0.1) | 5 | 2 | -39.85 | -37.8 | LEANING - |
| long, mid-range | 3 | 2 | +14.95 | +41.5 | LEANING + |
| short, at the entry | 1 | 1 | +0.70 | +23.4 | NOISE |
| short, mid-range (the night's first short) | 1 | 0 | -3.51 | -40.9 | NOISE |
| all | 17 | 9 | +52.08 | +16.0 | EDGE? (9 of 17) |

- The mechanical baseline (buy AT the thesis entry, thesis stop, thesis target) on the whole Brent 5m window: 19 trades, -$12.61 alone (split: search -$21.27, holdout +$15.80). The account beats it by about $65, at larger and uneven sizes.
- **The difference is where the longs were bought, not the levels used.** The seven longs filled 0.12 to 0.59 of a range BELOW the thesis entry made all the money and more; the five filled at the entry lost $39.85, the same result the mechanical rule gets there. Only 3 of 17 carried the thesis target as their own target; five of the eight winning longs left at 0.07 to 0.88 of the way to it.
- So the discretionary version does add something the grid misses: waiting for price to trade through the thesis entry and buying the overshoot. No variant here buys under the entry (the lowest limit is the entry itself). Seven trades is a lean, not an edge.
- The two shorts are -$2.81 together. The third (23.76 @ 93.56 toward 93.00) is open and not in the ledger; it is a leg-3 trade, the one leg the grid likes.

## Gut call on live money

**No** for any mechanical version: the cycle loses on every set on the desk's fill, the best of 71,524 lost on the holdout, and no filter survives. By hand, at the sizes used tonight: skip or shrink leg 1 (the short toward the entry, 7% wins in the holdout week) to 5% margin; keep leg 3 (the short from the thesis high) small, 5 to 10% margin, with the sell limit resting at the high and not a market order after it. Next test, the two leads this page produced: a long limit 0.1 to 0.6 of a range UNDER the thesis entry, and a sell limit resting at the thesis high (or at mid-range), each watched for 20 more fills before any size.

## Method

One script, deterministic, no LLM call: `services/strategies/evals/thesis_range_test.py`. Same cache and the same `--journal-asof` in, same numbers out.

- **The map.** Each journal thesis gives side, entry, stop, first target (`data.load_journaled_theses`). While a long thesis is in force the range is [entry, target]; a rewrite moves it; short and flat theses (4 of 78) and ranges under 0.2% wide have none. On WTI the levels are carried by the WTI/Brent 1h-close ratio at the hour the thesis was written.
- **The ladder (primary), 64,800 variants** = 4 entries (market at +0.75% over the entry; resting sell limit at range position 0.33 / 0.5 / 0.75) x 3 short sizes (5 / 10 / 20% margin) x 5 stops (0.25 / 0.36 / 0.375 / 0.5%, the 4h swing; the long's stop is the mirror) x 3 long sizes (10 / 20 / 30%) x 5 ratchets (none, break-even at 50 / 75%, trail 1 / 2 ATR) x 2 fills (resting orders, next-tick market) x 4 cycle settings (off; only while the thesis is the same; A frozen; B follows) x 9 (no filter, 3 ages, 2 widths, 3 sessions).
- **The range rule (second), 6,724 variants**: long limit at position 0 / 0.1 / 0.25 (target all or half the way, stop thesis / 0.36% / 1 ATR, flip short on the stop or not), short limit at 0.33 / 0.5 / 0.75 / 0.9 / 1.0 (target all or half the way, stop 0.36% / 1 ATR / over the range top, flip long on the stop or not), alone and together: 3,366. Then 19 filters one at a time on the 126 single-leg variants and the 20 best two-leg ones (2,774), then the best three filters combined (584). 10% margin.
- **Total 71,524.** All in `variants.csv`.
- **Interpretation choices.** (1) "Short toward the entry" has no stated trigger: the market entry uses +0.75%, the cell that reproduced the night; three limit positions are gridded beside it. (2) "5%" is 5% of the position's margin, 0.25% of price at 20x. (3) Every later leg's stop is the mirror of the short's (0.25% is what the operator used on the long). (4) One first short per thesis; the cycle it starts may outlive the thesis. (5) A break is a stop on a cycle leg: the cycle ends. A stopped first short is not a break, it is step 2. (6) In A a cycle leg keeps the floor and top of the cycle's start; in B each new leg takes the thesis in force (the short sold at the old high targets the new entry) and no thesis means no leg. (7) A bracket is frozen at its fill; only the ratchet moves a stop, at the desk tick, from the best price of the bars before. (8) Shorts take the short size, longs the long size.
- **Execution.** Orders are placed and pulled by the 15m desk tick: on 5m bars the :03 cron's orders are live from the :05 bar and read the thesis written by :03; a limit is only placed on the far side of the last close; after a close nothing new rests until the next tick. A limit entry is maker and needs a trade-through of one tick; on its fill bar the stop counts on the whole bar and the target never fills. A desk market order fills at the tick bar's open and pays half that bar's range. A resting follow-on order (the buy-stop at the short's stop, the sell limit at the high, the buy limit at the entry) fills on the bar that closed the leg before it, at that price; on that bar only the close can stop the new leg (the "flip-bar worst case" counts the whole bar). A stop is taker with 3 bps (a gap fills at the open); a target is a maker limit; stop and target on one bar is a loss. Fees 0.90 / 0.31 bps, measured. Funding on the hour. Weekend flat; the reopen gap is paid by whatever stop it jumps.
- **Selection.** The highest net on `brent_5m_search` among variants with 10+ trades and both search halves above zero (split 11-03 07:45Z). `brent_15m_search` is a cross-check only.
- **Baselines.** `baseline`: 200 seeded draws per rule per segment. "Holdout pool" = Brent 5m holdout + WTI 5m.
- **Adoption bar.** `short_ladder_test.judge` beside the live long book on the four whole datasets (one position per desk), with the grid neighbours as the plateau.
- **Hand trades.** `score_hand_trades` on `review/ledger.jsonl`, de-duplicated by id, each against the thesis in force at its entry.

Where it would break: three weeks is one regime (Brent 110 down to 93) and the holdout is five days with one weekend, so 5 to 19 Brent holdout trades per rule; the WTI sets share their days with the search set; a 5m bar cannot order a fill and a stop inside it (the loss rule, the whole-bar count on limit fills and the worst-case column bound it); the +0.75% trigger is fitted to one night; the pooled leg table counts the same market fills once per variant that takes them, so its n is not a count of independent trades; the headline blackout and the thesis's secondary invalidation text are not replayable; WTI levels lean on a price ratio, not a WTI thesis; the journal grows, so an unpinned rerun moves the split.

## Reproduce

    # the run of this page (about 12 minutes on 8 workers); exit 1 = the chosen rule does not hold
    PATHIEL_STATE_DIR=<pathia>/.state .venv/bin/python -m services.strategies.evals.thesis_range_test \
        --cache-dir <dir> --out-dir <dir> --journal-asof 2026-11-16T07:00Z

    # gate tests (46, no network)
    .venv/bin/python -m pytest -q services/strategies/tests/test_thesis_range.py

Outputs: `<out>/report.json` (the two spec rows and their grids, the pick, the named rules with their trades, baselines, the deflated view, families, conditions, legs, axes, the account's trades), `<out>/variants.csv` (one row per variant, every segment), `<out>/progress.log`.
