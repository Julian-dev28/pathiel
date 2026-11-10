# Strategy tournament and walk-forward round robin, 2026-11-10

**Verdict: nothing passes. No mechanical candidate and no round-robin
selector clears the bar at the desk's 6% risk; at 3% risk one candidate
(B_ret120, the dip-buy with a 5-day-return trend gate) clears it on a
middle third of +$2 and its neighbour spans disagree, so it is a lean, not
an edge. Do not wire a selector. The one change the data supports is a veto:
no dip-buy while the 120h return is negative, which would have blocked all
14 mechanical November longs.**

Operator, 2026-11-10: "dont pause it, I want you to find what works!!!! Try
a round robin with strategies that work." Research only; nothing is wired.

## Pass bar (written before the run)

A candidate or selector "works" only if, on Brent, out of sample:
net $ > the baseline's net AND > 0; positive in at least 2 of 3 equal
sub-windows; max drawdown under 35%; at least 15 trades; and net on WTI
>= 0. Ranked by net $ with drawdown alongside. Anything resting on fewer
than about 15 trades is a lean.

## Data and engine

- xyz:BRENTOIL 1h candles, the longest window the venue serves: 5000 bars,
  2026-04-16 06:00Z to 2026-11-10 16:00Z. The first 240 bars (10 days)
  warm the slowest feature, so every candidate is scored on the same
  198.4 days, 2026-04-26 06:00Z to 2026-11-10 16:00Z. Thirds are 66 days
  each. xyz:CL (WTI) over the same bars is the second instrument.
- Cache: `/private/tmp/claude-501/-Users-julian-dev-Documents-code-pathiel/7f5c1d95-343d-4878-a9f5-0f44e55178be/scratchpad/tournament/`
  (`xyz%3ABRENTOIL__paged_1h_*.json`, `xyz%3ACL__paged_1h_*.json`, the
  result JSONs). A refetch on a later day gets a later 5000-bar window.
- One engine for every candidate (`services/strategies/evals/strategy_tournament.py`):
  one decision per 1h bar at its open, levels from closed bars only, market
  entries at the open, intrabar stop/target checks with the stop judged
  first when both touch in one bar, a bar that opens through the stop fills
  at the open. Same costs for all: 4.5 bps taker each way (the target too),
  3 bps slippage on every market fill, 6% of equity at risk per trade sized
  off the stop, leverage fitted so the stop sits inside half the
  liquidation distance (20x cap; the baseline keeps the live 20/15 fit, the
  others may go to 10/8/5), one position at a time, the oil weekend flat,
  start equity $182.09. Event blackouts are off for everyone.
- The live loop ticks every 15 minutes and trades a thesis side; this
  baseline is the same mechanics on 1h ticks with no thesis (the always-long
  control). It is the floor the thesis has to beat, not the live record.

## Candidates (parameters fixed before the run)

| Family | Rule | Values run |
|---|---|---|
| A | the live dip-buy, mechanical: market entry inside the 24h range, stop 0.75 under the 24h low, target the 24h high less 0.5%, 2R gate, no entry past the edge | A_long (baseline), A_short (mirror control) |
| B | A's long only while the trend is up, the mirror short at the 24h high only while down | close vs EMA48, EMA120; sign of the 120h return |
| C | Donchian: a 1h close over the prior N-hour high buys at the next open, 2 ATR14 stop, 3 ATR14 trail off the best close; mirror short | N = 24, 48, 96 |
| D | momentum WITH the desk's price alarms (1.75% in 4h; 1% in one 1h bar for the 15m shock), stop 0.75 beyond the last 4h extreme, target 2R or 3R, out after 24h | target 2R, 3R |
| E | mean reversion both sides: bottom (top) quarter of the 24h range, stop k x ATR14 beyond the edge, far edge less 0.5% target, 1R gate; the 6% risk rule sizes it smaller by itself | k = 1.5, 2.0 |
| F | time-series momentum: long while the N-hour return is positive, short while negative, flipped at the close that changes the sign, 2 ATR14 stop | N = 120, 240 |
| G | flat | |

