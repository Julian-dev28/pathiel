# Wiggle scalp backtest, 2026-11-03

**Verdict: do not adopt.** No variant, on any coin, clears the adopt bar.
Every rule that found a profit in the first half of the window either lost
money or wiped out the slice's own capital in the second half. The
random-entry control, run with the same stop, leverage, sizing and fees,
loses about as much as the real rule does on silver and WTI, which means
the in-sample "edge" there is noise, not a pattern. Fees alone eat 20% to
well over 100% of gross at this trade frequency and size. Mean reversion,
as parameterized here, never found a single combination that was
profitable even in-sample.

Operator, 2026-11-03: "can u test a strategy where we play the wiggle?
like something small like 5% of the portfolio at 20x leverage
intraday/intrahour swing trade so we can still profit in a sideways
market like this?" This tests that idea directly: a separate, small,
fast mean-reversion book next to the oil desk's own far-target fade.

## Results (best variant per coin, walk-forward: params chosen on the
first half, run unchanged on the second)

52 days of 15m candles per coin, 2026-09-12 to 2026-11-03 (the venue's
whole history for these HIP-3 markets; see "Data window" below), split
into a 26-day in-sample half and a 26-day out-of-sample half. $64 account,
5% slice, 20x, a stop may cost at most 50% of the slice's own margin.

| Coin | Best variant | In-sample net | In-sample %/day | OOS net | OOS %/day | Win rate (in/OOS) | Trades/day | Fee share of gross | Max DD on slice (in/OOS) |
|---|---|---|---|---|---|---|---|---|---|
| xyz:BRENTOIL | `rf_lb1_tf0.5_sk1.5` (fade the 1h range, target the midpoint, stop 1.5x ATR beyond the edge) | +$4.25 | +3.29% | **-$1.36** | -2.09% | 53% / 43% | 1.9 / 1.6 | 34% / n/m* | 28% / 59% |
| xyz:CL (WTI) | `rf_lb4_tf1_sk1.5` (fade the 4h range, target the opposite edge, stop 1.5x ATR) | +$12.97 | +6.41% | **-$3.95** | **-100%** | 33% / 26% | 2.9 / 4.3 | 22% / n/m* | 49% / **232%** (slice wiped out) |
| xyz:SILVER | `rf_lb4_tf1_sk0.5` (fade the 4h range, target the opposite edge, stop 0.5x ATR) | +$4.38 | +3.36% | **-$8.46** | **-100%** | 22% / 11% | 6.5 / 6.8 | 74% / 306% | 62% / **259%** (slice wiped out) |

\* "n/m" = gross out-of-sample is near zero, so fees-over-gross blows up
and isn't a meaningful ratio; see the $ figures instead.

**Random-entry control** (same stop/leverage/sizing/fees as the chosen
rule above, entries at a random bar and side at the rule's own trade
rate, 20 seeds, out-of-sample window only):

| Coin | Control mean net | Control best seed | Control worst seed | Share of seeds positive |
|---|---|---|---|---|
| xyz:BRENTOIL | -$2.17 | +$0.90 | -$4.62 | 10% |
| xyz:CL | -$4.74 | -$0.56 | -$7.48 | 0% |
| xyz:SILVER | -$8.27 | -$5.47 | -$9.52 | 0% |

Silver and WTI: the real rule's out-of-sample loss (-$3.95, -$8.46) is
about the same size as the random control's mean loss (-$4.74, -$8.27).
That is the overfitting guard doing its job: a rule that loses the same
amount a random one would isn't demonstrating an edge, it's demonstrating
the cost of trading this often, this size, at 20x.

**Mean reversion** (rolling-mean z-score fade): on every coin, the walk-
forward search picked a "variant" that never placed a single order. The
reason is structural, not a data fluke: with `z_entry=1.5`, the reward
from entry to the mean is only `1.5 x std`, and the required minimum
reward (`min_r=1.2`) needs `1.5/(z_stop-z_entry) >= 1.2`, which the
grid's `z_stop` values (3.0, 3.5) never satisfy at `z_entry=1.5`. Every
combination with `z_entry>=2.0` DOES trade -- hundreds of times over 26
days -- and loses $6 to $25 in-sample on every coin. So "do nothing" is
genuinely the best-scoring mean-reversion combination on this data; it is
not a usable strategy, just the least-bad row in a losing grid.

