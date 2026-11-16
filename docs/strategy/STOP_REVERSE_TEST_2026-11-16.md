# Stop-and-reverse test, 2026-11-16

**Verdict: FAIL on every fill the desk can get, nothing adopted. 7,260 variants: 0 PASS and 0 LEANING among the 3,660 the desk could trade today. The replay reproduces tonight's three trades almost to the cent, and the same rule over the other three weeks wins 2 of 27 reverse longs on Brent (6 of 114 across the four datasets). Tonight was one good night.** One footnote: with a buy-stop resting at the short's stop instead of a market order after it, 12 rows (two neighbouring cells) clear the bar beside the live book, at 20% margin for +$5 to +$23 per dataset on 3 to 9 flips each; most lose alone on two or three of the four datasets. Research only. No live config, order or rule changed.

Operator, 2026-11-16: "I SHORTED TO OUR ENTRY, GOT CALLED ON 5% LOSS, LONGED TO HIGHER RANGE THAT I JUST HIT ... SHORT 5% ON A TRIP DOWN TO ENTRY, IF TRIPPED AT 5% ST, LONG THAT ENTRY TO LOCAL HIGH".

Data: Brent 5m candles 2026-10-26 11:45Z to 2026-11-16 07:00Z (20.8 days, primary), WTI 5m (19.3 days), Brent and WTI 15m from 2026-10-18 04:15Z (29.1 days), 78 journal theses, $182.09 start. Measured fees 0.90 / 0.31 bps (taker / maker, from the review ledger). Every number is from `services/strategies/evals/stop_reverse_test.py`; reproduce commands at the end.

The live dip-buy book on the same windows:

| dataset | trades | wins | net $ | max DD |
|---|---|---|---|---|
| brent_5m (primary) | 11 | 5 | +47.79 | 27.7% |
| brent_15m | 17 | 9 | +143.10 | 27.7% |
| wti_5m | 15 | 3 | -66.11 | 48.8% |
| wti_15m | 21 | 6 | -42.12 | 49.0% |

## Tonight's replay (2026-11-15 23:00Z to 2026-11-16 07:00Z, $214)

**It matches.** The rule with X = 0.75%, short stop +0.36%, flip by market order, long target = the thesis target, long stop 0.25%, third leg on, the operator's sizes (20% / 60% / 5% of equity as margin), left to find its own entries:

| leg | | entry | stop | target | exit | net $ |
|---|---|---|---|---|---|---|
| short | operator | 04:46Z 92.563 | 92.90 | 91.81 | stopped 05:47Z 92.925 | -3.36 |
| | rule | 04:10Z 92.558 | 92.891 | 91.81 | stopped 05:45Z 92.919 | -3.49 |
| reverse long | operator | 06:00Z 92.96 avg | 92.695 | 93.709 | target 06:32Z 93.722 | +20.39 |
| | rule | 05:55Z 92.990 | 92.758 | 93.701 | target 06:30Z 93.701 | +19.01 |
| third short | operator | 06:38Z 93.808 | 94.045 | 92.84 | open | |
| | rule | 06:40Z 93.745 | 94.083 | 92.99 | open, +0.24 at 07:00Z | |

- The short fires 36 minutes before the operator's, at the same price: the 03:04Z thesis put the long entry at 91.81, and 04:00Z was the first 5m close 0.75% over it.
- **The "local high" of tonight is the thesis target.** 93.709 is the 05:54Z thesis's first target (93.701, the 21:00Z hour high) plus a cent. The rolling highs at the flip: 4h = 92.80 (under the fill, so no trade), 8h = 93.577, 12h = 94.108 (not reached by 07:00Z). The 8h rule exits the long 12 cents early (+$15.64 against +$19.01); the 24h high less 0.5% is 93.637.
- With the operator's own short forced in (92.563 / 92.90 / 91.81) and the engine running the rest: the short's stop matches in 320 of 320 flip variants, all three legs in 64 (every market-order flip to the thesis target or the 24h target). Left to itself, 16 of 960 variants reproduce the whole night: exactly the X 0.75% / stop 0.36% / market flip / thesis-or-24h target cell.
- A buy-stop resting at 92.90 would have filled at 05:45Z, nine minutes before the 05:54Z thesis existed. Its target is then the 03:04Z thesis's 94.108, not reached. Tonight's long needed the fresh thesis.

