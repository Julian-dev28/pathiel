# Pullback entry and correlation guard test, 2026-11-02

**Verdict: keep live for both. No pullback entry and no cross-desk guard
clears the bar.**

| Fix | Best result | Why it fails |
|---|---|---|
| A, pullback entry (Brent) | `a3_k1_c2_far` beats live on 5 of 8, both thesis sets up (replay $414 to $702, journal $894 to $919) | geo-mean 0.87, sitting out also beats live on 5 of 8, 0 of 3 neighbours pass |
| A on silver | `a3_k1_c2_far` 4 of 8, geo 1.14 | not most, 0 of 3 neighbours, drawdown 68.8% vs 66.7% |
| B, guard as briefed (no silver short while oil short) | `b_same_silver_m0` 1 of 8, geo 0.93 | blocks a hedge: oil and silver move opposite ways |
| B, the data's pair (no oil long next to a silver short, and back) | `b_opposite_both_m0` 2 of 8 (2 lose, 4 tie), geo 1.08, drawdown 77.9% vs 79.7% | not most, cuts the replay $464 to $329, 0 of 1 neighbour |
| B3, same driver in both theses | never acts | silver theses never cite Iran or Hormuz (0 of 6 journal, 0 of 30 replay) |

Operator, 2026-11-02: "YES" to testing both after the oil thesis turned
short at 08:42Z and Brent fell from 109 to 106.19 with the desk flat, and
the silver short was stopped as silver jumped.

What the test found that matters more than the verdict:

1. **The thesis's own levels do not fit 20x.** At 20x on Brent the stop must
   sit within about 1.2% of the entry (half the way to liquidation). The
   09:25Z call (short 109.30, stop 110.90) is 1.46% wide: written for 5x.
   Every a4 attempt on 11-02 was refused by that gate (12 ticks).
2. **On 11-02 the two desks were never short together.** The real silver
   short opened 05:34Z while oil was LONG (since 03:50Z). Oil was flat from
   08:35Z. The briefed guard would have done nothing that day.
3. **Oil and silver move opposite ways.** Hourly correlation -0.36 over 208
   days, -0.67 on |Brent 1h| >= 1% hours, silver moved the other way in 85%
   of those hours. So oil long + silver short is ONE bet doubled (both lost
   on the de-escalation), and oil short + silver short is a hedge.

## 1. 11-02 regression

### Fix A, Brent, 08:45Z to 14:00Z, the live journal's calls

$225 desk, 15% risk cap, 20x. $ at 14:00Z is marked at the 14:00Z mid
(106.60). At stop / target: that trade closed there, before slippage.

| Rule | Shorts? | Entry | Stop | Target | $ at 14:00Z | At stop | At target |
|---|---|---|---|---|---|---|---|
| Live | no: 21 ticks refused as a chase | | | | 0 | | |
| a1, 8h high stop | no: 18 wider than the thesis stop, 3 past 20x | | | | 0 | | |
| a1, 4h high stop | 13:30Z | 108.84 | 110.05 | 105.53 | **+56.37** | -33.77 | +83.65 |
| a2, retest (6h/12h/24h) | 13:00Z | 108.66 | 109.82 | 105.53 | **+53.80** | -33.77 | +82.17 |
| a2 at the leverage the stop allows | 09:00Z | 109.26 | 111.11 | 105.53 | **+45.13** | -33.77 | +63.45 |
| a3, bounce limit (every k, N) | no: same refusals as a1 8h | | | | 0 | | |
| a4, thesis levels | no: 12 past 20x, 9 stale | | | | 0 | | |
| a4 at the leverage the stop allows | rested at 109.30 (09:25Z call), lapsed unfilled; rested at 107.20 (13:38Z call), unfilled at 14:00Z | | | | 0 | | |
| Far 24h edge as target (any a1/a2/a3) | no: price was already under the 24h low, no room | | | | 0 | | |

The a4-at-fit row is the same thing the hand-placed 109.30 limit did:
Brent never traded back up to 109.30 inside two hours.

### Fix A, silver, same window, the metals journal

| Rule | Entry | Exit | $ at 14:00Z |
|---|---|---|---|
| Live | none (20 chase, 1 under 2R) | | 0 |
| a1, any window | 13:45Z short 51.15, stop 51.60 | open | +27.35 |
| a3 k0.5 c4/c8, k1 c8, k1.5 c8 (the rest no fill) | 13:30Z short 50.48 to 50.66 | stopped 13:45Z in the jump | -32.71 to -32.89 |
| a4 4h | 09:00Z short 50.40 (05:33Z call) | stopped 13:30Z | -32.53 |

