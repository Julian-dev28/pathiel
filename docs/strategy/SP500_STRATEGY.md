# S&P 500 desk (built 2026-11-19, inactive until the operator flips it)

> Research log, not a performance record. **Verdict: NOISE.** The rule below has no measured edge on xyz:SP500 (`SP500_BACKTEST_2026-11-19.md`). It is the least bad of 186 variants, it loses under a cost stress, and no S&P thesis has been graded. Nothing here is a forecast.

Operator, 2026-11-19: "ADD A S&P500 MARKET STRATEGY 50X 5% LONG OR SHORT".

## The rule, in one line

In the thesis direction, a market entry only when the mid sits at the 24h edge: stop the larger of 1.0 x ATR14 (1h) and 0.4% of price beyond that edge, target the far 24h edge, 2R or better, 50x, never chase past the thesis entry, no entry after a break.

## The setup

| Item | Oil | S&P 500 |
|---|---|---|
| Desk id | `oil` | `sp500` |
| Coin | xyz:BRENTOIL | xyz:SP500 (venue max 50x, one margin tier) |
| Sides | long or short, the thesis picks | same |
| Equity share | 90% | 5% (when active) |
| Leverage | 20x, 15x when the stop is too far for 20x | 50x only. A stop too far for 50x is no trade, never a lower leverage |
| Leverage cap | the risk profile's `max_leverage` (20) | its own, 50 (`services/strategies/desk_leverage.py`; config `desk_max_leverage` lowers it) |
| Stop | 0.75 beyond the 24h edge | the larger of 1.0 x ATR14 and 0.4% of the mid beyond the 24h edge (`next_trade.SP500_RULE`). The floor binds on most ticks: the S&P's 1h ATR14 is 0.166% of price at the median |
| Stop budget | half the liquidation distance: 1.28% at 20x | half of 1.01%: 0.505% at 50x. So the mid must be within about 0.1% of the 24h edge |
| Target | far 24h edge minus 0.5% | the far 24h edge itself, no offset (`next_trade.target_offset_frac_for`). The S&P's median 24h range is 0.9% |
| Gate | 2R, never chase, no entry past the 24h edge | same |
| Risk per trade | `risk_profile.risk_per_trade_pct` of the desk's slice | same number, 5% slice |
| Weekend | flat Fri 16:00 to Sun 18:00 New York | no rule. Flattening lost 20R in the test |
| Events | EIA, FOMC, CPI, OPEC+ | FOMC, CPI, the monthly jobs report, a megacap earnings row with impact 3 |
| Ticks | `/desk-tick oil` every 15 min | `/desk-tick sp500` every 15 min |
| Levels | `scripts/oil_levels.py` | `scripts/oil_levels.py --coin xyz:SP500` (50x by default for that coin) |
| Price watcher | 1.0% in 15 min, 1.75% in 4h | 0.7% in 15 min, 0.75% in 4h; headline group `sp500` |

## The numbers

From `SP500_BACKTEST_2026-11-19.md`, the rule taking both sides with no thesis:

| Dataset | Trades | Per week | Win | Mean R | Net R | Max DD (R) | Held-out tail | Cost stress |
|---|---|---|---|---|---|---|---|---|
| 1h, 207 days | 103 | 3.48 | 29.1% | +0.103 | +10.64 | 14.39 | +7.69 (37 trades) | -14.09 |
| 15m, 52 days | 38 | 5.10 | 26.3% | +0.094 | +3.57 | 10.96 | +7.32 (16 trades) | -5.58 |

Why it is still NOISE: it loses 14R when costs are 4.5 bps plus 3 bps of slippage instead of 0.9 plus 1; it is -0.53R without its two best trades; three of its four grid neighbours lose; and the cell was picked after reading the whole sample, so its tail is not out of sample. The three variants picked on the first 70% of the data alone all lose on the rest.

The both-sides test took 3.5 to 5 entries a week. Live the thesis allows one side, so expect about half that. 7 in 10 stop.

## Is 50x workable

Mechanically, yes. Liquidation is 1.01% from entry at 50x. The stop may use half: 0.505%, three median hourly ATRs. The rule's median stop is 0.44%. In the test every trade fit 50x and no stop filled more than 0.01% past its level.

It is tight. 1% of 15m bars range more than the whole stop budget, and a round trip costs 0.09R at today's fees. A fee back at 4.5 bps turns the rule negative.

Lower leverage does not fix anything: the same rule at 40x made +0.06R and at 20x to 30x +5.3R with a losing tail. No leverage has an edge here, so there is no better number to recommend than the one asked for.

Risk in account terms: a stop costs `risk_per_trade_pct` of 5% of equity. At 6% that is 0.3% of the account a stop, and the test's worst run (14.4R) is 4.3% of the account. At 15% it is 0.75% and 10.8%. Check what `risk_per_trade_pct` is before turning the desk on.

