# Extended history test, 2026-11-03

**Verdict: no, not on its own — September would have lost money (-56.5%,
$64 to $27.82), and that loss is bigger than October's gain. Chained
back to back from one $64 account, the two months net -20.1% ($64 to
$51.14).** October alone is the strategy's best month tested so far
(+83.8%, 38 trades, 45% win rate, matching the already-known
`CLOSER_TARGET_TEST_2026-11-03.md` numbers for trade count and win rate
exactly; the dollar and drawdown figures differ because this run applies
the live 15% risk cap the operator adopted 2026-11-02, which that file's
own `bp` did not — see Caveats). September is new evidence, not
previously tested, and it is bad news: the live rule lost in a month that
chopped sideways (Brent buy-and-hold was -0.8% over the month) even
though the replayed thesis called the right direction most of the time
(70% of days matched the next-24h move, 73% the next-7d move). The
mechanics — a tight structural stop at 20x, re-entry after every exit —
turn a flat, choppy month into a string of stop-outs regardless of how
often the thesis itself was pointed the right way. **Leverage: 10x and
15x would have beaten 20x on September, October and the chained run**
(e.g. chained 10x nets +$43.89 against 20x's -$12.86) **but 20x still
wins on the live journal and on a pure trending control** — lower
leverage helps precisely because it shrinks every position below what
the 15% risk cap would otherwise size, cutting both the drawdown and the
upside, and chop punishes the bigger bet more than it rewards it. See
the leverage sweep section below.

Operator, 2026-11-03: "our current strategy is about a week and a half
old, can we maybe extend it backwards 30 days and see if it would have
had success?" The strategy had only been tested on the live journal
(10-18 onward, 12 trades) and one replayed month (09-30 to 10-30,
`/tmp/thesis-replay-30b`). This replays one more month further back
(08-31 to 09-29) with no lookahead and the same no-news, price-only
thesis generation, and scores the live rule on it alone, chained after
the already-tested month, and against mechanical controls.

## Results

$64 start, live rules (market entry off the 24h range, stop 0.75 beyond
structure, target 0.5% inside the far 24h edge as a limit, 2R gate, 20x,
15-minute ticks, **15% risk cap** `risk_cap_frac=0.15`, size-aware
slippage, funding on Brent).

| Window | Variant | Net $ | Return | Per-day % | Win rate | Max DD | Trades |
|---|---|---|---|---|---|---|---|
| September (08-31 to 09-30, new) | **live** | **-36.18** | **-56.5%** | -2.74% | 24% | 80.8% | 21 |
| September | always_long | +5.82 | +9.1% | 0.29% | 25% | 79.8% | 16 |
| September | always_short | +16.19 | +25.3% | 0.76% | 33% | 64.0% | 18 |
| September | daily_sign | -46.24 | -72.3% | -4.19% | 24% | 86.7% | 21 |
| September | buy & hold | -0.52 | -0.8% | - | - | - | - |
| October (09-30 to 10-30, already tested) | **live** | **+53.64** | **+83.8%** | +2.05% | 45% | 55.9% | 38 |
| October | always_long | +832.79 | +1301.2% | +9.20% | 56% | 58.0% | 25 |
| October | always_short | -55.92 | -87.4% | -6.67% | 13% | 93.4% | 23 |
| October | daily_sign | +351.33 | +548.9% | +6.43% | 54% | 71.3% | 28 |
| October | buy & hold | +9.77 | +15.3% | - | - | - | - |
| **Chained (Sep then Oct, equity carried over)** | **live** | **-12.86** | **-20.1%** | -0.37% | 37% | 80.8% | 59 |
| Live journal (10-18 to 11-03, context only, not chained) | live | +276.35 | +431.8% | +10.80% | 58% | 39.3% | 12 |

Thesis direction hit rate (does the replayed call match the move over the
next 24h / 7 days, graded the same way `grader.grade_thesis` scores a real
thesis):

| Window | 24h hit rate | 7d hit rate | n |
|---|---|---|---|
| September | 70.0% | 73.3% | 30 |
| October | 80.0% | 83.3% | 30 |

Target / stop / thesis-close breakdown (`stop` = the structural stop,
`thesis-close` = the thesis's own invalidation or a direction flip closing
the position early):

| Window | Target | Stop | Thesis-close | Trades |
|---|---|---|---|---|
| September | 2 | 11 | 8 | 21 |
| October | 6 | 3 | 29 | 38 |
| Chained | 8 | 14 | 37 | 59 |

All 30 of September's replayed theses called long (so did all 30 of
October's — the price-only replay never once called a short in either
month; see Caveats). September's own always_long control still made money
(+9.1%) over the same days the thesis-driven "live" row lost 56.5% on: the
live row is not the same bet as "be long all month." Each day's thesis
carries its own entry ceiling, stop and invalidation, and in September
those specific levels got hit repeatedly (11 stop-outs, 8 invalidations in
21 trades) even on a month the simple long control cleared for a small
profit.

