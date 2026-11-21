# HYPE desk (built 2026-11-20, inactive until the operator flips it)

> Research log, not a performance record. **Verdict: EDGE** on the trend-gated rule that ships (`HYPE_BACKTEST_2026-11-20.md`): positive on the full 1h sample, its held-out tail, both datasets, the cost stress, without its two best trades, and every grid neighbour. The plain 24h-range rule every other desk uses (no trend filter) is a clear LOSER on HYPE and does not ship. No HYPE thesis has been graded yet. Nothing here is a forecast.

Operator, 2026-11-20: "Start the research on HYPE and desk for hype, maybe."

## The rule, in one line

In the thesis direction, but never against the sign of the 120-hour return: a market entry off the 24h range, stop 1.0x ATR14 (1h) beyond the edge (no floor), target the far 24h edge (no offset), 2R or better, 10x, never chase, no entry after a break.

## The setup

| Item | Oil | S&P 500 | HYPE |
|---|---|---|---|
| Desk id | `oil` | `sp500` | `hype` |
| Coin | xyz:BRENTOIL | xyz:SP500 | HYPE (main dex, no HIP-3 prefix) |
| Sides | long or short, the thesis picks | same | same, but vetoed against the 120h trend |
| Equity share | 90% | 5% (when active) | 5% (proposed; see "Config to apply") |
| Leverage | 20x, 15x when the stop is too far for 20x | 50x only | 10x only, the venue's own max |
| Leverage cap | the risk profile's `max_leverage` (20) | its own, 50 | its own, 10 (`services/strategies/desk_leverage.py`) |
| Stop | 0.75 beyond the 24h edge | the larger of 1.0 x ATR14 and 0.4% of the mid | 1.0 x ATR14, **no floor** (`next_trade.HYPE_RULE`) |
| Stop budget (half the liquidation distance) | 1.28% at 20x | 0.505% at 50x | **2.63% at 10x** -- the median stop (1.5-2.0% of price in the backtest) sits well inside it, unlike the S&P's line |
| Target | far 24h edge minus 0.5% | far 24h edge, no offset | far 24h edge, no offset |
| Gate | 2R, never chase, no entry past the 24h edge | same | same |
| Trend veto | none | none | **new**: no entry against the sign of the 120h return (`next_trade.trend_gate`, span `TREND_GATE_SPAN_BY_COIN["HYPE"] = 120`) |
| Risk per trade | `risk_profile.risk_per_trade_pct` of the desk's slice | same | same, but see "Risk in account terms" below -- 6% produced a 55% drawdown on the slice in the backtest |
| Weekend | flat Fri 16:00 to Sun 18:00 New York | no rule | no rule -- HYPE trades 24/7, no weekly close at all |
| Events | EIA, FOMC, CPI, OPEC+ | FOMC, CPI, jobs report, megacap earnings | FOMC, CPI only (no jobs-report mapping; token unlocks reach the thesis as a catalyst, not a blackout) |
| Ticks | `/desk-tick oil` every 15 min | `/desk-tick sp500` every 15 min | `/desk-tick hype` every 15 min |
| Levels | `scripts/oil_levels.py` | `--coin xyz:SP500` | `--coin HYPE` (10x by default for that coin) |
| Price watcher | 1.0% in 15 min, 1.75% in 4h | 0.7% in 15 min, 0.75% in 4h | **2.8% in 15 min, 3.5% in 4h** -- HYPE is a louder coin; 0.7% would fire on about 1 in 25 bars |

## Why a trend veto, and why HYPE is different from oil and the S&P

Every other desk's rule fades the 24h range: buy near the low, sell near the high, structure only. On HYPE, in 207 days of 1h bars, that rule alone lost -17.75R (both the full sample and its held-out tail negative, at the venue's real 4.5bps main-dex fee -- not the xyz dex's 0.9bps growth-mode rate oil and the S&P get). The reason reads off the trade split: shorts carried almost all of the loss (-14.60R of the -17.75R) while HYPE rallied through most of the sample. Buying dips that are shallower than the trend, or selling rips that are shallower than the trend, is a structurally different bet from buying a dip IN a downtrend or selling a rip IN an uptrend -- and HYPE, a single-name token with real narrative and flow-driven trends, punishes the first and rewards the second far more than oil (which mean-reverts around a slower-moving physical-supply anchor) ever did.

`next_trade.trend_gate` already exists for exactly this: built for oil 2026-11-13 (`TREND_GATE_TEST_2026-11-13.md`), tested, and found NOT to help there ("the dip-buy's winners ARE the bottoms; the veto blocks exactly those") -- so it shipped off, in the simulator only, "so it can be re-run the day the record changes." HYPE is that day. Gating the exact same 24h-range rule with the sign of the 120-hour return turns -17.75R into +38.87R over the same 207 days, with the held-out tail, the cost stress, two-best-trades-removed and all four grid neighbours agreeing (`HYPE_BACKTEST_2026-11-20.md` has the full honesty bar). A second family (Fibonacci pullback, new) clears the same bar with an even cleaner quarter-by-quarter profile but is not wired into the live code -- see the backtest doc's "Fibonacci pullback" section for why the simpler, already-built mechanism shipped instead.