### Fix B, 11-02, the real fills

| Guard | What it does on 11-02 | Real net |
|---|---|---|
| None (live) | | **-41.10** |
| Same side, silver only (the brief's B1), any mult | nothing: oil never held a short | -41.10 |
| Same side, both desks, any mult | nothing | -41.10 |
| Opposite side, silver only, block | blocks the 05:34Z silver short (oil long) | -38.25 |
| Opposite side, silver only, halve | halves it | -39.67 |
| Opposite side, both desks, block | blocks the 03:50Z oil long (silver short held since 01:18Z) | **-7.23** |
| Opposite side, both desks, halve | halves the oil long and the 05:34Z silver short | -22.74 |

A blocked trade counts 0 here; the desk might have entered later. n = 1 day.

### Fix B, 11-02 in the sim (90/10, 00:00Z to 14:00Z, both journals)

| Entry rule + guard | Account at 14:00Z | What changed |
|---|---|---|
| Live | 181.27 | oil long 02:45Z -10.51, oil long 03:15Z -30.01, silver short 05:30Z -3.21 |
| Live + same side (B1) | 181.27 | nothing |
| Live + opposite, silver only | 184.48 | silver short 05:30Z blocked (oil long) |
| a2 retest + live guard | 227.33 | oil short 13:00Z, +40.88 open |
| a2 retest + opposite, silver only | 230.30 | both of the above |
| a4 + opposite, both desks | 203.14 | oil longs blocked while silver short, one long at 05:30Z -16.68 |

## 2. Oil and silver correlation

From the venue's 1h candles, 04-08 to 11-02 14:00Z (4,999 hours).

| Window | n | Correlation | Big Brent hours (>= 1%) | Correlation on them | Silver moved the other way |
|---|---|---|---|---|---|
| 208 days, hourly | 4,999 | **-0.36** | 314 | **-0.67** | 85% |
| Last 30 days, hourly | 720 | -0.33 | 18 | -0.71 | 94% |
| 208 days, daily | 208 | -0.37 | 43 (>= 3%) | -0.61 | 81% |

On a big Brent down hour silver rose 0.57% on average; on a big up hour it
fell 0.51%. Negative in every month (hourly -0.24 to -0.52; big hours -0.56
to -0.75). On 11-02 13:00Z Brent -1.98%, silver +1.23%.

Position P&L correlation is side x side x correlation, so:

| Pair | What it is |
|---|---|
| Oil short + silver short (both desks short, the brief's case) | a hedge: one wins what the other loses on an Iran headline |
| Oil long + silver short (11-02, 05:34Z to 08:35Z) | one bet, doubled: both lose on de-escalation |

The guard the data asks for is the opposite-side one. It is what was tested
as the "opposite" pair; the brief's same-side guard was run as a contrast.

## 3. Fix A on every dataset

Live rule: market entry off the 24h range, 2R gate, Brent stop 0.75 beyond
the edge (silver 1.3 x ATR14 with the 0.8% floor), target 0.5% inside the
far edge, limit target, 20x, 15% risk cap, size slippage (Brent) and
funding. $225. The pullback only runs when live refuses the tick as a chase
or under 2R, so a tick where live trades is unchanged (gate test).

Variants: a1 stop beyond the last 4/6/8/12 1h highs; a2 retest within
6/12/24 bars; a3 bounce k = 0.5/1.0/1.5 ATR, cancel after 2/4/8 h; a4
thesis fresh under 1/2/4 h. Target "far" (24h edge) or "thesis" (its first
target). "fit" = at the highest leverage the stop allows, 5x or more. Every
a1 to a3 stop is capped at the thesis's own stop; my reading of "never above
the thesis entry for a short", since the entry itself has to be below the
thesis entry for a chase to happen. The uncapped a1 is in the table: worse.

Brent, 8 datasets (live mean drawdown 82.6%; sitting out beats live on 5):

| Best of each mode | Beats / loses | Geo-mean | Theses W/L | Neighbours | Pullback trades, R |
|---|---|---|---|---|---|
| a1 12h far | 1 / 6 | 0.85 | 0 / 2 | 0 of 1 | 36, -0.3 |
| a1 8h thesis | 1 / 6 | 0.60 | 0 / 2 | 0 of 2 | 53, -16.4 |
| a2 12h thesis | 1 / 1 | 0.99 | 1 / 1 | 0 of 2 | 2, -2.3 |
| a2 12h thesis fit | 1 / 1 | 0.98 | 1 / 1 | 0 of 2 | 2, -2.3 |
| **a3 k1 c2 far** | **5 / 3** | 0.87 | 2 / 0 | 0 of 3 | 55, +7.0 |
| a3 k1.5 c4 far | 3 / 5 | 0.91 | 1 / 1 | 0 of 3 | 75, +42.1 |
| a4 (any) | 0 / 2 | 0.91 | 0 / 2 | 0 | 5, +0.8 |

Silver, 8 datasets (live drawdown 66.7%; sitting out beats live on 4):

| Best of each mode | Beats / loses | Geo-mean | Theses W/L | Neighbours | Pullback trades, R |
|---|---|---|---|---|---|
| a1 4h far | 3 / 5 | 0.88 | 0 / 2 | 0 of 1 | 41, -3.4 |
| a2 6h thesis | 1 / 0 | 1.01 | 1 / 0 | 0 of 1 | 2, +1.2 |
| **a3 k1 c2 far** | **4 / 3** | **1.14** | 0 / 1 | 0 of 3 | 55, +23.4 |
| a4 4h | 1 / 1 | 1.00 | 1 / 1 | 0 of 1 | 2, +0.1 |

Read plainly:

- **a1 loses everywhere.** Its trades lose on both coins (Brent -16.4R on
  53 trades with the thesis target). A nearer stop sold into a breakdown is
  a stop the next bounce takes. By regime it gave back -23.3R in chop.
- **a2 almost never fires** (2 to 5 trades in 208 days). The one that
  mattered was 11-02. Not enough to judge.
- **a3's own trades make money** (+7R to +45R pooled on Brent at k 1 to
  1.5) **but the account does not**: a resting short holds the one slot, so
  the live entry that would have come at the range edge does not. On always
  long over 208 days a3 k1 c4 made +4.4R on its own trades and the run
  ended $45 vs live's $63. Sum of R and final dollars disagree; the bar
  scores dollars.
- **a4 cannot work at 20x.** The theses size their stops for 5x. On Brent
  12 of the a4 refusals on 11-02 were the leverage gate; across all data
  a4 traded 5 times. At the leverage the stop allows it still trades 5:
  the R gate refuses the rest (the 08:40Z call pays 1.8R at its own entry
  once the target gets the live 0.5% offset).

Full grid: `/tmp/pullback-corr/a_variants.csv` (both coins, 48 variants,
with pullback trades and the regime split).

## 4. Fix B on every dataset

Both desks on one 15-minute clock (`portfolio.py`), each sized off its
share of the account, 90/10 live (70/30 below), 15% cap. The guard reads
the other desk's open position at the end of the previous tick.

| Dataset | Window | Live | Same, silver, block (B1) | Same, both, halve (B2) | Opposite, silver, block | Opposite, both, block |
|---|---|---|---|---|---|---|
| Journals | 10-31 08:38Z to now | 156 | 156 | 156 | 159 | 159 |
| Replay | 10-01 to 10-30 | 464 | 464 | 464 | 438 | 329 |
| Always long, 208d 1h | 04-30 to now | 70 | 74 | 98 | 70 | 70 |
| Always long, 52d 15m | 09-12 to now | 1265 | 1107 | 547 | 1265 | 1265 |
| Always short, 208d | | 4 | 4 | 16 | 4 | 4 |
| Always short, 52d | | 85 | 65 | 86 | 85 | 85 |
| Daily sign, 208d | | 18 | 17 | 15 | 16 | 55 |
| Daily sign, 52d | | 225 | 216 | 219 | 187 | 185 |

| Guard (90/10) | Beats / loses of 8 | Geo-mean | Mean DD (live 79.7%) | Neighbours |
|---|---|---|---|---|
| Same, silver, block (B1) | 1 / 5 | 0.93 | 80.2% | 0 of 1 |
| Same, silver, halve | 1 / 5 | 0.97 | 80.0% | 0 of 2 |
| Same, both, halve (B2) | 3 / 3 | 1.08 | 79.3% | 0 of 2 |
| Opposite, silver, block | 1 / 3 | 0.96 | 79.7% | 0 of 1 |
| Opposite, silver, halve | 1 / 3 | 0.99 | 79.7% | 0 of 2 |
| **Opposite, both, block** | **2 / 2** | **1.08** | **77.9%** | 0 of 1 |
| Opposite, both, halve | 2 / 2 | 0.97 | 78.4% | 0 of 2 |

Sitting out beats live on 6 of 8 portfolio sets (4 of 8 at 70/30), so the
guard needs 7 (5 at 70/30). At 70/30 the order is the same: opposite-both-block
2 / 2, geo 1.02, DD 69.7% vs 71.2%; B1 1 / 5, geo 0.83.

- **B1 (same side) blocks hedges** and loses 5 of 8. Both thesis datasets
  tie: the two desks' theses were on opposite sides (oil long, silver
  short) through October.
