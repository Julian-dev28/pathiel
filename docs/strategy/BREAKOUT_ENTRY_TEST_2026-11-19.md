# NOISE: the one grid point that "passes" is a 1-of-64 cherry-pick, not an edge

Operator, 2026-11-18/19: "ur like doing one trade every 2 weeks", "i need to see
some profitable trades soon". Two cases in the 24h before this test ran:

1. **2026-11-18 09:10Z**, short thesis (`oil-20261118T0910-m.json`) waits to
   sell a rally to 90.30-91.17. Brent instead fell from ~90.5 to ~87.6 after
   the EIA report. No entry.
2. **2026-11-19 00:32Z**, long thesis (`oil-20261119T0032-m.json`) waits to
   buy a dip to 88.40-88.80, target 91.664. Brent ran 88.6 -> 90.34 and hit
   the target. No entry.

**Answer: do not add this entry.** One of 64 variants nominally clears the
adoption bar (wins both halves on both datasets, drawdown within 5pp), but
by $7-10 on a $141-150 live base (5-7%), it is the ONLY point in the grid
that does, and every one of its immediate neighbors (one axis changed) flips
to LOSING one dataset outright, by 3-10x the margin. That is the signature
of scanning 64 combinations and landing on noise, not a parameter-stable
edge. On the two named cases: in case 1, the entry fires on 28 of 64
variants and **loses on every single one** (0 wins, 28 losses) — it catches
the direction but gets stopped by a bounce before the real move plays out.
In case 2 it fires on 44 of 64 and wins on exactly 6, all of them the same
fast 15m-confirm, tight fixed-R-target shape; every slower or farther-target
variant gets stopped on the subsequent pullback.

## Pre-registration (written before this file ran against real data)

The live entry rule (`services/strategies/next_trade.py` via `scripts/
oil_levels.py --next`, replayed exactly by `desk_tick_sim.live_params()`/
`live_brent_params()`) only buys a pullback to the thesis's own entry zone
and never chases past it. Both cases above are the same shape: price moves
away from the pullback zone, in the thesis's OWN direction, breaking the 8h
high/low on the way. Question: does a SECOND, additional entry on the
thesis side — a momentum/breakout entry on that 8h break — catch trades like
these two, without losing money on net, on both real datasets, both halves,
within a drawdown bar?

Two earlier tests already said no to adjacent fixes:
- Lowering the live 2R gate to 1.5R (`MIN_R_GATE_TEST_2026-11-18.md`):
  NOT ADOPTED, 1.5R trails 2.0R on net $ on both primary datasets.
- Resting the live rule's own trigger as a limit order
  (`feature/rest-entry-at-trigger-2026-11-18`, `rest_at_trigger_eval.py`):
  WORSE on net R than market-only, +3.89R vs +8.73R (OIL_STRATEGY.md's
  2026-11-18 entry on that branch).

This file reuses both tests' real-thesis datasets and loaders
(`services.strategies.backtest.data`, `desk_tick_backtest.
journaled_direction`, `live_params.live_brent_params`, `stop_cap_test`'s
`FIFTEEN_MS`/`START`/`_iso`/`closed_only`, `market_scout.load_coin_candles`)
and the min_r test's own generic `run_dataset` (full window + both halves +
long/short cuts over an arbitrary `Params` override — never min_r-specific)
rather than rebuilding any of that harness. Also notable prior art: mode
"a5" in `desk_tick_sim.py` is a close cousin (break of the prior 24h
high/low, 1h-close confirm only) built for silver and shipped live there
2026-11-04 — then found to be the review's LEAK (1 win in 10 real fills)
and turned off on every coin (`next_trade.BREAKOUT_COINS == frozenset()`,
`test_silver_breakout.py`). This test is a genuinely different shape (8h
not 24h, a confirmation-timeframe variant, a "back inside the level" stop
instead of "beyond the far structure," a size variant, a one-per-thesis
cap) run fresh against Brent, not a re-run of a5 — but the family's own
track record is a reason for skepticism going in, not after.