So the spec is what the operator did. The rest of this page is that rule on the other 20 days.

## The same rule on all the data

The variant that reproduces tonight (`SR_x0.75_p0.36_thesis_p0.25_tick_3flip_op`), alone on its own $182.09:

| dataset | n | win % | net $ | avg R | PF | max DD | short leg (n / wins / $) | reverse long (n / wins / $) | third (n / wins / $) |
|---|---|---|---|---|---|---|---|---|---|
| brent_5m | 62 | 16% | -114.20 | -0.63 | 0.27 | 65.8% | 33 / 6 / -20.12 | 27 / 2 / -94.31 | 2 / 2 / +0.23 |
| brent_15m | 67 | 8% | -139.98 | -0.93 | 0.09 | 76.9% | 36 / 4 / -37.18 | 31 / 1 / -102.80 | 0 |
| wti_5m | 64 | 25% | -83.47 | -0.29 | 0.49 | 52.5% | 36 / 11 / +2.91 | 25 / 3 / -86.86 | 3 / 2 / +0.48 |
| wti_15m | 72 | 13% | -137.70 | -0.78 | 0.18 | 76.9% | 41 / 9 / -23.91 | 31 / 0 / -113.79 | 0 |

Halves of the primary: -$58.84, -$55.36. Beside the live book (one position per desk): 15 trades, -$19.21, max DD 39.7% against 27.7%; Brent 15m -$106.78; WTI -$2.11 and -$34.94. **FAIL.**

The same with the long's target at the 8h local high (the operator's wording): Brent 5m 56 trades, 18% wins, -$99.49 alone, -$8.03 beside live; reverse long 19 trades, 2 wins, -$70.15 (10 of 29 stopped shorts had no room to the 8h high and did not flip). **FAIL.**

Why: the short is stopped by a 0.36% push up, and the long bought there carries a 0.25% stop. On Brent 5m the median bar range is 0.17%; the long is stopped on the next wiggle 24 times in 27. Tonight is one of the two that ran. At the operator's sizes the long is three times the short, so each of those stops costs three times what the short lost.

## Every family

