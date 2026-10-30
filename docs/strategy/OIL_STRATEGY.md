# Oil desk strategy: the old rules and the 2026-10-30 switch

On 2026-10-30 the oil desk stopped resting a limit at the 8-hour low and
started buying at the market when price sits low enough in the 24-hour range
to pay 2R. On our 9 journaled theses the new rules make 18.7% a day in the
backtest against 11.2% for the old ones, and they are the first rules that
still make money with no thesis at all over 50 days.

Operator words that set it: "switch it" (2026-10-30), after asking for the
most profit "PER DAY, THEN PER WEEK".

## What stays the same

Brent only (`xyz:BRENTOIL`), 20x isolated, the whole equity in each trade
(margin plus the opening fee), one position at a time, stop 0.75 beyond the
structure, target 0.5% of price inside the far edge as a resting limit order,
the stop never moves, a new entry is allowed after a target fills, a tick
every 15 minutes, theses written by Fable 5.1 on the rewrite schedule
(`scripts/thesis_due.py`). Never chase: no entry above a long thesis's entry.

## The two rule sets

| Rule | Old (until 2026-10-30) | New (from 2026-10-30) | Where it lives |
|---|---|---|---|
| Entry | Rest a limit at the 8h low (long) when the mid is within 0.25 ATR of it | Buy at the market at the live mid | `.claude/agents/desk-tick.md` step 5 |
| Structure for entry and stop | 8h range | 24h range | same |
| Stop | 0.75 under the 8h low | 0.75 under the 24h low, or under the mid if price already broke the low | same |
| Target | 24h high, 0.5% inside it | same | `bracket.TARGET_OFFSET_FRAC` |
| Minimum reward | 1.5R | 2R, judged on the offset target | `bracket.DEFAULT_MIN_R` |
| How a trade is judged | `oil_levels.py --check` | `place_bracket {..., dry_run: true}`, then the same call live | agent step 5 |
| Backtest twin | `Params()` with stop 0.75, offset 0.5% | `desk_tick_sim.live_params()` | `tests/test_bracket.py` keeps them in sync |

The open long from 109.46 (13.08 Brent) keeps its own stop at 108.95 and
target at 113.18. Only new trades use the new rules.

In practice the new rule means: most ticks price sits too high in the 24h
range for a long to pay 2R, so nothing happens. When price drops into the
lower part of the range (and stays at or under the thesis entry), the tick
buys at once instead of waiting for a fill at the 8h low.

## How we got here

Every change was backtested before it went live (operator rule since
2026-10-28). Newest first.

| Date | Change | Why |
|---|---|---|
| 2026-10-30 | Market entry, 24h range, 2R minimum | Strategy sweep ranked by profit per day (below) |
| 2026-10-30 | Target 0.5% inside the far edge, stop 0.75 beyond structure; live target moved 113.75 to 113.18 | Scenario sweep, 540 combos: $64 to $199.60 vs $123.91 for the rules before |
| 2026-10-28 | Thesis rewritten on a schedule by Fable 5.1: daily, after API/EIA/OPEC, after a close, after a 1.5% move, at most hourly | The 2026-10-25 thesis was still trading 2.7 days and one EIA report later |
| 2026-10-28 | Oil only | Cross-market test: without a thesis the same mechanics lose on Brent, gold, BTC and the S&P 500. The edge is the thesis |
| 2026-10-28 | Targets rest as limit orders, stops stay market | A limit target never missed in 26 test exits and pays the maker fee |
| 2026-10-28 | Stops stay fixed | Fixed +6.03R vs +1.97R for the best breakeven or trailing stop |
| 2026-10-27 | Whole equity per trade, no idle reserve | Operator: fees come out at the trade. The reserve went 10% to 2% to 0 |
| 2026-10-25 | 15-minute ticks kept; hourly tried and reverted the same night | Hourly made about half the return in the backtest |
| 2026-10-25 | Stop 0.50 beyond the edge (was 0.25) | The only buffer that turned the always-long control positive then |
| 2026-10-24 | 20x leverage, about $64 in the account | Operator's call; 10x was the safer option offered |
| 2026-10-23 | Brent through Claude only; `place_bracket` with a 1.5R floor | The old bot could take the margin a manual trade needed |

## ROI

Backtest, $64 start, 15-minute ticks, size-aware slippage, fees and
funding. "With thesis" = the 9 journaled theses, 2026-10-18 to 2026-10-30
(11.8 days). "No thesis" = always long. Per day is the compounding daily
rate; per week is (1 + per day)^7 - 1.

