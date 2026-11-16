# Thesis-range test, 2026-11-16

**Verdict: no mechanical edge found, nothing adopted. The operator's exact spec loses on the search set (-$35.77, 42 trades, 14% wins), on the Brent holdout (-$15.34, 18 trades) and on WTI (-$27.82 and -$92.74). The best of 39,124 variants made +$78.58 on the search set and -$5.60 on the holdout, -$15.48 and -$34.43 on WTI, and does not beat its own random-level baseline (65th percentile, needs the 95th). No condition filter holds across search, holdout and WTI. The account's real trades on the thesis levels are up $52.08 on 17 trips, and the money is in longs bought BELOW the thesis entry, a thing none of these rules does.** Research only. No live config, order or rule changed.

Operator, 2026-11-16: "find the edge that makes the mechanical version hold up, it's actually working you know, I always been using the desk's thesis". The spec: (1) short toward the current thesis entry; (2) stop 5% of the position's margin at 20x (0.25% of price); (3) if stopped, long 20% of equity as margin toward the thesis high, the stop ratcheted up; (4) at the thesis high, short back to the thesis entry and keep bouncing inside the range until the thesis is rewritten or a break invalidates it.

Data: Brent 5m candles 2026-10-26 11:45Z to 2026-11-16 06:45Z (20.8 days), WTI 5m (19.3 days), Brent and WTI 15m from 2026-10-18 04:15Z (29.1 days), the journal as it stood at 07:00Z (78 theses, 74 long), $182.09 start, measured fees 0.90 / 0.31 bps. Every number is from `services/strategies/evals/thesis_range_test.py`, run `/tmp/thesis-range/pinned/`.

## The split, fixed before the search

| set | what | window | days |
|---|---|---|---|
| SEARCH | Brent 5m, the older 58 theses | 10-26 11:45Z to 11-11 03:50Z | 15.7 |
| HOLDOUT | Brent 5m, the newest 20 theses (25%) | 11-11 03:50Z to 11-16 06:45Z | 5.1 |
| HOLDOUT | all of WTI, 5m and 15m | 10-28 / 10-18 to 11-16 | 19.3 / 29.1 |

`holdout_split` reads the thesis timestamps and nothing else. Every pick (the rule, the filter ranking) reads `brent_5m_search` only; the holdout walks start after the pick is written to the log. A rule HOLDS only if it has 10+ trades and both halves up on the search set, makes money on the Brent holdout, on WTI 5m and on WTI 15m, and beats the 95th percentile of its random-level baseline on the holdout data.

## The operator's exact spec

`O_mkt0.75_s20_p0.25_f20_be50_tick_bounce`: a market short at a desk tick with the close 0.75% over the thesis entry (the trigger that reproduced the night of 11-16), 20% margin, stop +0.25%; stopped: market long on the next desk tick, 20% margin, target the thesis target, stop -0.25%, break-even once half the way is covered; at the target: short back to the thesis entry, and on.

| set | n | win % | net $ | max DD | short leg (n / wins / $) | flip long (n / wins / $) | bounce short | vs random levels (median, p95) |
|---|---|---|---|---|---|---|---|---|
| SEARCH Brent 5m | 42 | 14.3% | -35.77 | 20.1% | 23 / 4 / -18.88 | 19 / 2 / -16.89 | 0 | -42.98, -15.02 |
| HOLDOUT Brent 5m | 18 | 16.7% | -15.34 | 12.7% | 9 / 1 / -9.39 | 8 / 2 / -4.47 | 1 / 0 / -1.49 | -18.77, -3.02 |
| HOLDOUT WTI 5m | 52 | 19.2% | -27.82 | 26.2% | 28 / 5 / -18.90 | 23 / 5 / -6.73 | 1 / 0 / -2.19 | -49.14, -15.90 |
| HOLDOUT WTI 15m | 75 | 6.7% | -92.74 | 51.2% | 40 / 3 / -44.58 | 35 / 2 / -48.16 | 0 | -83.33, -69.09 |

Search halves -$7.68 / -$28.09. Repo adoption bar beside the live book: **FAIL** (Brent 5m -$8.50, Brent 15m -$37.65, WTI 15m -$26.22, drawdown worse than live on three of four). The bounce almost never happens: 2 of 85 flip longs reached the thesis target with the thesis unchanged.

