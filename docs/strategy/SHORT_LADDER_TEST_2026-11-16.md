# Long ladder, short-to-entry and short ladder test, 2026-11-16

**Verdict: nothing passes, nothing is adopted. The long ladder above the entry (D) loses beside the live rule on every dataset: 0 of 1,728 variants beat live on all three. Shorting down to our own entry (A) and the short ladder (B) only make money on the first tick after a weekend, a fill the desk does not get.** Research only. No live config, order or rule changed.

**Follow-up, same day (sections E and F below). E, the long ladder sized in equity (20% of equity at +0.5%, 10% at +1%): FAIL, it loses beside the live rule on all three datasets and risks up to 10.3% of equity per trade. F, realistic Sunday-reopen fills for the shorts: none of the journal's reopen profit survives; two of 48 short-to-entry variants clear the bar on paper at +$3 to +$17 per dataset, with +$0.07 and +$0.08 in the second half. B still fails. Nothing adopted.**

Data: Brent 15m candles to 2026-11-16 01:00Z, 74 journal theses from 2026-10-18, $182.09 start, normal risk 6% of equity. Every number below is from `services/strategies/evals/short_ladder_test.py`; reproduce commands at the end.

The live long book on the same data (dip entry only, the rule as it trades):

| dataset | window | trades | wins | net $ | max DD |
|---|---|---|---|---|---|
| brent_journal (primary) | 10-18 to 11-16 | 17 | 9 | +143.10 | 27.7% |
| brent_replay30 | 09-30 to 10-30 | 32 | 16 | +141.23 | 26.0% |
| wti_journal | 10-18 to 11-16 | 21 | 6 | -42.12 | 49.0% |

Halves of the primary (split 2026-11-01 14:30Z): first half 9 trades, +$166.40; second half 8 trades, -$12.17.

## How to read the tables

- **alone**: the variant on its own equity, no live book.
- **beside live**: the only way it could run on the account (one position per desk). No ladder or short while the live long is open; an open one is closed at the tick the live long enters. "Net beside live" is what the variant adds to the live net. Above zero = it beats live on that dataset.
- **Verdict**: PASS or FAIL against the bar when the variant has 10 or more trades beside live on the primary. Under 10 it is LEANING (every money leg holds) or NOISE (one does not). Never rounded up.

## D. Long ladder above the entry (the main test)

The idea: the live rule never chases, so when Brent runs above the thesis entry and never dips back, the desk misses the move. The ladder buys anyway at reduced risk: tier 1 at +1% over the thesis entry with 20% of normal risk (1.2% of equity), more at +1.5% and +2%.

**It does not work.** 1,728 variants: 0 PASS, 0 LEANING, 56 FAIL, 1,672 NOISE.

| | brent_journal | brent_replay30 | wti_journal |
|---|---|---|---|
| variants that traded beside live | 808 | 1,104 | 592 |
| of those, above zero | 160 | 23 | 29 |
| median net beside live $ | -14.01 | -12.45 | -6.74 |
| worst / best net $ | -125.40 / +25.25 | -135.36 / +10.99 | -36.32 / +6.20 |
| variants with 10+ trades | 56 | 232 | 24 |
| of those, above zero | 0 | 0 | 5 |
| above zero on all three datasets | 0 | | |

Every variant with enough trades to judge on Brent loses. The 160 "winners" on the journal are one to three trades each, and none of them is positive on the replay as well.

By confirmation (all 576 variants of each kind, trades summed across them, beside live):

| confirmation | journal: trades / wins / variants above zero | replay30 | WTI |
|---|---|---|---|
| touch (buy stop at the tier) | 2,560 / 400 (16%) / 0 | 5,208 / 1,471 (28%) / 18 | 1,680 / 444 (26%) / 5 |
| daily close 00:00Z | 304 / 96 / 96 | 872 / 72 (8%) / 0 | 72 / 24 / 24 |
| daily close 21:00Z | 64 / 64 / 64 | 712 / 128 (18%) / 5 | 104 / 16 / 0 |

The daily-close rule cuts the damage because it almost never fires: on the journal it is a single trade (2026-10-23 00:00Z, bought 108.61, target 110.31 hit twelve hours later) repeated across the grid. On the replay the same rule buys 1 to 8 times per variant and wins 8% to 18%.

The operator's own example and its neighbours:

| variant | where | n | wins | win % | net $ | avg R | max DD vs live | verdict |
|---|---|---|---|---|---|---|---|---|
| +1/+1.5/+2%, ramp 20/40/60, touch, thesis target, live stop (24h low - 0.75) | journal | 1 | 0 | 0% | -4.00 | -1.09 | 28.0% vs 27.7% | NOISE |
| | replay30 | 7 | 1 | 14% | -33.50 | -0.60 | 34.8% vs 26.0% | |
| | WTI | 0 | 0 | - | 0.00 | - | 49.0% vs 49.0% | |
| same, stop 2 ATR under the fill | journal | 7 | 2 | 29% | -53.58 | -0.58 | 39.3% vs 27.7% | NOISE |
| | replay30 | 18 | 5 | 28% | -81.63 | -0.55 | 37.6% vs 26.0% | |
| | WTI | 6 | 3 | 50% | -4.06 | -0.33 | 51.0% vs 49.0% | |
| same, 20% on every tier | journal | 7 | 2 | 29% | -28.08 | -0.53 | 32.5% vs 27.7% | NOISE |
| | replay30 | 18 | 5 | 28% | -46.68 | -0.53 | 29.8% vs 26.0% | |
| | WTI | 6 | 3 | 50% | -2.07 | -0.28 | 50.3% vs 49.0% | |
| same ramp, daily close 00:00Z, 2 ATR stop | journal | 1 | 1 | 100% | +9.76 | +1.25 | 27.0% vs 27.7% | NOISE |
| | replay30 | 3 | 0 | 0% | -8.96 | -0.79 | 26.0% vs 26.0% | |
| | WTI | 0 | 0 | - | 0.00 | - | 49.0% vs 49.0% | |
| best of D by net: +1%, 2 tiers, 60% each, daily close 00:00Z, 1.5 ATR stop, 1R gate | journal | 1 | 1 | 100% | +25.25 | +1.65 | 25.9% vs 27.7% | NOISE |
| | replay30 | 3 | 0 | 0% | -28.87 | -1.13 | 30.7% vs 26.0% | |
| | WTI | 0 | 0 | - | 0.00 | - | 49.0% vs 49.0% | |

Walk-forward of the D best: first half +$25.25 (1 trade), second half $0.00 (0 trades). One trade in October is the whole result.

Three reasons it loses:

1. **The live stop does not fit.** From +1% above the entry, a stop 0.75 under the 24h low is too wide for 15x most of the time. With the live stop, 176 of 576 variants trade at all on the journal, the most active one 10 times, and none is above zero. The tighter ATR stops trade more and lose more (mean net beside live: live stop -$5.9, 2 ATR -$8.6, 1.5 ATR -$12.1 on the journal).
2. **There is no room left.** Across the grid the ladder was refused 46,068 ticks for "price already at or past the target", 60,400 for the R gate and 12,872 for leverage. A long thesis whose entry is 1% below the market usually has its first target only a little above it.
3. **More size loses more.** Mean net beside live on the journal: 20% per tier -$4.9, ramp -$7.3, 40% -$9.6, 60% -$13.8. Lower first tier loses more than a higher one (+0.5%: -$16.1; +2%: -$5.4): the closer to the entry the ladder buys, the more often it buys.

### The moves the live rule missed

A missed thesis = a long thesis that was right (price ended its life higher than it started) and whose entry was never touched, so the live rule could not buy.

| journal, 70 long theses with an entry | count |
|---|---|
| entry touched (the live rule could buy) | 47 |
| entry never touched | 23 |
| of those, direction right: **missed** | 16 |
| of the missed, price reached the first target | 1 |

The 16 missed theses are small moves. All are from 11-03 to 11-13. The market opened 0.2% to 1.7% above the entry and then ran at most 1.9% further, 0.7% at the median; one reached its target (11-09 19:50Z, entry 90.10).

| what the ladder caught | variants | theses caught | result |
|---|---|---|---|
| beside live, best case | 224 of 1,728 | 1 of 16 | -$17.95 to +$8.57 on that one trade; 16 of the 224 won it |
| beside live, the rest | 1,504 | 0 of 16 | - |
| alone (no live book), best case | 24 | 5 of 16 | 1 or 2 wins of 5, -$46.32 to -$7.37 |
| alone, any caught | 592 | 1 to 5 of 16 | 27 of the 592 made money on what they caught |

