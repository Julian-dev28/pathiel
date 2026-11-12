# Oil desk strategy: the old rules and the 2026-10-30 switch

On 2026-10-30 the oil desk stopped resting a limit at the 8-hour low and
started buying at the market when price sits low enough in the 24-hour range
to pay 2R. On our 9 journaled theses the new rules make 18.7% a day in the
backtest against 11.2% for the old ones, and they are the first rules that
still make money with no thesis at all over 50 days.

Operator words that set it: "switch it" (2026-10-30), after asking for the
most profit "PER DAY, THEN PER WEEK".

## What stays the same

Brent only (`xyz:BRENTOIL`), up to 20x isolated, the whole equity in each trade
(margin plus the opening fee) unless the stop would lose more than 15% of
the desk's equity, then smaller (`risk_profile.risk_per_trade_pct: 15`,
since 2026-11-02), one position at a time, stop 0.75 beyond the
structure, target 0.5% of price inside the far edge as a resting limit order,
the stop never moves, a new entry is allowed after a target fills, a tick
every 15 minutes, theses written by Fable 5.1 on the rewrite schedule
(`scripts/thesis_due.py`). Never chase: no entry above a long thesis's entry.
Since 2026-11-05 the desk is flat over the oil futures weekend (Fri 16:00 to
Sun 18:00 New York): no new entry inside it, an open position closed at the
window open (`services/strategies/weekend.py`).

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
| 2026-11-05 | **Live: weekend flat.** No new Brent/WTI entry or top-up inside the oil futures weekend (Fri 16:00 to Sun 18:00 New York: Fri 21:00Z to Sun 23:00Z in US winter, an hour earlier in summer), and an open oil position is closed at the window open (the desk-tick agent, within one 15-minute tick; `trigger_watch.py` wakes the tick at the open when it holds one). Stop and target otherwise untouched; silver and gold unchanged | Operator: "weekend flat". `BRENT_STOP_BUFFER_TEST` study B rerun 2026-11-05 at 6% risk (`/tmp/brent-buffer-6pct/report.json`): `wk_flat` beat live on 5 of 8 Brent datasets, lost 1 (always_short replay), geo 1.033, plateau, verdict adopt. On the two thesis datasets (live journal, 30 replayed theses) it ties live exactly: no journaled trade was open at a window start, so this is insurance against the Monday gap on weekend headlines (Hyperliquid trades 24/7, CME is shut), not a measured thesis edge. It helps on the controls (+12 to +18 points: always_long replay 153.6% vs 138.0%, daily_sign replay 80.5% vs 62.2%, always_long 30d 50.7% vs 38.6%, daily_sign 30d 30.6% vs 20.0%) and loses on always_short replay (-52.6% vs -48.4%). Silver was worse with it (0 of 3, geo 0.80; not adopted), gold untested. `services/strategies/weekend.py` (one window for the live gate and the sim, pinned minute by minute by `test_weekend.py`), `event_blackout` order EIA > weekend > scheduled > headline, `place_bracket` refuses "Weekend flat: ...", `scripts/weekend.py` tells the agent when to close, `desk_tick_sim.live_params()` carries `weekend_off="flatten"`; replays of earlier trades use `live_params_before_weekend_flat()` |
| 2026-11-04 | **Live: Brent leverage fit.** A new Brent entry whose stop is too far for 20x trades at 15x instead of being skipped; never lower than 15x. Trades that fit 20x stay 20x; the stop, target and the 15% risk cap are unchanged; top-ups never refit; silver unchanged | Operator: "TURN ON LEVERAGE FIT" after `FINAL_STRATEGY_TEST_2026-11-04.md`: `lev_fit_eia` beat live on 4 of 4 thesis datasets (journal $154 vs $118, Oct $70 vs $58, Sept -$5 vs -$34, chain +$60 vs -$8) with drawdown no worse. The test's candidates were 20/15/10/8/5 but it never used anything under 15x, so the live 20/15 set trades identically on that data; the operator wants nothing lower. `next_trade.fit_leverage` / `LEVERAGE_FIT`, used by place_bracket, the desk brief (live rule and Your size) and trigger_watch |
| 2026-11-03 | **Live: EIA blackout.** No new Brent/WTI entry or top-up from 2h before to 2h after the weekly EIA report (Wed 10:30 ET; Thu 12:00 ET in a federal-holiday week). Open positions, stops, targets and closes untouched | Operator: "DO THIS THEN" to `eia_2_2` (`OIL_EVENT_TIMING_TEST_2026-11-03.md`): beat live on 5 of 9 datasets, tied 4, never lost. `services/strategies/event_blackout.py` (same calendar and window as the backtest, pinned by `test_live_window_is_exactly_the_backtested_eia_2_2`); `place_bracket` refuses inside the window, the desk brief's live-rule line shows it, `trigger_watch.py` does not wake a tick for it. Resting limit entries placed before a window are not cancelled by it |
| 2026-11-03 | No change (no event-timing filter adopted; new `event_blackout_windows` field off by default) | Operator: "sure" to testing whether Brent behaves differently around the EIA/API reports, OPEC+ dates and NYMEX/ICE session structure (crypto has none of these, unlike the already-rejected 07:00-21:00 UTC session idea). Event study (`oil_calendar.py`, 52 days, EIA n=7/API n=8/OPEC n=2): no move/range difference from a matched null clears significance; sessions show the expected weekend-quiet/day-session-active pattern, no new idea. As a filter, blocking entries 1-2h either side of the EIA report beats live on 5 of 9 datasets, ties the other 4 exactly (free), never loses -- but is blocked from "adopt" only by a pre-existing control trade's stop-to-liquidation fraction (0.506) the filter cannot change either way. A near-miss, not adopted; flagged for a decision (`OIL_EVENT_TIMING_TEST_2026-11-03.md`) |
| 2026-11-03 | No change (leverage stays 20x; no-news replay says September would have lost) | Extending the replay 30 more days back (08-31 to 09-29): September alone -56.5% ($64 to $27.82) in a choppy, no-news month despite a 70% thesis direction-hit rate; chained after the already-tested October, the two months net -20.1%. A leverage sweep (5/10/15/20x) found 10x/15x would have beaten 20x on September, October and the chain, but 20x still wins on the live journal and a trending control -- regime-dependent, not adopted (`EXTENDED_HISTORY_TEST_2026-11-03.md`) |
| 2026-11-03 | No "wiggle" scalp book (keep the oil desk exactly as is; no live change) | Operator: "test a strategy where we play the wiggle ... 5% of the portfolio at 20x ... intraday/intrahour swing trade." A separate, small, fast mean-reversion book (range fade and rolling-mean z-score, new `scalp_sim.py`) tested on Brent, WTI and silver over the venue's full 52-day history for these markets, walk-forward split. Every rule that made money in-sample lost it (or wiped out the slice's own capital) out-of-sample on all three coins; the random-entry control lost about as much as the real rule on silver and WTI, so the in-sample edge reads as noise; mean reversion never found a profitable combination at all. Brent and silver scalps would also net with the oil and metals desks' own positions on this account (one position per coin per account) and would need a Hyperliquid subaccount to run independently; WTI is clean of that problem but still fails on the numbers. Nothing adopts (`SCALP_TEST_2026-11-03.md`) |
| 2026-11-03 | No closer target (keep the far 24h edge, 0.5% in) | Operator asked for a realistic-high check on the open 103.61 long (13R target). Fixed-R targets (4R-10R), wider offsets (2%-4%), the range midpoint/75% point and a hard cap on the target all lose or barely tie live on the real thesis sets; best is `target_cap_frac` 4-5%, which only ties because it almost never binds. A fixed-R target raises trade count (more re-entries into chop) and cuts the rare big win that pays for the losers. New field `desk_tick_sim.Params.target_cap_frac`, off by default (`CLOSER_TARGET_TEST_2026-11-03.md`) |
| 2026-11-02 | No regime filter, side override or range-scaled stop/target | The live rule fades the 24h range; lately it earned in chop (+41.0R) and lost in straight-line down days (-17.0R on 16 trades). No chop filter, with-trend filter, 20-day veto or override beat live on more datasets than sitting out does, or held across +-25% thresholds; every one lost or tied on the thesis sets. Closest: stop max(0.75, 0.15 x 24h range), 6 of 8 but no plateau, and it never binds at today's 2.3% range (`OIL_REGIME_TEST_2026-11-02.md`) |
| 2026-11-02 | Stop stays 0.75; weekends stay on; no weekend-only profit lock | A wider stop never survived the 11-02 dip (0.80 to 0.90 lose more, 0.95 and up fail 2R); 0.90 wins October only by refusing entries. Weekend-off: no thesis trade changes. Weekend lock at 1R: never lost but never touched a thesis trade (`BRENT_STOP_BUFFER_TEST_2026-11-02.md`; live journal still to run) |
| 2026-11-02 | No daily stop-out cap | Blocking new entries after 1 to 3 stops in a UTC day lost to no cap on the thesis sets; on Brent theses 2 stops in a day never happened (`STOP_CAP_TEST_2026-11-02.md`) |
| 2026-11-01 | Stops stay fixed; no profit lock | Moving the stop to entry + 0 to 1R after +1 to 2R beat the fixed stop in 1 of 48 thesis runs (`PROFIT_LOCK_TEST_2026-11-01.md`) |
| 2026-10-30 | Market entry, 24h range, 2R minimum | Strategy sweep ranked by profit per day (below) |
| 2026-10-30 | Target 0.5% inside the far edge, stop 0.75 beyond structure; live target moved 113.75 to 113.18 | Scenario sweep, 540 combos: $64 to $199.60 vs $123.91 for the rules before |
| 2026-10-28 | Thesis rewritten on a schedule by Fable 5.1: daily, after API/EIA/OPEC, after a close, after a 1.5% move, at most hourly | The 2026-10-25 thesis was still trading 2.7 days and one EIA report later |
| 2026-10-28 | Oil only | Cross-market test: without a thesis the same mechanics lose on Brent, gold, BTC and the S&P 500. The edge is the thesis |
| 2026-10-28 | Targets rest as limit orders, stops stay market | A limit target never missed in 26 test exits and pays the maker fee |
| 2026-11-02 | A stop loses at most 15% of the desk (`risk_per_trade_pct` 15, was 100); leverage stays up to 20x | Loss audit: the 11-02 long at full size (-$33.87) was the whole drawdown. Cap 15% geo 1.10 Brent, 1.17 silver vs live, beats live 3 of 8; a risk call, not a tested edge. Flat 10x/15x lost on both thesis sets. See LOSS_AUDIT_2026-11-02.md |
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