**Method.** A new entry, `desk_tick_sim.Params.pullback_entry == "a6"`
(full mechanics in that module's own Params block): on a live "chase" or
"min_r" refusal, buy/sell the break of the prior 8h high/low in the thesis
direction, confirmed on a closed 15m OR 1h bar, stop either back inside the
broken level by k x ATR14(1h) or at the 8h range's own midpoint, target
shared with every other pullback mode (the thesis's own target, the 24h far
edge, or a fixed 1.5R/2R — the 1.5R row needs its own `pb_breakout_min_r`
override, since the live 2R gate would otherwise refuse a target that pays
exactly 1.5R by construction), sized at full or half the desk's size, capped
at one fill per thesis call (`pb_breakout_cap_one`). The live pullback rule
is UNCHANGED (`live_brent_params()`'s fields are never touched) and stays
active; a6 is layered on top, one slot at a time — the sim's existing
one-position invariant already makes a6 and the pullback rule mutually
exclusive. Event blackouts, the weekend flat and live fees (taker 4.5bps,
maker 1.5bps, the measured `EXIT_SLIP_CURVE` via `size_slippage=True`) are
whatever `live_brent_params()` already enforces — untouched.

**Grid** (64 named variants): confirm (1h, 15m) x stop (level_atr k =
0.5/0.75/1.0, midpoint) x target (thesis, 24h-extreme, fixed 1.5R, fixed 2R)
x size (full, half). Every variant compared against "live" (`pullback_entry
= None`, i.e. `live_brent_params()` untouched) on the SAME data, SAME
engine (`desk_tick_sim.simulate`), SAME run (a6's fills sit in the same
`Run` as the pullback rule's own fills — the "combined" book the task asks
for is simply this run's own total; there is no separate combining step).

**Datasets, PRIMARY** (same pair the min_r test gated on):
- `brent_journal` — the live oil journal's theses (READ ONLY,
  `$PATHIEL_STATE_DIR/strategies/oil/theses`), 102 theses, 2026-10-18 to
  data end (2026-11-19 14:00Z) — this is also where the two named cases
  live.
- `brent_replay30` — 30 replayed Opus theses, price only
  (`/tmp/thesis-replay-30b/oil/theses`), READ ONLY.

**Adoption bar, fixed before any table below was read** (`variant_bar`):
1. Net $ beats "live" in BOTH halves of EVERY primary dataset.
2. Full-window max drawdown no more than 5.0 percentage points worse than
   "live", on every primary dataset.

A variant clearing both is a PASS candidate — UNLESS it fails either of two
sanity checks (`overall_verdict`), in which case it is NOISE instead: fewer
than 8 a6 fills on either primary dataset, or `is_isolated` — none of its
one-axis-away neighbors (confirm, stop, target or size changed one at a
time, the other three held) also wins the full window on both datasets. A
real, parameter-stable edge should survive a small nudge to k or the
confirmation timeframe; an isolated spike surrounded by neighbors that
instead win on ONE dataset and lose on the OTHER, by a much larger margin,
is what overfitting one grid point out of 64 looks like, and the bar says
so plainly rather than rounding up. A variant that beats "live" on the full
window of BOTH datasets (not necessarily both halves) and clears the
drawdown bar, with nothing passing outright, is LEANING. Anything else is
FAIL.

## Full grid, both datasets, both halves — live and the best 3 by edge

"Edge" = full-window net $ vs "live", summed over both primary datasets.
Ranked by edge; these are the three closest to passing, not a random
sample.

**brent_journal** (2026-10-18 04:07Z -> 2026-11-19 14:00Z, 102 theses)

| variant | window | trades | t/wk | win% | net R | net $ | max DD% |
|---|---|---|---|---|---|---|---|
| live | full | 19 | 4.10 | 52.6% | 12.30 | **$141.47** | 55.2% |
| live | H1 | 10 | 4.32 | 70.0% | 11.51 | $169.44 | 18.8% |
| live | H2 | 9 | 3.89 | 33.3% | 0.78 | -$27.96 | 55.0% |
| a6_1h_k0.75_thesis_half | full | 28 | 6.05 | 42.9% | 12.49 | $114.58 | 58.5% |
| a6_1h_k0.75_thesis_half | H1 | 15 | 6.48 | 53.3% | 12.95 | $161.61 | 26.0% |
| a6_1h_k0.75_thesis_half | H2 | 13 | 5.62 | 30.8% | -0.47 | -$47.03 | 58.5% |
| a6_1h_k0.75_thesis_full | full | 28 | 6.05 | 42.9% | 12.49 | $73.26 | 66.5% |
| a6_1h_k0.75_thesis_full | H1 | 15 | 6.48 | 53.3% | 12.95 | $134.79 | 35.1% |
| a6_1h_k0.75_thesis_full | H2 | 13 | 5.62 | 30.8% | -0.47 | -$61.53 | 65.2% |
| **a6_15m_k1.0_thesis_half** | full | 27 | 5.83 | 48.1% | 14.66 | **$151.64** | 55.0% |
| **a6_15m_k1.0_thesis_half** | H1 | 16 | 6.91 | 56.2% | 13.23 | $173.29 | 22.6% |
| **a6_15m_k1.0_thesis_half** | H2 | 11 | 4.75 | 36.4% | 1.43 | -$21.65 | 55.0% |

**brent_replay30** (2026-09-30 16:00Z -> 2026-10-30 16:00Z, 30 theses)

| variant | window | trades | t/wk | win% | net R | net $ | max DD% |
|---|---|---|---|---|---|---|---|
| live | full | 32 | 7.47 | 50.0% | 11.65 | **$149.69** | 54.6% |
| live | H1 | 24 | 11.20 | 37.5% | -0.50 | -$12.84 | 54.6% |
| live | H2 | 8 | 3.73 | 87.5% | 12.15 | $162.53 | 16.1% |
| a6_1h_k0.75_thesis_half | full | 41 | 9.57 | 51.2% | 16.92 | $199.72 | 52.4% |
| a6_1h_k0.75_thesis_half | H1 | 30 | 14.00 | 43.3% | 4.22 | -$3.01 | 52.4% |
| a6_1h_k0.75_thesis_half | H2 | 11 | 5.13 | 72.7% | 12.70 | $202.73 | 26.0% |
| a6_1h_k0.75_thesis_full | full | 41 | 9.57 | 51.2% | 16.92 | $237.89 | 50.2% |
| a6_1h_k0.75_thesis_full | H1 | 30 | 14.00 | 43.3% | 4.22 | $6.09 | 50.2% |
| a6_1h_k0.75_thesis_full | H2 | 11 | 5.13 | 72.7% | 12.70 | $231.80 | 35.1% |
| **a6_15m_k1.0_thesis_half** | full | 40 | 9.33 | 47.5% | 11.75 | **$157.27** | 53.6% |
| **a6_15m_k1.0_thesis_half** | H1 | 28 | 13.07 | 39.3% | -0.18 | -$9.97 | 53.6% |
| **a6_15m_k1.0_thesis_half** | H2 | 12 | 5.60 | 66.7% | 11.92 | $167.23 | 22.6% |

`a6_1h_k0.75_thesis_*` is the single best variant by RAW combined edge
(+$19.99 to +$23.14) — and it fails the bar outright: it wins replay30's
full window by $50-88 but LOSES brent_journal's full window by $27-68, and
the `_full` size variant also blows the drawdown bar (66.5% vs 55.2%,
+11.3pp). `a6_15m_k1.0_thesis_half` is the one variant (bolded) that
nominally clears both parts of the bar — net $ ahead of live in all four
halves, drawdown a hair BETTER than live on both datasets — but by $2-10
per half against a $141-170 live base, on a dataset of 27-40 trades where
8 of those trades (19->27, 32->40, the same gap on both datasets) are the
entire a6 contribution.

### Why `a6_15m_k1.0_thesis_half` is NOISE, not PASS

Every one of its 8 one-axis neighbors (`is_isolated` — computed, not
eyeballed):

| neighbor (one axis changed) | combined edge | wins both datasets? |
|---|---|---|
| a6_1h_k1.0_thesis_half (confirm) | -$8.86 | no |
| a6_15m_k0.5_thesis_half (stop) | -$0.65 | no |
| a6_15m_k0.75_thesis_half (stop) | +$0.93 | **no — journal +23.16, replay -22.23** |
| a6_15m_mid_thesis_half (stop) | +$2.17 | **no — journal -20.74, replay +22.91** |
| a6_15m_k1.0_24hext_half (target) | -$31.94 | no |
| a6_15m_k1.0_1.5R_half (target) | -$86.51 | no |
| a6_15m_k1.0_2R_half (target) | -$34.30 | no |
| a6_15m_k1.0_thesis_full (size) | -$5.63 | no |

Not one neighbor also wins both datasets; the two closest (k0.75, midpoint)
each win ONE dataset by 20+ dollars and lose the OTHER by about the same —
exactly the "wins big on one dataset, loses on the other" shape the
passing variant alone escapes, by a margin ($7-10 per half) far smaller
than the swings its neighbors show. That is a coin landing heads once in
64 tries, not a parameter-stable rule. **Verdict: NOISE.**

## Adoption bar scoring

| bar | result | why |
|---|---|---|
| 1. Net $ wins both halves, every primary dataset | pass (1 of 64: `a6_15m_k1.0_thesis_half`) | journal H1 +$3.85, H2 +$6.31; replay30 H1 +$2.87, H2 +$4.70 — all positive, all under $7 |
| 2. Drawdown not materially worse (<=5.0pp) | pass for that same variant | journal 55.0% vs 55.2% (better); replay30 53.6% vs 54.6% (better) |
| Isolation / fill-count sanity | **FAIL** | the only passing point has 0 of 8 neighbors also passing; trade count is right at the 8-fill-of-new-trades margin on both datasets |

**Verdict: NOISE.** Two of two mechanical bars pass for exactly one of 64
grid points; the isolation check that exists precisely to catch this (a
64-point scan is expected to throw up about one coincidental pass even with
zero true effect) fails it. Nothing is adopted.

## The two named cases

**2026-11-18 09:10Z short → 19:37Z** (`oil-20261118T0910-m.json`, sell a
rally to 90.30-91.17; Brent instead fell toward 87.6 after EIA):

- **Live: NO FILL** (confirmed — the rule only sells a rally up to the
  entry; price never rallied).
- **a6: fires on 28 of 64 variants, LOSES on all 28.** Representative fill
  (`a6_1h_k0.5_1.5R_full`, and 20+ others land on the identical bar): short
  **88.035 at 17:00Z**, stopped **19:00-19:15Z at -1.1 to -1.2R** (-$13 to
  -$28 depending on size/leverage). The entry correctly catches the
  breakdown direction — but by 17:00Z price has already fallen most of the
  way to that level, the stop sits tight (back inside the broken level by
  0.5-1.0x ATR), and a normal two-hour bounce clips it before the larger
  move continues down to 87.6. Zero of the 28 fills is a win.

**2026-11-19 00:32Z long → 14:00Z** (`oil-20261119T0032-m.json`, buy a dip
to 88.40-88.80, target 91.664; Brent ran 88.6 -> 90.34 with no entry):

- **Live: NO FILL** (confirmed — price never dipped to 88.40-88.80, it
  ran straight up).
- **a6: fires on 44 of 64 variants, wins on exactly 6** — all six the
  fast-confirm, tight-target shape: `a6_15m_k0.5/k0.75_1.5R/2R_full/half`.
  Entry **89.414 at 01:30Z** (15m confirm), target hit **01:45Z**, +1.22R
  to +1.68R (+$9.87 to +$25.68). Every OTHER fill — every 1h-confirm
  variant, and every 15m-confirm variant whose target is the far "thesis"
  or "24h extreme" level instead of a tight fixed R — enters 30-60 minutes
  LATER (02:00-02:30Z, after price had already run further), and is
  stopped on the following pullback by 03:00-11:30Z for -1.1R to -1.2R
  (-$11 to -$27). Including the one nominally-passing grid variant itself
  (`a6_15m_k1.0_thesis_half`): it fires at the SAME 01:30Z/89.414 entry as
  the winners, but because its target is the thesis's own far 91.664
  instead of a tight fixed R, it holds through the later chop and gets
  stopped at 11:30Z for -1.14R (-$18.08) — a loss, on the one grid point
  that otherwise "passes."

The mechanism is the same in both cases and consistent with the grid-wide
result: a6 correctly identifies the breakout DIRECTION (it enters short in
case 1, long in case 2, exactly when the operator wanted a fill), but
whether that specific fill wins depends almost entirely on how far away its
target sits — a tight target banks a quick, small win before the next
pullback; a structural or thesis target holds through that pullback and
eats the stop. No single choice of target wins both cases: the tight-R
target wins case 2 outright but is the single worst-performing target axis
on the full grid (-$34 to -$87 combined edge); the thesis target survives
better on the full grid but loses case 2 and every one of the 28 case-1
fills regardless of target choice.

## Caveats

- **Sample size.** 8 extra (a6) trades per primary dataset over the fill
  gap live->best-variant; 27-41 total trades per dataset at the grid's
  edges. Enough to see the full-window number move (and to see exactly
  which single point moves it, and why its neighbors do not), not enough
  to call a coincidental pass a rule.
- **The a5 precedent (silver, 2026-11-04).** A close cousin of this entry
  shipped live and was a review LEAK (1 win in 10 real fills) inside two
  weeks. This test's finding — direction is usually right, the specific
  fill's win/loss is target-distance-dependent and highly sensitive to the
  exact parameters — is consistent with that outcome, not a coincidence
  this test happens to also find.
- **EIA/headline blackout** is not modeled in `live_brent_params()`'s
  baseline (same as every sibling Brent eval; the live-only overlay is
  `services/strategies/event_blackout.py`). Case 1's fill at 17:00-19:15Z
  sits well after the EIA print (~15:30 UTC); not material to that result.
- **$64 starting equity** is the shared eval convention (`stop_cap_test.
  START`), not a claim about the live account's balance; every $ figure
  scales linearly with it, trade count and R do not depend on it.

## Reproduce

```
python -m services.strategies.evals.breakout_entry_test \
    --oil-theses-dir "$PATHIEL_STATE_DIR/strategies/oil/theses" \
    --oil-replay-dir /tmp/thesis-replay-30b/oil/theses
python -m services.strategies.evals.breakout_entry_test --quick   # fast smoke run
python -m pytest services/strategies/tests/test_breakout_entry_sim.py -q   # gate tests, <2s
```

Writes `/tmp/breakout-entry-test/report.json`, `summary.csv`, `cases.csv`.
Reads candles through the eval disk cache and the venue's public `/info`;
reads the oil journal READ ONLY. No live account, config, `place_bracket`,
or desk-tick agent touched. No LLM calls.

Files: `services/strategies/evals/breakout_entry_test.py`
(the 64-variant grid, the adoption bar and isolation check, the two named
cases); `services/strategies/tests/test_breakout_entry_sim.py` (gate tests
for the a6 signal, <2s, synthetic candles); `desk_tick_sim.py`'s new
`Params.pullback_entry == "a6"` fields and wiring (every default keeps
every existing result byte-identical — confirmed by the full, unmodified
`test_desk_tick_sim.py`/`test_pullback_sim.py`/`test_silver_breakout.py`
suites passing unchanged).

## Paper test 2026-11-19..21

NOISE did not settle whether `a6_15m_k1.0_thesis_half` is worth a second
look once there are more real fills to judge it on. Operator, 2026-11-19:
yes to a 2-day PAPER test of that one variant — log only, no orders, no
money, running next to the real desk so the two can be compared on the
exact same window.

**What runs.** `scripts/paper_breakout.py`, one invocation = one paper
tick, scheduled every 15 minutes:

```
.venv/bin/python scripts/paper_breakout.py --start <ISO8601 UTC>
```

Reads the live oil thesis journal the same way the live desk does
(`services.strategies.backtest.data.load_journaled_theses` +
`desk_tick_backtest.journaled_direction`, READ ONLY) and live xyz:BRENTOIL
15m/1h candles through the repo's existing public reader
(`pathiel.client.hl_client.fetch_hl_candles`, no disk cache). Every tick
replays `desk_tick_sim.simulate` — never reimplemented — from `--start`
through the latest closed bar, with the EXACT `a6_15m_k1.0_thesis_half`
Params (`scripts/paper_breakout.A6_OVERRIDES`, copied verbatim from this
eval's own `variants()`) built on `live_params.live_brent_params()`, the
same function every "live" baseline in this repo is built from. Because
the sim's no-lookahead property holds and the replay window only grows
forward, redoing the whole replay every tick and diffing it against the
last tick's own state (`diff_tick`) is exactly equivalent to a live step
function — `tests/test_paper_breakout.py` proves this wrapper and a direct
`simulate()` call agree bit for bit on the same bars.

Mechanics are the sim's own: taker entry fee + slippage, stop/target on 15m
bars (target is a resting limit — a touch does not fill, only a
trade-through), the Friday weekend flat, and the oil desk's SCHEDULED
blackouts (the weekly EIA report ±2h and every impact-3 macro event for
Brent, both precomputed from the same calendars the live overlay reads and
fed into the sim's own static `Params.event_blackout_windows` — not
reimplemented). Not modeled: the live desk's HEADLINE blackout
(`services.strategies.event_blackout`'s surprise-news overlay) — it cannot
be known ahead of the moment a headline fires, and replaying it would need
the same LLM-backed news read the live desk uses, which this script is
barred from calling. This is the same gap this file's own Caveats section
already carries for the eval baseline, not a new one.

**Where the log is.** `$PATHIEL_STATE_DIR/paper/breakout_a6.jsonl` (one
JSON object per OPEN/CLOSE event) and `breakout_a6_state.json` (the
persisted state a later tick resumes from). One line is also printed and
appended to `$PATHIEL_OIL_PROGRESS_LOG` (default `/tmp/oil-loop/
progress.log`, the same file the live desk-tick loop writes to):

```
PAPER-A6 <utc> mid=<mid> pos=<flat|side size@entry stop=.. tp=..> action=<NONE|OPEN|STOP|TARGET|FLAT> <reason>
```

`FLAT` covers every close that is not the position's own stop or target
price (weekend, invalidation, a thesis direction flip) — the reason text
says which.

**How to read the report.** `--report` (safe at any time, never trades):

```
.venv/bin/python scripts/paper_breakout.py --report
```

Prints this variant's own trades/wins/net R/net $ since `--start` (at the
eval's $64 scale, half size on every a6 fill — the real account balance
lives under `~/.pathiel`, which this script never opens, so every $ figure
here scales linearly with the real desk's actual equity, the same
convention this whole file used throughout) split into live-rule fills
(the unchanged main structural rule, still active underneath the a6
overlay) vs a6-only fills, next to the real desk's own closed trades in the
same window, read from `$PATHIEL_STATE_DIR/review/ledger.jsonl` (READ
ONLY). After `--end` (default `--start` + 48h), every invocation prints
this same report and takes no further action — the replay window never
extends past `--end`, so no new entry can ever be considered past it.

**First real-data check, 2026-11-19 (dry run, no state written).** Replaying
2026-11-17 23:30Z → 2026-11-19 23:30Z against the real journal and real
candles reproduces the exact case-2 fill this file's own table already
described: a6 long **89.414 at 01:30Z** (15m confirm), stopped **88.638 at
11:30Z for -1.14R**, matching the "-1.14R (-$18.08)" row in the two-named-
cases section above R-for-R (the $ figure differs only because that number
compounds off the full month-long dataset's own equity curve at that point,
not a fresh $64 at this test's own `--start` — R is equity-size invariant
and is the number that matched). One independent, real-data confirmation
that the mechanics replay here is byte-correct against the already-published
finding, not a new one.

**No live-money path exists to find.** No pathiel MCP tool, no hosted LLM
call, no write under `~/.pathiel` or `.env.local`; a static-import gate test
(`tests/test_paper_breakout_no_orders.py`) checks the script never imports
or calls an order-placing symbol directly.

Files: `scripts/paper_breakout.py`; `tests/test_paper_breakout.py` (signal
parity, stop/target/weekend mechanics, the event-blackout calendar, the
state machine, state persistence across three separate calls, the report
math, and the CLI); `tests/test_paper_breakout_no_orders.py` (the
no-order-import gate).
