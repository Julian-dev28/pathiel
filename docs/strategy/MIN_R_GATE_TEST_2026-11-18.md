# NOT ADOPTED: 1.5R trades more often and makes less money, on both real oil datasets

Operator, 2026-11-18: "drop the minimum from 2R to 1.5R, test it." Complaint: too few trades.

**Answer: both halves of the question point the same way.** 1.5R gets a handful more
trades (+2 on the live journal, +5 on the 30-day replay) but LESS total money than
today's 2R gate, on the full window of both real-thesis datasets, before any half-split
or plateau check even runs. Lower min_r admits weaker setups; on this data those setups
lose more than the extra trade count is worth.

| min_r | journal trades/wk | journal win% | journal net R | journal net $ | journal DD% |
|---|---|---|---|---|---|
| 1.5 | 4.65 | 52.4% | 10.98 | **$110.27** | 55.2% |
| 2.0 (live) | 4.20 | 52.6% | 12.30 | **$141.47** | 55.2% |

| min_r | replay30 trades/wk | replay30 win% | replay30 net R | replay30 net $ | replay30 DD% |
|---|---|---|---|---|---|
| 1.5 | 8.63 | 37.8% | 5.99 | **$37.26** | 73.4% |
| 2.0 (live) | 7.47 | 50.0% | 11.65 | **$149.69** | 54.6% |

## Pre-registration (written before this file ran against real data)

The live oil entry gate (`pathiel.agents.bracket.DEFAULT_MIN_R`, replayed exactly by
`desk_tick_sim.live_params().min_r`, both 2.0) refuses any setup that pays under 2R on
the offset target. Question: does 1.5R make more money, or just more trades?

**Method.** Grid `min_r` in `{1.25, 1.5, 1.75, 2.0, 2.5}` on
`services/strategies/evals/live_params.py::live_brent_params()` — the one place every
other Brent eval builds its "live" baseline (its own module docstring documents the
earlier drift this module exists to stop). `min_r` is the only field that changes; the
2.0 row ties `live_brent_params()` exactly by construction
(`tests/test_min_r_gate_eval.py::test_2_0_ties_live_brent_params_exactly`).

Datasets, the same set and loaders `stop_cap_test.py` uses for Brent:
- `brent_journal` — the live oil journal's theses (READ ONLY, `$PATHIEL_STATE_DIR/strategies/oil/theses`), 2026-10-18 to data end
- `brent_replay30` — 30 replayed Opus theses, price only (`/tmp/thesis-replay-30b/oil/theses`), READ ONLY
- `brent_<series>_<n>d` — no-thesis controls (always_long / always_short / daily_sign), 30 and 50 days

`brent_journal` and `brent_replay30` are **PRIMARY**: they carry a real call, so they
gate the verdict. The no-thesis controls are **context only** — they can't tell a real
edge from a lucky trend (same reasoning `stop_cap_test.py` and `brent_stop_buffer_test.py`
already use). $64 starting equity, the shared convention every sibling eval's "live
sizing" number already uses (`live_params.py`'s module docstring; `stop_cap_test.START`)
— not a claim about the live account's real balance.

Per value, per dataset: trades, trades/week, win rate, mean R, net R (sum of
`r_multiple`), net $ (sum of realized `net_pnl_usd`), max drawdown % and worst losing
streak (longest run of non-winning trades). Split by first half / second half of the
dataset's own window (`stop_reclaim_test.thirds(start, end, n=2)`) and by long/short.

**Adoption bar, fixed before any table below was read:**
1. **Net $ win in BOTH halves** — 1.5R's net $ > 2.0R's net $ in both halves of EVERY
   primary dataset.
2. **Drawdown not materially worse** — "materially" = more than 5.0 percentage points.
   1.5R's full-window max drawdown must sit within that of 2.0R's, on every primary
   dataset.