## Why September lost

- **A choppy, not trending, month.** Brent ranged from about 89 up to
  105.4 (around 09-11) and back down to the mid-90s by month end, net
  -0.8%. The 20x structural stop (0.75 beyond the 24h range edge) is sized
  for a trending setup; a month that round-trips repeatedly stops it out,
  then re-enters, then stops out again. `OIL_REGIME_TEST_2026-11-02.md`
  already flagged this exact failure mode ("lately it earned in chop
  (+41.0R) and lost in straight-line down days (-17.0R)") from the
  opposite angle — September shows the mechanics can also lose in chop
  when the thesis's own stop/invalidation levels, not just the
  structural ones, get run.
- **A high direction-hit rate did not save it.** 70% of days called the
  right next-24h move, yet the P&L is deeply negative. Direction-hit
  grades the call, not the trade: a long call that is "right" by closing
  higher 24h later can still stop the live position out intraday on the
  way there, then miss the rest of the move because a fresh thesis the
  next day enters again, at a new stop. This is the same gap
  `CLOSER_TARGET_TEST_2026-11-03.md` found between "does the far edge fill
  within 72h" (an entry-level question) and "does the rule make money over
  many entries" (a whole-month question) — here it is the thesis's
  direction call and the trade's outcome that disagree, not the target.
- **October's gain does not cover September's loss.** $64 compounds to
  $27.82 by end of September; running October's own rules from $27.82
  instead of $64 only recovers to $51.14 — 20% under where it started.
  The strategy needs both months to be at least roughly breakeven to call
  it durable; one bad month at this size erases most of a very good one.

## Leverage sweep (5x, 10x, 15x, 20x)

Operator, 2026-11-03: "try with 5x 10x and 15x is it better roi?" Every
window above, plus an always-long control over the full chained span, run
at each leverage. Mode (a) keeps the live 15% risk cap on and only
changes `Params.leverage`; mode (b) is reference-only, full slice margin
x leverage with no cap. The stop-vs-liquidation gate (stop inside half
the way to the liquidation price) still applies at every leverage and
only gets looser going down from 20x, never tighter — a setup refused at
20x can pass at 15x/10x/5x, never the reverse.

Net $, mode (a), $64 start:

| Dataset | 5x | 10x | 15x | 20x (live) |
|---|---|---|---|---|
| September (new) | +1.96 | -3.05 | -18.90 | **-36.18** |
| October | +28.66 | +49.30 | **+65.31** | +53.64 |
| Chained | +31.50 | **+43.89** | +27.12 | -12.86 |
| Live journal (context) | +56.67 | +142.58 | +246.22 | **+276.35** |
| Always-long control | +143.35 | +416.24 | +711.87 | **+1,008.19** |

Max drawdown / worst single loss as a share of equity, mode (a):

| Dataset | 5x | 10x | 15x | 20x |
|---|---|---|---|---|
| September | 39.5% / -8.5% | 63.7% / -15.3% | 77.3% / -15.4% | 80.8% / -15.5% |
| October | 22.9% / -5.5% | 41.0% / -11.1% | 49.5% / -15.4% | 55.9% / -15.5% |
| Chained | 39.5% / -8.5% | 63.7% / -15.3% | 77.3% / -15.4% | 80.8% / -15.5% |
| Live journal | 12.0% / -5.0% | 22.7% / -9.9% | 32.4% / -14.9% | 39.3% / -15.6% |
| Always-long | 36.2% / -9.5% | 60.0% / -15.2% | 75.5% / -15.4% | 79.8% / -15.5% |

Trades and `leverage`-gate refusals (tick-level — a persistent condition
gets refused every 15m tick it holds, not once per missed trade), mode
(a):

| Dataset | Trades (5x/10x/15x/20x) | Refused by the leverage gate (5x/10x/15x/20x) |
|---|---|---|
| September | 23 / 23 / 23 / 21 | 0 / 0 / 48 / 145 |
| October | 40 / 40 / 40 / 38 | 0 / 0 / 50 / 244 |
| Chained | 63 / 63 / 63 / 59 | 0 / 0 / 98 / 389 |
| Live journal | 12 / 12 / 12 / 12 | 0 / 38 / 93 / 145 |
| Always-long | 42 / 42 / 42 / 42 | 0 / 577 / 1,434 / 1,764 |

**Does the 15% cap bind before leverage does?** Checked directly
(`size_changed_trades`: of the trades a leverage shares with the 20x
baseline, how many opened at a different size). On Brent's real ~0.5-1%
stop distances the cap only becomes the binding constraint around
leverage >= 15-20x (`risk_cap_frac / stop_distance_frac`); below that,
leverage itself sizes the trade. Empirically: September 20/23, 20/23,
19/23 trades changed size at 5x/10x/15x; October 35/40 at each; chained
55/63, 55/63, 54/63; live journal 12/12 at every leverage; always-long
39/42, 39/42, 37/42. In other words, on nearly every trade below 20x,
leverage — not the cap — is what actually set the size.

**Confirm or contradict `LOSS_AUDIT_2026-11-02.md`** ("flat 10x/15x lost
on both thesis sets"): **contradicted on the two new windows.** On
September, October and the chained run, 10x and 15x beat 20x in net $
(and cut drawdown and the worst single loss substantially). **Confirmed
on the live journal and the always-long control**, where return rises
monotonically with leverage in both modes. The difference is regime, not
a correction to the earlier finding: the live journal and the trending
control are both mostly-one-direction stretches where leverage pays;
September and, to a lesser extent, October chop enough that a smaller,
leverage-bound position survives more stop-outs before the losses
compound. `LOSS_AUDIT_2026-11-02.md`'s own flat-leverage rows (no risk
cap) were run on 8 datasets as of 2026-11-02 — overlapping but not
identical to this run's September/October windows — so this is new
evidence on new data, not a re-run of the same numbers.

Mode (b) (full slice, no cap) tells the same regime story, with the cap's
smoothing removed: September 20x's loss widens to -40.92 (max DD 87.3%,
worst loss -23.3%) while 5x stays +1.96 (identical to mode a — the cap
never bound there anyway); the live journal's 20x row jumps to +419.84
(cap fully off), nearly 1.5x its capped number, underscoring how much of
20x's live-journal edge the 15% cap was already quietly giving back.