## Results, 6% risk (the bar), Brent, ranked by net $

| Candidate | Trades | Wins | Net $ | Max DD | Thirds $ | WTI net $ (n) | Pass |
|---|---|---|---|---|---|---|---|
| B_ret120 | 67 | 24 | +6.30 | 53.0% | -63 / -2 / +72 | +53.60 (69) | fail (thirds, DD) |
| G_flat | 0 | 0 | +0.00 | 0.0% | 0 / 0 / 0 | +0.00 (0) | fail |
| D_alarm_r2 | 120 | 55 | -1.05 | 33.4% | -18 / +14 / +3 | -66.92 (134) | fail |
| D_alarm_r3 | 119 | 55 | -14.08 | 36.4% | -30 / +7 / +9 | -80.10 (133) | fail |
| B_ema48 | 8 | 2 | -14.38 | 19.8% | +24 / -24 / -14 | -48.47 (5) | fail |
| F_tsmom120 | 182 | 47 | -25.88 | 89.9% | +132 / +204 / -362 | -86.25 (181) | fail |
| C_donch96 | 64 | 19 | -54.82 | 58.2% | -86 / +88 / -57 | +28.51 (57) | fail |
| C_donch48 | 86 | 28 | -63.44 | 62.8% | -53 / +42 / -53 | -42.81 (87) | fail |
| B_ema120 | 40 | 12 | -78.77 | 53.7% | -9 / -73 / +4 | +25.58 (38) | fail |
| C_donch24 | 120 | 36 | -121.08 | 81.7% | -71 / +50 / -100 | -95.75 (118) | fail |
| A_short | 121 | 30 | -140.18 | 89.3% | +174 / -263 / -51 | -130.41 (123) | fail |
| F_tsmom240 | 168 | 35 | -140.39 | 92.2% | -29 / -101 / -10 | -12.33 (166) | fail |
| E_mr_atr2 | 147 | 54 | -141.99 | 83.1% | -52 / -86 / -4 | -166.28 (150) | fail |
| **A_long (baseline)** | 118 | 32 | -149.43 | 87.6% | -151 / -0 / +2 | -154.97 (123) | fail |
| E_mr_atr1.5 | 181 | 57 | -169.56 | 94.7% | -82 / -85 / -2 | -171.99 (199) | fail |

A_long exits: 82 stops, 19 targets, 16 weekend flats. Each stop costs
exactly 6.0% of equity, a 2R target pays about 11.4%; at a 27% win rate
the expectancy is -0.19R a trade. By month the mechanical long made
May -79.78, Jun -69.17, Jul +1.15, Aug -4.99, Sep -0.88, Oct +32.10,
Nov -27.81: the same shape as the live record (October up, November down).

### Round robin, 6% risk

| Selector | Trades | Wins | Net $ | Max DD | Thirds $ | WTI net $ | Picks | Pass |
|---|---|---|---|---|---|---|---|---|
| RR_7d_look7d | 122 | 42 | +244.00 | 64.8% | +196 / -138 / +186 | -49.17 | F_tsmom120 x6, A_short x5, F_tsmom240 x4, A_long x3, others 1-2 | fail (DD, WTI) |
| RR_7d_look14d | 144 | 36 | -127.33 | 89.4% | +46 / -147 / -26 | -128.97 | F_tsmom120 x7, C_donch96 x4, A_short x4, A_long x3 | fail |
| RR_3d_look7d | 124 | 41 | +157.46 | 69.9% | +245 / -180 / +92 | -19.47 | F_tsmom120 x10, A_short x9, F_tsmom240 x8, E_mr_atr2 x7, A_long x7 | fail (DD, WTI) |
| RR_3d_look14d | 137 | 34 | -141.78 | 83.5% | +27 / -101 / -67 | -101.71 | F_tsmom120 x12, A_short x10, A_long x8, C_donch96 x7, E_mr_atr1.5 x7 | fail |
| EQUAL_SPLIT (14 slices) | 1541 | 486 | -79.20 | 51.2% | -22 / -16 / -40 | -67.76 | all | fail |
| HINDSIGHT best single (B_ret120) | 67 | 24 | +6.30 | 53.0% | | +53.60 | hindsight, not tradeable | n/a |

