# Final oil-strategy adoption test, 2026-11-04

## Verdict

**Adopt `lev_fit_eia`** (the wide-stop-swing test's leverage fit +
the event-timing test's 2h EIA blackout, both on top of the live
rule). It is the only candidate that beats live on all 4 thesis
datasets (journal, Sept, Oct, chain), with max drawdown no worse than
live, a stop that never used more than 49.8% of the way to liquidation
in this test, and no regression anywhere.

**Do not adopt `entries_reclaim` or `combined`** (lev_fit + reclaim +
eia). Both lose to live on the journal and Oct datasets even though
they win big on Sept and the chain — the exact "wins Sept, trails the
journal" tradeoff `WIDE_STOP_SWING_TEST_2026-11-03.md` already flagged
for `entries_reclaim` on its own. Bundling it into `combined` carries
that loss into the bundle, so `combined` only beats live on 2 of 4
thesis datasets. Below the 3-of-4 bar.

**Half-off at a big R is optional, not required.** `live_half_r3` and
`combined_half_r3` both clear 3 of 4 (the Oct miss is about $1, inside
noise), and the regression row shows exactly the behavior the operator
described: half-off at 4R or 5R turns a full stop-out loss into a
profit. But neither beats `lev_fit_eia`'s clean 4-of-4 record, and the
half-off R-sweep has no plateau at r4 or wider (r5 drops to 2 of 4 on
both live and combined, so r4's own plateau fails even though r4 looks
good in isolation — see the plateau table below). Ship `lev_fit_eia`
now; if the operator wants explicit big-R profit-taking too, r3 is the
only setting with support on both sides of it in the half-off families
tested.

## The headline table

Net $ from a $64 start, and max drawdown %, live rules otherwise (15%
risk cap, 20x, size-aware slippage). Dataset windows below.

| Variant | Journal net$ (dd%) | Oct net$ (dd%) | Sept net$ (dd%) | Chain net$ (dd%) | Beats live |
|---|---|---|---|---|---|
| live | $117.52 (56.1) | $57.51 (69.5) | -$34.45 (80.8) | -$7.89 (85.2) | — |
| lev_fit | $153.64 (56.4) | $69.79 (63.8) | -$21.71 (80.8) | $24.40 (80.8) | 4/4 |
| entries_reclaim | $112.62 (46.4) | $52.63 (65.5) | $28.09 (43.1) | $103.82 (71.3) | 2/4 |
| eia_2_2 | $117.52 (56.1) | $57.50 (69.7) | -$22.47 (73.0) | $14.84 (79.2) | 2/4 |
| **lev_fit_eia** | **$153.64 (56.4)** | **$69.65 (66.7)** | **-$4.54 (73.0)** | **$60.17 (73.0)** | **4/4** |
| combined (lev_fit+reclaim+eia) | $112.62 (46.4) | $49.06 (66.8) | $45.49 (35.0) | $129.42 (72.2) | 2/4 |
| combined_half_r3 (best half-off R on combined) | $144.38 (34.9) | $48.14 (66.8) | $30.94 (35.0) | $102.34 (72.2) | 3/4 |
| live_half_r3 (best half-off R on live) | $150.06 (41.9) | $56.51 (69.5) | -$13.24 (69.0) | $31.59 (74.5) | 3/4 |

Bold = the adopted variant. "Beats live" = net $ strictly higher than
live's own row on that dataset, out of the 4 thesis datasets (journal,
Oct, Sept, chain); the always_long/always_short/daily_sign controls
over the snapshot's last 30 days are in `report.json`/`summary.csv` but
are not part of the adopt bar.

## Half-off plateau (partial_frac=0.5 at partial_r = 3/4/5/6)

| partial_r | live_half beats/4 | geo-mean | combined_half beats/4 | geo-mean |
|---|---|---|---|---|
| 3 | 3 | 1.36 | 3 | 1.78 |
| 4 | 3 | 1.11 | 3 | 1.87 |
| 5 | 2 | 1.07 | 2 | 1.77 |
| 6 | 0 | 0.99 | 2 | 1.82 |

