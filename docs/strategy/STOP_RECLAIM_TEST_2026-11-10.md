# Stop-reclaim test, 2026-11-10

**Verdict: FAIL the stated bar. Not wired live.** The rule is in the
simulator, off (`desk_tick_sim.Params.reclaim_after_stops = None`), with
its eval and gate tests, so it can be re-run the day the record changes.

## The rule

Operator, 2026-11-09/10 ("yes"): the oil thesis kept calling "buy the dip",
got stopped, and the next thesis moved the buy lower: 96.10, 95.60, 93.90,
92.60 were each stopped (four straight -1R, about -$46 at 6% risk) while
Brent slid 7.6% on Iran-deal optimism.

STOP-RECLAIM: after 2 stop-outs on one side inside the last 24h, no new
entry on that side until a CLOSED 1h candle closes back beyond the most
recent stopped trade's stop price (long: 1h close > that stop; short:
1h close < it). A reclaim clears the block. Mirror for shorts.

Sim: `desk_tick_sim.reclaim_blocks` (pure), `Params.reclaim_after_stops`
/ `reclaim_window_h`, counted in `Run.refused["reclaim"]`, each skipped
setup in `Run.reclaim_blocked`. Eval: `services/strategies/evals/
stop_reclaim_test.py`. Tests: `services/strategies/tests/test_stop_reclaim.py`.

## The bar (stated before the run)

On both thesis datasets (the live oil journal, the 30-thesis Opus replay):
the reclaim variant's net $ not worse than the baseline over the whole
window AND not worse in at least 2 of 3 equal sub-windows (each third its
own run from the starting equity). Controls are context, not judges.

Setup: the live Brent rule (`live_params()`: market entry off the 24h
range, 0.75 stop buffer, limit target 0.5% inside the far edge, 2R, 20x,
no entry after a break, weekend flat) at $194 and the live 6% risk cap,
size slippage on. Fresh candles to 2026-11-10 05:30Z.

## Results

| dataset | variant | trades | wins | total R | net $ | max DD | thirds net $ | blocked |
|---|---|---|---|---|---|---|---|---|
| brent_journal (10-18 to 11-10, 48 theses) | baseline | 14 | 7 | 7.66 | +83.23 | 23.9% | +20.88 / +77.85 / -15.39 | |
| | reclaim | 13 | 6 | 4.24 | +39.43 | 28.2% | +20.88 / +77.85 / -43.61 | 11 ticks, 1 entry |
| brent_replay30 (09-30 to 10-30) | baseline | 28 | 14 | 8.00 | +91.11 | 30.2% | -28.74 / +25.24 / +102.17 | |
| | reclaim | 28 | 14 | 8.00 | +91.11 | 30.2% | same | 0 |
| always_long 30d | baseline | 24 | 10 | 5.77 | +46.87 | 42.9% | +49.39 / +131.02 / -79.40 | |
| | reclaim | 21 | 10 | 6.20 | +58.39 | 38.9% | +49.39 / +131.02 / -73.92 | 24 |
| always_short 30d | baseline | 18 | 4 | -6.33 | -63.58 | 37.8% | -14.10 / -23.79 / -33.69 | |
| | reclaim | 15 | 4 | -4.02 | -45.68 | 29.3% | -14.10 / -0.44 / -33.69 | 16 |
| daily_sign 30d | baseline | 24 | 10 | 2.96 | +15.66 | 49.2% | +49.39 / +131.02 / -94.25 | |
| | reclaim | 20 | 9 | 5.67 | +51.21 | 40.6% | +49.39 / +131.02 / -77.34 | 15 |
| always_long 50d | baseline | 41 | 16 | 7.56 | +49.00 | 42.9% | -2.46 / +84.85 / -22.77 | |
| | reclaim | 38 | 16 | 7.07 | +51.09 | 38.9% | -9.62 / +84.85 / -14.59 | 27 |
| always_short 50d | baseline | 37 | 8 | -10.96 | -100.80 | 63.5% | -31.59 / -69.30 / -0.36 | |
| | reclaim | 30 | 8 | -4.95 | -63.64 | 52.1% | -6.70 / -52.19 / -0.36 | 36 |
| daily_sign 50d | baseline | 49 | 18 | -8.09 | -86.18 | 59.5% | -96.36 / +84.85 / -44.96 | |
| | reclaim | 43 | 16 | -4.25 | -59.49 | 56.8% | -89.85 / +84.85 / -19.69 | 29 |

Bar: journal full window -$43.80 (worse), thirds 2/3 -> **fail**.
Replay30: no-op (+$0.00, 3/3) -> pass. Verdict FAIL.

## Why it lost on the journal

One trade. On 2026-11-04 the sim's longs at 100.63 and 99.24 were both
stopped (14:00Z and 16:00Z); the 17:30Z long at 94.15 (stop 93.27) is the
one the rule skipped, because no 1h bar ever closed back above 98.06 (the
last stop) inside the window. In the baseline that long rode to the
weekend flat at 97.41: +3.42R, +$49.75, the best trade of the month. The
rule's 11 blocked ticks are that one entry and the ticks after it. On
11-09 the sim's journal replay took only two longs (95.60 and 93.13, both
stopped) under either variant: the rule had nothing to save there, since
the sim never found the third and fourth entries the live desk took.

The controls all improve (6 of 6 on net $ and on max drawdown): with no
thesis, "two stops then wait for a reclaim" acts as a trend filter on
straight-line days. That is not the question asked; the thesis sets are.

## Context runs (not part of the bar)

| variant | journal net $ | replay30 | note |
|---|---|---|---|
| after 3 stops, 24h | +83.23 (= baseline) | = baseline | never fires: 3 same-side stops in 24h never happened in the sim |
| after 2 stops, 12h | +86.92 (+3.69) | = baseline | 5 blocked ticks; the 11-04 block expires before the dip is bought again |

n = 1 blocked entry on the thesis data either way: there is no edge to
read here, only one trade's sign. The 11-09 incident itself is four real
fills the sim does not reproduce, so a real-fills check (the pattern of
`no_break_real_fills.py`) is the honest next test once the ledger holds
10+ "second stop in 24h" entries.

## Re-run

    PATHIEL_STATE_DIR=<pathia>/.state python3 -m services.strategies.evals.stop_reclaim_test \
        --oil-theses-dir <pathia>/.state/strategies/oil/theses --cache-dir /tmp/stop-reclaim/cache

Exit 0 = pass, 1 = fail, 2 = no thesis dataset found. Report:
`<out-dir>/report.json`, blocked entries `<out-dir>/blocked_entries.csv`.