The same spec with the other ratchets and the other flip fill (n | win % | net $, search || Brent holdout || WTI 5m || WTI 15m):

| ratchet | flip fill | search | Brent holdout | WTI 5m | WTI 15m |
|---|---|---|---|---|---|
| none | next-tick market | 42, 14.3%, -35.77 | 18, 16.7%, -15.34 | 52, 19.2%, -29.37 | 75, 6.7%, -92.74 |
| break-even at 50% (spec) | next-tick market | 42, 14.3%, -35.77 | 18, 16.7%, -15.34 | 52, 19.2%, -27.82 | 75, 6.7%, -92.74 |
| break-even at 75% | next-tick market | 42, 14.3%, -35.77 | 18, 16.7%, -15.34 | 52, 19.2%, -29.37 | 75, 6.7%, -92.74 |
| trail 1 ATR | next-tick market | 46, 15.2%, -46.62 | 18, 22.2%, -12.23 | 69, 15.9%, -55.81 | 77, 9.1%, -91.70 |
| trail 2 ATR | next-tick market | 46, 13.0%, -54.26 | 18, 16.7%, -15.34 | 61, 19.7%, -40.18 | 75, 6.7%, -91.73 |
| none | resting buy-stop | 33, 27.3%, +29.65 | 11, 36.4%, +12.73 | 59, 16.9%, -42.15 | 58, 15.5%, -30.58 |
| break-even at 50% | resting buy-stop | 33, 27.3%, +29.65 | 11, 36.4%, +12.73 | 59, 16.9%, -40.73 | 58, 13.8%, -42.53 |
| trail 1 ATR | resting buy-stop | 42, 26.2%, +5.68 | 15, 26.7%, +3.13 | 68, 17.6%, -47.17 | 76, 13.2%, -65.51 |
| trail 2 ATR | resting buy-stop | 39, 25.6%, +1.97 | 15, 26.7%, +2.89 | 63, 15.9%, -50.49 | 68, 14.7%, -48.55 |

- **The ratchet does nothing to the spec.** With a 0.25% stop the long is out before any ratchet can act: the break-even rows equal the no-ratchet row on Brent to the cent, and the trails are worse.
- **The resting buy-stop is the one thing that moves the sign on Brent**: +$29.65 on search and +$12.73 on the Brent holdout (97th percentile of random levels on that segment). It does not hold: WTI loses $40.73 and $42.53, the pooled holdout data is -$28.00 against a random-level 95th percentile of -$12.01, and counting the whole flip bar against the long (the low after the fill) turns it to -$0.60 on search and -$8.17 on the Brent holdout. A long with a 0.25% stop filled inside a bar whose range is 0.17% lives or dies on that assumption.
- The spec with the wider stops, next-tick fill: -$34.07 (0.36%), -$37.02 (0.375%), -$22.57 (0.5%), -$5.68 (swing) on search; all negative on the Brent holdout (-$18.80 to -$27.69).

## The best rule on the search set, and what it did after

`O_mkt0.75_s20_p0.25_f30_trail2.0_resting_bounce+sess_ny`: the spec's short and stop, placed in the New York session (13:00 to 22:00Z) only, flip by a resting buy-stop at 30% margin, stop trailed 2 ATR under the best price, bounce on.

| set | n | win % | net $ | max DD | avg R | flip-bar worst case | random levels: median, p95 | percentile |
|---|---|---|---|---|---|---|---|---|
| SEARCH Brent 5m | 24 | 37.5% | +78.58 | 10.3% | +1.14 | +25.31 | -19.27, +25.07 | 100 |
| HOLDOUT Brent 5m | 8 | 25.0% | **-5.60** | 10.2% | -0.45 | -15.85 | -5.24, +13.07 | 45.5 |
| HOLDOUT WTI 5m | 32 | 25.0% | **-15.48** | 19.1% | -0.24 | -36.23 | -28.61, +10.06 | 71 |
| HOLDOUT WTI 15m | 49 | 12.2% | **-34.43** | 32.5% | -0.44 | -34.43 | -40.08, +2.59 | 62.5 |
| holdout data pooled (Brent 5m holdout + WTI 5m) | 40 | | -21.08 | | | | -32.64, +9.10 | 65 |