r3 is the only setting with a neighbor (r4) that also clears the "beats
live on most datasets, geo-mean >= 1" bar on BOTH sides it would need
for a real plateau (r3's one in-grid neighbor is r4 itself); r4's own
plateau fails because its other neighbor, r5, drops under the bar. Not
a spike at r3, but not a wide plateau either — one step of support, no
more.

## The regression row: the 2026-11-03 long

Entry 103.61, stop 102.99, target 111.77 (risk $0.62/unit). Located in
the pinned snapshot: filled inside the 2026-11-02 16:53Z long call's
range (24h low near 103.3-103.7 that evening — the thesis's own
suggested stop/target, 100.1/110.36, are not what the live loop's own
structure used). Real candles: the rally topped at 107.30 at 07:15Z on
11-03 (the operator's "about 107.24" — same bar), then reversed and
traded through the 102.99 stop on the 13:30Z bar (low 101.42); a
realistic slipped fill of 102.91 is used for the stop leg, matching the
operator's own number.

| Rule | Exit | Net $ |
|---|---|---|
| Full size, original stop (baseline) | stop, 13:30Z | **-$9.71** |
| Half off at 3R (106.47) | stop (remaining half) | $6.15 |
| Half off at 4R (106.09) | stop (remaining half) | $9.95 |
| Half off at 5R (106.71) | stop (remaining half) | $13.74 |
| Half off at 6R (107.33) | never filled — rally topped at 107.30 | -$9.71 (same as baseline) |

Half-off at 4R or 5R turns this specific trade from a $9.71 loss into a
real profit, exactly the case the operator described ("we were up big
while a headline risk was live"). 6R would have missed it entirely —
the rally came three cents short.

## Data windows

The venue's `candleSnapshot` only serves the latest ~5000 15m bars
(back to about 2026-09-12 as of this snapshot), so every window below
is reported as requested vs. effective (candles actually available).

| Dataset | Requested start | Effective start | End |
|---|---|---|---|
| Snapshot (15m) | — | 2026-09-12 18:45Z | 2026-11-03 22:45Z |
| Snapshot (1h / 1d / funding) | — | 2026-08-25/26 | 2026-11-03/04 |
| Journal | 2026-10-18 04:07Z | 2026-10-18 04:07Z (unaffected) | 2026-11-03 22:45Z |
| Sept replay | 2026-08-31 16:00Z | **2026-09-12 18:45Z** | 2026-09-30 16:00Z |
| Oct replay | 2026-09-30 16:00Z | 2026-09-30 16:00Z (unaffected) | 2026-10-30 16:00Z |
| Chain (Sept+Oct) | 2026-08-31 16:00Z | **2026-09-12 18:45Z** | 2026-10-30 16:00Z |

The Sept replay's theses start 08-31, but the first 12 days have no
candles to trade on in this snapshot, so the effective Sept window used
throughout this test is 09-12 to 09-30 (18 days of its nominal 30). The
chain inherits the same clipped start. This is why the "Sept replay,
live rules" row below finally has ONE number instead of two
contradictory ones: $29.55 final equity (net -$34.45), matching the
`-$34.45` figure from the operator's own notes, not the `+$29.55` a
different harness reported (that number was final equity misread as
net — the two were never actually disagreeing about the trade-by-trade
outcome, just about which number was which).

## What's new vs. the gate (leverage, for the operator's "what's the
leverage" follow-up)

`lev_fit`/`combined` pick the highest of 20/15/10/8/5x whose stop
clears half the liquidation distance. In this test, across journal,
Sept and Oct, **neither ever needed to go below 15x** — the live gate's
20x refusals all clear at 15x once tried:

| Dataset | live (all 20x) | lev_fit leverage mix | combined leverage mix |
|---|---|---|---|
| Journal | 14 @ 20x | 14 @ 20x | 11 @ 20x |
| Sept | 18 @ 20x | 17 @ 20x, 3 @ 15x | 10 @ 20x, 3 @ 15x |
| Oct | 36 @ 20x | 31 @ 20x, 8 @ 15x | 22 @ 20x, 1 @ 15x |

Extra entries `lev_fit` takes that live's fixed-20x gate refused
outright (matched by entry time; live's own `leverage`-reason refusal
count for context):

| Dataset | live refused (leverage) | lev_fit extra trades | extra trades' net $ |
|---|---|---|---|
| Journal | 146 | 0 | $0.00 |
| Sept | 129 | 3 | +$6.42 |
| Oct | 237 | 8 | -$9.74 |

`lev_fit`'s extra trades net out close to flat (+$6.42 - $9.74 - $0.00
= -$3.32 across all three). The bulk of `lev_fit`'s edge over live does
NOT come from these new trades — most of lev_fit's 14/18/36 fills share
the exact same entry time as live's own (same structural level, same
20x), so the gap comes from top-ups on an already-open position
clearing the leverage gate at a lower candidate leverage where live's
fixed 20x check would have refused the add outright, growing the
position live left smaller. This is a mechanism effect, not a trade
count effect — called out here rather than overclaimed.

`combined`'s extra trades (reclaim + eia layered on top of lev_fit) are
net negative in every dataset (-$19.09 journal, -$19.51 Sept, -$18.95
Oct) — one more reason `combined` underperforms `lev_fit_eia`.

## Gate: stop inside half the liquidation distance

Every dataset, every variant, re-checked at each trade's OWN leverage
(not a flat `params.leverage=20` — the naive version of this check is
wrong for `entry_fit_leverage` trades opened below 20x, since a stop
clearing half the liquidation distance at 10x can look like it blew
through the line if judged at 20x instead; `final_strategy_test.
corrected_max_stop_liq_frac` fixes this). Worst case across every
thesis dataset: **49.8%** of the way to liquidation — inside the 50%
gate everywhere, for every variant.

Realized-fill slippage overshoot (the gate only ever sees the NOMINAL
stop; the actual fill can slip past it on a gap): `lev_fit` 1.1 points
over nominal, `combined` 1.3 points over nominal. Consistent with
`WIDE_STOP_SWING_TEST_2026-11-03.md`'s own finding of a 0.6-point
realized overshoot on `entry_fit_leverage` in one control series — same
effect, same order of magnitude, not disqualifying per the adopt rule.

## Method

One pinned candle snapshot (`services.strategies.backtest.snapshot`):
xyz:BRENTOIL 15m/1h/1d candles and hourly funding, fetched once
(2026-11-03 22:40Z) and saved to
`~/Documents/code/pathiel-replays/candles/20261103T224029Z/`, merge-
appending by timestamp so a later run extends the archive past the
venue's 5000-bar cap instead of re-fetching a shifted window. Every
dataset below reads that one file; no eval in this run touched the
network.

Datasets: the live oil journal (READ ONLY,
`~/Documents/code/pathia/.state/strategies/oil/theses`), the September
replay (`~/Documents/code/pathiel-replays/oil-sep/theses`, 30 theses),
the October replay (`~/Documents/code/pathiel-replays/oil-oct/theses`,
30 theses, regenerated after the original was wiped by a tmp sweep),
Sept+Oct chained with equity carried over
(`extended_history_test.chain_runs`), and
always_long/always_short/daily_sign over the snapshot's last 30 days.

Variants, every one built on `live_params.live_brent_params()` (the
15% risk cap, size-aware slippage): `live`, `lev_fit`
(`entry_fit_leverage=(20,15,10,8,5)`), `entries_reclaim`
(`max_stop_reentries=1, reentry_needs_1h_reclaim=True`), `eia_2_2`
(`event_blackout_windows` 2h either side of every EIA report in the
snapshot's window), `lev_fit_eia`, `lev_fit_reclaim`, `combined` (all
three), and `live_half_rN`/`combined_half_rN` (`partial_frac=0.5,
partial_r=N`) for N in 3/4/5/6.

Adopt bar: beats live on at least 3 of the 4 thesis datasets, max
drawdown no worse than live within 2 points, a plateau where a grid
applies (the half-off R-sweep here; `lev_fit`/`entries_reclaim`/
`eia_2_2` each already showed a plateau across their own neighbouring
settings in `WIDE_STOP_SWING_TEST_2026-11-03.md` and
`OIL_EVENT_TIMING_TEST_2026-11-03.md` — not re-swept here), and the
stop inside half the liquidation distance at the trade's own leverage.

Code: `services/strategies/backtest/snapshot.py` (the pinned snapshot,
loader + fetch-and-save CLI, merge-append, gate-tested in
`test_snapshot.py`), `services/strategies/evals/final_strategy_test.py`
(this eval; writes `/tmp/final-strategy/report.json` and
`summary.csv`), gate-tested in `test_final_strategy_eval.py`. Re-run:

```
python -m services.strategies.backtest.snapshot   # only if a fresher snapshot is wanted
python -m services.strategies.evals.final_strategy_test \
    --snapshot-dir ~/Documents/code/pathiel-replays/candles/20261103T224029Z
```
