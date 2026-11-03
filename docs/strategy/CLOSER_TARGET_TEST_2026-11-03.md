# Closer target backtest, 2026-11-03

**Verdict: keep the live far-edge target. No closer target beats it.** Every
fixed-R target (4R to 10R), every wider percentage offset (2% to 4%), the
24h range midpoint and its 75% point all lose money or barely tie live on
the real thesis sets and the 30-day controls. The only variant that ever
matches live is the loosest cap (`target_cap_frac` 5%, 4%), and only
because it almost never binds — it is not a different strategy, it is live
with a rare safety ceiling. A tighter cap, or any closer rule that actually
changes behaviour, costs money.

Operator, 2026-11-03, on the open long of 38.05 xyz:BRENTOIL from 103.61
(stop 102.99, risk $23.59, target 111.77, about 13R): "whats the realistic
high? ... test it". An empirical check over 208 days (setups where price
sat in the bottom 35% of its 24h range) found the far-edge target fills
first within 72h only 15% of the time, against 58% stop-first, and the
median 72h high is only +3.3%. That reads like the target is unrealistic.
It backtests as the opposite: the rare big win it does occasionally pay
more than makes up for the many trades that never reach it, and a closer
target cuts the tail off without cutting the losses.

## Results

$64 start, live rules otherwise (market entry off the 24h range, 2R gate,
stop 0.75 beyond the edge, limit target, 20x, 15-minute ticks, 15% risk
cap, size-aware slippage and funding on Brent).

| Dataset | Variant | Net $ | Per-day % | Win rate | Max DD | Trades |
|---|---|---|---|---|---|---|
| brent_journal (live oil journal) | **live** | **+394.32** | **13.09%** | 58% | 41.0% | 12 |
| brent_journal | tr7 (~7R, "around 108") | -8.85 | -0.92% | 31% | 53.0% | 16 |
| brent_journal | off_3 (3% inside edge) | +48.79 | 3.60% | 100% | 16.2% | 1 |
| brent_journal | rtarget_mid (range midpoint) | +160.96 | 8.17% | 100% | 16.2% | 3 |
| brent_journal | cap_5 (5% cap) | +394.32 | 13.09% | 58% | 41.0% | 12 |
| brent_replay30 (30 replayed theses) | **live** | **+57.78** | **2.17%** | 45% | 66.1% | 38 |
| brent_replay30 | tr7 | -50.05 | -4.95% | 23% | 90.5% | 52 |
| brent_replay30 | off_3 | -2.29 | -0.12% | 60% | 16.6% | 5 |
| brent_replay30 | rtarget_mid | +1.60 | 0.08% | 46% | 40.9% | 13 |
| brent_replay30 | cap_5 | +57.78 | 2.17% | 45% | 66.1% | 38 |
| brent_always_long_30d (control) | **live** | **+179.55** | **4.56%** | 43% | 78.3% | 30 |
| brent_always_long_30d | tr7 | -53.77 | -5.93% | 14% | 89.4% | 21 |
| brent_always_long_30d | cap_5 | +179.55 | 4.56% | 43% | 78.3% | 30 |