## Method

`services/thesis/evals/replay.py --desk oil --days 30 --end-day
2026-09-30 --model opus`, same as-of hour (16:00 UTC) and model
(`claude-opus-5-5`, the `opus` alias) as the existing 09-30-to-10-30
replay. Wrote all 30 days (08-31 to 09-29 inclusive) to
`/tmp/thesis-replay-sep/oil/theses`, 0 skipped, 0 degraded after retry.
**396,415 tokens** (296,557 input / 99,858 output), **$3.53**, via
`services/llm`'s local-Claude-CLI shell-out — no hosted LLM API call.

**Candle-fetch fix.** `replay.py`'s `fetch_all_candles` asks for `count`
hourly bars ending at real wall-clock now; with `--end-day` pinning the
window into the past, the old formula sized `count` off `--days` alone,
which for this window (69 days before today) reached back only to
~2026-09-27 — short of 08-31 by about a month, so the earliest days in the
window would have built their analytics on zero closed 1h bars. Fixed
`fetch_all_candles` to also account for the real gap between now and the
window's start when `end` is given (`services/thesis/evals/replay.py`);
`end=None` (a window ending at real now, every run before this one)
reproduces the old formula exactly — proved in
`services/thesis/tests/test_replay.py`'s new fetch_all_candles tests. A
2-day smoke test (`--days 2 --end-day 2026-09-02`) confirmed 08-31's
thesis wrote with real structural levels before the full 30-day run.

