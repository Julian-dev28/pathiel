# Silver stop floor test, 2026-11-02

Operator, 2026-11-02 ("yes"). The stop-cap test (`STOP_CAP_TEST_2026-11-02.md`)
found the live silver stop is not the one the 30-day silver test validated.
The test ran a fixed 0.41 buffer (1.3 x silver's mean 1h ATR14). Live runs
1.3 x the 1h ATR14 at each tick (`next_trade.stop_buffer_for`): a median
of 0.11 and never above 0.37 from 10-31 to 11-02. Should it get a floor?

## Verdict

**Adopt a floor of 0.8% of the mid**: silver buffer = max(1.3 x ATR14_1h,
0.008 x mid). At silver 50 that is 0.40, the buffer the 30-day test ran.

- It beat the live rule on all 8 silver datasets. On the 30 replayed theses
  $64 became $253 instead of $143. Drawdown 32% instead of 46%. 12 trades
  instead of 22, 58% win instead of 36%.
- On the live journal (10-31 to 11-02 06:45Z) it skips both stopped shorts
  of 2026-11-02 and opens one short at 50.31. $66.15 vs $54.61.
- It works mostly through the 2R gate, not by surviving stops. With a 0.1
  to 0.2 buffer, entries in the middle of the range pass 2R and get stopped
  by noise. With the floor they fail 2R and the desk waits. Of the live
  rule's 22 entries on the replay, the floor refuses 9: 8 of them were
  live stop-outs and 1 was a small win. It turns 1 stop into a target and
  no win into a stop.
- The result is a plateau, not a spike: $0.41 to $0.50 and 0.7% to 0.8% all
  land between $252 and $268 on the replay. A dollar floor of 0.45 ties it
  (score 3.760 vs 3.755 over all 8 sets). The percent floor wins the tie
  because it scales with price.
- Just raising the multiplier does not do it: 1.6 x ATR made $208, 2.0 x
  made $105. On a quiet tick 2 x a tiny ATR is still tiny; on a busy tick
  it gets too wide for 2R.
- Brent is unchanged: with every floor set, the Brent journal, the 30
  replayed Brent theses and 3 Brent controls match trade for trade.

## Results

$64 start, live rules (market entry off the 24h range, 2R gate, target 0.5%
inside the 24h edge, limit target, 20x on a 25x coin, 15-minute ticks).
Final equity; for the 30 theses also max drawdown, trades, win rate, avg R.

| Dataset | Live: 1.3 x ATR | Floor 0.8% of mid | Floor $0.45 | Floor $0.41 | Fixed 0.41 (30-day test) |
|---|---|---|---|---|---|
| Silver, 30 replayed theses | $142.97, DD 46%, 22, 36%, -0.01R | **$253.36**, DD 32%, 12, 58%, 0.94R | $268.36, DD 31%, 12, 58%, 0.99R | $264.75, DD 34%, 13, 62%, 0.92R | $173.95, DD 38%, 13, 54%, 0.81R |
| Silver live journal (4 theses, 10-31 to 11-02) | $54.61: 2 stops, short open | **$66.15**: 0 stops, short open | $69.89 | $66.15 | $66.15 |
| Always short, replay window | $71.89 | **$662.68** | $685.44 | $298.21 | $184.44 |
| Always long, replay window | $0.59 | **$0.72** | $0.61 | $0.50 | $0.61 |
| Always short, last 30 days | $42.21 | **$509.35** | $556.60 | $229.21 | $227.71 |
| Always long, last 30 days | $0.55 | **$0.67** | $0.56 | $0.47 | $0.64 |

Daily sign equals always short on both windows (silver fell the whole
time), so its rows are the same. Days with 2 or more stops on the 30
theses: 4 live, 0 with any floor of 0.41 or more.

The curve on the 30 replayed theses (final equity):

| Floor | 0.25 | 0.30 | 0.35 | 0.41 | 0.45 | 0.50 | 0.60 |
|---|---|---|---|---|---|---|---|
| Dollars | $199 | $244 | $226 | $265 | $268 | $252 | $181 |

| Floor | 0.5% | 0.6% | 0.7% | 0.8% | 0.9% | 1.0% |
|---|---|---|---|---|---|---|
| Percent of mid | $212 | $222 | $259 | $253 | $207 | $180 |

Past about 0.5 / 0.9% the wider stop fails 2R too often and trades dry up.
A one-off probe at $1.00 or 1.5% took no trade at all: every entry failed
the 20x line (the stop may use half the distance to liquidation, about 0.75
at silver 50 and 20x on a 25x coin).

### What the floor blocked

Floor 0.8% of mid, 30 replayed theses. "Ticks" counts every 15-minute tick
the floored stop failed a gate the live stop passed; consecutive ticks on
one setup are one episode.

| Gate | Ticks | Episodes | Live entries refused | What they did under the live stop |
|---|---|---|---|---|
| 2R | 21 | 10 | 8 | 8 stops |
| 20x line | 1 | 1 | 1 | 1 win, +3.7% of equity (10-07 long, invalidation exit) |

With the 0.8% floor the 20x line refused 1 tick across all 8 datasets. It
starts to bind at 0.9% (12 ticks on each always-short control). The 2R
gate is the one that binds.

### 2026-11-02

| | Live (sim) | Floor 0.8% |
|---|---|---|
| 00:15Z short 49.88 | stop 50.10, stopped 01:00Z | refused: pays under 2R with stop 50.29 |
| 01:15Z short 50.17 | stop 50.41, stopped 05:30Z | refused: pays under 2R with stop 50.59 |
| about 05:30Z | short 50.46, open, +$5.05 | short 50.31, open, +$2.72 |

The live desk took the same two stops (-$1.82, -$2.56) and a third short
at 05:35Z.

## Caveats

- **One regime.** Silver fell 19% over the replay window; the last-30-day
  controls cover nearly the same days. Wider stops pay in a one-way market.
  A range or an uptrend is untested. Always long goes to zero under every
  variant: no stop width saves a wrong side at 20x.
- **Bigger loss per stop.** The desk sizes by margin, not by risk, so a
  wider stop loses more per stop. 10-11 short: -5.0% of desk equity with
  the live stop, -19.6% with the floor. A floored stop at 0.8% costs at
  least 16% of the silver slice when it hits (20 x 0.8%, plus fees). Fewer stops is what pays for it.
- **Small live sample.** The journal is 4 silver theses over 2 days.
- **Compounding.** Final equity compounds, so one early trade moves the
  whole curve (0.35 below 0.30 on the replay). Read the plateau, not one
  point.
- **Attribution walk** (stop-to-win, refused entries) is prices plus fees,
  no slippage or funding, one entry at a time. Final equity is the full sim.

## The live change (not made here)

1. `services/strategies/next_trade.py`: add the constant and a `mid`
   argument. Brent returns first, so it never sees the floor.

   ```python
   ATR_BUFFER_MULT = 1.3
   STOP_BUFFER_FLOOR_FRAC = 0.008   # SILVER_STOP_FLOOR_TEST_2026-11-02.md

   def stop_buffer_for(coin, atr14, mid=None):
       if coin == BRENT:
           return BRENT_STOP_BUFFER
       if not atr14:
           return None
       buf = ATR_BUFFER_MULT * atr14
       if mid:
           buf = max(buf, STOP_BUFFER_FLOOR_FRAC * mid)
       return round(buf, 6)
   ```

2. Pass the mid at both callers: `services/strategies/api.py:328`
   (`stop_buffer_for(coin, lv.get("atr14_1h"), lv.get("mid"))`) and
   `scripts/trigger_watch.py:55` (`stop_buffer_for(coin, levels.get("atr14_1h"), mid)`).
3. The desk-tick agent computes the stop itself, so its text is the change
   that moves orders. `.claude/agents/desk-tick.md` lines 108 to 112 (and the
   copy in `agents/desk-tick.md`): "silver's stop is the larger of 1.3 x the
   levels script's `atr14_1h` and 0.8% of the mid, beyond the 24h edge
   (docs/strategy/SILVER_STOP_FLOOR_TEST_2026-11-02.md)".
4. Docs: the Stop row in `docs/strategy/SILVER_DESK.md` and the comment at
   `pathiel/static/oil_desk.js:292`.
5. Tests: a `stop_buffer_for` case (silver, ATR 0.1, mid 50 gives 0.40;
   ATR 0.5 gives 0.65; Brent gives 0.75), and the silver sim parity test
   (`test_silver_desk_parity.py`) with `stop_buffer_floor_frac=0.008`.

## How to rerun

```
.venv/bin/python -m services.strategies.evals.silver_stop_floor_test \
    --metals-theses-dir <pathia>/.state/strategies/metals/theses \
    --oil-theses-dir <pathia>/.state/strategies/oil/theses \
    --oil-replay-dir /tmp/thesis-replay-30b/oil/theses \
    --silver-theses-dir /tmp/thesis-replay-silver/theses \
    --silver-cache-dir /tmp/thesis-backtest/cache \
    --cache-dir /tmp/silver-floor/cache
```

About 5 seconds. Writes `/tmp/silver-floor/report.json` (every variant and
dataset, scores, buffer stats, the Brent proof, 2026-11-02),
`/tmp/silver-floor/summary.csv` (one row per dataset and variant) and
`/tmp/silver-floor/entries.csv` (every live entry re-judged with each
variant's stop). A new `--cache-dir` fetches fresh candles.

Sim options: `Params.stop_buffer_floor` and `stop_buffer_floor_frac` in
`services/strategies/backtest/desk_tick_sim.py`, off by default and read only
with `stop_buffer_atr` on. With them off the full `stop_cap_test` report and
its CSV are byte-identical to before (cmp). Gate tests:
`services/strategies/tests/test_silver_stop_floor.py` and
`test_silver_stop_floor_eval.py`.

No live config, order or thesis was touched. No Fable; existing replays and
the live journals only, read only.
