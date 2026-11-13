# 5-day trend veto test, 2026-11-13

**Verdict: FAIL the stated bar. Not wired live.** The veto is in the
simulator, off (`desk_tick_sim.Params.trend_gate_span = None`), with its
pure rule (`next_trade.trend_gate`), its eval and its gate tests, so it can
be re-run the day the record changes. No live constant, no change to
`next_trade`, `place_bracket`, `oil_levels.py --next`, `trigger_watch.py`
or the desk-tick agent.

Why in one line: the dip-buy's winners ARE the bottoms. The veto blocks
exactly those. On the live journal it would have skipped all eight
November longs (six stops, about -$96) but also the 11-04 17:30Z long at
94.15 (+3.6R, +$52) and the 11-10 13:45Z long at 88.31 (+5.3R, +$78,
still open at the data end), and a 10-29 long that hit target (+$28).
Net: the baseline keeps +$129.67, the veto +$91.31 (-$38.36), with no
plateau across spans.

## The rule

Operator, 2026-11-13 ("yes"), after the strategy tournament
(`STRATEGY_TOURNAMENT_2026-11-10.md`) found B_ret120, the mechanical
dip-buy gated by the sign of the 120h return, the best of 15 candidates
(+$6.30 over 67 trades, a lean) and that the gate would have blocked all 14
mechanical November longs. The tournament had no thesis; the desk's
October profit came from the thesis, so the test that matters is the veto
on top of the REAL journaled theses.

TREND VETO: no new LONG while Brent's 120-hour return (the last CLOSED 1h
close against the close 120 closed 1h bars earlier) is negative. Mirror
for shorts: no new SHORT while it is positive. A zero return and too
little history block nothing. Open positions are never touched.

Sim: `desk_tick_sim.trend_gate_blocks` (pure) reading `next_trade.
trend_gate` (the one function the live rule would also read),
`Params.trend_gate_span`, counted in `Run.refused["trend"]`, each skipped
setup in `Run.trend_blocked`. Eval: `services/strategies/evals/
trend_gate_test.py`. Tests: `services/strategies/tests/test_trend_gate.py`
(35, including every November long on the real 1h closes, in
`tests/fixtures/brent_1h_closes_2026-10-20_to_11-12.json`).

## The bar (stated before the run)

On the live oil journal, the 120h span, all four:

1. net $ beats the baseline (the live rule as it stands);
2. max drawdown no worse;
3. the neighbouring spans (96h and 144h) also beat the baseline, so it is
   a plateau, not a lone spike;
4. it does not lose on the last third: the veto's last-third net $ is not
   negative and not below the baseline's.

Setup: the stop-reclaim eval's baseline (`stop_reclaim_test.base_params`:
`live_brent_params`, market entry off the 24h range, 0.75 stop buffer,
limit target 0.5% inside the far edge, 2R, 20x, no entry after a break,
weekend flat, size slippage on) at the tournament's $182.09 and the live
6% risk cap. Two candle sets: the reclaim eval's 11-10 05:30Z snapshot
(the same 14 journal trades as `STOP_RECLAIM_TEST_2026-11-10.md`, whose
+$83.23 at $194 is +$78.12 at $182.09, trade for trade) and a fresh fetch
to 2026-11-12 18:30Z (67 theses, 10-18 to 11-12). The 30-thesis Opus
replay, the no-thesis controls (30 and 50 days) and WTI (the journal's
SIDES applied to xyz:CL, the Brent-priced entry ceiling and invalidation
stripped) are context, not judges.

## Results, live journal

| candles to | variant | trades | wins | total R | net $ | max DD | thirds net $ | blocked W / L / open | blocked $ |
|---|---|---|---|---|---|---|---|---|---|
| 11-10 05:30Z | baseline | 14 | 7 | 7.66 | +78.12 | 23.9% | +19.60 / +73.07 / -14.44 | | |
| | veto 120h | 7 | 5 | 8.05 | +91.31 | 7.2% | +19.60 / +64.74 / +0.00 | 2 / 5 / 0 | +1.18 |
| 11-12 18:30Z | baseline | 15 | 7 | 6.54 | +129.67 | 28.9% | +40.66 / +48.95 / +18.77 | | |
| | veto 120h | 7 | 5 | 8.05 | +91.31 | 7.2% | +40.66 / +41.40 / +0.00 | 2 / 6 / 1 | +63.04 |

"Blocked $" is what the skipped entries would have made, walked one slot
at a time with their own stop and target (positive = the veto cost it).
Thirds on the fresh set: 10-18 to 10-26 16:55Z, to 11-04 05:42Z, to 11-12.