- **Opposite side, both desks** is the one that would have saved 11-02
  ($34 on the real fills) and cuts drawdown, but it ties 4 sets, cuts the
  best thesis set by 29% (replay: 35 refused ticks, oil longs next to
  silver shorts, and in October those were both right), and its halving neighbour
  loses money.
- **B3** acts 0 times on both thesis sets: equal to live. The driver tags
  come from each thesis's own argument (one-liner, bull, bear): oil 19 of
  19 cite Iran or Hormuz, silver 0 of 6 (4 cite rates). The replayed
  theses carry no news, so no tags. Not testable until the silver desk
  writes its oil link into the thesis.

Full grid: `/tmp/pullback-corr/b_variants.csv`.

## 5. Sample size

| What | Size |
|---|---|
| Brent and silver history | 5,000 1h bars each (208 days), 5,000 15m (52 days) |
| Thesis sets | Brent 30 replayed + 19 journal; silver 30 replayed + 6 journal |
| Days with both desks on live calls | 2.2 (10-31 08:38Z to 11-02 14:00Z) |
| a2 trades | 2 to 5 per coin in all data |
| a4 trades | 5 Brent, 1 to 2 silver |
| Real 11-02 fills | 4 trades, 1 day |
| Big Brent hours for the correlation | 314 (208 days), 18 (30 days) |