**alone** = the variant on its own equity. **beside live** = the only way it runs on the account: nothing opens while the live long is open (66% to 86% of the tonight-variant's trades open inside a live long), and an open sleeve position is closed when the live long enters. Verdicts are read on the desk's own fill: a market order placed after the stop, one 5m bar late, paying half that bar's range.

Best variant per family on the desk's fill (Brent 5m; all are X 0.25% with the swing stop):

| family | variants (desk fill) | best | n alone | win % | net $ alone | avg R | PF | max DD alone | beside live: n, net $, DD vs 27.7% | halves beside live | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| S. short leg alone (test A) | 60 | swing stop, 20% margin | 43 | 53% | +8.37 | -0.17 | 1.09 | 18.1% | 14, +23.83, 21.8% | +7.25 / +16.59 | **FAIL** |
| SR. flip to the local high | 720 | swing, 12h high, 1 ATR stop, op sizes | 49 | 65% | +67.54 | +0.05 | 1.61 | 16.7% | 14, +33.87, 21.8% | +7.25 / +26.62 | **FAIL** |
| SR. flip to 24h high less 0.5% | 240 | swing, 0.25% stop, 20% margin | 45 | 51% | -0.79 | -0.20 | 0.99 | 19.5% | 16, +26.67, 22.0% | +4.41 / +22.26 | **FAIL** |
| SR. flip to the thesis target | 240 | swing, 0.25% stop, 20% margin | 52 | 48% | +3.81 | -0.17 | 1.04 | 18.3% | 17, +24.04, 22.3% | +1.42 / +22.62 | **FAIL** |
| SR3. three legs, local high | 1,440 | swing, 12h high, 1 ATR stop, op sizes | 51 | 63% | +78.59 | -0.01 | 1.87 | 13.1% | 14, +33.87, 21.8% | +7.25 / +26.62 | **FAIL** |
| SR3. three legs, 24h high | 480 | swing, 0.25% stop, 20% margin | 45 | 51% | -0.79 | -0.20 | 0.99 | 19.5% | 16, +26.67, 22.0% | +4.41 / +22.26 | **FAIL** |
| SR3. three legs, thesis target | 480 | swing, 0.25% stop, 20% margin | 53 | 49% | +3.86 | -0.17 | 1.04 | 18.3% | 17, +24.04, 22.3% | +1.42 / +22.62 | **FAIL** |

The same seven rows on the other three datasets (net $ alone / beside live):

| family best | brent_15m | wti_5m | wti_15m |
|---|---|---|---|
| S | -54.99 / +18.54 | +5.63 / -6.90 | -28.82 / -14.02 |
| SR local | -37.98 / +16.42 | -0.50 / -0.88 | -28.59 / -32.88 (DD 62.1% vs 49.0%) |
| SR 24h | -67.54 / +15.17 | +22.46 / -7.19 | -40.94 / -13.61 |
| SR thesis | -79.37 / +3.98 | +33.67 / -5.25 | -62.62 / -19.51 |
| SR3 local | -23.90 / +16.42 | -8.98 / -1.43 | -31.73 / -33.03 |
| SR3 24h | -67.54 / +15.17 | +22.46 / -7.19 | -40.94 / -13.61 |
| SR3 thesis | -79.37 / +3.98 | +33.67 / -5.25 | -62.62 / -19.51 |

Every one fails the same two legs: it does not add money on every dataset (WTI loses beside live) and drawdown is worse than live on WTI. Of the 7,260 variants, six make money alone on all four datasets; all six are resting-fill rows with the swing stop, and none of them passes.

Read the "best" rows as a ceiling. They are all the widest stop (over the 4h swing high), which is rarely hit, so they are test A's short with 6 to 14 flips attached: the +$24 to +$34 beside live on Brent is the short leg's own +$23.83.

The whole grid, per family (median net $ over the family, alone / beside live; in brackets the variants above zero beside live):

| family | variants | brent_5m | brent_15m | wti_5m | wti_15m | above zero beside live on all four |
|---|---|---|---|---|---|---|
| S | 60 | -26.77 / -3.14 (23) | -52.90 / -20.70 (12) | -14.79 / -3.42 (6) | -28.94 / -4.24 (15) | 0 |
| SR local | 1,440 | -34.36 / -3.65 (469) | -56.97 / -23.76 (295) | -23.11 / -2.07 (470) | -39.49 / -8.71 (99) | 1 |
| SR 24h | 480 | -31.98 / -3.41 (170) | -56.64 / -23.70 (110) | -17.74 / -2.64 (129) | -37.30 / -6.99 (54) | 0 |
| SR thesis | 480 | -23.11 / -5.79 (136) | -43.11 / -14.54 (126) | -19.41 / -4.70 (85) | -33.75 / -12.12 (37) | 9 |
| SR3 local | 2,880 | -36.91 / -4.22 (898) | -60.12 / -25.71 (578) | -23.73 / -2.09 (948) | -40.88 / -8.95 (188) | 1 |
| SR3 24h | 960 | -34.39 / -3.95 (326) | -59.04 / -24.18 (208) | -17.81 / -2.38 (210) | -40.21 / -7.04 (104) | 0 |
| SR3 thesis | 960 | -19.06 / -2.99 (389) | -36.49 / -11.74 (314) | -8.91 / -3.42 (278) | -27.18 / -12.27 (61) | 26 |

Every family's median loses on every dataset, alone and beside live.

### What each choice does (7,200 flip variants, Brent 5m, alone)

| axis | level | mean net $ | above zero | short leg mean $ | reverse long mean $ | reverse long wins |
|---|---|---|---|---|---|---|
| short stop | +0.25% (5% of margin) | -50.68 | 17 / 1,800 | -21.10 | -28.79 | 31% |
| | +0.36% (operator) | -53.78 | 10 / 1,800 | -24.19 | -29.36 | 25% |
| | +0.5% | -48.68 | 51 / 1,800 | -19.83 | -29.02 | 25% |
| | over the 4h swing high | -1.89 | 799 / 1,800 | -0.29 | -0.75 | 51% |
| long target | 4h high | -46.51 | 113 / 1,440 | -21.46 | -23.34 | 31% |
| | 8h high | -44.80 | 161 / 1,440 | -20.00 | -23.23 | 31% |
| | 12h high | -35.62 | 210 / 1,440 | -16.64 | -18.80 | 36% |
| | 24h high less 0.5% | -39.51 | 121 / 1,440 | -14.12 | -24.59 | 21% |
| | thesis target | -27.34 | 272 / 1,440 | -9.53 | -19.94 | 29% |
| long stop | 0.25% (operator) | -39.73 | 127 / 1,800 | -20.41 | -19.67 | 15% |
| | under the short's entry | -40.23 | 195 / 1,800 | -16.95 | -23.04 | 24% |
| | 1 ATR | -42.06 | 281 / 1,800 | -12.49 | -29.09 | 35% |
| | 2 ATR | -33.01 | 274 / 1,800 | -15.55 | -16.12 | 61% |
| flip fill | resting buy-stop | -33.67 | 475 / 3,600 | -16.72 | -16.61 | 31% |
| | market, one bar late (desk) | -43.85 | 402 / 3,600 | -15.98 | -27.35 | 29% |
| third leg | off | -38.09 | 283 / 2,400 | | | |
| | to the long's entry | -39.35 | 281 / 2,400 | | | third leg mean -1.02 |
| | to the thesis entry | -38.83 | 313 / 2,400 | | | third leg mean -0.26 |
| size | 5% margin | -13.41 | 223 / 1,440 | -7.06 | -6.16 | |
| | 10% margin | -25.50 | 216 / 1,440 | -13.13 | -12.02 | |
| | 20% margin | -46.23 | 206 / 1,440 | -22.78 | -22.87 | |
| | 1% risk at the stop | -33.70 | 49 / 1,440 | -23.26 | -9.53 | |
| | operator (20 / 60 / 5%) | -74.95 | 183 / 1,440 | -15.53 | -59.31 | |

- **Both legs lose on average at every level of every axis.** No setting of any one dial turns the mean positive.
- **The tight percent stops are the problem.** A short with a 0.25% to 0.5% stop wins 19% to 34% of the time and its flip 25% to 31%. Only the swing stop is near break-even, and it rarely flips (10,365 flips against 24,135 to 32,560).
- **Size does not change the sign, only the amount.** The operator's mix loses the most because it puts 60% of equity on the leg that loses the most.
- **The third leg is nothing either way** (a few cents to a dollar): it fires only after a flip wins, which is rare.
- **The 4h high is seldom a target at all**: a short stopped 0.36% up is usually already at the 4h high, so there is no room and no flip.

## The resting-order footnote

"Resting" means a buy-stop sitting at the short's stop price (and, for the third leg, a sell limit at the long's target), filled by the venue with no desk in the loop. The desk's tools place one bracket per desk and no such order; by hand on the venue it can be done. 12 of the 3,600 resting rows are a PASS on the repo bar. They are two neighbouring cells at four of the five sizes (not the operator's mix): X 0.75%, short stop 0.36%, long target = thesis target, long stop 0.25% (third leg either way) or under the short's entry (third leg to the thesis entry).

