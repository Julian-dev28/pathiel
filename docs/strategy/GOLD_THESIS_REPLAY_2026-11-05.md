# Gold thesis replay, 2026-11-05

Operator: "replay gold". The gate set in `GOLD_STRATEGY_TEST_2026-11-05.md`
before gold could go live at 10% of equity: the formula lost without a
thesis, so it had to be tested with one.

## Method

- **Theses:** 30, one a day at 16:00Z from 2026-10-06 to 2026-11-04
  (`services.thesis.evals.replay --desk metals --primary xyz:GOLD --days 30
  --model opus`). Opus, as in the silver replay, because the harness keeps
  Fable's meter for live rewrites. 50 model calls including validator
  repair rounds; reported cost $5.16.
- **Saved:** `~/Documents/code/pathiel-replays/metals-gold30/`.
- **No lookahead:** `replay.truncating_fetcher` returns only bars closed by
  each replay time (`services/thesis/tests/test_replay.py`). Spot checks:
  the theses quote the as-of 24h high and low.
- **Known leaks:**
  - the daily snapshot mid lags up to 16h;
  - today's funding and open interest stand in for every day;
  - price data only, no news.
- **Backtest:** `services/strategies/evals/gold_thesis_replay_test.py` on
  candle snapshot `20261105T153000Z-gold`. Live gold params from $64 at the
  **6% risk cap**, no-break on. Variants: floor 0.6 / 0.8 / 1.0% x leverage
  fit 20/15 vs fixed 20.
- **Pass rule, written before the run:** the live variant on the replay must
  be net positive AND beat always_long, always_short and daily_sign on the
  same window AND keep max drawdown under 35%.

## Results

| Dataset | Floor 0.6 fit | Floor 0.8 fixed 20 | Floor 1.0 fit |
|---|---|---|---|
| **Replayed theses** | -$15.47 (23 trades, 17% win) | **-$15.92 (17, 6%)** | -$12.80 (14, 7%) |
| always_long | -$4.37 | +$9.53 | -$0.18 |
| always_short | +$0.53 | -$4.89 | -$10.14 |
| daily_sign | +$0.53 | -$4.89 | -$10.14 |
| journaled gold thesis | -$1.13 | $0.00 (0 trades) | $0.00 |
| buy and hold (1x) | -$0.90 (-1.4%) | | |

- **Theses:** all 30 called short.
- **Direction hit rate:** 57% at 1 day, 60% at 3 days, 67% at 7 days.

## Verdict

**Do not activate.** It fails the pass rule on two counts: net is not
positive (-$15.92, -24.9%), and it beats none of the controls. Drawdown, at
26.7%, was the only check it passed.

The thesis read the direction right more often than not (67% at 7 days),
but trades lost anyway. Gold's daily range is about 1.35%, against 3.0% for
Brent. With the stop beyond the 24h edge and the target at the far edge,
noise reaches the stop before the move reaches the target: 6% of trades
won.

Gold needs different mechanics, such as a target set at a multiple of the
stop. Our formula's edge is a volatility edge, and gold doesn't have the
volatility. Not pursued without a new test.

## Caveats

- One regime: gold fell about 4% in the window.
- 30 cold daily calls with no news.
- Opus rather than the live writer's Fable.
