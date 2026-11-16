# Intraday 5-minute test, 2026-11-16

**Verdict: no 5-minute rule passes, nothing is adopted. 208 variants in five families, at 6% and 1.2% risk: 0 PASS, 0 LEANING. Four families FAIL, the thesis-aligned one is NOISE. The fee is not what kills it (0.9 bps a side on this account); the desk's own latency is: a fill one 5m bar late costs 13.5 bps a round trip, 78% of a median 5m bar, and three of the five ideas lose even at zero cost.** Research only. No live config, order or rule changed.

Operator, 2026-11-15: "anyway you can test an intraday theory? i want 5 min market wins too".

Data: Brent 5m candles 2026-10-26 02:30Z to 2026-11-16 05:00Z (21.1 days, 6,078 bars) and WTI 5m 2026-10-27 15:00Z to 2026-11-16 05:00Z (19.6 days, 5,640 bars), 76 journal theses, $182.09 start. The venue serves only its last 5,000 candles per interval (17 days of 5m, 3.5 days of 1m); the first four days come from older cached snapshots. Every number is from `services/strategies/evals/intraday_5m_test.py`; reproduce commands at the end.

The live long book on the same windows (the rule as it trades, `short_ladder_test.prepare`):

| dataset | trades | wins | net $ | max DD |
|---|---|---|---|---|
| brent_5m (primary) | 12 | 6 | +71.79 | 27.7% |
| wti_5m | 16 | 4 | -51.38 | 48.8% |

Halves of the primary (split 2026-11-05 15:45Z): 7 trades, +$67.55; 5 trades, +$6.41.

## The five families at a glance (6% risk, the desk's real fills)

| family | variants | trades alone, Brent | win % | net $ alone, sum over the grid (Brent / WTI) | variants above zero alone (Brent / WTI) | best variant beside live: n, net $, max DD vs live | verdict |
|---|---|---|---|---|---|---|---|
| 1. Opening-range breakout | 72 | 801 | 26% | -2,777 / -2,284 | 26 / 21 | 2 trades, +31.24, 25.4% vs 27.7% | **FAIL** |
| 2. Session-VWAP reversion | 12 | 373 | 24% | -1,053 / -1,022 | 0 / 0 | 3 trades, +37.30, 22.0% vs 27.7% | **FAIL** |
| 3. Trend pullback to the 20-EMA | 8 | 412 | 37% | -534 / -725 | 0 / 0 | 20 trades, +13.78, 31.8% vs 27.7% | **FAIL** |
| 4. Big 5m bar, continuation or fade | 24 | 2,418 | 20% | -3,564 / -3,319 | 0 / 0 | 18 trades, -83.25, 52.9% vs 27.7% | **FAIL** |
| 5. Thesis-aligned (families 1-3) | 92 | 654 | 21% | -3,005 / -1,722 | 10 / 11 | 1 trade, +19.00, 26.0% vs 27.7% | **NOISE** |

How to read it:

- **alone**: the variant on its own $182.09, no live book. **beside live**: the only way it could run on the account (one position per desk): no intraday trade while the live long is open, an open one closed when the live long enters.
- **Verdict**: the repo's bar (beat live on every dataset, drawdown no worse, both halves, every grid neighbour, without the best day) with at least 30 trades beside live on the primary. Under 30 is LEANING or NOISE, never PASS. No variant of families 1, 2, 3 or 5 reaches 30 trades beside live (the live long is open most of the time), so a family is also a FAIL when it has variants with 30 or more trades alone and none of them makes money alone on both datasets. Family 5 has no variant with 30 trades either way: NOISE.
- The "best variant" column is the best of its grid. With 208 tries, read it as the ceiling, not the expectation.

At 1.2% risk the trades are the same and a fifth the size: every verdict is the same, the losses a quarter to a half as large (sums alone on Brent: -725, -327, -135, -1,788, -732).

## Do costs alone kill 5-minute trading here?

No. Latency does, and for three families there is nothing to kill.

| | Brent | WTI |
|---|---|---|
| median 5m bar, open to close | 7.6 bps | 8.0 bps |
| median 5m bar, high to low | 17.3 bps | 18.2 bps |
| median ATR14 (5m) | 19.9 bps | 20.7 bps |