The correlation is the one solid number here: 4,999 hours, negative in
every month. Every guard verdict rests on 2 thesis sets and 6 controls
where both desks follow fixed rules.

## 6. What would change the call

- **Run the theses at the leverage they are written for.** The test says the
  20x half-liquidation line is what blocked the short, not the entry rule
  alone. A desk that trades the thesis's levels at its own 5x (risk cap
  15%) is a different strategy; it needs its own test, not a variant here.
- **a2 retest:** right on 11-02, 2 trades in 208 days otherwise. Rerun
  monthly; with 15 or more a2 trades it can be judged.
- **Opposite-side guard:** rerun after silver has 15 or more closed trades
  under the live rules. If the two desks keep opening oil long next to
  silver short and both keep losing on the same headline, the 11-02 row
  stops being one day.

## 7. Live change

None. The closest candidates, if the operator wants to watch them:
`a3_k1_c2_far` (Brent 5 of 8, both thesis sets up, geo 0.87) and the
opposite-side guard on both desks (geo 1.08, drawdown 77.9% vs 79.7%, 2 of
8). Neither is adopted.

## 8. How it was run

```
python3 -m services.strategies.evals.pullback_correlation_test \
    --oil-theses-dir <journal>/oil/theses --metals-theses-dir <journal>/metals/theses
```

About a minute. Journals were copied read only to
`/tmp/pullback-corr/journal` at 13:57Z and run from the copy. Writes
`/tmp/pullback-corr/report.json`, `a_variants.csv`, `b_variants.csv`,
`correlation.csv`, `nov2.json`, `progress.log`. Candles and funding cached
in `/tmp/pullback-corr/cache` (public `/info`, read only, data to
11-02 14:00Z).

Code:

- `desk_tick_sim.py`: `Params.pullback_entry` (a1 to a4), `pb_struct_h`,
  `pb_target`, `pb_cap_thesis_stop`, `pb_retest_h`, `pb_bounce_atr`,
  `pb_cancel_h`, `pb_fresh_h`, `pb_fit_leverage`; `simulate(...,
  entry_guard=...)`; `Trade.entry_kind`, `Trade.leverage`,
  `Run.position_side`, `Run.guard_scaled`, `Run.open_position_levels`. All
  off by default. `desk_split_test`, `brent_stop_buffer_test` and
  `oil_regime_test` outputs are byte-identical before and after (every CSV
  by cmp, the reports less their timestamps).
- `portfolio.py`: `CorrGuard`, `guard_mult`, `simulate_portfolio(...,
  guard=...)`, `PortfolioRun.guard_log`. None keeps the old result.
- Gate tests: `services/strategies/tests/test_pullback_sim.py` (each mode's
  levels, lapse rules, the cap, fit leverage, live priority, guard block /
  scale / top-up, the portfolio guard), `test_pullback_corr_eval.py`
  (variants, correlation, the pair sign, driver tags, the guard on the real
  fills, and the 11-02 rows for both fixes on the real bars in
  `fixtures/nov2_brent_silver_bars.json`).

No live config, order, thesis, agent or skill file was touched. No LLM
calls.
