# Gold desk (built 2026-11-05, inactive until the operator flips it)

Operator, 2026-11-05: "stop the silver tick, and silver market, add gold
tick and gold market, same formula, headlines, thesis, price action, high
lows, etc, the things that have been giving us success, use your gut to get
us into profit, allocate 10% of our equity". Silver (live 2026-10-31 to
11-05) went 0 for 5: every trade a short sold past the 24h edge, every one
stopped, -$14.41 (`SILVER_DESK.md`).

The metals desk (`services/strategies/desks/metals.py`, desk id `metals`)
now has xyz:GOLD as its primary and xyz:SILVER as the secondary for the
gold-silver ratio. The tick never trades silver: a thesis on silver is
"instrument not primary, rewrite first" (`scripts/thesis_due.py`), and the
price watcher never wakes the desk on it.

## The test first

`GOLD_STRATEGY_TEST_2026-11-05.md`: the live rule on gold's own candles
(52 days of 15m bars, 183 days of 1h bars, nine no-thesis control windows,
the one journaled gold thesis). **Verdict: the formula without a thesis
loses on gold in 7 of 9 windows and in every hourly window; no variant
clears the adopt bar. Recommendation: do not activate on this evidence.
Run a gold thesis replay (the test silver got before it went live) first.**
The test did settle the rule's shape: stop floor 0.8% (plateau 0.6 to 0.8%,
best at 0.8%, lowest drawdowns), leverage fit 20/15 (never hurt, kept the
stop inside half the liquidation distance), no entry after a break, never
chase.

## The setup

| Item | Oil | Gold |
|---|---|---|
| Desk id | `oil` | `metals` (gold primary since 2026-11-05, silver for the ratio) |
| Coin | xyz:BRENTOIL | xyz:GOLD (venue max 25x) |
| Equity share | 90% | 10% (when active) |
| Leverage | 20x, 15x when the stop is too far for 20x | same (`next_trade.LEVERAGE_FIT_COINS`) |
| Risk per trade | a stop loses at most 15% of the desk | same |
| Stop | 0.75 beyond the 24h edge | the larger of 1.3 x 1h ATR14 and 0.8% of the mid, beyond the 24h edge (`next_trade.stop_buffer_for`; on gold the floor binds nearly always: 1.3 x ATR is about 0.34% of price) |
| Entry, target, gate | market off the 24h range, far 24h edge minus 0.5%, 2R; never chase; no entry past the 24h edge | same |
| Ticks | `/desk-tick oil` every 15 min | `/desk-tick metals` every 15 min (`--max-lev 25` on the levels script) |
| Price watcher | `scripts/trigger_watch.py` every 1 min | same; headline group `metals`, price shock 0.7% in 15 min |
| Events | EIA blackout, impact-3 blackout | impact-3 blackout: FOMC, CPI, the midterms, the Iran-talks row; jobs, PCE, Fed minutes, China data, summits as catalysts |

Config to apply when the operator activates it (`.agent-config.json`
through the `config` tool; the desk ships inactive):

    "active_desks": ["oil", "metals"],
    "desk_equity_share": {"oil": 0.9, "metals": 0.1},
    "coin_allowlist": ["xyz:BRENTOIL", "xyz:CL", "xyz:GOLD"]

Today's live config (operator, 2026-11-05) is `active_desks: ["oil"]`,
`desk_equity_share: {"oil": 0.9}`, allowlist Brent and WTI: the metals
desk ticks `SKIP desk not active` until the lines above are applied.

## What changed for gold

- **Thesis and intel** (`desks/metals.py`): news queries on the gold price,
  central bank buying, ETF flows, real yields, the dollar, the Fed, Iran as
  the safe-haven driver, China's imports; policy topics add tariffs on
  metals; prediction markets add "gold price" and "recession";
  `catalyst_kinds` are real generator kinds now (`fomc`, `cpi`, `cftc_cot`;
  the old `cpi_release`/`nfp_release` matched nothing). The CFTC COT source
  already maps the Gold label to COMEX gold (088691).