**The ladder does not catch the missed moves.** Beside the live book it reaches at most one of the 16, because the live long is usually already open from an earlier thesis (65% of the ladder's trades overlap a live long, section C) or the tier has no room. Alone it reaches up to five and loses on them.

Tonight's case: +0.5% over the thesis entry (92.75 to 92.80) is 93.21 to 93.26, and +1% is 93.68 to 93.73. Price sat at 92.6 to 93.1, so no tier of any tested ladder would have filled.

## A. Short down to our own entry

While the desk waits to buy a dip (long thesis, mid above its entry), short the mid with the thesis entry as the target. 48 variants: stop (0.75 over the 24h high, or 1 / 1.5 / 2 ATR over the mid), minimum planned R (0 / 1 / 2), once per thesis or re-entry, full or 20% risk.

**On paper it adds money beside live. Without the weekend-reopen fills it does not.**

| | brent_journal | brent_replay30 | wti_journal |
|---|---|---|---|
| variants above zero beside live (of 48) | 44 | 41 | 45 |
| median net beside live $ | +25.04 | +18.57 | +15.04 |
| variants above zero alone | 35 | 27 | 41 |
| above zero beside live on all three | 36 | | |
| same, with the reopen-tick entries removed | 13 | | |

Summed over all 48 variants on the journal: +$2,096.09 beside live, of which the trades entered on the first tick after a weekend made +$2,185.53 (128 of 956 trades). Everything else nets -$89.44. The reopen tick (Sunday 22:00Z in summer time, 23:00Z in winter) is the first bar the weekend-flat rule allows. Its open is the perp's stale weekend price, and the futures reprice it at once: sold 107.12 and covered 105.35 two hours later on 10-18, 98.07 to 96.83 in 15 minutes on 11-08, 93.38 to 92.70 in 15 minutes on 11-15. The backtest sells that open. The desk, on a 15-minute loop, does not.

| variant | where | n | wins | win % | net $ | avg R | max DD vs live | verdict |
|---|---|---|---|---|---|---|---|---|
| **best of A by net: 1 ATR stop, 1R gate, once per thesis, full risk** | journal alone | 31 | 15 | 48% | +30.53 | +0.23 | 28.7% | |
| | journal beside live | 12 | 7 | 58% | +156.02 | +0.99 | 20.6% vs 27.7% | **FAIL** |
| | replay30 beside live | 21 | 9 | 43% | +177.15 | +0.91 | 37.2% vs 26.0% | |
| | WTI beside live | 9 | 8 | 89% | +181.53 | +1.97 | 27.0% vs 49.0% | |
| structural stop (24h high + 0.75), no R gate, once, full risk | journal beside live | 11 | 10 | 91% | +70.80 | +0.44 | 20.6% vs 27.7% | FAIL |
| | replay30 beside live | 22 | 12 | 55% | +29.07 | +0.19 | 27.7% vs 26.0% | |
| | WTI beside live | 10 | 8 | 80% | +41.50 | +0.38 | 38.7% vs 49.0% | |
| 2 ATR stop, 1R gate, re-entry, 20% risk (the one closest to passing) | journal alone | 25 | 14 | 56% | +13.46 | +0.29 | 4.2% | |
| | journal beside live | 16 | 10 | 63% | +19.07 | +0.50 | 25.7% vs 27.7% | **FAIL** |
| | replay30 beside live | 30 | 15 | 50% | +12.73 | +0.25 | 25.7% vs 26.0% | |
| | WTI beside live | 10 | 6 | 60% | +11.73 | +0.72 | 46.3% vs 49.0% | |

Why each fails:

- **Best by net** (1 ATR, 1R, once, full risk): drawdown worse on the replay (37.2% vs 26.0%); first half -$9.05, second half +$88.78; with the reopen entries removed the journal's +$156.02 is +$12.53 and the first half is -$65.34.
- **Structural stop**: drawdown worse on the replay; its only neighbour loses (-$39.26); replay net without reopen entries -$3.35.
- **2 ATR, 1R, re-entry, 20% risk**: clears the five older legs (beats live on all three, drawdown no worse on all three, halves +$10.34 / +$4.42, neighbours +$22.34 / +$16.70 / +$13.35, +$11.51 after dropping its best day). It fails the reopen leg: 3 of its 16 journal trades are reopen ticks and made $16.16 of its $19.07; without them WTI is -$0.65 and the second half is +$0.07 on one trade. Four sibling variants at 20% risk clear the older legs the same way and fail the same leg. 5 of 48 clearing a bar is also what a grid of this size produces by chance.

Does it beat doing nothing? Alone, 35 of 48 variants are above zero on the journal, 27 on the replay, 41 on WTI, reopen fills included, on a month where Brent went 107, 113, 89, 93. The near-pass variant alone made +$13.46 on 25 trades, and the second half of the journal holds 3 of its trades. That is not an edge.

The operator's own short (92.714, stop 93.80, target 92.48): 0.215R planned, 0.149R after fees and stop slippage. It needs to win 87% of the time to break even.

## B. Tiered short ladder from a higher entry

No short at the market. Tier k sells only at reference x (1 + k x step), with the reference the long thesis's entry or the 24h VWAP, steps +0.5 / +1 / +1.5 / +2%, 2 or 3 tiers, one stop one step above the last tier, target back at the reference.

**Interpretation of "risk 20%":** 20% of the normal per-trade risk budget. Normal is 6% of equity, so a 20% tier risks 1.2% of equity at the ladder's stop. Schemes tested: 20%, 40% or 60% on every tier, and a ramp 20 -> 40 -> 60. **EOD close:** the tier needs a daily candle to CLOSE above its level, then sells the next bar's open. Two closes tested: 00:00Z (the venue's 1d candle) and 21:00Z (the futures settlement hour).

192 variants: 0 PASS, 0 LEANING, 20 FAIL, 172 NOISE.

| | brent_journal | brent_replay30 | wti_journal |
|---|---|---|---|
| variants that traded beside live | 92 | 136 | 84 |
| of those, above zero | 37 | 28 | 28 |
| median net beside live $ | -2.86 | -6.87 | -3.57 |
| variants above zero alone | 32 | 18 | 44 |
| above zero beside live on all three | 1 | | |
| same, with reopen-tick entries removed | 0 | | |

Summed over the grid on the journal: +$313.03 beside live, +$307.48 of it from reopen-tick entries.

| what | finding |
|---|---|
| EOD close confirmation | Almost never fires (mean 0.5 to 0.6 trades per variant on the journal) and loses when it does: mean net -$3.3 (00:00Z) and -$2.1 (21:00Z) on the journal, -$4.3 / -$5.4 on the replay, -$2.2 / -$2.0 on WTI |
| touch (resting limit) | Mean +$10.3 journal, -$2.4 replay, +$0.2 WTI |
| step | +0.5% trades most (mean 7.3 trades) and loses on all three; +1.5% and +2% barely trade (0.2 and 0.1 trades) |
| reference | thesis entry +$2.3 / -$5.1 / +$0.5; VWAP +$1.0 / -$2.9 / -$3.1 |
| risk scheme | no scheme is positive on the replay or WTI; bigger tiers lose more there |

| variant | where | n | wins | win % | net $ | avg R | max DD vs live | verdict |
|---|---|---|---|---|---|---|---|---|
| **best of B by net: thesis entry, +0.5% steps, 3 tiers, 60% each, touch** | journal alone | 53 | 37 | 70% | -50.26 | +0.07 | 47.3% | |
| | journal beside live | 25 | 18 | 72% | +68.05 | +0.24 | 29.1% vs 27.7% | **FAIL** |
| | replay30 beside live | 22 | 13 | 59% | -3.07 | +0.17 | 39.7% vs 26.0% | |
| | WTI beside live | 20 | 14 | 70% | +22.84 | +0.51 | 49.7% vs 49.0% | |

Walk-forward of the B best: first half +$32.70 (13 trades), second half +$16.73 (12 trades); without reopen entries +$32.70 / +$4.74. It fails on the replay (net -$3.07), on drawdown on all three datasets, on the plateau, and without reopen entries (replay -$30.12, WTI -$24.60). It wins often and small (72%, +0.24R) and gives it back on the stops: its seven journal losses are -$21 to -$41 each, against a median win of $4.63.

## C. Fit with the live long book

How the ALONE run's trades sit against the live long's holding periods, journal, summed over each grid:

| study | trades | overlap a live long | opened while the long was open | live long entered during the trade |
|---|---|---|---|---|
| D long ladder | 5,912 | 3,816 (65%) | 3,240 | 576 |
| A short to entry | 1,976 | 1,114 (56%) | 1,106 | 8 |
| B short ladder | 1,176 | 840 (71%) | 812 | 28 |

Per variant:

| variant | journal | replay30 | WTI |
|---|---|---|---|
| A best by net | 68% (21 of 31) | 29% | 77% |
| A 2 ATR / 1R / re-entry / 20% | 48% (12 of 25) | 18% | 62% |
| B best by net | 64% (34 of 53) | 40% | 73% |
| D best by net | 67% (2 of 3) | 25% | 100% |

More than half of every variant's signals arrive while the desk already holds its long: price is above the entry because the long is working. Those are the trades the one-position rule removes, and for the shorts they are the losers (A best: +$30.53 alone on 31 trades, +$156.02 beside live on 12). A live long entering DURING a short is rare (8 of 1,976 for A): the short's target is the long's entry, so the short is usually out first.

Combined P&L (live net + the variant run only while no long was open):

| variant | journal | replay30 | WTI |
|---|---|---|---|
| live alone | +143.10 | +141.23 | -42.12 |
| + D best | +168.35 | +112.36 | -42.12 |
| + A best by net | +299.12 | +318.38 | +139.41 |
| + A 2 ATR / 1R / re-entry / 20% | +162.17 | +153.96 | -30.39 |
| + B best by net | +211.15 | +138.16 | -19.28 |

The A and B rows include the reopen-tick fills. Without them: A best +$155.63 on the journal, A 20% +$146.01 on the journal and -$42.77 on WTI, B best +$185.89 on the journal, +$111.11 on the replay and -$66.72 on WTI.

## Gut call

No live money on any of it.

- **D (long ladder):** no. It is a way to buy late with a stop that does not fit, and the data says so on all three datasets. The moves the dip rule misses are small (0.7% median run) and the ladder does not catch them.
- **A (short to entry):** no. The whole profit is one kind of fill the desk cannot get. What is left is a 0.2R to 1R scalp against the desk's own thesis.
- **B (short ladder):** no. High win rate, losses several times the wins, negative on the replay.
- **Worth a follow-up, not a trade:** the reopen tick itself. On three Sundays in this window the perp opened 0.7% to 1.7% above where the short was covered 15 minutes to two hours later. That is a fill question (1m candles and the book at 22:00Z / 23:00Z), not a strategy yet, and n = 3.

## E. Long ladder sized in equity (follow-up)

Operator: "test the tiered entry 20% equity at .5%+ 10% equity at 1%+". When Brent is +0.5% over the thesis long entry, buy with 20% of desk equity as margin; at +1%, add 10%. Size = margin x the fitted leverage (20x, 15x on a wide stop), so tier 1 is a notional of about 4x equity. Stop: the live rule's, 0.75 under the 24h low. Target: the thesis's first target. Buy stop at the tier, taker fees, one ladder per thesis.

**FAIL. It loses beside the live rule on every dataset, and alone on every dataset.**

The operator's tiers (`E_eq20_10_touch_thesis_lo24_r0`):

| fills | where | n | wins | win % | net $ | avg R | max DD vs live | verdict |
|---|---|---|---|---|---|---|---|---|
| open print | journal alone | 13 | 6 | 46% | -16.20 | -0.24 | 33.5% | |
| | journal beside live | 10 | 1 | 10% | -85.24 | -0.67 | 42.7% vs 27.7% | **FAIL** |
| | replay30 alone | 10 | 4 | 40% | -7.62 | -0.21 | 25.6% | |
| | replay30 beside live | 9 | 2 | 22% | -51.55 | -0.55 | 39.3% vs 26.0% | |
| | WTI alone | 15 | 6 | 40% | -12.44 | -0.23 | 28.7% | |
| | WTI beside live | 5 | 1 | 20% | -18.92 | -0.50 | 54.4% vs 49.0% | |
| realistic reopen | journal alone | 12 | 6 | 50% | -9.76 | -0.20 | 33.5% | |
| | journal beside live | 9 | 1 | 11% | -75.92 | -0.66 | 42.7% vs 27.7% | **NOISE** (n under 10; every leg fails) |
| | replay30 beside live | 9 | 2 | 22% | -51.75 | -0.56 | 40.4% vs 26.0% | |
| | WTI beside live | 5 | 1 | 20% | -17.67 | -0.47 | 54.4% vs 49.0% | |

Walk-forward, beside live: first half -$25.51 (3 trades), second half -$34.03 (7 trades); with realistic reopen fills -$25.51 / -$28.75. Neighbours (10/5% and 30/15% margin, 1R gate): -$45.41, -$119.98, -$79.89.

**What each tier really risks at its stop** (price to the stop, fees and stop slippage, % of equity; the desk's normal trade risks 6%):

| | tier 1: 20% of equity at +0.5% | tier 2: 10% of equity at +1% | both tiers, worst trade |
|---|---|---|---|
| journal, mean / max | 5.4% / 6.6% | 3.5% / 3.9% | 10.3% |
| replay30, mean / max | 5.4% / 6.3% | 3.0% / 3.8% | 10.0% |
| WTI, mean / max | 5.4% / 6.7% | 3.4% / 4.1% | 10.8% |

Said plainly: "20% of equity" sounds small and is not. At 20x it is a position four times the account, and the first tier alone carries about the desk's whole normal risk (5.4% on average, up to 6.7%). With the second tier the trade risks up to 10.8% of equity, 1.7 to 1.8 times what the live rule allows, on an entry the live rule refuses. The risk-sized ladder of section D put 1.2% on its first tier.

Why it loses:

- **The live long cuts it.** 5 of its 10 journal trades (3 of 9 on the replay, 3 of 5 on WTI) end when price falls back to the thesis entry and the live rule buys. The ladder bought 0.5% to 1% higher and is closed there at a loss on a 4x-equity position. That is the design: it buys the part of the move the dip rule waits out, and the dip usually comes.
- **The live stop rarely fits.** 147 ticks on the journal and 535 on the replay were refused because 0.75 under the 24h low was too wide for 15x from that far up.
- **It caught 1 of the 16 missed theses** beside live (+$10.55 on it), 2 alone.

The grid (162 variants: margins 10/5, 20/10, 30/15; touch or a daily close; thesis or 24h-high target; live or ATR stop; 0 / 1 / 2R gate), beside live:

| | brent_journal | brent_replay30 | wti_journal |
|---|---|---|---|
| variants that traded | 120 | 141 | 105 |
| of those, above zero (open print / realistic) | 15 / 15 | 0 / 0 | 15 / 18 |
| median net $ (open print / realistic) | -29.56 / -26.39 | -23.63 / -24.06 | -5.42 / -5.42 |
| variants with 10+ trades, above zero | 0 of 18 | 0 of 33 | 6 of 9 |
| above zero on all three | 0 | | |

Verdicts: 0 PASS, 0 LEANING on either fill model (18 FAIL and 144 NOISE on the open print; 9 FAIL and 153 NOISE with realistic fills). The best by net is a daily-close variant with one trade (+$33.79 on the journal, -$61.50 on the replay, -$24.99 on WTI).

## F. Realistic Sunday-reopen fills for A and B (follow-up)

Operator: "why can't you just make them apply when realistic like the Sunday reopen". The post-weekend entries are kept and filled the way the desk would get them.

**The model.** The desk cron runs at :03/:18/:33/:48 and a tick takes a minute or two, so the earliest order after the reopen lands about five minutes in. The fill is the OPEN of the 5m candle that starts five minutes after the reopen; the rest of the 15m bar is that candle's and the next one's high and low; slippage is half that 5m candle's high-low range, never under the sim's own 3 bps. This is measured on candles, not taken from the repo's book-impact curve (`desk_tick_sim.EXIT_SLIP_CURVE`), which was walked on a weekday book and says 0.9 bps at this size. The venue serves only its last 5,000 candles per interval, so 5m data exists for the last three reopens. For the older ones the fill is the next 15m tick (the reopen bar's close) at the median measured slippage of that coin.

| reopen | coin | open print | realistic fill | move | slippage | source |
|---|---|---|---|---|---|---|
| 10-18 22:00Z | Brent | 107.15 | 106.01 | -1.06% | 11.2 bps | next tick |
| 10-25 22:00Z | Brent | 110.55 | 110.25 | -0.27% | 11.2 bps | next tick |
| 11-01 23:00Z | Brent | 111.81 | 111.88 | +0.06% | 11.2 bps | 5m |
| 11-08 23:00Z | Brent | 98.099 | 96.899 | -1.22% | 20.1 bps | 5m |
| 11-15 23:00Z | Brent | 93.407 | 92.842 | -0.60% | 9.6 bps | 5m |
| 11-08 23:00Z | WTI | 91.87 | 90.392 | -1.61% | 24.2 bps | 5m |
| 11-15 23:00Z | WTI | 87.393 | 87.004 | -0.45% | 5.7 bps | 5m |

On the 1m candles of 11-15 Brent traded 93.41, 93.01, 92.96, 92.86, 92.78 in the first five minutes: 0.4% of the 0.8% drop was gone inside the first minute. The reprice is over before the desk's first tick.

**How much of the old reopen profit survives** (summed over each grid, beside live; "reopen trades" = trades entered on a reopen tick):

| study | dataset | reopen trades: open print -> realistic | survives | grid net: open print -> realistic |
|---|---|---|---|---|
| A short to entry (48) | journal | +2,185.53 -> -87.34 | none | +2,096.09 -> +149.92 |
| | replay30 | +1,199.12 -> +752.96 | 63% | +1,583.48 -> +939.65 |
| | WTI | +1,878.32 -> -172.19 | none | +1,662.87 -> +82.65 |
| B short ladder (192) | journal | +307.48 -> +44.80 | 15% | +313.03 -> +2.37 |
| | replay30 | +76.26 -> +146.06 | all (and more) | -769.48 -> -669.42 |
| | WTI | +320.38 -> +53.91 | 17% | -250.78 -> -571.50 |

On the journal and WTI the reopen profit is gone. On the replay 63% of A's survives, and every reopen in that window (10-04 to 10-25) is a "next tick" fill, because the venue no longer has the 5m candles: it is the weaker of the two measurements.

### A with realistic fills

48 variants: **2 PASS**, 30 FAIL, 16 NOISE (judged on the five older legs: with real fills there is nothing to remove). 22 are above zero beside live on all three datasets (36 on the open print); 13 keep drawdown no worse; 9 are positive in both halves.

| variant | where | n | wins | win % | net $ | avg R | max DD vs live | verdict |
|---|---|---|---|---|---|---|---|---|
| **1 ATR stop, no R gate, once per thesis, 20% risk** | journal alone | 52 | 32 | 62% | -6.42 | -0.04 | 8.8% | |
| | journal beside live | 22 | 15 | 68% | +6.81 | +0.17 | 27.5% vs 27.7% | **PASS** |
| | replay30 beside live | 23 | 10 | 43% | +16.54 | +0.33 | 24.9% vs 26.0% | |
| | WTI beside live | 13 | 9 | 69% | +6.62 | +0.31 | 47.3% vs 49.0% | |
| **2 ATR stop, 1R gate, re-entry, 20% risk** (the near-pass of section A) | journal alone | 23 | 12 | 52% | +3.00 | +0.07 | 4.2% | |
| | journal beside live | 14 | 8 | 57% | +4.86 | +0.16 | 27.4% vs 27.7% | **PASS** |
| | replay30 beside live | 29 | 15 | 52% | +9.03 | +0.14 | 24.8% vs 26.0% | |
| | WTI beside live | 9 | 5 | 56% | +3.15 | +0.22 | 48.7% vs 49.0% | |
| old best by net: 1 ATR, 1R, once, full risk | journal beside live | 10 | 5 | 50% | +28.96 (was +156.02) | +0.22 | 22.4% vs 27.7% | FAIL |
| | replay30 beside live | 21 | 9 | 43% | +106.81 (was +177.15) | +0.49 | 27.0% vs 26.0% | |
| | WTI beside live | 8 | 5 | 63% | +39.02 (was +181.53) | +0.57 | 40.0% vs 49.0% | |

Walk-forward: 1 ATR / no gate / once / 20%: +$6.67 (9 trades) then +$0.08 (13 trades). 2 ATR / 1R / re-entry / 20%: +$4.73 (13 trades) then +$0.07 (1 trade). Old best: -$37.38 then +$39.43.

Read the two PASS rows for what they are:

- The money is $3 to $17 per dataset on $182, at one fifth of normal risk. At full risk the same two rules fail on drawdown.
- The second half of the window made 8 cents and 7 cents. All of it is October.
- Alone, the first one loses on the journal (-$6.42) and WTI (-$3.38). It only "beats live" because the one-position rule removes the shorts opened while the long was working.
- With its best day dropped the second keeps +$0.99.
- 2 of 48 clearing five legs by cents is what this grid produces by chance. On the open print 5 cleared the same legs; 4 of those 5 no longer do.

### B with realistic fills

192 variants: 0 PASS, 0 LEANING, 20 FAIL, 172 NOISE. None is above zero beside live on all three datasets.

| variant | where | n | wins | win % | net $ | avg R | max DD vs live | verdict |
|---|---|---|---|---|---|---|---|---|
| best by net: thesis entry, +1% steps, 2 tiers, 60% each, touch | journal beside live | 13 | 10 | 77% | +54.44 | +0.37 | 22.9% vs 27.7% | **FAIL** |
| | replay30 beside live | 16 | 10 | 63% | +19.03 | +0.07 | 42.8% vs 26.0% | |
| | WTI beside live | 6 | 3 | 50% | -22.93 | -0.20 | 57.2% vs 49.0% | |
| old best: +0.5% steps, 3 tiers, 60% each, touch | journal beside live | 23 | 16 | 70% | +42.02 (was +68.05) | +0.20 | 29.1% vs 27.7% | FAIL |
| | replay30 beside live | 22 | 13 | 59% | -13.62 (was -3.07) | +0.15 | 38.8% vs 26.0% | |
| | WTI beside live | 19 | 13 | 68% | -23.52 (was +22.84) | -0.01 | 58.6% vs 49.0% | |

Walk-forward of the new best: +$50.80 (8 trades) then +$1.47 (5 trades).

### Gut call on the follow-up

- **E (equity ladder): no.** It is the D ladder with four to five times the risk on tier 1 (5.4% of equity against 1.2%), and it loses the same way, faster.
- **A with real fills: no live money.** It passes a bar by cents on one month, with nothing in November. If the operator wants to keep watching one rule, it is "2 ATR stop, 1R gate, 20% risk", on paper only.
- **B: no.**

## Method

One script, deterministic, no LLM call: `services/strategies/evals/short_ladder_test.py`. Same candles and cache in, same numbers out (two runs on one cache gave byte-identical `variants.csv`).

- **Live book:** `desk_tick_sim.simulate` with `trend_gate_test.base_params`: the live Brent rule (market entry off the 24h range, never above the thesis entry, 0.75 stop buffer, 0.5% offset limit target, 2R gate, no entry after a break, weekend flat), 6% risk, the 20/15 leverage fit, size slippage. The fusion sweep's baseline.
- **Datasets:** the live journal on Brent (74 theses, 10-18 to 11-16, primary); the 30-thesis replay on Brent (09-30 to 10-30); the journal on WTI, with each thesis's entry and target carried over by the WTI/Brent 1h-close ratio at the hour it was written. The control series (always-short and so on) have no thesis entry, so none of the three ideas is defined on them.
- **Costs:** the sim's own: taker 4.5 bps, maker 1.5 bps, 3 bps stop slippage or the book curve by size, hourly funding. Market fills pay the taker fee and slippage; a resting short tier and a target are maker fills; a target needs a trade-through by one tick.
- **Sizing:** `desk_tick_sim.risk_capped_notional` at (tier share x 6%) of equity, capped by what is left of the full-port size at the position's leverage. Leverage from `next_trade.fit_leverage`: 20x, 15x when the stop is too wide for 20x, refused when too wide for 15x.
- **Bar order inside a 15m bar:** funding on a 1h close; closes at the open (live long enters, weekend flat, thesis no longer long; for D a 1h close under the thesis invalidation); new fills; the stop; then the target. A fill and the stop on one bar is a loss. A target never fills on a bar the position was filled on. A bar that opens beyond the stop fills at its open.
- **D details:** tiers at thesis entry x (1 + first + j x 0.5%), first = 0.5 / 1 / 1.5 / 2%; buy stop at the tier (taker) or the daily-close rule; one ladder per thesis; stop set at the first fill (24h low - 0.75, or 1.5 / 2 ATR under the fill) and shared by every tier; target the thesis's first target or 0.5% inside the 24h high; a tier is refused with no room to the target, under the R gate, or when the stop does not fit 15x.
- **A details:** entry at the mid on any 15m tick with a long thesis in force and the mid above its entry; closed at market when the thesis stops being long or a rewrite puts the entry at or above the mid.
- **B details:** the reference floats until the first tier fills, then is frozen; after a stop the ladder re-arms only once a bar trades back to the reference.
- **Beside live:** sized off the live book's marked equity plus the variant's own P&L. The live trades keep their own sizes, so the combined net is the live net plus the variant's. Drawdown is measured on the summed marked curve, tick by tick.
- **The bar:** `RULE_FUSION_2026-11-13.md` and `FINAL_STRATEGY_TEST_2026-11-04.md`, strict reading. Beside live, the variant must (1) add money on every dataset, (2) have max drawdown no worse than live on every dataset, (3) add money in both halves of the primary, (4) have every grid neighbour add money on the primary, (5) still add money with its best exit day removed. Added here: (6) legs 1 and 3 must hold with the trades entered on the first tick after a weekend removed. The script reports who would pass legs 1 to 5 alone (`passing_without_reopen_leg`): five A variants, all at 20% risk.
- **Multiple testing:** 1,968 variants were tried. A lone survivor of a grid this size would not be evidence. There is none.
- **Follow-up (E, F):** the grid is 2,130 variants with E's 162, run twice: pass 1 as above (reopen ticks at the 15m open print, six legs), pass 2 with `reopen_fills` / `apply_reopen` repricing every reopen tick (five legs, nothing removed). The live long book is the same sim run in both passes; its own trades are not repriced. E sizes with `equity_tier_notional` (margin share x equity x leverage, net of the opening fee, capped by the full-port size) and reports `stop_risk_pct` per tier. The journal held 75 theses by the follow-up run (one written after the candle end); every pass-1 number above reproduced to the cent.

Where it would break: the 15m bar model cannot order a fill and a stop inside one bar (handled by always taking the loss); the WTI rows lean on a price ratio, not a WTI thesis; the replay overlaps the journal for 10-18 to 10-30; one month of journal is one regime (Brent 113 to 89); the reopen-tick finding rests on three Sundays.

## Reproduce

    # the full run, both passes (about 50 s, one process); exit 0 = a variant passes in either pass
    # (two A variants do in pass 2, see section F); --no-realistic runs pass 1 only
    PATHIEL_STATE_DIR=<pathia>/.state .venv/bin/python -m services.strategies.evals.short_ladder_test \
        --cache-dir <fresh dir> --out-dir <dir>

    # one study, and the trades of named variants in the report
    PATHIEL_STATE_DIR=<pathia>/.state .venv/bin/python -m services.strategies.evals.short_ladder_test \
        --cache-dir <same dir> --out-dir <dir> --only D --detail D_f1_t3_ramp_touch_thesis_lo24_r0

    # gate tests (74, no network)
    .venv/bin/python -m pytest -q services/strategies/tests/test_short_ladder.py

Outputs: `<out>/report.json` (every variant on every dataset, alone and beside live, the legs, the missed theses, the best variants' trades), `<out>/variants.csv` (one row per variant), `<out>/variants_realistic.csv` (the same grid with realistic reopen fills; in the report under `realistic`, with the fill table and `reopen_profit`), `<out>/best_trades.csv`, `<out>/progress.log`. The replay set is read from `--oil-replay-dir` (default `/tmp/thesis-replay-30b/oil/theses`) and skipped when missing.