The selector is out of sample by construction (the first lookback window is
flat, a pick only reads trades that exited before the rebalance, a trade
open across a rebalance runs to its own exit). Its sign flips with the
lookback: +$244 and +$157 at 7 days, -$127 and -$142 at 14 days; the two
winners lose on WTI and run 65-70% drawdowns. That is one setting out of
four, on one instrument: noise, not an edge. Both winners live mostly on
F_tsmom120, which alone lost $26 and gave back $362 in the last third.

## Sensitivity: 3% risk a trade (not the bar; shows what the DD leg costs)

`--risk 0.03`. Sizing does not change a candidate's sign, only the
compounding; at 6% seven stops in a row is a 35% drawdown, so the DD leg
binds every active candidate with over 100 trades.

| Candidate | Trades | Wins | Net $ | Max DD | Thirds $ | WTI net $ | Pass |
|---|---|---|---|---|---|---|---|
| F_tsmom120 | 182 | 47 | +23.46 | 67.3% | +69 / +89 / -135 | -23.80 | fail |
| **B_ret120** | 67 | 24 | **+14.63** | 30.1% | -33 / +2 / +45 | +39.43 | **PASS** |
| D_alarm_r2 | 120 | 55 | +4.84 | 17.9% | -8 / +9 / +4 | -31.91 | fail (WTI) |
| D_alarm_r3 | 119 | 55 | -1.63 | 20.0% | -14 / +5 / +7 | -40.64 | fail |
| B_ema48 | 8 | 2 | -5.84 | 10.1% | +13 / -12 / -7 | -25.73 | fail |
| C_donch96 | 64 | 19 | -21.59 | 34.9% | -48 / +57 / -31 | +21.88 | fail |
| C_donch48 | 86 | 28 | -23.55 | 35.2% | -27 / +31 / -27 | -9.07 | fail |
| B_ema120 | 40 | 12 | -41.28 | 30.9% | -3 / -43 / +4 | +19.02 | fail |
| C_donch24 | 120 | 36 | -66.39 | 54.7% | -37 / +37 / -66 | -43.61 | fail |
| F_tsmom240 | 168 | 35 | -78.10 | 68.4% | -7 / -67 / -4 | +33.85 | fail |
| A_short | 121 | 30 | -83.33 | 65.1% | +87 / -126 / -44 | -70.96 | fail |
| E_mr_atr2 | 147 | 54 | -89.49 | 56.6% | -25 / -63 / -1 | -123.94 | fail |
| A_long (baseline) | 118 | 32 | -97.01 | 63.5% | -104 / +2 / +6 | -104.43 | fail |
| E_mr_atr1.5 | 181 | 57 | -128.89 | 74.9% | -43 / -84 / -2 | -133.60 | fail |

Selectors at 3%: RR_7d_look7d +136.15 (DD 38.6%, WTI -13.73), RR_3d_look7d
+96.42 (DD 43.0%, WTI +1.46), RR_7d_look14d -70.96, RR_3d_look14d -88.78,
EQUAL_SPLIT -42.44. All fail.

## Post hoc: neighbours of the one passer (run after the results, never ranked)

`--post-hoc`. B_ret120 passes at 3% on a +$2 middle third. Its neighbour
spans, Brent net $ / WTI net $:

| Span | 6% Brent | 6% WTI | 3% Brent | 3% WTI | 3% thirds |
|---|---|---|---|---|---|
| B_ret72 | -50.07 | -17.25 | -17.25 | +1.02 | -13 / -18 / +14 |
| B_ret96 | -40.92 | +134.09 | -12.10 | +78.29 | -28 / +4 / +11 |
| **B_ret120** | +6.30 | +53.60 | +14.63 | +39.43 | -33 / +2 / +45 |
| B_ret168 | +90.88 | -28.34 | +59.05 | -2.83 | +12 / -10 / +57 |
| B_ret240 | -98.79 | -54.18 | -50.30 | -16.76 | -43 / -40 / +33 |