2026-11-03 extended this on the current rules and data: fixed 4R-10R
targets, 2%/3%/4% offsets, the range midpoint/75% point and a target cap
(`target_cap_frac`) all lose or barely tie live on the real thesis sets;
see `CLOSER_TARGET_TEST_2026-11-03.md`.

2026-11-03: old alpha-swarm research (D4/D5) found shorting a crowded-long
funding z-spike (z>=2.0 of trailing-24h funding vs trailing-30d) a real
edge on 40 crypto majors. Rebuilt it on Brent/silver's own public funding
history as a `Params.funding_z_filter` (off by default): **0 of 36 tested
cells clear EVIDENCE_DOCTRINE.md**, and as a filter it is a no-op on both
the live journal and the oil replay (no qualifying spike ever lands in
those windows; silver's journal gets dozens of refusals that never
changed a trade). Brent's own funding runs net NEGATIVE over its 8-month
history (longs were paid, not charged), and a crowded-long spike (the
signal this filter needs) fires 1-3 times in 244 days -- nowhere near the
40-coin pool D4 needed. Not adopted. See
`FUNDING_EXTREME_TEST_2026-11-03.md`.
2026-11-03/04 ("fewer, wider, longer", after September lost 56% at 20x on
11 stops in 21 trades and three same-thesis longs stopped twice in 30
minutes on 2026-11-03): a wider stop (48h/24h edge plus 0.5-3x ATR14_1h)
alone loses on the thesis sets and often pushes the stop past half the
liquidation distance. Fitting the leverage DOWN to the stop
(`entry_fit_leverage`, highest of 20/15/10/8/5x that still clears the 20x
line, same 15% risk cap) beats live on both available thesis datasets
(journal +21% more, Sept replay +43% less lost) and on the chain, and
halves the loss on the 2026-11-03 three-long day; one re-entry per thesis
gated on a 1h close back inside the range (`max_stop_reentries=1`,
`reentry_needs_1h_reclaim`) wins big on Sept and the chain but trails
live on the journal. Not adopted: the October replay is unavailable
(regenerating -- macOS cleared `/tmp` around 16:00Z) this run, so the
plateau check has only two thesis datasets to stand on (journal, Sept),
and `entry_fit_leverage`'s realized fills touch 0.506 of the liquidation
distance (0.006 over) in one control series. Re-run with
`--oil-replay-dir` pointed at October's regenerated set before shipping.
See `WIDE_STOP_SWING_TEST_2026-11-03.md`.