- **Events** (`services/intel/scheduled_events.py`): the coin group is
  `metals` = {xyz:SILVER, xyz:GOLD}; `silver` stays as an alias so the data
  file's rows and the 12-hour scan's output are unchanged. FOMC and CPI
  (generated) block gold; the Iran-talks and election rows (impact 3)
  block gold; jobs, PPI, PCE, Fed minutes, China data, summits, holidays
  reach the thesis as catalysts and the brief's "Events next 72h" line.
- **Headlines** (`services/intel/headline_watch.py`): group `metals` (was
  `silver`): Fed/Powell surprises, tariffs on gold/bullion/metals, Trump on
  the Fed, plus central bank gold buying, Iran/Hormuz escalation (the
  safe-haven bid, the oil pattern) and a yields/dollar shock; Trump posts on
  the Fed, metals tariffs and Iran; Polymarket odds on the Fed, gold, Iran;
  price shock 0.7% in 15 minutes (4 prints a month on gold's last 30 days).
- **The rule** (`services/strategies/next_trade.py`): `GOLD`,
  `GOLD_STOP_BUFFER_FLOOR_FRAC` 0.8% (`stop_buffer_floor_frac_for`), gold in
  `LEVERAGE_FIT_COINS`. `scripts/oil_levels.py --coin xyz:GOLD --next SIDE
  --thesis-entry ENTRY --max-lev 25` answers the tick.
- **Rewrite gate** (`scripts/thesis_due.py`): a thesis with a side on a coin
  that is not the desk's primary is due at once, past every gap.
- **Backtest baseline** (`services/strategies/evals/live_params.
  live_gold_params`): silver's shape plus gold's floor and the 20/15 fit,
  read from `next_trade` at call time, pinned to the test's chosen variant.
- **Review** (`services/review`, `evals/loss_audit.py`): xyz:GOLD is a
  metals coin; a metals thesis is judged on its own instrument's bars.
- **Agent text**: `.claude/agents/desk-tick.md` (and the plugin copies,
  `scripts/build_plugin_skill.py`) say gold where they said silver.

## Size

10% of equity is the desk's slice; a stop costs at most 15% of it, so about
1.5% of account equity per losing trade. At the 0.8% floor the rule
qualifies an entry on about one day in four (a 24h range of 2.1% or more),
4 to 8 trades a month in the test windows.

## Risks

- The formula has no standalone edge on gold (the test's controls lose in
  every direction); the desk's return is the thesis's direction call, and
  the only gold thesis on record (short 4028, 2026-10-23) did not pay.
- Gold's history on the venue is short on 15m bars (52 days) and one regime
  (gold -12% over six months). A rising gold market is untested.
- Two tick loops and two thesis writers again: gold's rewrites run on the
  live thesis model (Fable), the same weekly meter oil uses.
- Gold at $4100 with a 0.8% floor is a $33 stop beyond the edge; a wide 24h
  range puts the stop over 1.5% away and the fit drops the entry to 15x.

## Decision log

| Date | Change tested | Verdict | Evidence |
|---|---|---|---|
| 2026-11-05 | Gold as the metals primary: stop floor 0 / 0.3 / 0.4 / 0.5 / 0.6 / 0.8 / 1.0% x leverage fixed 20 / fit 20/15 on xyz:GOLD's own candles; 9 no-thesis control windows (15m and hourly), the one journaled gold thesis | **Built, inactive. Do not activate on this evidence**: every variant loses on 7 of 9 controls, nothing clears the adopt bar. Rule shape if activated: floor 0.8% (plateau 0.6 to 0.8%, best drawdowns), fit 20/15, no-break, never chase. Gate: a gold thesis replay | `GOLD_STRATEGY_TEST_2026-11-05.md` |