No plateau: 72 and 96 lose on Brent, 168 wins on Brent and loses on WTI,
240 loses on both. F_tsmom neighbours (72, 96, 168) all lose on Brent at
both risk levels. A span that only works at one value is a fluke.

## What the trend gate would have done to the live loss

Mechanical trades on the real bars, 2026-11-02 to 11-10 (the live desk's
-$64.78 slide, 1 win in 6):

| Rule | Trades | Wins | Longs | Sum of equity fractions |
|---|---|---|---|---|
| A_long (the mechanics without the thesis) | 14 | 2 | 14 | -58.7% |
| B_ret120 (the same with the 120h-return gate) | 4 | 1 | 0 | -9.6% (all shorts) |
| B_ret120, long leg only | 0 | 0 | 0 | 0 |

And 10-22 to 11-01 (the live +$69.72 run): A_long 3 trades, 3 wins,
+34.5%; B_ret120 2 trades, 2 wins, +22.3% (it sat out one winner). The gate
is a veto that costs a little in the up weeks and removes the November
longs entirely. It is still a 67-trade lean with no neighbour plateau.

## Reading

1. The dip-buy mechanics without a thesis lose over 198 days on both
   instruments (A_long -$149 Brent, -$155 WTI), as `OIL_REGIME_TEST_2026-11-02.md`
   already found on 208 days. The live desk's October gain came from the
   thesis side in a rising market, not from the entry rule.
2. Shorts with the same machinery lose more (A_short -$140, worst DD 89%):
   headline spikes stop tight shorts, as the live always-short control
   showed. Trend following (C, F) is whipsawed on 1h bars and gives back
   its gains in the last third. Mean reversion with a wider stop (E) is
   the worst family: the tight stop was not the problem.
3. Trading with the price alarms (D) is the only family near zero on
   Brent (-$1 at 2R, 55 wins of 120, DD 33%) but loses on WTI (-$67). Not
   an edge either; the earlier alarm short test
   (`services/strategies/evals/alarm_short_test.py`) found the same.
4. The round robin amplifies whatever ran last week. On this data that is
   a coin flip between +$244 and -$142 depending on the lookback, and
   both winners lose on WTI.
5. The 35% DD bar is unreachable at 6% risk for any rule with a 25-35%
   win rate. Whether the desk keeps 6% is a sizing decision, not a
   strategy one; halving it halves the drawdown and leaves every sign.

## What wiring B_ret120 live would take (for the record; not recommended as a strategy change)

- `services/strategies/next_trade.py`: a `trend_gate(closes_1h, span_h=120)` returning the allowed side, read by `next_trade` as a veto on the long leg (and, only if the operator wants the short leg, as the permission for a rip-sell short).
- `scripts/oil_levels.py` / `services/strategies/levels.py`: print the 120h return next to the 24h range so the tick never computes it by eye.
- `services/strategies/backtest/desk_tick_sim.py`: a `Params.trend_gate_span` so the thesis replays can score the veto on the journaled theses before it goes live (the real test: this tournament has no thesis).
- Tests for the gate, a parity test between the sim and `next_trade`, a row in OIL_STRATEGY.md. About half a day. Re-check: `python3 -m services.strategies.evals.strategy_tournament --cache-dir <dir>` must keep B_ret120 >= 0 on both instruments.

## Reproduce

```
.venv/bin/python -m services.strategies.evals.strategy_tournament --cache-dir <cache> --json result.json          # exit 1: nothing passes
.venv/bin/python -m services.strategies.evals.strategy_tournament --cache-dir <cache> --risk 0.03 --post-hoc
.venv/bin/python -m pytest -q services/strategies/tests/test_strategy_tournament.py                              # 22 gate tests
```