## The operator's own S&P position

The operator holds a hand-placed xyz:SP500 long (0.18 @ 7171.2, 50x cross, no stop; 2026-11-19). Hyperliquid nets one position per coin per account, so a desk entry would merge into it.

The rule, in code: on xyz:SP500 any position or resting order the desk did not place is the operator's (`pathiel/agents/ownership.py`: no pathiel cloid on the opening fills or the order, no journal entry). While one exists:

- `scripts/desk_slot.py --desk sp500` answers `"slot": "operator"`, `"reason": "operator position open on xyz:SP500"`. The tick logs `action=HOLD operator position open on xyz:SP500` and stops before the brief.
- `place_bracket` (any mode, dry run included), `sweep_brackets` and `set_leverage` refuse on that coin. `close_position`, `set_stop`, `set_target` and `cancel_order` already refused a position or order the desk did not place.
- A read that fails counts as his. The desk holds rather than guess.

The desk trades the S&P again on the first tick after his position and orders are gone. `operator_override: true` in a call is the one way through, and a tick never passes it. Gate test: `tests/test_sp500_operator_guard.py`.

His position has no stop. An isolated 50x long from 7171.2 liquidates near 7,099; his is cross, so the account's free margin moves that level. The desk will not put a stop on it.

## Config to apply

The desk ships inactive: `/desk-tick sp500` logs `SKIP desk not active` until this is saved. One call (`update_config`, or the same keys in `.agent-config.json`):

    update_config {
      "active_desks": ["oil", "sp500"],
      "desk_equity_share": {"oil": 0.90, "metals": 0.05, "sp500": 0.05}
    }

Oil stays 90%. The inactive metals desk goes from 10% to 5% so the three add up to 1.0 if it comes back. Nothing else is needed: the 50x cap is the code default for this desk. To run it lower without a code change:

    update_config {"desk_max_leverage": {"sp500": 25}}

`update_config` refuses a cap outside 1 to 50. With a lower cap the rule still refuses any stop that does not fit that leverage.

Then start the tick: `/loop 15m /desk-tick sp500`, or rerun `/desk-loops` (it now creates the sp500 cron). Restart the MCP server and `scripts/trigger_watch.py` first so they load the new desk.

## What changed in the code

- **Desk** (`services/strategies/desks/sp500.py`): one instrument. News queries on the index, the Fed, Treasury yields, Nvidia and megacap earnings, the jobs and inflation prints, tariffs. The existing `indices` desk is untouched and stays off.
- **Rule** (`services/strategies/next_trade.py`): `SP500_RULE`, `stop_buffer_for`, `leverage_fit_for` (50x only), `target_offset_frac_for`. Read by the levels script, `GET /api/desks/sp500/next`, the Levels card, the brief's live-rule line and the price watcher.
- **Leverage cap per desk** (`services/strategies/desk_leverage.py`): `place_bracket`, `size_trade` and the brief's "Your size" use the desk's cap. Every other coin gets the risk profile back unchanged (`test_sp500_desk.py`, `tests/test_sp500_place_bracket.py`). A public install gets no code default.
- **Events** (`services/intel/scheduled_events.py`): coin group `sp500`. FOMC and CPI block. US macro rows reach it by kind. The jobs report blocks it and only it. Megacap earnings rows are added by hand (the event-calendar skill says how); none is on the calendar today: Nvidia reported 2026-11-17 and the January dates are not announced.
- **Headlines** (`services/intel/headline_watch.py`): group `sp500` with its own patterns (the Fed, tariffs, megacap guidance, a shutdown or a downgrade, a trading halt), direction words (risk-on is bullish) and price thresholds measured on the coin.
- **Tick** (`.claude/agents/desk-tick.md`, `.claude/skills/desk-tick/SKILL.md`, the plugin copies): the operator slot, the coin, the levels command, the rule.

## Where it breaks

- The thesis is wrong more often than a coin flip. The rule has nothing of its own to fall back on.
- Fees. Growth mode ends, the fee goes from 0.9 to 4.5 bps, and the rule is negative.
- A release nobody put on the calendar. The stop budget is 0.5%; a surprise print moves 1% in an hour.
- A bar that runs through the stop. About once every 13 days a 15m bar ranges more than the whole distance to liquidation.
- The ownership read. If the venue stops echoing cloids and the journal is lost, the desk treats its own position as the operator's and holds. It fails safe, and it stops managing.
- `risk_per_trade_pct` set for oil's 3% range. On a 29% win rate it is too large for this desk.

## Before it is switched on

Not a blocker in code, a recommendation: run the desk on a thesis for two weeks on paper (`refresh_desk sp500`, grade the calls) or at the venue minimum, and read the review before giving it 5%. The test says the rule alone does not pay.