**Plateau check:** none of the chosen range-fade variants have a plateau
(every immediate neighbour on at least one of the three grid axes --
lookback hours, target fraction, stop x ATR -- either traded at a loss or
wasn't tested because the axis edge was reached). That is a second,
independent reason not to adopt: the in-sample numbers above are each a
lone spike, not a stable region of the grid.

**Gates, on the chosen 15m range-fade variant, on and off:**

| Coin | Gate | In-sample net (trades) | OOS net (trades) |
|---|---|---|---|
| xyz:BRENTOIL | base (both sides, no filter) | +$4.25 (49) | -$1.36 (42) |
| xyz:BRENTOIL | regime: chop only | +$2.49 (28) | -$0.92 (19) |
| xyz:BRENTOIL | thesis direction only | $0.00 (0) | -$1.14 (11) |
| xyz:CL | base | +$12.97 (76) | -$3.95 (111) |
| xyz:CL | regime: chop only | +$0.58 (66) | -$0.58 (53) |
| xyz:SILVER | base | +$4.38 (170) | -$8.46 (176) |
| xyz:SILVER | regime: chop only | +$4.54 (118) | -$5.90 (69) |
| xyz:SILVER | thesis direction only | $0.00 (0) | +$0.34 (34) |

The chop-only regime gate cuts trade count by a third to a half and
softens the out-of-sample loss everywhere, but never flips the sign on
Brent or WTI. "Trade only in the desk's own thesis direction" produced
ZERO trades in-sample on both coins that have a live journal (the
thesis's side never matched the fade's side in that half), so it could
not even be judged fairly there; its small out-of-sample numbers (-$1.14
Brent, +$0.34 silver) are too few trades over too short a window to read
as anything.

**Sizing** (5% of equity, the ask, vs. 15%, the live full-account risk cap
used elsewhere in this repo; full 52-day window, not split, same risk cap
of 50% of the slice's own margin per stop):

| Coin | 5% slice net | 5% %/day | 15% slice net | 15% %/day |
|---|---|---|---|---|
| xyz:BRENTOIL | +$2.50 | +1.11% | +$7.08 | +1.07% |
| xyz:CL | +$6.19 | +2.09% | +$14.41 | +1.77% |
| xyz:SILVER | -$3.94 | -100% (ruined) | -$12.55 | -100% (ruined) |

The dollar P&L scales with the slice (roughly 3x from 5% to 15%, since the
risk cap scales with it too); the sign of the result does not change
either way. Bigger size does not fix a rule with no edge, it just moves
more money through the same losing (or marginal) pattern.

**5m, exploratory** (only 17 days of history available after the 52-day
total is halved -- see "Data window"; 8.5 days in-sample, 8.5 out-of-
sample, too short to trust on its own):

| Coin | Variant | In-sample net | OOS net |
|---|---|---|---|
| xyz:BRENTOIL | `rf_lb4_tf1_sk1` | -$0.03 | -$5.04 (slice ruined) |
| xyz:CL | `rf_lb4_tf1_sk1.5` | +$2.06 | **+$2.60** |
| xyz:SILVER | `rf_lb2_tf0.75_sk1.5` | +$0.97 | **+$1.10** |

WTI and silver are the only out-of-sample-positive results anywhere in
this study on 5m bars. Neither has a plateau and both windows are thin
(8.5 days); read this as "worth re-running once the venue has more 5m
history for these markets," not as a result to trade on.

## Adopt bar (the operator's rule, applied literally)

Required: positive out-of-sample net after fees on Brent or WTI; beats
the random control; a plateau, not a spike; tolerable drawdown on the
slice.

| Coin | Positive OOS net | Beats control | Plateau | DD tolerable (<=50% of slice) | Adopt |
|---|---|---|---|---|---|
| xyz:BRENTOIL | No (-$1.36) | Yes (control loses more, -$2.17) | No | No (59%) | **No** |
| xyz:CL | No (-$3.95) | Yes (control loses more, -$4.74) | No | No (232%, slice wiped out) | **No** |

Brent and WTI both lose LESS than their random control out-of-sample, so
"beats control" passes on a technicality -- both the rule and the coin
flip are losing money, just not quite as fast as a random trader would.
That is not a real pass: the rule still fails "positive OOS net",
"plateau" and "drawdown tolerable" outright, and the adopt bar requires
all four. Nothing adopts.

## Data window

The venue's `candleSnapshot` caps one request at 5000 bars, so this eval
pages backward in <=5000-bar windows (`data.fetch_candles_paged_cached`,
new this test) to get the longest history available. For all three xyz
HIP-3 markets (`xyz:BRENTOIL`, `xyz:CL`, `xyz:SILVER`) that turned out to
be the SAME limit: **2026-09-12 to 2026-11-03 (about 52 days)** at 15m --
the markets themselves only went live around 2026-09-12, not a request
cap. 5m history is capped the same way; this run asked for 17 days of it
(8.5/8.5 split) to keep the exploratory 5m check inside that window
without starving the main 15m study of runtime.

## Sizing and the risk cap used

5% of equity is the ask; 15% is also run (the live `risk_per_trade_pct`
cap used elsewhere in this repo, OIL_STRATEGY.md). Within the slice, a
stop may cost at most **50% of the slice's own margin** (`risk_frac=0.5`
in `scalp_sim.Params`) -- this is a risk cap on top of leverage, separate
from `bracket.check_leverage`'s own 20x / half-the-liquidation-distance
gate, which every trade in this study still has to clear.

**A modeling caveat, found while building this:** the simulator sizes new
trades off the WHOLE account's own running equity (barely dented by a 5%
or 15% book's losses), not off a ring-fenced sub-account holding only the
slice's capital. So when a losing run's cumulative P&L on the slice
exceeds the slice's own starting capital, the sim's "slice view" of the
equity curve goes negative (drawdowns over 100%, e.g. WTI's 232% and
silver's 259% above) even though the real position sizing never actually
stopped. Those results are flagged `slice_ruined` in the report and
treated as disqualifying (the walk-forward search is not allowed to pick
a `slice_ruined` variant as a "winner," and the adopt bar requires a
tolerable drawdown). Before ever running this live as an isolated
allocation, it would need either its own real sub-account (see below) or
a hard stop in the code once the slice's own capital is gone -- this
backtest's sizing formula does not have one.

## Netting: can this coin even run as its own book on this account?

Hyperliquid nets ONE position per coin per account. This account already
runs:

- **The oil desk, on `xyz:BRENTOIL`.** A Brent scalp on the same account
  would merge straight into the oil desk's own position -- there is no
  way to hold two independent Brent positions on one account. Over the
  52-day window the oil desk itself opened 12 trades and was flat 87.7%
  of the time (read from a real replay of the oil journal over the same
  window, `scalp_sim.oil_desk_flat_at`). Gating the scalp to "only open
  while the oil desk is flat" and re-running the chosen variant on the
  full window (not re-validated out-of-sample on its own) gives +$3.01,
  49% win rate, 44% max drawdown -- mildly interesting, but this number
  was never itself walk-forward tested, so read it as a curiosity, not a
  result.
- **The metals desk, on `xyz:SILVER`.** Same problem in principle (one
  account, one position per coin); the metals desk logged zero trades on
  silver in this exact window, but that is a fact about this window, not
  a guarantee -- the desk could open a silver position on any later day
  and would then net with any live silver scalp.
- **`xyz:CL` (WTI) has no active desk on this account today.** A WTI
  scalp could run standalone, no subaccount needed -- but it still fails
  the adopt bar outright (deeply negative out-of-sample, slice wiped out).

**Bottom line on netting:** a Brent or silver scalp that is meant to run
AT THE SAME TIME as the oil or metals desk, independent of what that desk
is doing, needs its own Hyperliquid subaccount (separate margin, its own
one-position-per-coin slot). Running it on the same account only works by
gating it to the other desk's idle time, which this test shows is
possible to model but was not separately validated as profitable. None of
this changes the headline verdict: adopt nothing, on any account
structure, because the numbers themselves don't clear the bar.

## Method

- `services/strategies/backtest/scalp_sim.py`: a new, pure, deterministic
  intraday mean-reversion simulator. Two rule families (`range_fade`:
  fade the high/low of the last N hours, target a fraction of the range;
  `mean_revert`: fade a rolling mean/std z-score, target the mean). Reuses
  `pathiel.agents.bracket.check_leverage` / `check_levels` for the 20x and
  reward:risk gates and `regime.py`'s causal chop label for the optional
  gate, so this can never drift from the live 20x rule. Maker fees on
  every limit entry and target, taker fee plus slippage on every stop and
  time exit, liquidation checked every bar.
- `services/strategies/evals/scalp_test.py`: fetches the longest history
  available per coin, grid-searches both rule families on the first half
  of the window, runs the winner unchanged on the second half, checks the
  grid's plateau, runs a 20-seed random-entry control at the same trade
  rate, re-runs the chosen variant with each gate on/off and at both
  sizing options, and builds the netting gate from a real replay of the
  live journal. Writes `/tmp/scalp-test/report.json` and `summary.csv`.
- `services/strategies/tests/test_scalp_sim.py` (20 tests) and
  `test_scalp_test_eval.py` (13 tests): synthetic data, no network, no
  LLM. Cover both rule families filling and exiting at their stop/target,
  the min-R and leverage gates, risk-capped sizing, the thesis-direction
  and "other desk is flat" gates, the regime chop gate, liquidation, no
  lookahead (future bars never change a past tick's decision, and a
  tick's own bar never feeds its own structure), the random control's
  determinism, and the new paged candle fetcher (`data.py`).
- Read-only throughout: candles from the venue's public `/info` endpoint
  and the eval's own disk cache (`/tmp/scalp-test/cache`); the oil and
  metals journals are read, never written. No LLM calls. Nothing here
  touches live trading code, config, or the account.

Rerun:

```
python -m services.strategies.evals.scalp_test \
    --oil-theses-dir <pathia>/.state/strategies/oil/theses \
    --metals-theses-dir <pathia>/.state/strategies/metals/theses
```
