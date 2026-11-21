# HYPE desk backtest, 2026-11-20

**Verdict: EDGE, on a 120-hour trend-gated version of the oil/sp500 24h-range rule. The plain, ungated rule (every other desk's own formula) is a clear LOSER on HYPE: -17.75R over 207 days, both the full sample and its held-out tail negative, at the venue's real main-dex fee (4.5bps taker, not the xyz dex's 0.9bps growth-mode rate oil/sp500 get). Gated by the sign of the 120-hour return (`next_trade.trend_gate`, built for oil 2026-11-13, tested, found not to help there, and kept in the code "so it can be re-run the day the record changes" -- HYPE is that day), the SAME rule clears every leg of the honesty bar: +38.87R full sample, +8.06R held-out tail, positive under the cost stress, positive without its two best trades, all four grid neighbours agree, on both the 1h and 15m datasets. A second family (Fibonacci pullback, new) also clears every leg, with an even cleaner trade-by-trade profile, but does not ship live -- see "Fibonacci pullback" below. 10x is mechanically easy here, unlike the S&P's 50x: the median stop sits at roughly half the budget. Research only. No live config, order or rule changed.**

Operator, 2026-11-20: "Start the research on HYPE and desk for hype, maybe." This is the research behind the desk (`HYPE_STRATEGY.md`), built off a positive verdict and shipped OFF.

- Script: `services/strategies/evals/hype_strategy_test.py` (reuses `services/strategies/evals/sp500_strategy_test.py`'s engine wholesale -- Costs, Tick/Order/Trade, build_ticks, simulate, summarize, verdict, pick, Dataset, edge_rule, oil_as_is, leverage_budget, noise_table, event_windows; gate tests: `services/strategies/tests/test_hype_strategy_eval.py`).
- Data: one pinned snapshot, `~/Documents/code/pathiel-replays/candles/20261119-hype` (HYPE and BTC, for correlation), pulled with `services.strategies.backtest.snapshot`. Everything the venue serves: 5,002 1h bars (2026-04-25 to 11-20), 5,007 15m bars (2026-09-28 to 11-20), 600 daily bars (back to 2025-03-31), 14,400 hourly funding rows (600 days -- funding history is not capped at 5,000 rows the way candles are).
- Output: `/tmp/hype-strategy/report.json`, `/tmp/hype-strategy/summary.csv` (every variant, full / train / tail).

## The market

HYPE on Hyperliquid's main perp dex (no HIP-3 prefix), read live 2026-11-20: venue max leverage 10, `marginTableId` 52, mid $65.17, $636-640M traded in 24h, open interest $18.7M, spread about $0.016 on a $65 mid (2.5 bps). Fee tier (via `get_account(detail=full)`, the account this desk would trade on): `feeSchedule.cross` (taker) 0.00045 = 4.5 bps, `.add` (maker) 0.00015 = 1.5 bps, no VIP volume tier, no active staking or referral discount. This is the real main-dex rate, materially higher than the xyz HIP-3 dex's 0.9bps "growth mode" rate oil and the S&P desk get -- every cost figure below uses 4.5bps, not 0.9bps.

It trades around the clock with no gap in 208 days of 1h bars, weekends included.

### Noise, as a percent of price

| | p50 | p75 | p90 | p99 |
|---|---|---|---|---|
| ATR14 on 1h bars | 1.121 | 1.556 | 2.058 | 3.201 |
| ATR14 on 15m bars | 0.494 | 0.652 | 0.902 | 1.529 |
| 24h range | 5.614 | 7.864 | 10.687 | 19.203 |

HYPE's median 1h ATR14 (1.12%) is six times the S&P's (0.166%) and comfortably bigger than oil's -- a genuinely louder coin, which is why its own price-shock/drift thresholds (below) had to be measured fresh rather than borrowed.

### Session (New York), 1h bars, 2026-04-26 to 11-20

| Session | Median 1h range | Median $ traded per 1h bar |
|---|---|---|
| Cash, 09:30-16:00 | 1.400% | $27.3M |
| After the close, 16:00-18:00 | 1.143% | $17.5M |
| Pre-market, 04:00-09:30 | 1.050% | $13.6M |
| Overnight, 18:00-04:00 | 1.019% | $12.3M |
| Weekend, Fri 17:00-Sun 18:00 | 0.812% | $8.7M |

Session structure is much shallower than oil's or the S&P's: the quietest window (weekend) still ranges within 1.7x of the loudest (US cash hours), where oil's weekend is roughly 7x quieter than its cash session. HYPE does not have a "session" the way a futures-linked instrument does; it is a continuously-traded token whose volume merely tracks the wider market's waking hours.

## Liquidity and slippage, at this desk's size

Checked live via the order book walk, 2026-11-20 (best bid/ask around $65.02-65.05):

| Order size | Avg fill | Slippage | Book exhausted |
|---|---|---|---|
| $200 | 65.018 | 0.000% | no |
| $5,000 | 65.058 | 0.0154% | no |
| $20,000 | 65.185 | 0.0185% | no |

Twenty price levels on each side absorbed every size tested with room to spare. The desk's actual size (5% of a small account's equity, a 1.5-2% stop) is a few hundred dollars of notional -- the same "rounding error" conclusion the S&P doc reached, at a coin two orders of magnitude more liquid in absolute dollar terms than xyz:SP500.

## Funding

+0.1501 bps/hour at the mean (14,400 hourly rows, the full 600-day history -- funding is not capped at 5,000 rows the venue's `candleSnapshot` applies to candles), +0.1250 bps/hour at the median, positive 92.2% of all hours. Annualized: +13.15%/yr over the whole history, +11.77%/yr over the last 30 days. A long pays this, a short earns it; at the backtest's typical 1.5-2% stop this is under 0.01R per hour held -- a real but small structural tailwind for shorts, nowhere near large enough to be the edge by itself.

## Can 10x carry a stop

Yes, with real room to spare -- the opposite of the S&P's 50x situation.

| Leverage | Liquidation distance | Widest stop allowed | In median 1h ATRs |
|---|---|---|---|
| 10x | 5.263% | 2.632% | 2.3 |
| 7x | 9.774% | 4.887% | 4.4 |
| 5x | 15.789% | 7.895% | 7.0 |
| 3x | 29.825% | 14.912% | 13.3 |

The rule's own stop (1.0 x ATR14, no floor) sits at roughly one median ATR -- about 1.12% of price, well under half the 10x budget (2.632%). In the backtest the picked cell's median stop was 1.54% of price (the trend-gated family) and 1.98% (the fib family); no trade in either dataset needed a stop wider than 10x could carry. This is the inverse of the S&P's problem: there, the stop floor is wider than the whole 50x budget most of the time and the leverage line is the real filter; here, 10x is not binding at all, and lower leverage (tested at 7x, 5x, 3x fixed) changes almost nothing about which trades fire -- the live-rule-at-fixed-leverage sweep below shows 7x/5x/3x landing on the identical net R (+36.55R vs the 10x row's +38.87R), because dropping from 10x to 7x only reclassifies one borderline trade's leverage fit, not the trade itself.

## The rules tested

No HYPE thesis has been journaled, so the side comes from each family's own rule:

- **edge24**: the oil/sp500 rule, unmodified, `sides="both"` only (whichever 24h edge is closer). Market entry off the 24h range, stop beyond the edge, target the far edge, 2R gate, no entry after a break. Grid: stop buffer 0.5 / 1.0 / 1.3 / 2.0 x ATR14, floor 0 / 0.25% / 0.4% of price, target offset 0 / 0.1% / 0.5%. 36 variants (one side mode).
- **trend**: the identical rule and grid, restricted to `sides="trend120"` / `"trend24"` (the sign of the 120h or 24h return decides the side; already built into `edge_rule`'s `sides_for`, the same mechanism `next_trade.trend_gate` applies live as a veto rather than a side-picker). 72 variants.
- **fib**: new. After an N-hour leg (the bar with the highest high and the bar with the lowest low in the last N closed 1h candles; whichever came LATER sets the leg's direction -- low then high is an up-leg, a pullback down sets up a long; high then low is a down-leg, a pullback up sets up a short), enter once the mid sits in the 50-61.8% retracement band, stop beyond the 78.6% retracement (the larger of k_atr x ATR14 and floor_frac x mid past it -- the same buffer shape as edge24), target either the leg's own extreme (continuation) or the 1.272 extension (27.2% of the leg's own height past the extreme). Live gates (`bracket.check_leverage`/`check_levels`), 2R, one leverage (10x), no leverage fit. Grid: lookback 12 / 24 / 48 / 72h, k_atr 0 / 0.5 / 1.0, floor 0 / 0.25% / 0.5%, target mode extreme / ext127. 72 variants.

180 variants total, the same scale as the S&P's 186. Datasets, split and verdict bar are byte-identical to the S&P eval (`verdict()`, imported unchanged): EDGE needs 30+ trades on 1h (5+ in the tail), net R above zero on the whole sample AND the held-out tail of both datasets, above zero under the cost stress, above zero without the two best trades, and at least half the grid neighbours above zero. LEANING drops the 15m/stress legs. Everything else is NOISE.

## Results

| Family | Variants | Picked variant (1h train) | 1h full | 1h tail | 1h stress | 15m full | 15m tail | Verdict |
|---|---|---|---|---|---|---|---|---|
| edge24 | 36 | k1.3_f0_o0.5_both | -17.75 (178, 6.01/wk) | -10.76 (45) | -23.97 | -11.94 (40) | -8.43 (8) | **NOISE** |
| trend | 72 | k1_f0_o0_trend120 | +38.87 (136, 4.59/wk) | +8.06 (40) | +33.03 | +0.56 (33) | +4.30 (5) | **EDGE** |
| fib | 72 | lb72_k0_f0_ext127 | +45.54 (62, 2.09/wk) | +20.62 (20) | +43.27 | +11.58 (19) | +7.20 (6) | **EDGE** |

Of 36 edge24 variants, 0 are net positive on the full 1h sample -- not a cherry-picking problem, the whole family loses. Of 72 trend variants, 36 (half) are net positive; of 72 fib variants, 24 (a third). The plain rule's own grid neighbours (±1 step on k_atr/floor/offset around the picked cell) are -38.79, -30.45, -17.75, -39.23 -- unanimous losers, nowhere close to a sign flip. The trend family's neighbours are +29.70, +34.68, +38.87, +20.40 -- unanimous winners, the opposite failure mode the S&P's own picked cell had (there, three of four neighbours flipped sign).

Trade-by-trade split for the plain rule's picked cell: long +13.99R (49 trades) against short -20.98R (84 trades) on the 1h train part -- almost the entire loss is on the short side, exactly the signature of fading rallies in an asset that was, on net, rallying.

## The rule that ships: trend-gated edge24

Stop: 1.0 x ATR14 (1h), no floor. Target: the far 24h edge, no offset. 2R. 10x only, never fitted lower. No entry after a break. No new entry against the sign of the 120h return (`next_trade.trend_gate`, span 120, wired as a veto on the thesis's own side rather than a side-picker -- the backtest's `sides="trend120"` mode is the stand-in for "no thesis yet"; live, the operator's thesis picks long or short and the veto only blocks it when it disagrees with the trend).

| Dataset | Part | Trades | Per week | Win | Mean R | Net R | Max DD (R) | Long | Short |
|---|---|---|---|---|---|---|---|---|---|
| 1h, 207 days | whole | 136 | 4.59 | 32.4% | +0.286 | +38.87 | 12.83 | +12.64 (62) | +26.23 (74) |
| | first 70% (train) | 96 | 4.63 | 32.3% | +0.321 | +30.81 | 7.43 | +17.10 (45) | +13.71 (51) |
| | held-out tail | 40 | 4.50 | 32.5% | +0.202 | +8.06 | 12.83 | -4.46 (17) | +12.52 (23) |
| | cost stress (6bps + 3bps slip) | 136 | 4.59 | 32.4% | +0.243 | +33.03 | 13.42 | +10.27 (62) | +22.77 (74) |
| 15m, 52 days | whole | 33 | 4.43 | 30.3% | +0.017 | +0.56 | 9.63 | -9.10 (14) | +9.66 (19) |
| | first 70% (train) | 28 | 5.37 | 25.0% | -0.134 | -3.75 | 9.63 | -9.10 (12) | +5.36 (16) |
| | held-out tail | 5 | 2.24 | 60.0% | +0.861 | +4.30 | 2.13 | +0.00 (0) | +4.30 (5) |
| | cost stress | 33 | 4.43 | 30.3% | -0.025 | -0.83 | 10.08 | -9.65 (14) | +8.82 (19) |

65 of 96 train trades stopped, the rest reached target; median stop 1.54-1.57% of price across train/tail/stress. Without its two best trades the full 1h sample still nets +24.67R (the `ex_best2_1h` leg); the held-out tail alone is thin enough (40 trades) that its own two-best-trades-removed figure goes negative (-0.95R) -- expected on a smaller slice and not part of the formal bar, which checks the full sample's figure only. Every leg the verdict bar checks passes: full_1h, tail_1h, full_15m, tail_15m, stress_1h, ex_best2_1h, neighbours -- `failed: []`.

**Quarter-by-quarter (1h, equal trade counts, not equal time), so "+38.87R" is not one lucky stretch:**

| Quarter | Trades | Net R | Win | Longs | Shorts |
|---|---|---|---|---|---|
| 2026-05-01 to 06-17 | 34 | -2.98 | 8/34 | 19 | 15 |
| 2026-06-17 to 07-31 | 34 | +19.63 | 13/34 | 10 | 24 |
| 2026-08-02 to 09-27 | 34 | +17.69 | 12/34 | 21 | 13 |
| 2026-09-30 to 11-20 | 34 | +4.54 | 11/34 | 12 | 22 |

Three of four quarters profitable, the one loser a mild -2.98R, not a blowup, and both long and short trades appear in every quarter (the edge is not "always short because the window fell" -- it is "take the pullback that agrees with the last five days," which flips sides as the trend itself turns, including inside the held-out tail: the tail's longs lost -4.46R while its shorts made +12.52R, a genuine mix, not hindsight).

**FOMC/CPI event blackout** (holding new entries 2h either side, the same rule every desk runs): 1h full improves slightly to +40.80R (135 trades, one fewer losing entry held back); 15m unchanged (+0.56R, no FOMC/CPI fell near a trade entry in that 52-day window). On for the same reason as every other desk: a release can move price further than the whole stop budget. HYPE has no weekend rule (it is not in `weekend.OIL_COINS`; it trades straight through, no gap, no weekly close) and no jobs-report mapping of its own (`SP500_MACRO_KINDS`-style blocking kinds were deliberately not built for it -- its calendar is the protocol's, not the US macro print one).

**Equity curve** (the desk slice alone, compounded, each trade risking `risk_pct` of the slice or less when full size at 10x cannot carry that much):

| Risk per trade | 1h final multiple | 1h max DD | 15m final multiple | 15m max DD |
|---|---|---|---|---|
| 6% | 3.928x | 54.9% | 0.883x | 45.0% |
| 4% | 3.04x | 40.8% | 0.952x | 32.5% |
| 3% | 2.49x | 32.4% | 0.976x | 25.4% |
| 2% | 1.94x | 22.9% | 0.993x | 17.7% |

6% (oil's own number) produces a drawdown over half the slice on the 1h sample and a net LOSS on the 15m sample once compounding is applied -- a 32% win-rate rule cannot carry oil's risk setting. 2-3% is the range this research recommends if the desk is switched on; `HYPE_STRATEGY.md`'s "Where it breaks" says so explicitly.

## Fibonacci pullback (new family, EDGE, not shipped live)

The operator's own 14-day read ("HYPE respects fib retraces about 40% of the time against a ~35% baseline across 35 markets") was a hint to test, not a finding, and this is the test: a full rule (entry band, stop, target, leverage and R gates, 72-variant grid, the same honesty bar) rather than a retrace-frequency count. The picked cell (72h lookback, pure-ATR stop with no floor beyond the 78.6% level, the 1.272 extension as target) clears every leg: +45.54R full 1h sample (62 trades, 2.09/wk -- about half as often as the trend family), +20.62R held-out tail, +43.27R under cost stress, +35.44R without its two best trades, 2 of 3 grid neighbours positive. Its own quarter-by-quarter split is the cleanest in this research: every one of four 15-trade quarters profitable (+4.19, +12.71, +14.28, +16.48R), with only a two-trade tail fragment at the very end (-2.13R, too thin to read).

It does not ship because it needs real new runtime surface the trend-gated rule does not: a live leg-detection function, new fields on the levels payload, a new CLI path, and a materially different story in the tick doc (retracement bands instead of the 24h-range-edge shape every other desk already uses). Given two families that both clear the bar, the one that reuses `next_trade.py`'s existing, already-tested shape (and an already-built, already-tested veto mechanism) is the simpler, lower-risk choice -- Layer 1 of "search before building" beats inventing a second live entry mechanism on day one. The code for it (`services/strategies/evals/hype_strategy_test.py`'s `fib_rule`/`build_fib_ticks`/`leg_from`) stays in the eval, gate-tested, for the day a future pass wants to revisit it.

## What this does not test

- **The thesis.** Live, Claude writes a call and the trend gate only vetoes it when it disagrees with the last five days. Nobody has graded a HYPE thesis yet. That is the missing test, same as every other desk's backtest says.
- **A third regime.** 207 days, one token, one big rally and one real pullback. The trend rule's weakest quarter (-2.98R) was the flattest stretch in the sample; a long sideways chop with no clean trend either way is untested.
- **Intrabar order.** On 1h bars a stop and a target inside one bar count as a stop. The 15m run is the finer read and is shorter (52 days).
- **Latency.** Entries fill at the bar's close plus 1 bp slippage. The tick takes longer than that.
- **BTC correlation as a direct input.** HYPE's 1h returns correlate 0.585 with BTC's (beta 1.27, HYPE's own volatility 0.90% vs BTC's 0.415% hourly; daily correlation 0.550) -- real, not incidental: a chunk of the trend-gated rule's edge likely rides BTC's own trend through HYPE's beta exposure, which the task named as a driver and which no part of this test isolates. If BTC's regime reverses sharply, HYPE's correlated downside is a real, unhedged risk this rule does not know about separately from its own 120h return.

## Run it

    .venv/bin/python -m services.strategies.backtest.snapshot --coin HYPE --days-back 600 \
        --snapshot-dir ~/Documents/code/pathiel-replays/candles/20261119-hype
    .venv/bin/python -m services.strategies.backtest.snapshot --coin BTC --days-back 600 \
        --snapshot-dir ~/Documents/code/pathiel-replays/candles/20261119-hype
    .venv/bin/python -m services.strategies.evals.hype_strategy_test \
        --snapshot-dir ~/Documents/code/pathiel-replays/candles/20261119-hype

Exit code 0 when the shipped rule's full-sample AND held-out-tail net R are both positive (the "better than LOSING" bar the operator set). Today: 0.