Search halves +$10.13 / +$68.45: the second half holds a $34.17 flip long and the one winning bounce short ($18.70). It fails four of the six legs (Brent holdout, WTI 5m, WTI 15m, random levels). The repo bar beside the live book reads LEANING (9 trades on the primary, +$21.80; every money leg up), which is the same footnote the stop-and-reverse test left: beside live it trades once on WTI 5m. **Not a candidate.**

The journal is live. A thesis written at 08:14Z moved the split by a day in an earlier run (holdout from 11-12 02:04Z): the same rule was picked (+$67.52 on search) and lost $5.56 on the holdout. `--journal-asof` pins it.

## Best of N against the field

39,124 variants; 17,402 have 10+ trades on the search set, 6,519 of those are above zero, 2,698 pass the selection gate.

| | search net | Brent holdout | WTI 5m | WTI 15m |
|---|---|---|---|---|
| the pick | +78.58 | -5.60 (46th percentile of the field) | -15.48 (55th) | -34.43 (28th) |
| median variant | -5.59 | -4.74 | -17.77 | -21.61 |
| share above zero | 37% | 26.9% | 15.5% | 17.5% |
| the search's top 50: median, share above zero | | -4.54, 38% | +17.10, 72% | +2.38, 58% |
| rank correlation, search net vs this | | 0.13 | 0.44 | 0.43 |

- On the clean holdout (later days, same market) the search order carries almost nothing: 0.13. Being the best of 39,124 bought a median outcome.
- The search's winners do carry to WTI (0.44, top 50 at 72% above zero). WTI is another instrument on the SAME days as the search set, so this is mostly the same moves seen twice, not a second sample. It says the result is not a Brent quirk; it does not say it persists.

## Random-level baseline

Each rule against itself with the levels moved: for the ladder, every thesis's whole range is displaced by a uniform draw of up to one width either way; for the range rule, each thesis's limit is drawn uniformly inside the range. 200 seeded draws, everything else held.

| rule | search: rule vs median / p95 | holdout pool: rule vs median / p95 | percentile | beats p95 |
|---|---|---|---|---|
| operator spec (next-tick) | -35.77 vs -42.98 / -15.02 | -43.16 vs -66.72 / -30.64 | 86.5 | no |
| spec, resting flip | +29.65 vs -29.25 / +14.30 | -28.00 vs -58.53 / -12.01 | 82.5 | no |
| the pick | +78.58 vs -19.27 / +25.07 | -21.08 vs -32.64 / +9.10 | 65 | no |
| best range rule (`LS_b0.1...+not_post_close`) | +40.49 vs +12.77 / +35.06 | -26.56 vs -9.91 / +28.14 | 21 | no |
| baseline long: buy the thesis entry, thesis stop and target | -21.27 vs -4.29 / +18.12 | -0.34 vs +4.86 / +29.87 | 36.5 | no |
| sell the thesis target, stop +0.36%, back to the entry | +15.32 vs +0.57 / +17.45 | +11.43 vs -6.21 / +13.33 | 93.5 | no |

The thesis levels are better than displaced ones for the ladder (every ladder row sits above its random median), and not by enough: nothing clears the 95th percentile on the holdout data. **Selling the thesis target is the nearest miss**: +$11.43 on 6 holdout trades against a random-level median of -$6.21, up on WTI 5m (+$13.64, 4 trades) and WTI 15m (+$12.07, 5), LEANING on the repo bar. It is 2 trades on the search set and 0 wins of 2 on the Brent holdout. Label: NOISE with a lean, 13 trades in all.

## Which condition is the edge

None. Dollars per trade, pooled over the 3,600 unfiltered ladder variants and the same variants under each filter:

| filter on the first short | search | Brent holdout | WTI 5m | WTI 15m |
|---|---|---|---|---|
| none | -0.10 | -0.40 | -0.48 | -0.56 |
| thesis under 2h old | +0.45 | -0.65 | -0.43 | -0.31 |
| thesis 2 to 8h old | -0.33 | -0.40 | -0.39 | -0.56 |
| thesis 8h+ old | -0.25 | -2.24 | -1.21 | -0.86 |
| range under 4 ATR wide | +0.42 | -0.28 | -0.15 | -0.19 |
| range 4+ ATR wide | -0.48 | -0.66 | -1.00 | -1.04 |
| Asia session | -0.78 | -0.24 | -0.61 | -0.83 |
| London session | +0.36 | -0.92 | +0.37 | -0.12 |
| New York session | +0.13 | -1.16 | -0.74 | -0.91 |

Four filters are positive on the search set; none is positive on the Brent holdout. The two that point the same way everywhere are negatives: a stale thesis (8h+) and a wide range (4+ ATR) are worse on all four. That is a reason to skip, not an edge.

The range rule's 19 filters (146 carriers) tell the same story. Ranked on search: New York, 8h+ old, entry near the price when written. On the Brent holdout New York is -$0.71 a trade, New York with near-the-price is -$1.67, and every combination is negative on WTI. The post-close rewrite, confidence, the EIA blackout and the 1h / 4h trend do not separate winners on all four sets either (the 1h uptrend is the closest: +0.82, +0.15, +0.04, -0.20).

What does matter is an order type and a leg, not a condition:

| leg, per trade (resting flip variants) | search | Brent holdout | WTI 5m | WTI 15m |
|---|---|---|---|---|
| first short toward the entry: win %, $ | 28.2%, -0.03 | 8.8%, -1.17 | 25.9%, -0.18 | 24.4%, -0.28 |
| flip long, resting buy-stop | 38.1%, +0.61 | 51.7%, +0.89 | 32.8%, -0.74 | 32.3%, -0.32 |
| flip long, next-tick market | 17.7%, -1.47 | 47.1%, +0.34 | 22.5%, -1.49 | 16.5%, -1.79 |
| bounce short at the thesis high | 82.2%, +7.16 | 0.0%, -1.16 | 37.4%, +2.75 | 39.7%, +2.64 |

- **The short toward the entry loses on every set.** It is the leak, and it is the leg the spec opens first every time.
- The flip long by market order loses $1.47 to $1.79 a trade outside the holdout week. The resting buy-stop is worth $0.55 to $2.08 a trade over it on every set and is positive on Brent in both periods (+$0.61, +$0.89); it is negative on WTI (-$0.74, -$0.32), and it is the leg the flip-bar worst case hits.
- The bounce short is positive on three of four sets and is the same trade as "sell the thesis target" above. It is rare (3% of trades) and never wins on the Brent holdout.
- Axis means over the grid: the swing stop is the least bad stop (+$0.62, -$2.37, -$7.76, -$1.89 against -$6.58, -$1.65, -$16.10, -$23.48 for 0.25%); long size only scales the result; the limit entry at mid-range (0.5) is the least bad entry (+$1.56, +$2.77, -$2.33, -$5.75).

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
- The two shorts are -$2.81 together. The third (23.76 @ 93.56 toward 93.00) is open and not in the ledger.

## Gut call on live money

**No** for any mechanical version: the spec loses on every set, the best of 39,124 lost on the holdout, and no filter survives. By hand: the first short is the leg that loses everywhere (8.8% wins on the holdout week), so keep it at 5% margin or skip it; the long after it only works on paper with a buy-stop resting at the short's stop. Next test, and the only lead this page produced: a long limit 0.1 to 0.6 of a range UNDER the thesis entry, and a sell limit at the thesis target, both resting, watched for 20 more fills before money.

## Method

One script, deterministic, no LLM call: `services/strategies/evals/thesis_range_test.py`. Same cache and the same `--journal-asof` in, same numbers out.