Score across all 8 Brent datasets (journal, replay30, and always-long /
always-short / daily-sign on both the replay window and the last 30 days):
no variant beats live on more than 2 of 8. The best geo-mean (final equity
over live's) is `cap_4` at 1.016 and `cap_5` at 1.001 — both within
rounding of a tie, not a win. Every `target_r` variant (tr4 to tr10) has a
geo-mean under 0.23: on this sample a fixed-R target is a clear loser, not
a plateau of neighbouring near-misses.

Silver secondary check (top 3 Brent variants — cap_5, cap_3.5, cap_3 — on
silver's own live rule, `SILVER_LIVE`): cap_5 and cap_3.5 tie live on every
one of the 8 silver datasets (the cap never binds tight enough to change a
silver trade); cap_3 loses on 5 of 8 where it does bind. Nothing here
changes the Brent verdict.

### Current (open) trade regression — long from 103.61

Real structural levels at the entry tick (closed 1h bars only): 24h low
103.74, 24h high 112.33 (the live stop, 103.74 - 0.75 = 102.99, matches
exactly). Brent's high since entry, fetched fresh: **105.51**.

| Variant | Target | % from entry | R multiple | Already filled? |
|---|---|---|---|---|
| live (far edge, 0.5% in) | 111.77 | +7.87% | 13.2R | No |
| tr7 (the operator's "~108") | 107.95 | +4.19% | 7.0R | No |
| off_3 (3% inside edge) | 108.96 | +5.16% | 8.6R | No |
| cap_2 (2% cap) | 105.68 | +2.00% | 3.3R | No |
| cap_3 (3% cap) | 106.72 | +3.00% | 5.0R | No |
| rtarget_mid (range midpoint) | 108.04 | +4.27% | 7.1R | No |
| rtarget_p75 (75% of range) | 110.18 | +6.34% | 10.6R | No |

Not one of these, including the tightest 2% cap, has filled yet. Brent's
+1.84% move off the entry so far is still short of even the closest target
tested. This matches the backtest: a closer target would not have banked
this trade any sooner than the far edge has so far, and the historical
sample says that on balance it is the far edge, when it does hit, that pays
for itself.

## Why the far edge wins despite rarely filling

The empirical numbers in the operator's ask are about this one setup
class (bottom 35% of the 24h range); the backtest runs every entry the live
rule actually takes, thesis and control alike, over 30 to 50+ days. Two
things happen when the target is pulled in:

1. **More round trips.** `reentry_after_target=True` (the live rule) means
   a filled target immediately re-opens the search for a new entry. A
   closer target fills more often, which in a choppy or declining market
   (most of the 30-day replay and the recent controls) means more entries
   into conditions that go on to stop out. `tr7`'s trade count jumps from
   live's 38 to 52 on `brent_replay30`, with the win rate dropping and the
   drawdown growing.
2. **The tail pays.** On `brent_journal`, live's 12 trades include at least
   one that ran close to the far edge; cutting that trade off at 7R or 3%
   removes most of its P&L while leaving every stop-out unchanged. The
   geo-mean ratios show this cleanly: every `target_r` variant is a loser
   by a wide margin, not a close miss.

The cap (`target_cap_frac`) is the gentlest version of this and it shows
the same pattern in miniature: a 5% cap matches live byte-for-byte on most
datasets (it never binds), a 4% cap gives up a hair, and tighter caps give
up more, in proportion to how often they actually change the target.
There is no point on this grid where pulling the target in helps.

## Method

New: `Params.target_cap_frac` (desk_tick_sim.py) caps whichever target rule
is in force (far edge, `target_offset_frac`, `target_r`, or
`target_offset_range_frac`) at `entry +/- target_cap_frac x entry`,
whichever is nearer to the entry — "the nearer of (live target, entry +/-
X%)" from the ask. Default `None`, off; applied only inside `_setup` (the
live structural entry), not pullback or flip entries. Pure helper
`cap_target`. The 24h range midpoint and 75% point needed no new field:
`target_offset_range_frac` (already in desk_tick_sim from
OIL_REGIME_TEST_2026-11-02.md) at 0.5 / 0.25 is exactly that.

Harness: `services/strategies/evals/closer_target_test.py`, modelled on
`brent_stop_buffer_test.py` (reuses its scoring, plateau and verdict
helpers directly) and `oil_regime_test.py` / `pullback_correlation_test.py`
for the dataset and regression patterns. Datasets: the live oil journal
(read only), the 30 replayed Brent theses, always-long / always-short /
daily-sign on the replay window and the last 30 days, and a silver
secondary check with silver's own live rule (`SILVER_LIVE`,
`BRENT_STOP_BUFFER_TEST_2026-11-02.md`). Adopt bar: beats live on most
datasets with a geo-mean of 1 or more, a plateau across neighbouring
settings (not a single spike), drawdown no worse, stop inside half the way
to liquidation — the same bar every 2026-11-02 test used. None of the 17
variants (6 target_r, 3 offset, 6 cap, 2 range-fraction) clears it.

Gate tests: `services/strategies/tests/test_closer_target_sim.py` (the
`target_cap_frac` field and `cap_target`/`_setup` pure logic, default-off
byte-identical check) and `test_closer_target_eval.py` (the variant grid,
`per_day_pct`, the summary CSV rows, and the current-trade regression,
including a causality check that bars after the entry tick cannot change
its levels). Both synthetic, no network, under the gate-test budget.

Reproduce:

    python -m services.strategies.evals.closer_target_test \
        --oil-theses-dir <pathia>/.state/strategies/oil/theses \
        --metals-theses-dir <pathia>/.state/strategies/metals/theses

Writes `/tmp/closer-target/report.json` and `summary.csv`.

## Risks and caveats

- **Sample overlap.** `brent_replay30` and the 30-day controls cover the
  same recent, mostly-down Brent stretch as several other 2026-11-02 tests
  (`BRENT_STOP_BUFFER_TEST`, `OIL_REGIME_TEST`). A regime where live itself
  is losing money makes "sitting out" and "do less" look artificially
  strong; this is the same caveat those tests carry, not a new one. The
  `brent_journal` thesis set (the live desk's own 12 trades) is independent
  evidence pointing the same way (+13.1%/day live versus every closer
  target losing or barely tying).
- **The empirical ask and the backtest answer different questions.** "How
  often does the far edge fill in 72h" (15%) is about one entry; "does a
  closer target make more money over many entries" is about the whole
  rule, including how often a filled target reopens a new, worse trade.
  Both numbers are true; only the second is actionable for a live rule
  change.
- **`target_cap_frac` is net-new and used nowhere live.** It is off by
  default and every existing backtest result stays byte-identical
  (`test_closer_target_sim.py`); it only changes behaviour where an eval
  explicitly sets it, as here.
- **No change to the live rule.** `bracket.py` and
  `services/strategies/next_trade.py` (`TARGET_OFFSET_FRAC`,
  `DEFAULT_MIN_R`) are untouched. This test does not ship anything; it is
  evidence for the operator's "test it".