Round trip, market in and stop out, on a median Brent bar:

| scenario | what it is | fee (taker / maker) | entry slip | stop slip | round trip | share of a median 5m range |
|---|---|---|---|---|---|---|
| free | next 5m open, no costs | 0 / 0 | 0 | 0 | 0 | 0 |
| bot | next 5m open, book-curve slippage; needs an order bot the desk does not have | 0.90 / 0.31 bps | 0.9 bps | 0.9 bps | 3.6 bps | 21% |
| **watcher (primary)** | a watcher fires on the 5m close, the desk tick places the order: filled one 5m bar later, half that bar's range as slippage (never under 3 bps) | 0.90 / 0.31 bps | 8.7 bps | 3.0 bps | **13.5 bps** | **78%** |
| tick15 | no watcher rule: the order waits for the next 15-minute cron tick | 0.90 / 0.31 bps | 8.7 bps | 3.0 bps | 13.5 bps | 78% |
| repo_fee | watcher, with the sim's fee constants | 4.5 / 1.5 bps | 8.7 bps | 3.0 bps | 20.7 bps | 119% |

- **The fee**: read from the daily review's ledger (`.state/review/ledger.jsonl`, this account's own xyz oil fills), not from `get_user_fees` (that returns the main-dex schedule, 4.5 / 1.5 bps, which xyz does not charge). Taker 0.90 bps a side, the median of 9 round trips closed by a stop or by hand. Maker 0.31 bps, from 3 target exits that rested as limits; 3 other target exits paid the taker rate on both legs. The desk's backtests use 4.5 / 1.5 bps: five times too high for xyz. It is the `repo_fee` row.
- **The slippage**: the realistic-fill model of `SHORT_LADDER_TEST_2026-11-16.md` (half the fill bar's range). Checked on 1m candles (the last 3.5 days): two minutes after a 5m open the price sits a median 4.2 bps away on Brent (90th percentile 12.6), 4.3 bps on WTI. So half the range (8.7 bps) is about twice the measured two-minute drift, on top of the one-bar delay. It is a harsh reading. The `bot` row is the kind one.

What each family makes alone at 6% risk, summed over its grid, as the costs come on (Brent / WTI; in brackets the variants above zero):

| family | free | bot | watcher (primary) | tick15 | repo_fee |
|---|---|---|---|---|---|
| 1. ORB (72) | -1,708 (27) / -1,135 (30) | -2,026 (27) / -1,445 (29) | -2,777 (26) / -2,284 (21) | -2,405 (26) / -2,148 (21) | -3,121 (25) / -2,655 (19) |
| 2. VWAP (12) | -230 (4) / -173 (3) | -518 (4) / -446 (2) | -1,053 (0) / -1,022 (0) | -949 (0) / -1,012 (0) | -1,205 (0) / -1,171 (0) |
| 3. Pullback (8) | **+552 (8)** / **+221 (5)** | +65 (4) / -73 (3) | -534 (0) / -725 (0) | -625 (0) / -515 (0) | -806 (0) / -885 (0) |
| 4. Big bar (24) | -1,648 (1) / -815 (9) | -2,467 (0) / -1,906 (2) | -3,564 (0) / -3,319 (0) | -3,438 (0) / -3,120 (0) | -3,745 (0) / -3,469 (0) |
| 5. Thesis-aligned (92) | -1,670 (16) / +16 (37) | -2,035 (15) / -472 (30) | -3,005 (10) / -1,722 (11) | -2,902 (10) / -1,612 (12) | -3,345 (6) / -2,111 (8) |

Read it by row:

- **ORB, VWAP fade, big bar**: negative with no costs at all. There is no edge for costs to eat.
- **Pullback**: the one idea with something in it before costs. All 8 variants make money on Brent for free, 5 of 8 on WTI. The gross is at most 12 bps of notional per trade (9.9 bps on the best variant). A bot keeps a little of it on Brent (4 of 8 above zero) and none on WTI. The desk's real fill (12.9 to 18.3 bps a trade) takes all of it: 0 of 8 on either market.
- **Could the desk execute it?** Not today. The tick is an LLM agent every 15 minutes. `scripts/trigger_watch.py` polls every minute but only asks the dip-buy rule and the headline step; a 5m-signal rule would be new code. With it, the fill is the `watcher` row. Without it, `tick15`. Stops and targets rest on the venue either way, so only the entry and the time stop are late.

## 1. Opening-range breakout

Range = the first 15 or 30 minutes of the session; enter on the first 5m close outside it within 90 minutes; stop at the other side or the middle; target 1.5 / 2 / 3 R; flat two hours after the signal. Sessions: Sunday reopen, London 07:00Z, New York 13:00Z, EIA, API, all five. 72 variants: 0 PASS, 0 LEANING, 72 NOISE under the bar (no variant has 30 trades beside live); the 12 with 30 or more trades alone ("all" sessions) all lose alone on both datasets. **FAIL.**

By session, alone at 6% risk, summed over the 12 variants of each (events in the window: 3 reopens, 15 London and 15 New York opens on Brent, 3 EIA, 3 API):

| session | Brent: trades / wins / net $ / variants above zero | WTI | free of costs (Brent / WTI) |
|---|---|---|---|
| London 07:00Z | 174 / 37 / -862.88 / 0 of 12 | 150 / 23 / -894.44 / 0 of 12 | -672 / -708 |
| New York 13:00Z | 150 / 9 / -1,022.63 / 0 of 12 | 120 / 20 / -609.04 / 0 of 12 | -917 / -488 |
| all five | 399 / 104 / -1,338.31 / 0 of 12 | 339 / 88 / -1,105.49 / 0 of 12 | -961 / -667 |
| Sunday reopen | 12 / 12 / +98.95 / 6 of 12 | 12 / 6 / +55.16 / 6 of 12 | +191 / +44 |
| EIA | 30 / 19 / +146.77 / 9 of 12 | 33 / 24 / +263.92 / 9 of 12 | +335 / +487 |
| API | 36 / 28 / +200.87 / 11 of 12 | 36 / 18 / +5.50 / 6 of 12 | +316 / +197 |

London and New York breakouts fail on every variant, on both markets, with or without costs: New York wins 9 of 150 on Brent. The three event sessions are positive, and they are 2 or 3 events each repeated across the grid.

| variant | where | n | wins | win % | net $ | avg R | PF | max DD vs live | $ per trade | gross vs cost, bps | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| all sessions, 15m range, far-side stop, 2R (most trades) | Brent alone | 32 | 8 | 25% | -114.71 | -0.53 | 0.24 | 64.4% | -3.59 | -11.9 vs 16.8 | NOISE |
| | Brent beside live | 8 | 2 | 25% | -35.14 | -0.35 | 0.34 | 36.1% vs 27.7% | -4.39 | -0.1 vs 22.3 | |
| | WTI alone | 29 | 7 | 24% | -85.54 | -0.40 | 0.45 | 47.0% | -2.95 | -1.4 vs 18.5 | |
| | WTI beside live | 6 | 2 | 33% | +12.44 | +0.24 | 1.41 | 46.6% vs 48.8% | +2.07 | +24.8 vs 14.8 | |
| London, 15m, far side, 2R | Brent alone | 15 | 3 | 20% | -77.28 | -0.70 | 0.14 | 44.6% | -5.15 | -14.4 vs 17.2 | NOISE |
| New York, 15m, far side, 2R | Brent alone | 13 | 1 | 8% | -85.18 | -0.85 | 0.06 | 46.8% | -6.55 | -35.7 vs 21.3 | NOISE |
| **reopen, 30m, mid stop, 2R (best of the family by net)** | Brent beside live | 2 | 2 | 100% | +31.24 | +1.04 | - | 25.4% vs 27.7% | +15.62 | +84.6 vs 25.5 | **NOISE** |
| | WTI beside live | 1 | 0 | 0% | -7.67 | -1.36 | 0 | 52.0% vs 48.8% | -7.67 | -8.8 vs 9.2 | |
| reopen, 30m, far side, 2R | Brent beside live | 2 | 2 | 100% | +18.88 | +0.61 | - | 26.3% vs 27.7% | +9.44 | +84.3 vs 25.5 | NOISE |
| | WTI beside live | 1 | 1 | 100% | +21.94 | +1.96 | - | 44.4% vs 48.8% | +21.94 | +69.9 vs 5.6 | |
| EIA, 15m, far side, 2R | Brent alone | 2 | 1 | 50% | +6.22 | +0.30 | 4.29 | 9.9% | +3.11 | +57.4 vs 41.4 | NOISE |
| | WTI alone | 3 | 3 | 100% | +54.48 | +1.62 | - | 3.3% | +18.16 | +148.3 vs 19.8 | |
| API, 15m, far side, 1.5R | Brent alone | 3 | 3 | 100% | +30.88 | +1.43 | - | 2.9% | +10.29 | +34.1 vs 5.8 | NOISE |
| | WTI alone | 3 | 2 | 67% | +8.18 | +0.13 | 2.06 | 6.4% | +2.73 | +18.3 vs 8.9 | |

- Walk-forward, beside live: reopen / 30m / far side / 2R made +$15.71 then +$2.22, one trade in each half. It beats live on both datasets with drawdown no worse and holds without its best day; it fails the plateau (its 15-minute neighbour never traded) and has 2 trades. Three variants of that one rule are the only rows of the 208 positive on both datasets and in both halves.
- API beside live: 0 trades. The live long was open at all three reports.
- Best / worst hour (UTC entry, all 72 variants pooled, alone): 22Z, 60 trades, +$219.67 (the hour after the API report); 07Z, 252 trades, -$1,341.01 (London).
- Collisions: 610 of 801 trades alone on Brent (76%) and 582 of 690 on WTI (84%) overlap a live long.

## 2. Session-VWAP reversion

Fade a 5m close k x ATR14 from the session VWAP (the CME day, from 18:00 New York) back to the VWAP, only when the 1h trend is flat (the last 1h close within 0.5 ATR of its 20-EMA) and the session is an hour old. k 2 / 3 / 4, stop 1 or 2 ATR, flat after 60 or 120 minutes. 12 variants: 0 PASS, 0 LEANING; 0 of 12 above zero alone on either market, 8 of them with 30 or more trades. **FAIL.**

| variant | where | n | wins | win % | net $ | avg R | PF | max DD vs live | $ per trade | gross vs cost, bps | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **k 4, 1 ATR stop, 120 min (best by net)** | Brent alone | 14 | 4 | 29% | -22.61 | -0.18 | 0.74 | 22.2% | -1.62 | +9.7 vs 16.3 | **NOISE** |
| | Brent beside live | 3 | 2 | 67% | +37.30 | +1.00 | 4.09 | 22.0% vs 27.7% | +12.43 | +43.8 vs 14.1 | |
| | WTI alone | 19 | 5 | 26% | -64.05 | -0.49 | 0.36 | 40.2% | -3.37 | +0.4 vs 15.8 | |
| | WTI beside live | 4 | 1 | 25% | -13.88 | -0.31 | 0.27 | 53.4% vs 48.8% | -3.47 | +1.1 vs 19.0 | |
| k 3, 2 ATR stop, 120 min (most trades over 30) | Brent alone | 33 | 10 | 30% | -86.23 | -0.36 | 0.44 | 55.0% | -2.61 | +0.6 vs 14.4 | NOISE |
| | WTI alone | 25 | 7 | 28% | -94.13 | -0.53 | 0.15 | 54.5% | -3.77 | -9.8 vs 17.2 | |

- Grid, alone: Brent 373 trades, 24% wins, median variant -$97.18; WTI 338 trades, 33% wins, median -$90.06. Beside live: Brent 76 trades, 3 of 12 variants above zero, median -$22.93; WTI 64 trades, 0 of 12, median -$12.96.
- Walk-forward of the best, beside live: +$27.11 (2 trades) then -$15.13 (3 trades). Without its best day: -$1.88.
- Best / worst hour (pooled, alone): 11Z, 22 trades, +$50.41; 01Z, 34 trades, -$161.08.
- Collisions: 80% on Brent, 85% on WTI.
- Same finding as `SCALP_TEST_2026-11-03.md` on 15m bars: fading does not pay on this market.

## 3. Trend pullback to the 20-EMA

Trend up on closed 1h bars (the 1h close more than 0.5 ATR above its 20-EMA; "4h" uses the 80-EMA): after a new session high, buy the first 5m bar that dips to the 20-EMA and closes above it. Mirrored for a down trend. Stop 0.5 or 1.5 ATR beyond the pullback bar, target 1.5 or 3 R, flat after two hours. 8 variants: 0 PASS, 0 LEANING; all 8 have 48 to 55 trades alone on Brent and all 8 lose alone on both markets. **FAIL.**

| variant | where | n | wins | win % | net $ | avg R | PF | max DD vs live | $ per trade | gross vs cost, bps | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **1h trend, 0.5 ATR stop, 1.5R (best)** | Brent alone | 55 | 23 | 42% | -37.44 | -0.09 | 0.85 | 35.9% | -0.68 | +9.9 vs 12.9 | **NOISE** |
| | Brent beside live | 20 | 10 | 50% | +13.78 | +0.10 | 1.12 | 31.8% vs 27.7% | +0.69 | +16.2 vs 14.1 | |
| | WTI alone | 45 | 16 | 36% | -91.40 | -0.35 | 0.53 | 56.1% | -2.03 | +4.9 vs 15.2 | |
| | WTI beside live | 9 | 5 | 56% | +11.83 | +0.04 | 1.33 | 49.0% vs 48.8% | +1.32 | +18.0 vs 13.4 | |
| same, at 1.2% risk | Brent beside live | 20 | 10 | 50% | +4.01 | +0.10 | 1.16 | 27.1% vs 27.7% | +0.20 | +16.8 vs 14.3 | NOISE |
| | WTI beside live | 9 | 5 | 56% | +0.75 | +0.04 | 1.08 | 49.1% vs 48.8% | +0.08 | +14.7 vs 13.5 | |

The same variant alone, as the costs come on (Brent / WTI): free +$62.01 / +$105.10; bot +$18.86 / +$26.18; watcher -$37.44 / -$91.40; tick15 -$12.65 / -$42.66; repo_fee -$89.68 / -$117.04.

- Grid, alone: Brent 412 trades, 37% wins, median variant -$70.83; WTI 337 trades, 32% wins, median -$91.68. Beside live: Brent 156 trades, 2 of 8 above zero, median -$4.99; WTI 72 trades, 8 of 8 above zero, median +$13.70.
- Walk-forward of the best, beside live: -$15.11 (15 trades) then +$59.78 (9 trades). Without its best day: -$6.21. Drawdown worse than live on both datasets at 6%.
- Best / worst hour (pooled, alone): 02Z, 32 trades, +$166.12; 09Z, 28 trades, -$187.42.
- Collisions: 69% on Brent, 81% on WTI. Beside live it trades only when the desk has no long, which is why the beside-live numbers look better than alone: the sleeve sits out the hours the long is working.
- This is the closest thing to an idea in the test: a real gross of about 10 bps a trade, against a cost of 13 to 15 bps on the desk's fills.

## 4. Big 5m bar: continuation or fade

A 5m bar whose range is over m x ATR14 (m 2 / 3 / 4): trade with its body or against it, stop half the bar's range away, target 1.5 or 3 R, flat after 30 or 120 minutes. 24 variants: 16 FAIL (30 or more trades beside live), 8 NOISE, 0 above zero on either market, alone or beside live. **FAIL.**

| | Brent alone: trades / wins / net $ | WTI alone | free of costs (Brent / WTI), variants above zero |
|---|---|---|---|
| continuation (12) | 1,190 / 250 (21%) / -1,705.98 | 937 / 185 (20%) / -1,763.29 | -710 (0) / -1,040 (1) |
| fade (12) | 1,228 / 226 (18%) / -1,858.27 | 926 / 195 (21%) / -1,555.43 | -938 (1) / +225 (8) |

| variant | where | n | wins | win % | net $ | avg R | PF | max DD vs live | $ per trade | gross vs cost, bps | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **m 4, continuation, 3R, 120 min (least bad)** | Brent alone | 32 | 9 | 28% | -81.04 | -0.44 | 0.45 | 58.9% | -2.53 | +5.4 vs 18.5 | **NOISE** |
| | Brent beside live | 18 | 4 | 22% | -83.25 | -0.54 | 0.31 | 52.9% vs 27.7% | -4.63 | +2.8 vs 20.0 | |
| | WTI alone | 24 | 3 | 13% | -110.72 | -0.74 | 0.20 | 62.2% | -4.61 | -11.1 vs 22.2 | |
| | WTI beside live | 10 | 1 | 10% | -64.09 | -0.87 | 0.01 | 75.0% vs 48.8% | -6.41 | -18.4 vs 26.3 | |
| m 2, fade, 1.5R, 30 min (most trades) | Brent alone | 211 | 40 | 19% | -181.60 | -0.63 | 0.19 | 99.7% | -0.86 | +1.2 vs 16.1 | **FAIL** |
| | Brent beside live | 79 | 11 | 14% | -227.64 | -0.66 | 0.18 | over 100% vs 27.7% | -2.88 | -0.1 vs 16.9 | |
| | WTI alone | 165 | 31 | 19% | -180.08 | -0.59 | 0.17 | 98.9% | -1.09 | +0.5 vs 18.2 | |

- The most active variant loses the whole $182.09 in three weeks: 211 trades, each paying 16 bps on a position up to 20 times the account.
- Walk-forward of the least bad, beside live: -$84.84 then -$7.15.
- Best / worst hour (pooled, alone): 21Z, 6 trades, +$0.03; 13Z, 223 trades, -$417.85. No hour of the day makes money.
- Collisions: 59% on Brent, 71% on WTI.

## 5. Thesis-aligned scalps

Every family 1 to 3 variant, keeping only the signals on the side of the journal thesis in force at the signal's close (long when the thesis is long). 92 variants: 0 PASS, 0 LEANING, 92 NOISE; none has 30 trades, alone or beside live. **NOISE**, and the filter does not help.

| variant | where | n | wins | win % | net $ | avg R | PF | max DD vs live | $ per trade | gross vs cost, bps | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **EIA breakout, 15m, far side, 1.5R, thesis side (best by net)** | Brent alone | 2 | 2 | 100% | +22.85 | +1.13 | - | 4.0% | +11.43 | +69.0 vs 11.9 | **NOISE** |
| | Brent beside live | 1 | 1 | 100% | +19.00 | +1.46 | - | 26.0% vs 27.7% | +19.00 | +58.1 vs 8.4 | |
| | WTI beside live | 1 | 1 | 100% | +16.28 | +1.47 | - | 45.4% vs 48.8% | +16.28 | +73.9 vs 11.5 | |
| pullback 1h / 0.5 ATR / 1.5R, thesis side | Brent alone | 22 | 7 | 32% | -47.37 | -0.31 | 0.64 | 31.4% | -2.15 | +3.6 vs 12.2 | NOISE |
| | Brent beside live | 9 | 4 | 44% | -3.17 | -0.03 | 0.95 | 28.0% vs 27.7% | -0.35 | +12.4 vs 13.4 | |
| | WTI alone | 19 | 8 | 42% | -29.19 | -0.19 | 0.74 | 30.8% | -1.54 | +8.2 vs 13.7 | |
| | WTI beside live | 5 | 4 | 80% | +32.19 | +0.56 | 4.66 | 42.5% vs 48.8% | +6.44 | +36.8 vs 13.6 | |

- Grid, alone: Brent 654 trades, 21% wins, 10 of 92 variants above zero, median -$33.89; WTI 562 trades, 36% wins, 11 of 92, median -$11.60. Beside live: Brent 227 trades, 1 of 64 traded variants above zero, median -$14.80; WTI 176 trades, 11 of 68, median -$12.81.
- The best is one EIA report (2026-10-28) in the first half and nothing in the second. Its two grid neighbours lose (-$14.80, -$2.57).
- The thesis filter on the best pullback: 55 trades and -$37.44 unfiltered, 22 trades and -$47.37 on the thesis side. Fewer trades, lower win rate (42% to 32%).
- Best / worst hour (pooled, alone): 16Z, 12 trades, +$56.45; 07Z, 160 trades, -$1,110.82.
- Collisions: 65% on Brent, 75% on WTI. A thesis-side signal comes when the thesis is working, which is when the desk already holds its long.

## Fit with the live long book

| family | Brent: alone trades overlapping a live long | WTI | Brent: trades alone -> beside live |
|---|---|---|---|
| 1. ORB | 610 of 801 (76%) | 582 of 690 (84%) | 801 -> 231 |
| 2. VWAP | 297 of 373 (80%) | 288 of 338 (85%) | 373 -> 76 |
| 3. Pullback | 286 of 412 (69%) | 273 of 337 (81%) | 412 -> 156 |
| 4. Big bar | 1,423 of 2,418 (59%) | 1,316 of 1,863 (71%) | 2,418 -> 1,041 |
| 5. Thesis-aligned | 427 of 654 (65%) | 420 of 562 (75%) | 654 -> 227 |

The live long was open for most of the window, so the one-position rule removes 57% to 80% of the intraday trades on Brent. Combined P&L on Brent (live net + the sleeve beside it, 6% risk): live alone +$71.79; + reopen ORB (30m, mid, 2R) +$103.03; + pullback best +$85.57; + VWAP best +$109.09; + big-bar least bad -$11.46. The three that add money do it on 2, 20 and 3 trades and fail on WTI, on drawdown or in the first half.

## Multiple testing

208 variants, each at two risk sizes: 416 judged rows, 0 PASS, 0 LEANING, 32 FAIL, 384 NOISE. Beside live, 7 of 208 are above zero on both datasets at 6% risk (8 at 1.2%). Three of them are also positive in both halves, and they are one rule (reopen, 30m range, far-side stop) at three targets: 2 trades on Brent, 1 on WTI. A grid this size produces that by chance.

## Gut call

No live money on any 5-minute rule.

- **The desk is the wrong tool for it.** An LLM tick fills minutes late. Late costs 13.5 bps a round trip on a market whose median 5m bar is 17 bps wide and moves 7.6 bps open to close. No signal tested earns that.
- **Three of the ideas have no edge even for free**: London and New York opening-range breakouts, fading the VWAP, anything off a big bar.
- **Pullback** has a small real gross (about 10 bps a trade). Capturing it needs resting orders placed by code, not by a tick: a separate build, and a bot on this account is a decision for the operator, not a rule change.
- **Worth watching on paper, not trading**: breakouts of the first 30 minutes after the Sunday reopen and after the EIA and API reports. Positive alone on both markets, on 3 events each. It lines up with the reopen finding in `SHORT_LADDER_TEST_2026-11-16.md` (the reprice runs for an hour or two). Three more weeks of 5m candles would make it 6 events; still short of 30.
- **One finding to act on, eval side only**: the desk's backtests charge 4.5 / 1.5 bps and the account pays 0.90 / 0.31 on xyz. The sim's constants are five times the measured rate (`loss_fix_test.py` already notes it). That makes the earlier tests conservative, not wrong, and it does not rescue anything here.

## Method

One script, deterministic, no LLM call: `services/strategies/evals/intraday_5m_test.py`. Same cache in, same numbers out (two runs on one cache gave a byte-identical `variants.csv`; the run takes about 4 seconds).

- **Reused, not rebuilt:** the candle and funding loaders (`backtest/data.py`, `market_scout.load_coin_candles`), the fee and slippage constants, `risk_capped_notional` and the book curve (`desk_tick_sim`), the 20/15 leverage fit (`next_trade.fit_leverage`), the weekend clock (`weekend.py`), the report calendar (`backtest/oil_calendar.py`), the live long book, its masks, drawdown, collisions, drop-best-day and the bar itself (`short_ladder_test.prepare`, `max_dd_pct`, `collisions`, `drop_best_day`, `judge`).
- **Data:** the venue's last 5,000 5m candles per coin, merged with older cached snapshots (`--extra-5m-glob`, default `/tmp/review/*/cache` and `$PATHIEL_STATE_DIR/pool/work/*/cache`); the merged series is written to the cache directory so a rerun reads the same bars. A hole over 30 minutes cuts the series (`contiguous_from`). Bars still forming are dropped. 1m candles are used only for the latency check.
- **Signals read closed bars only.** A signal from bar i is known at its close. ATR14 and the 20-EMA are on 5m bars; the 1h / 4h trend on closed 1h bars; the session is the CME day (18:00 New York); the thesis is the one in force at the signal's close, the same journal the other tests use (on WTI only its side carries over).
- **Fills:** the open of the 5m bar at `fill_time` (next bar for `free` and `bot`; one bar later for `watcher`; :05/:20/:35/:50 after the next cron tick for `tick15`). A signal is skipped when that bar is missing, in the weekend, past its time stop, while another intraday trade is open, when the stop is under 5 bps from the fill or does not fit 15x. The target is set from the real fill.
- **Exits, per bar:** funding on the hour; closes at the open (live long enters, weekend flat, time stop; market, taker, same slippage rule as an entry); the stop (resting, taker, 3 bps or the book curve; a bar that opens beyond it fills at its open; it can fire on the fill bar); the target (resting limit, maker, needs a trade-through by one tick, never on the fill bar). A stop and a target on one bar is a loss. A trade open at the end is marked at the last close.
- **Sizing:** `risk_capped_notional` at 6% or 1.2% of equity at the stop, capped by the full-port size at the fitted leverage. With stops this tight the cap binds often, so the mean risk per trade is about 5% at the 6% setting.
- **Not applied to the sleeve:** the live rule's event and headline blackouts (the EIA and API breakouts trade the event on purpose) and NO_BREAK_ENTRY. Weekend flat is applied.
- **Beside live:** sized off the live book's marked equity plus the sleeve's own P&L. The live trades keep their own sizes, so the combined net is the live net plus the sleeve's, and a combined drawdown over 100% means the account would have been emptied.
- **Halves:** each half is its own run with a fresh live book, as in the earlier tests, so the halves do not add up to the full run.
- **The bar:** `short_ladder_test.judge` with the reopen leg off (reopen entries here are at least 15 minutes after the reopen) and 30 trades instead of 10.
- **No replay dataset:** the 30-thesis replay ends 2026-10-30 and 5m candles start 2026-10-26.

Where it would break:

- **Three weeks, one regime** (Brent 113 to 89 to 93). A walk-forward half is 10 days.
- **Event sessions are 3 events each.** Nothing can be said about them yet.
- **5m bars cannot order a stop and a target inside one bar** (always the loss) and hide the path inside the fill bar.
- **The half-range slippage is a model.** It is about twice the measured two-minute drift. The conclusion does not rest on it: the `bot` row uses the book curve and still has no family above zero on both markets.
- **The maker fee is three fills.** At a taker-only target the round trip is 0.6 bps worse.
- **The WTI theses are Brent's**, side only.
- **The first four days of 5m candles come from snapshots in `/tmp` and the pool work directory.** On a machine without them the window starts 2026-10-29 and the counts change.

## Reproduce

    # the full run (about 4 s); exit 1 = no variant passes
    PATHIEL_STATE_DIR=<pathia>/.state .venv/bin/python -m services.strategies.evals.intraday_5m_test \
        --cache-dir <fresh dir> --out-dir <dir>

    # keep the trades of named variants in the report and in best_trades.csv
    PATHIEL_STATE_DIR=<pathia>/.state .venv/bin/python -m services.strategies.evals.intraday_5m_test \
        --cache-dir <same dir> --out-dir <dir> --detail pb_1h_s0.5_t1.5 orb_reopen_r30_opp_t2

    # gate tests (43, no network)
    .venv/bin/python -m pytest -q services/strategies/tests/test_intraday_5m.py

Outputs: `<out>/report.json` (every variant at both risk sizes on both datasets, alone and beside live, the legs, every cost scenario, the cost table, the 1m latency check, the family summaries, the best variants' trades), `<out>/variants.csv` (one row per variant and risk size), `<out>/best_trades.csv`, `<out>/progress.log`.