- **The map.** Each journal thesis gives side, entry, stop, first target (`data.load_journaled_theses`). While a long thesis is in force the range is [entry, target]; a rewrite moves it; short and flat theses (4 of 78) and ranges under 0.2% wide have none. On WTI the levels are carried by the WTI/Brent 1h-close ratio at the hour the thesis was written.
- **The ladder (primary), 32,400 variants** = 4 entries (market at +0.75% over the entry; resting sell limit at range position 0.33 / 0.5 / 0.75) x 3 short sizes (5 / 10 / 20% margin) x 5 stops (0.25 / 0.36 / 0.375 / 0.5%, the 4h swing; the long's stop is the mirror) x 3 long sizes (10 / 20 / 30%) x 5 ratchets (none, break-even at 50 / 75%, trail 1 / 2 ATR) x 2 flip fills (resting stop-entry, next-tick market) x 2 (bounce on / off) x 9 (no filter, 3 ages, 2 widths, 3 sessions).
- **The range rule (second), 6,724 variants**: long limit at position 0 / 0.1 / 0.25 (target all or half the way, stop thesis / 0.36% / 1 ATR, flip short on the stop or not), short limit at 0.33 / 0.5 / 0.75 / 0.9 / 1.0 (target all or half the way, stop 0.36% / 1 ATR / over the range top, flip long on the stop or not), alone and together: 3,366. Then 19 filters one at a time on the 126 single-leg variants and the 20 best two-leg ones (2,774), then the best three filters combined (584). 10% margin.
- **Total 39,124.** All in `variants.csv`.
- **Interpretation choices.** (1) "Short toward the entry" has no stated trigger: the market entry uses +0.75%, the cell that reproduced the night; three limit positions are gridded beside it. (2) "5%" is 5% of the position's margin, 0.25% of price at 20x. (3) The long's stop is the mirror of the short's (0.25% is what the operator used). (4) One first short per thesis. (5) A break is a stop on a bounce leg: the cycle ends. (6) A bracket is frozen at its fill; only the ratchet moves a stop, at the desk tick, from the best price of the bars before. (7) The flip's target is the thesis target in force when the flip fills.
- **Execution.** Orders are placed and pulled by the 15m desk tick: on 5m bars the :03 cron's orders are live from the :05 bar and read the thesis written by :03; a limit is only placed on the far side of the last close; after a close nothing rests until the next tick. A limit entry is maker and needs a trade-through of one tick; on its fill bar the stop counts on the whole bar and the target never fills. A desk market order fills at the tick bar's open and pays half that bar's range. A resting stop-entry fills on the stop's own bar at the stop price, taker, 3 bps; on that bar only the close can stop the new leg (the "flip-bar worst case" column counts the whole bar). A stop is taker with 3 bps (a gap fills at the open); a target is a maker limit; stop and target on one bar is a loss. Fees 0.90 / 0.31 bps, measured. Funding on the hour. Weekend flat; the reopen gap is paid by whatever stop it jumps.
- **Selection.** The highest net on `brent_5m_search` among variants with 10+ trades and both search halves above zero (split 11-03 07:45Z). `brent_15m_search` is a cross-check only.
- **Baselines.** `baseline`: 200 seeded draws per rule per segment. "Holdout pool" = Brent 5m holdout + WTI 5m.
- **Adoption bar.** `short_ladder_test.judge` beside the live long book on the four whole datasets (one position per desk), with the grid neighbours as the plateau.
- **Hand trades.** `score_hand_trades` on `review/ledger.jsonl`, de-duplicated by id, each against the thesis in force at its entry.

Where it would break: three weeks is one regime (Brent 110 down to 93) and the holdout is five days with one weekend, so 8 to 18 holdout trades per rule; the WTI sets share their days with the search set; a 5m bar cannot order a fill and a stop inside it (the loss rule, the whole-bar count on limit fills and the worst-case column bound it); the +0.75% trigger is fitted to one night; the headline blackout and the thesis's secondary invalidation text are not replayable; WTI levels lean on a price ratio, not a WTI thesis; the journal grows, so an unpinned rerun moves the split.

## Reproduce

    # the run of this page (about 5 minutes on 8 workers); exit 1 = the chosen rule does not hold
    PATHIEL_STATE_DIR=<pathia>/.state .venv/bin/python -m services.strategies.evals.thesis_range_test \
        --cache-dir <dir> --out-dir <dir> --journal-asof 2026-11-16T07:00Z

    # gate tests (44, no network)
    .venv/bin/python -m pytest -q services/strategies/tests/test_thesis_range.py

Outputs: `<out>/report.json` (the spec, the pick, the named rules with their trades, baselines, the deflated view, families, conditions, legs, axes, the account's trades), `<out>/variants.csv` (one row per variant, every segment), `<out>/progress.log`.