3. **Plateau** — at min_r 1.25 and 1.75 (the grid's immediate neighbours of 1.5), the
   net-$ edge over 2.0R must be at least half of 1.5R's own edge, on every primary
   dataset where 1.5R actually has a positive edge to begin with.

All three must pass to ADOPT. Two of three with a stated reason is LEANING. Fewer is
NOT ADOPTED. No live constant, config, or `place_bracket` changed here either way —
adoption is the operator's call on this result.

Separately: replay the last 7 days of real desk ticks
(`/tmp/oil-loop/progress.log`'s `action=NONE` lines that quote "pays X R") and count how
many would have entered at 1.5R — R >= 1.5 AND no OTHER reason (chase, no-entry-after-a-
break, a headline/EIA blackout, a tool fault) blocking it, since none of those move with
min_r. Each qualifying episode is matched to the min_r=1.5 `brent_journal` run's own
trades by entry time, so "what it would have done" is read off the SAME sim the grid
above runs on real, publicly-read candles — not a second hand-rolled walk. The 7-day
window is anchored to the log's own last timestamp, not wall-clock time, so a re-run
reproduces the same window.

## Full grid

**brent_journal** (2026-10-18 04:07Z -> 2026-11-18 19:30Z, 96 theses)

| min_r | trades | trades/wk | win% | mean R | net R | net $ | max DD% | worst streak |
|---|---|---|---|---|---|---|---|---|
| 1.25 | 21 | 4.65 | 47.6% | 0.355 | 7.45 | $46.71 | 58.9% | 3 |
| **1.5** | 21 | 4.65 | 52.4% | 0.523 | 10.98 | $110.27 | 55.2% | 3 |
| 1.75 | 20 | 4.42 | 55.0% | 0.660 | 13.21 | $168.48 | 55.2% | 3 |
| **2.0 (live)** | 19 | 4.20 | 52.6% | 0.647 | 12.30 | $141.47 | 55.2% | 3 |
| 2.5 | 14 | 3.10 | 42.9% | 0.466 | 6.52 | $41.85 | 55.0% | 3 |

**brent_replay30** (2026-09-30 16:00Z -> 2026-10-30 16:00Z, 30 theses)

| min_r | trades | trades/wk | win% | mean R | net R | net $ | max DD% | worst streak |
|---|---|---|---|---|---|---|---|---|
| 1.25 | 42 | 9.80 | 35.7% | 0.083 | 3.47 | $7.66 | 73.4% | 7 |
| **1.5** | 37 | 8.63 | 37.8% | 0.162 | 5.99 | $37.26 | 73.4% | 6 |
| 1.75 | 36 | 8.40 | 41.7% | 0.310 | 11.16 | $135.09 | 60.9% | 5 |
| **2.0 (live)** | 32 | 7.47 | 50.0% | 0.364 | 11.65 | $149.69 | 54.6% | 4 |
| 2.5 | 18 | 4.20 | 38.9% | 0.189 | 3.41 | $15.59 | 55.5% | 4 |

Shape: net $ climbs from 1.25 up through 1.75/2.0, then falls off at 2.5 (too few
trades to carry the fixed costs). **1.5R sits on the losing side of that curve, not at
its peak**, on both primary datasets.

## Half split (first half / second half of each dataset's own window)

**brent_journal**

| min_r | H1 trades | H1 net $ | H1 DD% | H2 trades | H2 net $ | H2 DD% |
|---|---|---|---|---|---|---|
| 1.25 | 10 | $111.49 | 28.3% | 11 | -$64.77 | 58.7% |
| **1.5** | 10 | $123.75 | 28.3% | 11 | -$13.48 | 54.9% |
| 1.75 | 10 | $148.23 | 28.3% | 10 | $20.26 | 55.0% |
| **2.0 (live)** | 10 | $169.44 | 18.8% | 9 | -$27.96 | 55.0% |
| 2.5 | 5 | $56.33 | 17.3% | 9 | -$14.48 | 55.0% |

1.5R loses to 2.0R in H1 ($123.75 vs $169.44, edge -$45.69) and is close but still
behind nothing to brag about in H2 (-$13.48 vs -$27.96, the ONE half where 1.5R is
ahead — edge +$14.48). Bar 1 needs BOTH halves; it only gets one.

**brent_replay30**

| min_r | H1 trades | H1 net $ | H1 DD% | H2 trades | H2 net $ | H2 DD% |
|---|---|---|---|---|---|---|
| 1.25 | 32 | -$41.20 | 73.4% | 10 | $48.86 | 16.1% |
| **1.5** | 29 | -$34.36 | 73.4% | 8 | $71.62 | 17.8% |
| 1.75 | 28 | -$12.02 | 60.9% | 8 | $147.11 | 17.8% |
| **2.0 (live)** | 24 | -$12.84 | 54.6% | 8 | $162.53 | 16.1% |
| 2.5 | 14 | -$25.49 | 53.4% | 4 | $41.09 | 16.1% |

1.5R loses to 2.0R in BOTH halves here (H1 -$34.36 vs -$12.84, edge -$21.52; H2 $71.62
vs $162.53, edge -$90.91). Bar 1 fails outright on this dataset.

## Long / short split

No real short ever filled, at any min_r, on either primary dataset, across the whole
period — the oil desk's short calls have spent this window pinned by the chase gate
(the 7-day tick replay below shows this directly: most "pays X R" NONE ticks also say
"mid above/below thesis entry", i.e. blocked by chase, not by R). Every primary trade in
this table is long. (The `always_short` control, context only, does short — see
`report.json`/`summary.csv` for its full table; it loses at every min_r on both halves,
same shape as the primary datasets.)

## Context: no-thesis controls (not part of the adoption bar)

Full-window net $, 1.5R vs 2.0R (30-day windows unless noted):

| dataset | 1.5R net $ | 2.0R net $ | 1.5R beats 2.0R? |
|---|---|---|---|
| brent_always_long_30d | -$40.76 | -$36.32 | no |
| brent_always_short_30d | -$32.09 | -$42.32 | yes |
| brent_daily_sign_30d | -$19.12 | -$33.09 | yes |
| brent_always_long_50d | -$13.40 | $25.77 | no |
| brent_always_short_50d | -$59.06 | -$60.80 | yes |
| brent_daily_sign_50d | -$40.45 | -$39.64 | no |

Mixed, as expected from a no-thesis series that isn't picking sides on purpose — exactly
why these are context, not gate. Full per-variant, per-half numbers for all six: `/tmp/
min-r-gate/report.json` (`datasets.brent_*`) and `summary.csv`.

## Adoption bar scoring

| bar | result | why |
|---|---|---|
| 1. Net $ wins both halves | **FAIL** | journal H1 edge -$45.69; replay30 H1 edge -$21.52; replay30 H2 edge -$90.91 |
| 2. Drawdown not materially worse (<=5.0pp) | **FAIL** | replay30: 1.5R DD 73.4% vs 2.0R 54.6%, gap 18.8pp |
| 3. Plateau (1.25R, 1.75R keep >=50% of 1.5R's edge) | pass (moot) | 1.5R never has a positive full-window edge over 2.0R on either primary dataset, so there is no edge for a neighbour to preserve — the check is vacuously true, not a point in 1.5R's favour |

**Verdict: NOT ADOPTED.** Not close either: 1.5R trails 2.0R on the full window before
the half-split or plateau check even runs.

## 7-day replay of real desk ticks

`/tmp/oil-loop/progress.log`, anchored to its own last timestamp (2026-11-18 19:23Z),
trailing 7 days (2026-11-11 19:23Z onward): 425 `action=NONE` rows quote a "pays X R"
clause. Counting rows where R clears the gate AND no other reason (chase, break,
blackout, a tool fault) blocks it:

| min_r | qualifying ticks |
|---|---|
| 1.25 | 10 |
| **1.5** | **4** |
| 1.75 | 1 |
| 2.0 (live) | 0 |
| 2.5 | 0 |

Zero at 2.0R is consistent with the real desk never logging a live entry off this gate
in the window — the live rule was never actually tested here, it just never got the
chance. The 4 ticks at 1.5R fold into 2 distinct episodes (consecutive re-evaluations of
the same held-open setup, grouped with a 20-minute gap tolerance):

| episode start | ticks | max R quoted | what it would have done |
|---|---|---|---|
| 2026-11-16 02:40Z | 1 | 1.52R | **Matched** a min_r=1.5 sim trade: long from 92.10, exit `target` 2026-11-16 06:30Z, +1.79R, **+$40.23** |
| 2026-11-16 13:10Z | 3 | 1.79R | No sim trade matched within 30 minutes — explained below |

The first episode is a clean win, and it's the ONLY one of the 4 qualifying ticks across
7 real days that resolves cleanly: one extra trade, and it happened to work. The second
episode has no 1:1 match because the same min_r=1.5 sim is already in an (earlier,
losing) position by then — it entered at 12:30Z on a tick this log's "pays 1.5R+" filter
doesn't happen to flag (a different structural R at that exact bar), got stopped at
14:15Z for -1.11R / -$31.50, then opened again at 15:00Z and stopped again at 18:30Z for
-1.10R / -$26.62. That is the real mechanism behind the grid's headline number: 1.5R
enters more, including setups the 2R gate correctly declines, and several of those
extra entries lose.

Net read: in 7 real days this is a net +4 ticks / 2 episodes question, not a flood of
missed trades — the chase gate (never buy above / sell below the thesis entry) blocks
the overwhelming majority of "pays X R" ticks regardless of min_r, which is also why
trades/week only moves from 4.20 to 4.65 on the journal (+11%), not a multiple.

## Caveats

- **EIA/headline blackout is not modeled in this backtest baseline** (`live_brent_params()`
  leaves `Params.event_blackout_windows` off, same as every sibling Brent eval — the
  live-only overlay lives in `services/strategies/event_blackout.py`, OIL_STRATEGY.md's
  2026-11-03 entries). The tick replay above already excludes blackout-flagged rows from
  "qualifying," so this doesn't bias the 1.5R count; it only means the grid's own trade
  count could include a tick the live desk would also have blacked out. Not material
  here (no blackout-era entry was close to the margin in either primary dataset).
- **$64 is a normalization, not the live balance.** Every "net $" figure scales with it
  linearly; the trade count, win rate and R figures do not depend on it at all.
- **Half split keeps full lookback.** The second half's 24h-range/ATR context is built
  from the WHOLE dataset's 1h candles, not just that half's own bars (`_window`'s
  convention, shared with `thirds()`-based splits elsewhere in this codebase) — so an
  early trade in half 2 isn't penalized for a cold-start range.
- **Sample size.** 21 and 37 trades on the two primary datasets at 1.5R. Enough to see
  the full-window number move the wrong way on both, not enough to rule out a
  different 30-day stretch landing differently — which is exactly why the half-split and
  the 7-day real-tick replay are both in this file instead of just the one headline row.

## Reproduce

```
python -m services.strategies.evals.min_r_gate_test \
    --oil-theses-dir "$PATHIEL_STATE_DIR/strategies/oil/theses" \
    --oil-replay-dir /tmp/thesis-replay-30b/oil/theses
python -m services.strategies.evals.min_r_gate_test --quick   # fast smoke run
python -m pytest services/strategies/tests/test_min_r_gate_eval.py -q   # gate tests, <2s
```

Writes `/tmp/min-r-gate/report.json`, `summary.csv`, `tick_replay.csv`. Reads candles
through the eval disk cache and the venue's public `/info`; reads the oil journal and
`/tmp/oil-loop/progress.log` READ ONLY. No live account, config, `place_bracket`, or
desk-tick agent touched. No LLM calls.

Files: `services/strategies/evals/min_r_gate_test.py`;
`services/strategies/tests/test_min_r_gate_eval.py`.