Bar on the fresh set: net -$38.36 (fail), DD better (pass), neighbours
96h -$106.10 and 144h -$29.13 (fail), last third +$0.00 vs +$18.77 (fail).
On the 11-10 snapshot: net +$13.19 (pass), DD (pass), 96h -$54.55 (fail),
last third (pass). Either way: **FAIL**.

**Corrected 2026-11-13: leverage fit.** The runs above used a baseline
without the live 20/15 leverage fit (`live_params.live_brent_params` now
carries it, read off `next_trade.leverage_fit_for`; RULE_FUSION_2026-11-13.md
section 5). Re-run on candles to 2026-11-13 04:30Z, both baselines on the
same data (`trend_gate_test` with and without `--no-lev-fit`):

| | was (unfitted, to 11-13 04:30Z) | **now (fitted, the live rule)** |
|---|---|---|
| journal baseline | 16 trades, 8 wins, +142.82, DD 28.9%, last third +27.24 | **16 / 8 / +142.82, DD 27.7%, last third -1.11** |
| journal veto 120h | 7 / 5 / +91.31, DD 7.2%, last third +0.00 | **8 / 6 / +134.06, DD 7.2%, last third +0.00** |
| net delta | -51.51 | **-8.76** |
| neighbours 96h / 144h | +23.57 / +100.54 (both under) | **+55.73 / +100.54 (both under)** |
| replay30 baseline / veto | +85.51 / +95.22 | **+141.23 / +118.97** |
| WTI journal sides baseline / veto | -22.90 / +29.26 | **-42.92 / +37.66** |
| bar | net fail, DD pass, plateau fail, last third fail: **FAIL** | net fail, DD pass, plateau fail, last third pass (0.00 vs -1.11): **FAIL** |

Verdict unchanged: FAIL. The fit lets the veto keep one more November
trade (the 11-03 14:00Z long at 15x, which stops), so the veto's own net
rises with the baseline's; the gap narrows to -$8.76 but the plateau still
fails on both neighbours and the veto still blocks the 11-04 and 11-10
bottoms.

### By month (journal, entered in; fresh set)

| variant | October net $ (trades / wins) | November net $ (trades / wins) |
|---|---|---|
| baseline | +119.26 (8 / 6) | -57.20 (7 / 1), plus the open 11-10 long |
| veto 72h | +51.30 (4 / 3) | 0 (0 / 0) |
| veto 96h | +37.13 (5 / 3) | -13.57 (1 / 0) |
| veto 120h | +91.30 (7 / 5) | 0 (0 / 0) |
| veto 144h | +119.26 (8 / 6) | -18.73 (1 / 0) |
| veto 168h | +119.26 (8 / 6) | -18.73 (1 / 0) |

### The entries the 120h veto skipped (journal, fresh set)

| tick | side | entry | 120h return | what it would have done | R | $ |
|---|---|---|---|---|---|---|
| 10-29 05:45Z | long | 109.90 | -1.30% | target 10-30 01:30Z | +1.95 | +28.43 |
| 11-02 03:00Z | long | 110.16 | -0.61% | stop 11-02 08:30Z | -1.13 | -15.80 |
| 11-04 14:00Z | long | 100.60 | -11.34% | stop 11-04 15:45Z | -1.10 | -15.90 |
| 11-04 16:00Z | long | 99.21 | -12.67% | stop 11-04 16:30Z | -1.08 | -16.01 |
| 11-04 17:30Z | long | 94.15 | -17.21% | weekend flat 11-06 21:00Z | +3.59 | +52.26 |
| 11-09 07:45Z | long | 95.57 | -7.87% | stop 11-09 11:15Z | -1.11 | -15.87 |
| 11-09 13:00Z | long | 93.10 | -8.64% | stop 11-09 14:15Z | -1.10 | -15.93 |
| 11-10 06:45Z | long | 89.75 | -4.00% | stop 11-10 08:45Z | -1.10 | -15.93 |
| 11-10 13:45Z | long | 88.31 | -8.07% | open at the data end (11-12 18:15Z) | +5.32 | +77.79 |

Blocked winners 2 (+1 open winner) vs blocked losers 6. The six stops
sum to -$95.44; the three winners to +$158.48. The veto removed the
November slide and the November bounce together.

## Span plateau and WTI

Net $ (max DD). Journal = the live journal; replay30 = the 30-thesis Opus
replay (09-30 to 10-30); WTI = the journal's sides on xyz:CL.