The 20%-margin row of the first cell, `SR_x0.75_p0.36_thesis_p0.25_resting_3thesis_m20`:

| dataset | alone: n, net $ | beside live: n, net $ | short (n / wins / $) | flip (n / wins / $) | third (n / wins / $) | beside live without tonight | flip-bar worst case | same rule, market flip |
|---|---|---|---|---|---|---|---|---|
| brent_5m | 50, -4.51 | 12, +20.98 | 7 / 2 / +0.09 | 4 / 2 / +10.57 | 1 / 1 / +10.32 | +17.56 | -5.39 | -7.43 |
| brent_15m | 62, -30.84 | 21, +23.34 | 10 / 1 / -28.44 | 8 / 4 / +40.38 | 3 / 2 / +11.40 | +18.96 | +23.34 | -61.89 |
| wti_5m | 44, +40.06 | 9, +18.95 | 5 / 2 / +1.22 | 3 / 2 / +2.25 | 1 / 1 / +15.48 | +18.53 | +16.50 | +0.59 |
| wti_15m | 64, -20.51 | 15, +12.52 | 9 / 4 / +5.91 | 5 / 2 / +10.27 | 1 / 0 / -3.66 | +10.12 | +12.52 | -10.38 |

Halves beside live: +$8.77, +$12.21. It holds without tonight. What stands against it:

1. **It needs the order the desk does not have.** The same rule with a market flip is a FAIL on every row (last column).
2. **It loses alone** on three of four datasets. It only adds money in the gaps the live long leaves, on 12 trades in three weeks, 4 of them flips and one a third leg that made half the Brent total.
3. **The first cell fails the harsh reading of the flip bar.** The model lets only the bar's close stop a long that was filled inside that bar. Counting the whole bar (as if the low came after the fill) turns Brent 5m to -$5.39. The second cell (stop under the short's entry) survives it: +$17.78, +$14.69, +$17.41, +$7.21 beside live.
4. **12 rows of 7,260 is 0.17%**, two cells of 1,440, picked after the fact. The short-ladder grid produced the same kind of survivor (5 of 48) and it failed the next check.
5. It is not the operator's rule: tonight's long was a market order six to thirteen minutes after the stop and used a thesis written after it.

Label: PASS on paper under an order the desk cannot place; not a candidate for live money.

## Skill or one good night

One good night.

- The rule that reproduces tonight wins 2 of 27 reverse longs on Brent 5m in three weeks, 6 of 114 over the four datasets, and loses $83 to $140 of $182 alone on each.
- Tonight's long won because Brent ran 0.9% in 35 minutes after the stop. In 24 of the other 26 flips it came back 0.25% first.
- The +$17 of the night is real money. The same three orders placed every time the setup appeared since 10-26 would have cost about $114.
- The short leg alone is test A again, with tighter stops: 0 of 60 variants above zero on every dataset, alone or beside live.

Gut call on live money: **no** for the rule as traded (market flip, 60% of equity on the long). If the operator wants to keep one thing on paper, it is the resting version with the long's stop under the short's entry at 5% to 10% margin: watch it for 20 more flips before any money.

## Method

One script, deterministic, no LLM call: `services/strategies/evals/stop_reverse_test.py`. Same cache in, same numbers out.