| Setup | Per day | Per week | $64 ends | Trades / won | Max drawdown |
|---|---|---|---|---|---|
| Old rules, with thesis | 11.2% | 110% | $224.63 | 8 / 62% | 30% |
| Old rules, no thesis, same 11.8 days | 8.1% | 72% | $160.31 | 10 / 50% | 35% |
| **New rules, with thesis** | **18.7%** | **233%** | **$487.66** | 8 / 75% | 29% |
| New rules, no thesis, same 11.8 days | 18.7% | 233% | $487.66 | 8 / 75% | 29% |
| New rules at 15x, with thesis | 14.7% | 160% | $322.49 | 8 / 75% | 22% |
| New rules at 10x, with thesis | 10.2% | 97% | $201.93 | 8 / 75% | 16% |

Under the new rules the thesis changed no trade in these 11.8 days: every
thesis was long, Brent rose, and no invalidation printed. Its job is the
days it says flat or short, which this window did not have.

No thesis, longer windows (always long):

| Window | Old rules per day | New rules per day | New rules max drawdown |
|---|---|---|---|
| Last 30 days | +1.01% | +9.48% | 67% |
| Last 50 days | -4.88% (98% drawdown) | +4.67% | 87% |
| First half of the 50 (Sep 11 to Oct 5, Brent flat to down) | -12.21% | -0.37% | |
| Second half of the 50 (Oct 5 to Oct 30, Brent up) | +3.05% | +9.96% | |

There were no theses before 2026-10-18, so a 50-day "with thesis" run cannot
be made.

Live account: funded to about $64 when the 20x desk started (2026-10-24);
$97.62 equity on 2026-10-30 00:15Z, of which $25.40 is the open long's
unrealized profit. Monthly and compounding projections in USD are in
`ROI.md` (gitignored).

## Why the new rules win

What each part adds, per day (with thesis / no thesis over 50 days):

| Rules | With thesis | No thesis, 50 days |
|---|---|---|
| Old | 11.2% | -4.88% |
| + market entry only | 12.4% | -4.22% |
| + 24h range only | 7.0% | -0.35% |
| + 2R minimum only | 11.8% | -3.14% |
| + market entry and 24h range | 15.0% | +3.42% |
| + all three (new rules) | 18.7% | +4.67% |

Market entry and the 24h range only work together. Our reading of the
trades: waiting for a limit at the 8h low missed the buys, and the 8h low
put the stop inside normal noise. The 24h low gives the stop room; the market entry takes the trade
while price is still low in that range. The 2R minimum keeps only the buys
near the bottom of the range.

Stress on the new rules: double slippage 18.1% a day (holds); the 1h clock
10.6% a day (it needs the 15-minute tick).

## Tested and rejected (2026-10-30 sweep)

Each was one change from the old rules; per day with thesis, old = 11.2%.

| Idea | Per day | Verdict |
|---|---|---|
| Take half off at 1R | 6.7% | Cuts the winners that pay for the losers |
| Half off at 1R, then stop to breakeven | 1.3% | Worse: breakeven stops get hit, then price goes on |
| Fixed 1.5R / 2R / 3R targets instead of the range edge | 1.8% to 5.8% | Lose on every series |
| Breakeven stop after 1R | 1.8% | Lost again (also lost 2026-10-28) |
| Trailing stop | 2.1% | Lost again |
| Close after 6h or 12h | 1.2% / 8.7% | Cuts trades before the target |
| Close after 24h or 48h | 11.2% | No change with thesis, worse without |
| Rest entries up to 0.5 or 1 ATR from the edge | 8.0% / 4.3% | More fills, worse ones |
| Re-entry off after a target | 4.1% | Gives up the second leg |
| Trade only 07:00 to 21:00 UTC | 9.0% | Better without thesis, worse with it |
| Target 0.75%, 1% or 1.5% inside the edge | 10.4% / 7.4% / 7.7% | 0.5% stays best |
| 1h clock | 7.4% | Slower ticks miss entries |

## Risks and when to re-check

- **Sample size.** 8 thesis trades, all long, in a rising market. The
  per-day rates rank the setups; they are not a forecast.
- **20x drawdowns.** On the 50-day no-thesis run the new rules' worst day
  was -57.7% and worst week -61.2%. Each stop-out costs about 15 to 27% of
  equity at 20x. Seven stops in a row (it happened in September on the
  control) takes 80% of the account. 15x cuts the worst drawdown from 87% to
  81% and gives up a fifth of the daily rate.
- **Market entries pay the taker fee** (4.5 bps) and slippage; the backtest
  includes both and the double-slippage run still holds.
- **Re-check after 30 live trades** under the new rules:
  `python -m services.strategies.evals.strategy_sweep --theses-dir <journal>/strategies/oil/theses --cache-dir /tmp/strategy-sweep/cache`
  (the sweep compares every variant against `live_params()`, now the new rules).

Full sweep tables: `docs/overhaul/BACKTEST_RESULTS_2026-10-25.md`, sections
2026-10-29 and 2026-10-30.