## Risks and when to re-check

- **Live result, first trade under the 0.5% offset target (2026-10-30).**
  The 13.08 long from 109.46 closed at 113.22 for +$49.21 (account $121.29).
  Brent then ran to 114.07, so the old 113.75 target would also have filled,
  for $6.93 more. The offset pays on moves that stall short of the edge;
  this one did not. Keep a tally of both outcomes per trade before the
  30-trade re-check.
- **A triggered limit target can fill as a taker.** The take-profit is a
  trigger order with a limit price; when it triggers with the market already
  through the limit, it crosses the book (`crossed: true`, 0.9 bps on the
  xyz dex, $0.13 on this trade). The backtest assumes the maker fee, a
  difference of under 1 bp.

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

## Decision log

| Date | Change tested | Verdict | Evidence |
|---|---|---|---|
| 2026-11-04 | Final adoption test: `lev_fit` (entry_fit_leverage), `entries_reclaim`, `eia_2_2` blackout, and half-off at 3/4/5/6R, alone and combined, on one pinned candle snapshot across the live journal, Sept replay, Oct replay, and the Sept+Oct chain | **Adopt `lev_fit_eia`** (lev_fit + eia_2_2): beats live on 4/4 thesis datasets, max DD no worse than live, stop never past 49.8% of the liquidation distance. Reject `entries_reclaim`/`combined` (2/4 — loses on journal and Oct despite winning big on Sept/chain). Half-off at 3R is optional (3/4, not required to clear the bar) | `FINAL_STRATEGY_TEST_2026-11-04.md` |
| 2026-11-04 evening | **Live: no entry after a break** (`next_trade.NO_BREAK_ENTRY`): no market entry while the mid is past the 24h range's own edge (a long under the 24h low). Operator: "go", "DO WHAT U THINK IS MORE PROFITABLE" | **Adopted on the real fills, against the Brent replay.** Review LEAK range=past own edge: 10 real trades since 10-20, 1 win, -$117.26, p=0.002; the other 6 made +$61.91. Replay (LOSS_AUDIT_2026-11-02): Brent 1/8 geo 0.66, silver 3/8 1.26. The resting edge limit scored worse (2/8 0.52, silver 0.40), so market entries inside the range stay. Re-check: `python3 -m services.strategies.evals.no_break_real_fills` must stay net >= 0 | `REVIEW_2026-11-04.md`; `evals/no_break_real_fills.py`; `tests/test_no_break.py` |
| 2026-11-05 evening | **Live: weekend flat** (`weekend_off="flatten"`, `services/strategies/weekend.py`): no new oil entry Fri 16:00 to Sun 18:00 New York, an open oil position closed at the window open. Operator: "weekend flat" | **Adopted.** Study B rerun at 6% risk: `wk_flat` beats live 5 of 8 Brent datasets, loses 1, geo 1.033, plateau. Ties the two thesis datasets exactly (insurance against the Monday headline gap, not a thesis edge); +12 to +18 points on the long/daily-sign controls, -4 on always_short. Silver worse (0 of 3, geo 0.80), gold untested: oil coins only. Re-check: `python3 -m services.strategies.evals.brent_stop_buffer_test` must keep `live` == `wk_flat` on every Brent dataset | `/tmp/brent-buffer-6pct/report.json`, `summary.csv`; `services/strategies/tests/test_weekend.py`, `tests/test_weekend_script.py`; `test_brent_stop_buffer_eval.py` |
| 2026-11-10 | **Stop-reclaim rule, tested, NOT adopted** (`desk_tick_sim.Params.reclaim_after_stops`, off): after 2 same-side stops in 24h, no new entry on that side until a closed 1h candle closes back beyond the last stop. Operator: "yes" after four stopped longs on 11-09/10 (96.10, 95.60, 93.90, 92.60) | **Fails its bar** at $194 and 6% risk: live journal -$43.80 (+83.23 to +39.43), one blocked entry: the 11-04 17:30Z long at 94.15 that rode +3.4R to the weekend flat; thirds 2/3. Replay30 a no-op (never 2 same-side stops in 24h). Controls all better (6/6 net $ and max DD) but do not decide. After 3 stops: never fires. 12h window: +$3.69, still n=1. Re-check: `python3 -m services.strategies.evals.stop_reclaim_test` must pass before any wiring | `STOP_RECLAIM_TEST_2026-11-10.md`; `evals/stop_reclaim_test.py`; `tests/test_stop_reclaim.py` |
| 2026-11-10 | **Strategy tournament + walk-forward round robin, research only** (`evals/strategy_tournament.py`): 14 mechanical candidates (dip-buy long/short controls, trend-gated dip-buy, Donchian, alarm momentum, wide-stop mean reversion, time-series momentum, flat) on the venue's full 198-day 1h Brent window and WTI, one engine, same costs, 6% risk; selectors rebalance every 7/3 days on the best trailing 7/14 days. Operator: "find what works, try a round robin" | **Nothing passes.** Baseline A_long -$149.43 (118 trades, 27% wins); best candidate B_ret120 +$6.30 (67 trades, DD 53%, WTI +$53.60) fails thirds and DD; selectors +$244 / +$157 at a 7-day lookback but -$127 / -$142 at 14 days and negative on WTI: noise. At 3% risk B_ret120 passes on a +$2 middle third with no neighbour plateau (72/96/240 lose). The 120h-return veto would have blocked all 14 mechanical November longs. No wiring. Re-check: `python3 -m services.strategies.evals.strategy_tournament --cache-dir <dir>` (exit 1 while nothing passes) | `STRATEGY_TOURNAMENT_2026-11-10.md`; `evals/strategy_tournament.py`; `tests/test_strategy_tournament.py` |
| 2026-11-13 | **5-day trend veto on the journaled theses, tested, NOT adopted** (`desk_tick_sim.Params.trend_gate_span`, off; `next_trade.trend_gate`, the pure rule): no new long while the 120h close-to-close return is negative, no new short while positive. Operator: "yes" after the tournament's B_ret120 | **Fails its bar** at $182.09 and 6% risk. Live journal to 11-12: baseline +$129.67 (15 trades, DD 28.9%) vs veto +$91.31 (7 trades, DD 7.2%), net -$38.36; neighbours 96h +$23.57 and 144h +$100.54 both under the baseline (no plateau); last third $0 vs +$18.77. It blocks all 8 November longs (6 stops, -$95) but also the 11-04 94.15 long (+3.6R) and the 11-10 88.31 bottom (+5.3R, +$78): the dip-buy's winners are the bottoms. On the 11-10 candle snapshot it beat the baseline +$13.19 but 96h still failed. Replay30 +$95.22 vs +$85.51; WTI with the journal's sides +$29.26 vs -$24.77; controls better on 12 of 16 by trade-count cuts. Re-check: `python3 -m services.strategies.evals.trend_gate_test` must pass before any wiring | `TREND_GATE_TEST_2026-11-13.md`; `evals/trend_gate_test.py`; `tests/test_trend_gate.py` |