- **Rule.** Short leg: journal thesis long, a closed bar at or over thesis entry x (1 + X), X = 0.25 / 0.5 / 0.75%; target = the thesis entry (resting limit); stop +0.25% (= 5% of the position's margin at 20x), +0.36%, +0.5%, or 0.05% over the 4h high (never closer than 0.1%); one cycle per thesis. Reverse leg: on the short's stop, long with target = highest high of the 4 / 8 / 12 hours before the bar, or 24h high x 0.995, or the thesis's first target; stop 0.25% under the fill, 0.05% under the short's entry, or 1 / 2 x ATR14(1h); refused with under 0.1% of room to the target or a stop outside 0.1% to 2%. Third leg: on the long's target, short with the short leg's stop rule, target = the long's entry or the thesis entry; no further flip.
- **Interpretation choices.** (1) "5% loss" is read as 5% of the position's margin: 0.25% of price at 20x; the operator's actual stop was 0.36% (7.8% of margin). (2) "Local high" is gridded as 4 / 8 / 12h rolling highs; tonight's actual target was the thesis target, kept as its own family. (3) One cycle per thesis: the journal writes a thesis every 9 hours on average, and without a cap the short re-fires every bar. (4) The third leg's target tonight (92.84) sits between the long's entry (92.96) and the thesis entry (92.10): both are tested. (5) A new thesis does not move a resting stop or target. (6) No R gate on any leg.
- **Grid.** 60 short-alone variants (3 X x 4 stops x 5 sizes) plus 7,200 with the flip (3 X x 4 short stops x 5 long targets x 4 long stops x 2 fills x 3 third-leg settings x 5 sizes) = **7,260 tried**, all reported in `variants.csv`. 3,660 are on the desk's fill.
- **Sizes.** Margin = 5 / 10 / 20% of equity at 20x on every leg; "r1" = 1% of equity lost at the stop, capped at the whole equity at 20x; "op" = the operator's night (20% short, 60% long, 5% third). Equity = start (or the live book's marked equity) plus the sleeve's own P&L.
- **Fills** (`INTRADAY_5M_TEST_2026-11-16.md`'s model): a desk market order fills at the open one 5m bar after the bar that follows the signal (the next bar on 15m data) and pays half that bar's range, never under 3 bps; a stop is a taker fill with 3 bps slippage (tonight's real stop slipped 2.7 bps); a target is a maker limit that needs a trade-through by one tick and never fills on its own fill bar; stop and target on one bar is a loss; a bar that opens beyond the stop fills at its open. Fees 0.90 / 0.31 bps, measured (`intraday_5m_test.measured_fees`). Funding on the hour. Weekend flat.
- **The two flip fills.** "tick": the market order above. "resting": a buy-stop at the short's stop price, same bar, taker, 3 bps; the third leg a maker sell limit at the long's target. On that bar only the close can stop the new leg; the audit reruns passers with the whole bar counted.
- **Beside live.** `short_ladder_test.prepare` (the live Brent rule at 6% risk, 20/15 leverage fit, the sim's own fees) gives the live long book; the sleeve's net is what it adds. Collisions for the tonight-variant alone: 47 of 62 trades (76%) open while a live long is open on Brent 5m, 66% on Brent 15m, 86% and 85% on WTI.
- **Datasets.** Brent 5m is the primary (the venue keeps 5,000 bars; an older cached snapshot extends it to 10-26). The 15m sets add eight October days at coarser bars, where stop-and-target-on-one-bar bites harder. WTI carries each thesis entry and target over by the WTI/Brent 1h-close ratio at the hour it was written.
- **The bar.** `short_ladder_test.judge` (OIL_STRATEGY.md's adoption bar, strict reading): beside live, add money on every dataset, drawdown no worse than live on every dataset, both halves of the primary (split 2026-11-05 21:15Z, by entry time), every grid neighbour above zero on the primary, and still above zero without the best exit day. At least 10 trades beside live on the primary; under 10 is LEANING or NOISE, never PASS. A family is the best verdict among its desk-fill variants.
- **Audit of passers** (`audit`): net alone, legs beside live, net without the trades entered on the night the rule came from, the flip-bar worst case, and the twin under the other fill.

Where it would break: three weeks is one regime (Brent from 110 down to 94, range 88 to 114) and 27 flips is a small sample for the exact tonight-variant; a 5m bar cannot order a fill and a stop inside it (handled by the loss rule and the audit's worst case); the half-range slippage is about twice the measured two-minute drift, so the market flip is read harshly, but the flip leg also loses with the resting fill (mean -$16.61); the WTI rows lean on a price ratio, not a WTI thesis; the thesis at a bar is read at the bar's open, so a thesis written inside a bar counts from the next one.

## Reproduce

    # the full run (about 2 minutes on 8 workers); exit 1 = no desk-fill variant passes
    PATHIEL_STATE_DIR=<pathia>/.state .venv/bin/python -m services.strategies.evals.stop_reverse_test \
        --cache-dir <fresh dir> --out-dir <dir>

    # keep the trades of named variants in the report
    PATHIEL_STATE_DIR=<pathia>/.state .venv/bin/python -m services.strategies.evals.stop_reverse_test \
        --cache-dir <same dir> --out-dir <dir> --detail SR_x0.75_p0.36_thesis_sentry_resting_3thesis_m10

    # gate tests (28, no network)
    .venv/bin/python -m pytest -q services/strategies/tests/test_stop_reverse.py

Outputs: `<out>/report.json` (families, axes, the replay with every variant's trades for the night, the audit, the trades of the best and the two named variants), `<out>/variants.csv` (one row per variant, every dataset), `<out>/progress.log`. This page's run: `/tmp/stop-reverse/run-0703/`.