**The backtest.** New `services/strategies/evals/extended_history_test.py`:
reads any ordered list of `--window NAME=PATH` theses directories, fetches
Brent 15m (paged — this window exceeds the venue's 5000-bar single-request
cap) / 1h / funding once, and for each window runs the live rule
(`desk_tick_sim.live_params()` + `risk_cap_frac=0.15`, reusing
`brent_stop_buffer_test.run_dataset`/`summarize` byte-for-byte) against
that window's own thesis direction and the always_long / always_short /
daily_sign controls, plus Brent buy-and-hold. Separately chains every
given window end to end through `desk_tick_sim.simulate`, carrying one
leg's final equity into the next leg's starting equity (`chain_runs`),
summarized through the same `summarize` via a synthetic `Run` of the
concatenated trades and equity curve (`summarize_chain`). The
thesis-direction hit rate recomputes `grader.grade_thesis`'s direction-hit
sign test directly off entry price and closed 1h bars, so it has no
dependency on the full `contracts.Thesis` model the reduced
`ThesisDirection` the simulator reads already strips away. The leverage
sweep (`leverage_sweep_single` / `leverage_sweep_chain`) reruns each
window/leg at `leverage` 5/10/15/20 in both sizing modes (`leverage_params`:
mode (a) keeps `risk_cap_frac=0.15`, mode (b) sets it to `None`), scoring
each through the same `summarize`, and counts size-changed trades
(`size_changed_count`, matched by `entry_time` against that sweep's own
20x row).

    python -m services.strategies.evals.extended_history_test \
        --window sep=/tmp/thesis-replay-sep/oil/theses \
        --window oct=/tmp/thesis-replay-30b/oil/theses \
        --journal-dir <pathia>/.state/strategies/oil/theses

Writes `/tmp/extended-history/report.json`, `summary.csv` and
`leverage_summary.csv`.

Gate tests: `services/thesis/tests/test_replay.py` (4 new cases: the
fetch-count fix matches the old formula when `end` is unset, extends it
when `end` is far in the past, and does not inflate it when `end` is
recent) and `services/strategies/tests/test_extended_history_eval.py` (20
cases: `--window` parsing, buy-and-hold, the direction-hit rate, the
chained run and its drawdown via an injected `simulate_fn` stub, the
summary CSV rows, and the leverage sweep — the risk-cap-vs-leverage sizing
math directly on `risk_capped_notional`, and the sweep's own wiring on the
degenerate zero-candle case through the REAL `simulate`/`chain_runs`, not
a stub). All synthetic, deterministic, no network. `python -m pytest
services/thesis services/strategies -q`: 1359 passed (the 20 new
extended-history cases and 4 new replay cases are part of that total).

## Risks and caveats

- **Price data only, no news.** Both replayed months, like the existing
  30-day replay, give the model candles and nothing else — no headlines,
  no EIA/OPEC data, no intel packet. The live desk's real thesis has that
  context every day. All 30 September calls and all 30 October calls were
  long: a price-only model reading a slow grind up (Sept) or a strong rally
  (Oct) defaults to buying the dip every single day. Whether a
  news-informed thesis would have called some of September's chop
  differently (or sat out) is untested here.
- **The 15% risk cap changes the comparison to earlier docs.**
  `CLOSER_TARGET_TEST_2026-11-03.md`'s and
  `BRENT_STOP_BUFFER_TEST_2026-11-02.md`'s own `bp` construction
  (`dataclasses.replace(base_params(), starting_equity=START)`) does not
  actually set `risk_cap_frac`, despite both files' docstrings describing
  their datasets as running under the live 15% cap. This eval sets it
  explicitly, matching the real live rule adopted 2026-11-02
  (`LOSS_AUDIT_2026-11-02.md`). That is why October's live-rule trade
  count (38) and win rate (45%) match `CLOSER_TARGET_TEST`'s numbers for
  the same window exactly, while the dollar return and max drawdown do
  not (+$53.64/55.9% DD here vs. the file's +$57.78/66.1% DD) — the cap
  changes position sizing, not which trades fire.
- **Chained equity is a thought experiment, not a replay of the live
  desk.** The live desk did not run in August or September; the chain
  answers "if the account had been exactly this size and had followed
  only these rules," not "what the account actually did." The live
  journal row is read-only context for comparison, not part of the chain.
- **The leverage sweep is also regime-dependent, same as the headline
  verdict.** Lower leverage wins on September/October/chained and loses
  on the live journal and the always-long control for the same reason the
  live rule itself lost in September: these are different market
  stretches, not a universal "lower leverage is better" result. The
  "refused by the leverage gate" counts are tick-level (a setup that stays
  refused for hours of 15m ticks counts once per tick), not a count of
  distinct missed trades — read them as "how much of the window a setup
  sat refused," not "how many trades were lost."
- **Sample size.** Two months, 30 thesis-days each, one coin, one side of
  history per month (all long). Controls and buy-and-hold give some sense
  of regime, but this is still a small, non-adversarial sample — it says
  the live rule is NOT yet proven safe across regimes, not that it is
  proven unsafe.
- **No change to the live rule.** `bracket.py`,
  `services/strategies/next_trade.py` and the live desk config are
  untouched. This is evidence for the operator's question, not a shipped
  change.