## Is 10x workable

Mechanically, yes, with real room to spare -- unlike the S&P's 50x line, which the stop floor sits right against. Liquidation is 5.26% from entry at 10x; the stop may use half, 2.63%. HYPE's own 1h ATR14 is 1.12% of price at the median (1.56% at p75, 2.06% at p90), so a 1.0x-ATR stop with no floor sits at roughly half the stop budget on a typical hour and still inside it at the 90th percentile. In the backtest the picked cell's median stop was 1.54% of price (trend family) and 1.98% (fib family); no trade in either backtest needed a stop wider than 10x could carry.

On a wild hour the desk still sits out: 24h-range entries and the 2R gate already filter most of it, and `leverage_fit_for(HYPE) == (10.0,)` (one candidate, never refitted lower, matching the S&P's own `(50.0,)` pattern) refuses outright rather than trading smaller. About 6.4% of all 1h bars in the sample ranged past the whole 10x stop budget (2.63%); weekend hours are quieter than weekday ones here (3.4% vs 7.7%), the opposite of a risk one might expect from "thin weekend liquidity."

Risk in account terms: a stop costs `risk_per_trade_pct` of 5% of equity. The backtest's equity curve (205 days, compounded, the trend rule's own trades) at 6% risk per trade: 3.93x the slice, 54.9% max drawdown. At 2%: 1.94x, 22.9% drawdown. The 15m dataset (52 days, a different regime -- HYPE fell 25% over that window) is nearly flat at 6% risk (0.88x, meaning a net loss on the slice) and barely positive at 2% (0.99x). **Check `risk_per_trade_pct` before turning this desk on; 6% (oil's own number) is too large for a 29-37% win-rate rule on a slice this size.** A lower number, 2-3%, is the one this research recommends if the desk is switched on.

## Liquidity, at this desk's size

HYPE's order book easily absorbs the desk's size. A $200 market buy fills with 0% measured slippage; $5,000 costs 0.0154%; $20,000 costs 0.0185% with the book nowhere near exhausted (checked live, 2026-11-20: spread about 2.5 bps on a $65 mid, $636M of 24h volume, $18.7M open interest). The desk's actual size at a 5% equity share and a 2-6% stop would be a few hundred dollars of notional -- a rounding error in this book, the same conclusion the S&P doc reached about its own tiny size.

## Funding

+0.15 bps/hour at the mean over the sample's full history (600 days of hourly rows, far longer than the 1h candle window), positive 92.2% of the time, +13.2%/yr annualized over the whole history and +11.8%/yr over the last 30 days. A long pays this; a short earns it. At a typical 1.5-2% stop this is under 0.01R per hour held -- small next to the rule's own edge, and not a reason to prefer either side.

## The operator's own HYPE position

The operator holds a hand-placed HYPE short (-16.61 @ 65.6222, 10x cross, a reduce-only take-profit resting for 7.53 @ 64.62, no stop; confirmed live, read only, via `get_account(detail=full)` and `get_positions`, 2026-11-20). Hyperliquid nets one position per coin per account, so a desk entry would merge into it, and a desk order placed while only the TP order rests would otherwise cancel it first (`bracket.place_bracket` clears a coin's stranded triggers before an entry).

The rule, in code: on HYPE any position or resting order the desk did not place is the operator's (`pathiel/agents/ownership.py`: no pathiel cloid on the opening fills or the order, no journal entry). While his position or his TP order exists:

- `scripts/desk_slot.py --desk hype` answers `"slot": "operator"`, `"reason": "operator position open on HYPE"`. The tick logs `action=HOLD` with that reason and stops before the brief.
- `place_bracket` (any mode, dry run included), `sweep_brackets` and `set_leverage` refuse on HYPE. `close_position`, `set_stop`, `set_target` and `cancel_order` already refused a position or order the desk did not place.
- His resting take-profit (7.53 @ 64.62, reduce-only) is read as his, exactly like the position: a reduce-only flag alone never implies desk ownership. The desk never cancels it, never sweeps it, never folds it into anything.
- A read that fails counts as his. The desk holds rather than guess.

The desk trades HYPE again on the first tick after his position and orders are gone. `operator_override: true` in a call is the one way through, and a tick never passes it. Gate test: `tests/test_hype_operator_guard.py` (the exact position and TP order above).

His position has no stop. It is cross-margined, short, 10x; its liquidation sits at 68.2033 (upside) per the account read above -- free margin elsewhere on the account moves that level. The desk will not put a stop on it.

## Config to apply

The desk ships inactive: `/desk-tick hype` logs `SKIP desk not active` until this is saved. One call (`update_config`, or the same keys in `.agent-config.json`):

    update_config {
      "active_desks": ["oil", "hype"],
      "desk_equity_share": {"oil": 0.90, "metals": 0.05, "hype": 0.05}
    }

Oil stays 90%. The inactive metals desk goes from 10% to 5% so the three add up to 1.0 if it comes back -- the same accounting move the sp500 desk made. If the sp500 desk is active at the same time, the three (oil, sp500, hype) need a share each that sums to 1.0; a reasonable split keeping oil dominant: `{"oil": 0.85, "sp500": 0.05, "hype": 0.10}` or similar -- the operator's call, not this doc's. Nothing else is needed: the 10x cap is the code default for this desk. To run it lower without a code change:

    update_config {"desk_max_leverage": {"hype": 5}}

`update_config` refuses a cap outside 1 to 50. With a lower cap the rule still refuses any stop that does not fit that leverage.

Then start the tick: `/loop 15m /desk-tick hype`, or rerun `/desk-loops` (it now creates the hype cron, `12,27,42,57 * * * *`). Restart the MCP server and `scripts/trigger_watch.py` first so they load the new desk.

## What changed in the code

- **Desk** (`services/strategies/desks/hype.py`): one instrument, HYPE. News queries on the protocol itself (buybacks, HLP vault, exploits), crypto regulation, and bitcoin/crypto risk appetite. `token_unlock` turns on automatically (`desk_rules.is_crypto_desk`, keyed off `pathiel/agents/unlock_map.json`'s "Hyperliquid": "HYPE").
- **Rule** (`services/strategies/next_trade.py`): `HYPE_RULE`, read by `stop_buffer_for`, `target_offset_frac_for`, `leverage_fit_for` the same way SP500_RULE is. New: `TREND_GATE_SPAN_BY_COIN`/`trend_gate_span_for`, and `next_trade()` grew two optional parameters (`closes_1h`, `trend_gate_span`) that apply the existing `trend_gate` veto next to the entry delay -- every other coin passes neither and is unaffected.
- **Levels** (`services/strategies/levels.py`, `scripts/oil_levels.py`): `summarize()` now carries `closes_1h` (the closed 1h closes the trend gate reads); the CLI widens its 1h fetch window for a coin with a trend-gate span and defaults `HYPE` to its own venue max/desk cap (10x), the same way it already does for `xyz:SP500` (50x).
- **Leverage cap per desk** (`services/strategies/desk_leverage.py`): `DESK_MAX_LEVERAGE["hype"] = 10.0`. Every other coin is unchanged.
- **Operator guard** (`pathiel/agents/ownership.py`): `OPERATOR_GUARD_COINS` adds `"HYPE"`.
- **Events** (`services/intel/scheduled_events.py`): coin group `hype`. FOMC and CPI block through `GENERATED_BLOCKING`, same as every desk; no macro-kind mapping (HYPE's own calendar is the protocol's, not the US macro print calendar).
- **Headlines** (`services/intel/headline_watch.py`): group `hype` with its own patterns (protocol actions, an exploit/hack, a token unlock, exchange competition, crypto regulation, a bitcoin ETF/liquidation shock), direction words, and price thresholds measured on HYPE's own candles (2.8% shock / 3.5% drift -- about 4x the S&P's, because HYPE is a much louder coin; borrowing the S&P's 0.7% would fire on roughly 1 in 25 bars).
- **Tick** (`.claude/agents/desk-tick.md`, `.claude/skills/desk-tick/SKILL.md`, the plugin copies): the operator slot, the coin, the levels command, the rule, the trend-gate veto, no weekend script, FOMC/CPI block with no jobs-report mapping.

## Where it breaks

- The thesis is wrong more often than a coin flip. The trend veto has nothing of its own to say about direction beyond "not against the last five days" -- it filters bad counter-trend entries, it does not pick sides.
- The regime. The backtest covers 207 days with one big rally and one real drawdown; the trend rule's weakest quarter (the first, before the rally properly started) made -2.98R, not a blowup, but a reminder that a long, range-bound chop with no clean trend either way is the scenario this rule has not been tested through.
- Fees. If the main-dex fee tier rises above the stress row's 6bps, or HYPE loses its current average cost structure, the rule's edge (about 0.29R a trade) thins further.
- A bar that runs through the stop. The 10x budget is comfortable at the median but about 6% of hours exceed it; a surprise (an exploit, a delisting threat) moves price further than any stop in minutes.
- The ownership read. If the venue stops echoing cloids and the journal is lost, the desk treats its own position as the operator's and holds. It fails safe, and it stops managing.
- `risk_per_trade_pct` set for oil's range. At 6% the backtest's worst drawdown was 55% of the slice -- too large for a 29-37% win-rate rule. Lower it before switching this on.

## Before it is switched on

Not a blocker in code, a recommendation: run the desk on a thesis for two weeks on paper (`refresh_desk hype`, grade the calls) or at the venue minimum, at 2-3% risk per trade, not 6%, and read the review before giving it 5% of equity. The backtest says the gated rule pays; nothing here has graded a single real HYPE thesis yet.