| span | journal, to 11-10 | journal, to 11-12 | replay30 | WTI journal sides | journal blocked W / L / open |
|---|---|---|---|---|---|
| baseline | +78.12 (23.9%) | +129.67 (28.9%) | +85.51 (30.2%) | -24.77 (43.7%) | |
| 72h | +51.31 (8.0%) | +51.31 (8.0%) | +37.31 (16.4%) | -4.66 (15.2%) | 4 / 7 / 1 |
| 96h | +23.57 (12.6%) | +23.57 (12.6%) | +46.15 (18.9%) | -15.55 (17.4%) | 4 / 5 / 1 |
| **120h** | +91.31 (7.2%) | +91.31 (7.2%) | +95.22 (8.8%) | +29.26 (16.7%) | 2 / 6 / 1 |
| 144h | +100.54 (8.0%) | +100.54 (8.0%) | +73.73 (27.0%) | +3.93 (17.0%) | 1 / 5 / 1 |
| 168h | +100.54 (8.0%) | +100.54 (8.0%) | +86.78 (17.0%) | +25.11 (16.7%) | 1 / 5 / 1 |

No plateau: 72h and 96h lose a third to two thirds of the journal's net
(they also veto the October longs), 144h and 168h keep more of it only
because they veto less. On replay30 the order flips (120h best, 144h
worst of the long spans). WTI with the journal's sides: the baseline
loses -$24.77 and every span cuts the loss, 120h to +$29.26; a cross-check
that says "fewer trades on a losing tape lose less", not that the veto
has an edge (its blocked set there is 2 wins vs 12 losses on 20 baseline
trades of which 5 won).

## Context: the no-thesis controls (fresh set, 120h vs baseline, net $)

| series | Brent 30d | Brent 50d | WTI 30d | WTI 50d |
|---|---|---|---|---|
| always_long | +120.03 vs +54.96 | +75.78 vs +92.10 | +29.26 vs -52.09 | +4.05 vs -19.10 |
| always_short | -59.23 vs -48.58 | -92.73 vs -103.86 | -35.66 vs -78.65 | -43.71 vs -56.98 |
| daily_sign | +35.24 vs -32.16 | -7.51 vs -61.02 | +11.03 vs -81.85 | +29.38 vs -68.59 |

The 120h veto beats the baseline on 12 of 16 datasets and has a lower or
equal max drawdown on 16 of 16; it loses on the live journal, Brent
always_long 50d, Brent always_short 30d and the WTI replay30 (+$12.20 vs
+$131.98). The controls repeat the tournament: with no thesis, a trend
veto on a dip-buy is mostly a trade-count cut, and the drawdown falls
because fewer trades are taken. The dataset that decides is the journal,
and there the veto gives back the bounce.

## Reading

1. The thesis already carries the trend call. In October the theses were
   long in an uptrend and the veto changed little (it cost one target, the
   10-29 long). In November the theses kept calling the dip-buy in a
   downtrend; the veto blocked the six stops AND the two bottoms, and the
   bottoms paid more than the stops cost.
2. A 5-day return is slow. On 11-10 13:45Z the 120h return was still
   -8.1% while price had already turned; every span in the grid (72 to
   168h) still said "down" at that tick. A faster signal would not have
   been a 5-day trend veto.
3. n is small either way: 9 blocked entries, 3 of them the whole answer.
   The reclaim test (n = 1) had the same shape. Re-run when the journal
   holds 30+ November-style entries.
4. Drawdown: the veto's 7.2% vs 28.9% is real but bought by sitting out;
   the bar asked for net first.

## Re-run

    PATHIEL_STATE_DIR=<pathia>/.state .venv/bin/python -m services.strategies.evals.trend_gate_test \
        --oil-theses-dir <pathia>/.state/strategies/oil/theses \
        --oil-replay-dir /tmp/thesis-replay-30b/oil/theses --cache-dir <fresh dir> --out-dir <dir>

Exit 0 = pass, 1 = fail, 2 = no journal. Report `<out-dir>/report.json`
(per dataset, variant, thirds, months, the November slide, the journal's
trades per variant), blocked entries `<out-dir>/blocked_entries.csv`.
`--spans 72 96 120 144 168` (default), `--no-wti`, `--no-controls`. The
11-10 snapshot run above used the stop-reclaim eval's cache directory
with `--no-wti --no-controls`. Gate tests:
`.venv/bin/python -m pytest -q services/strategies/tests/test_trend_gate.py`.
