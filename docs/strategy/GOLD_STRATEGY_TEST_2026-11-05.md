# Gold strategy test, 2026-11-05

## Verdict

**The live formula does not make money on gold by itself. Build the desk,
keep it inactive, and gate activation on a gold thesis replay.** Every
variant of the live rule (market entry off the 24h range, stop beyond the
edge, 2R to the far edge, 15% risk cap, 20x) loses on 7 of 9 no-thesis
control windows, and on all 3 hourly 183-day windows by 50 to 60 dollars
of 64. No variant clears the adopt bar. The one journaled gold thesis
(short 4028, 2026-10-23) lost up to $14 with a tight floor and traded
nothing with the 0.8% one.

What the data does settle is the shape of the rule, if the desk goes live:

- **Stop floor 0.8% of the mid (silver's live floor), unchanged.** The floor
  grid is a clear plateau at 0.6 to 0.8%, peaking at 0.8%: summed over the
  nine controls -$159 (0.8%) vs -$184 (0.6%), -$202 (0.5%), -$247 (1.0%),
  -$355 (bare ATR). On the 15m windows 0.6% and 0.8% tie (-$20 vs -$24)
  and 0.8% does it on half the trades with the lowest drawdowns (48% worst
  vs 81%). A tighter floor is the worst row everywhere: gold's 1.3 x ATR14
  is 0.34% of price, and noise stops the tight stops out.
- **Leverage fit 20/15, kept.** It changed no trade on the six 15m windows
  (gold's range plus the floor fits 20x at the venue's 25x) and on the
  hourly windows held the worst stop at 0.433 of the way to liquidation
  where fixed 20x reached 0.506 on realized fills. On the journaled gold
  thesis it turned 91 "leverage" refusals into 8 (a 0.8% floor at $4028 is
  a $32 stop; stops over 1.5% away need 15x).
- **No entry after a break, never chase: unchanged.**

So `live_gold_params` = `live_silver_params` + leverage fit 20/15: the
same formula the operator asked for, and the one the grid prefers.

## Why not activate on this evidence

The controls are the formula with a naive direction (always long, always
short, the 20-day trend sign). On Brent the same controls were mixed and
the thesis carried the desk; on silver always_short made $327 in a month
silver fell 19%. On gold the controls lose with every direction in every
window, including always_short while gold fell 12% over six months: the
24h-range entry on gold buys dips and sells rips in a market that trends
slowly and whipsaws inside the day, and the 2R target to the far edge is
hit on 25 to 45% of entries. The desk's P&L on gold would be the thesis's
direction call, and the only gold thesis on record did not pay. Silver got
a 30-day thesis replay before it went live (SILVER_THESIS_TEST_2026-10-31.md);
gold should get the same (about 30 model calls on the operator's meter)
before 10% of equity rides on it.

If the operator activates anyway (his call, as the silver breakout was):
10% slice, 15% of the slice per stop, so about 1.5% of equity per losing
trade, 4 to 8 trades a month at the 0.8% floor.

## Results

Net $ from $64 per window, trades, win rate, max drawdown. Every variant
built on `live_params.live_silver_params` (per-tick 1.3 x ATR14 stop, 15%
risk cap, market entry off the 24h range, 2R gate, no entry after a break,
20x capped at the venue's 25x) with the floor and the leverage rule set
explicitly. `fit` = leverage fit 20/15; `fix20` = fixed 20x. The fix20 rows
are identical to fit on the 15m windows and within $2 on the hourly ones,
so the table shows fit.

### Per dataset: net $ from $64 (trades, win rate, max drawdown), leverage fit 20/15

| Dataset | Window | Gold move | floor 0.0% | floor 0.3% | floor 0.5% | floor 0.6% | floor 0.8% | floor 1.0% |
|---|---|---|---|---|---|---|---|---|
| journal_gold | 2026-10-23 to 2026-10-30 | -0.54% | -13.87 (10, 10%, 25%) | -13.87 (10, 10%, 25%) | -9.33 (8, 0%, 15%) | -2.80 (2, 0%, 5%) | +0.00 (0, -, 0%) | +0.00 (0, -, 0%) |
| always_long_30d | 2026-10-06 to 2026-11-05 | -1.16% | -42.15 (17, 18%, 80%) | -42.25 (17, 18%, 80%) | -19.29 (8, 25%, 70%) | -16.31 (8, 25%, 69%) | +19.92 (4, 50%, 32%) | -3.02 (3, 33%, 31%) |
| always_short_30d | 2026-10-06 to 2026-11-05 | -1.16% | -26.99 (13, 31%, 62%) | -29.11 (12, 25%, 64%) | -11.52 (9, 33%, 44%) | -4.90 (8, 38%, 44%) | -13.66 (4, 25%, 43%) | -23.46 (3, 0%, 42%) |
| daily_sign_30d | 2026-10-06 to 2026-11-05 | -1.16% | -30.85 (13, 23%, 66%) | -30.85 (13, 23%, 66%) | -11.52 (9, 33%, 44%) | -4.90 (8, 38%, 44%) | -13.66 (4, 25%, 43%) | -23.46 (3, 0%, 42%) |
| always_long_50d | 2026-09-14 to 2026-11-05 | -3.92% | -49.71 (29, 21%, 92%) | -49.41 (28, 21%, 92%) | -22.45 (16, 31%, 82%) | -29.19 (15, 27%, 81%) | +0.23 (8, 38%, 48%) | -20.32 (5, 20%, 54%) |
| always_short_50d | 2026-09-14 to 2026-11-05 | -3.92% | -12.84 (21, 38%, 62%) | -15.77 (20, 35%, 64%) | +4.01 (15, 40%, 44%) | +17.39 (11, 46%, 44%) | -0.55 (5, 40%, 43%) | -12.64 (4, 25%, 42%) |
| daily_sign_50d | 2026-09-14 to 2026-11-05 | -3.92% | -23.32 (22, 32%, 66%) | -23.32 (22, 32%, 66%) | +18.98 (16, 44%, 44%) | +17.39 (11, 46%, 44%) | -16.54 (5, 20%, 43%) | -24.95 (4, 0%, 48%) |
| always_long_1h_183d | 2026-05-06 to 2026-11-05 | -12.28% | -62.02 (103, 26%, 98%) | -61.30 (98, 28%, 98%) | -61.34 (80, 28%, 98%) | -61.01 (67, 27%, 97%) | -58.29 (41, 24%, 94%) | -58.73 (26, 15%, 95%) |
| always_short_1h_183d | 2026-05-06 to 2026-11-05 | -12.28% | -61.10 (87, 30%, 98%) | -61.32 (83, 29%, 98%) | -59.51 (74, 30%, 98%) | -56.33 (60, 30%, 95%) | -46.40 (39, 31%, 88%) | -45.23 (23, 26%, 85%) |
| daily_sign_1h_183d | 2026-05-06 to 2026-11-05 | -12.28% | -46.03 (69, 35%, 85%) | -46.37 (66, 35%, 86%) | -39.13 (57, 35%, 83%) | -46.70 (47, 32%, 81%) | -29.96 (30, 33%, 78%) | -36.05 (15, 20%, 64%) |

The hourly rows run the sim with one decision per 1h bar
(`Params.tick_minutes=60`), the loop the operator tried for one evening on
oil and reverted; they are here for the sample size (183 days, four times
the 15m history), not as a forecast of the 15m loop's return. Their
drawdowns (80 to 98%) are what 40 to 100 stop-outs at 15% of equity each
do to a $64 account over six months of a falling market; `liquidated` is
false in every row.

### The floor curve: sum of net $ over the nine control windows (9 x $64)

| Floor | Leverage | Net $ (9 controls) | Net $ (6 x 15m windows) | Net $ (3 x hourly 183d) | Trades | Worst DD | Positive windows (of 9) |
|---|---|---|---|---|---|---|---|
| 0.0% | fix20 | -355.06 | -185.86 | -169.20 | 374 | 98% | 0 |
| 0.0% | fit | -355.01 | -185.86 | -169.15 | 374 | 98% | 0 |
| 0.3% | fix20 | -359.75 | -190.71 | -169.04 | 359 | 98% | 0 |
| 0.3% | fit | -359.70 | -190.71 | -168.99 | 359 | 98% | 0 |
| 0.4% | fix20 | -293.73 | -135.06 | -158.67 | 327 | 98% | 0 |
| 0.4% | fit | -293.68 | -135.06 | -158.62 | 327 | 98% | 0 |
| 0.5% | fix20 | -201.83 | -41.79 | -160.04 | 284 | 98% | 2 |
| 0.5% | fit | -201.77 | -41.79 | -159.98 | 284 | 98% | 2 |
| 0.6% | fix20 | -184.42 | -20.52 | -163.90 | 235 | 97% | 2 |
| 0.6% | fit | -184.56 | -20.52 | -164.04 | 235 | 97% | 2 |
| **0.8%** | fix20 | -159.98 | -24.26 | -135.72 | 139 | 96% | 2 |
| **0.8%** | **fit** | **-158.91** | **-24.26** | **-134.65** | **140** | **94%** | **2** |
| 1.0% | fix20 | -246.74 | -107.85 | -138.89 | 86 | 95% | 0 |
| 1.0% | fit | -247.86 | -107.85 | -140.01 | 86 | 95% | 0 |

### Adopt bar, vs the baseline `floor_0.8_fit` (silver's live rule as is)

Beats the baseline on most of the 9 controls with a geo-mean ratio of 1 or
more, ends above $64 on at least half of them, a plateau on the floor grid,
max drawdown within 2 points of the baseline, every stop inside half the
liquidation distance at the trade's own leverage. Nothing passes; the
baseline itself is positive on 2 of 9.

| Variant | Beats baseline (of 9) | Geo-mean ratio | Positive (of 9) | Max DD | Max stop/liq | Plateau | Adopt |
|---|---|---|---|---|---|---|---|
| floor_0.8_fix20 | 2 | 0.970 | 2 | 95.8% | 0.506 | no | no |
| floor_0.6_fix20 | 4 | 0.783 | 2 | 97.3% | 0.506 | no | no |
| floor_0.6_fit | 4 | 0.781 | 2 | 97.3% | 0.433 | no | no |
| floor_0.5_fit | 4 | 0.733 | 2 | 97.9% | 0.433 | no | no |
| floor_1.0_fit | 1 | 0.823 | 0 | 95.0% | 0.466 | no | no |
| floor_0.4_fit | 1 | 0.638 | 0 | 97.8% | 0.433 | no | no |
| floor_0.3_fit | 0 | 0.443 | 0 | 98.4% | 0.433 | no | no |
| floor_0.0_fit | 0 | 0.437 | 0 | 98.3% | 0.433 | no | no |

The 0.506 on the fixed-20x rows is a realized overshoot (the fill's
slippage against a stop judged at the mid), the same effect
FINAL_STRATEGY_TEST_2026-11-04.md's `corrected_max_stop_liq_frac` was
written for; the fit rows stay at 0.433 because the wide stops traded at
15x.

### The journaled gold thesis

The metals journal holds one gold call: short 4028 at 2026-10-23 05:52Z
(stop 4042, targets 3988.9 / 3900), held for its 7-day horizon in the sim.
Gold closed the week -0.54%: the direction was right by a hair, the rule
lost with every tight floor (ten entries, every one stopped on the
invalidation) and took nothing at 0.8%.

| Variant | Net $ | Trades | Exits | Refusals (chase / min_r / break / leverage) |
|---|---|---|---|---|
| floor_0.0_fit | -13.87 | 10 | invalidation 10 | 477 / 152 / 5 / 0 |
| floor_0.3_fit | -13.87 | 10 | invalidation 10 | 477 / 152 / 5 / 0 |
| floor_0.5_fit | -9.33 | 8 | invalidation 8 | 477 / 161 / 8 / 0 |
| floor_0.6_fit | -2.80 | 2 | invalidation 2 | 477 / 183 / 7 / 0 |
| floor_0.8_fit | +0.00 | 0 | - | 477 / 179 / 8 / 8 |
| floor_0.8_fix20 | +0.00 | 0 | - | 477 / 96 / 8 / 91 |

477 of the 672 ticks were "chase" refusals: the mid sat under the short's
4028 entry for most of the week. One thesis is a data point, not a test;
the thesis replay above is the missing dataset.

## Gold vs silver vs Brent, the numbers behind the floor

Last 30 days of 15m bars per coin (gold snapshot 2026-10-06..11-05, the
others from the 2026-11-03 snapshot):

| | Gold | Silver | Brent |
|---|---|---|---|
| Mean 1h ATR14, % of price | 0.26% | 0.48% | 0.60% |
| 1.3 x ATR14, % of price | 0.34% | 0.63% | 0.79% |
| Median 24h range, % | 1.35% | 2.49% | 3.01% |
| 15m close moves of 0.5% or more (30 days) | 11 | 80 | 88 |
| 15m close moves of 0.7% or more | 4 | 35 | 31 |
| 15m close moves of 1.0% or more | 1 | 12 | 12 |
| p99.5 of the 15m close move | 0.47% | 0.88% | 0.94% |

Gold moves at about half silver's pace. The 0.8% floor is 60% of gold's
median 24h range, so a 2R entry needs a day whose range is 2.1% or more:
about one day in four, which is why the 0.8% rows trade 4 to 8 times a
month. The headline watcher's gold price shock is set at 0.7% in 15
minutes from the same table (4 prints a month, about silver's 1.2% rate).

## Method

One pinned snapshot (`services.strategies.backtest.snapshot`), fetched once
on 2026-11-05 15:30Z into
`~/Documents/code/pathiel-replays/candles/20261105T153000Z-gold/`:
xyz:GOLD 15m 2026-09-14 12:15Z to 11-05 15:30Z (5005 bars, the venue's
cap), 1h 2026-04-11 to 11-05 (5001 bars), 1d 240 bars, 5760 funding rows.
Venue max leverage 25 (metaAndAssetCtxs, dex xyz). Mean 1h ATR14 over the
1h history 12.39 (0.287% of price). No eval run touched the network.

Datasets: the metals journal's gold theses (READ ONLY,
`~/Documents/code/pathia/.state/strategies/metals/theses`;
`stop_cap_test.same_market` keeps the one gold call and drops the 15 silver
ones), always_long / always_short / daily_sign over the last 30 days and
the whole 15m history with 15m ticks, and the same three over the whole 1h
history with hourly ticks (after the 20-day trend sign's warm-up). Each
window reports gold's own first-to-last close move.

Variants: floor 0 / 0.3 / 0.4 / 0.5 / 0.6 / 0.8 / 1.0% of the mid x
leverage fixed 20x or fit 20/15, every one on `live_params.live_silver_params`
with both knobs set explicitly. Baseline `floor_0.8_fit`, silver's live
rule. Adopt bar as in the table above (`gold_strategy_test.adopt_verdict`).

Code: `services/strategies/evals/gold_strategy_test.py` (writes
`/tmp/gold-strategy/report.json` and `summary.csv`), gate-tested in
`services/strategies/tests/test_gold_strategy_test_eval.py` (synthetic
data, no network). The chosen rule is pinned by
`services/strategies/tests/test_gold_desk.py` (`live_gold_params` equals
the test's `floor_0.8_fit` variant; sim and live agree on 3000 random gold
markets). Re-run:

```
python -m services.strategies.backtest.snapshot --coin xyz:GOLD --days-back 240 \
    --snapshot-dir ~/Documents/code/pathiel-replays/candles/20261105T153000Z-gold   # only for fresher candles
python -m services.strategies.evals.gold_strategy_test \
    --snapshot-dir ~/Documents/code/pathiel-replays/candles/20261105T153000Z-gold
```

## Caveats

- Nine control windows from one coin over one falling half-year are one
  regime. The floor plateau is robust across them; the sign of the
  formula's edge on gold is not tested on a rising gold market.
- The 15m history is 52 days (the venue's 5000-bar cap); the hourly rows
  trade a different cadence than the live 15m loop.
- The journal dataset is one thesis. The thesis replay (the silver
  precedent) is the test this file does not contain.
