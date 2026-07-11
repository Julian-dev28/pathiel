# W-Y1 — Young-listing frontier (xyz HIP-3 dex) + the min_history_bars=60 floor

Lane Y, 2026-07-10. Scripts: `hypotheses/W-Y0_fetch.py` (cache),
`hypotheses/W-Y1_young_backtest.py` (H1/H2/H3), `hypotheses/W-Y2_floor_counterfactual.py`
(PIT log counterfactual). Data: `hypotheses/W-Y_cache_xyz_daily.json` (100 xyz markets,
86 with history, oldest 270 daily bars so EVERY coin's young window is in-sample),
`W-Y_cache_blocked_1h.json`, `W-Y_cache_universe.json`. Results:
`W-Y1_results.json`, `W-Y2_counterfactual.json`.

## Hypotheses (pre-registered)

- H1 MOVER CONTINUATION: young (bar-age 2-59) coin prints >= +8% day on >= $3M
  dollar volume -> LONG next open. Sweep hold {1,2,3,5}d x stop {8,15,20}%.
- H2 CRASH SIDE: young coin prints <= -8% day (same $3M floor) -> BOTH
  continuation-SHORT and fade-LONG, same sweeps.
- H3 POST-LISTING DRIFT: sign of first 3 completed days -> hold 10d same direction.
- CONTROL: identical rules on the SAME coins' mature windows (bar > 60).

Discipline: signal at day-i close, fill at day-(i+1) open; costs 25 bps/side
(0.50% RT) and 40 bps/side; one open episode per coin; incomplete holds dropped;
MC null = 2000 iters of same-coin same-window random-entry portfolios, one-sided
p on mean net@25; OOS split = coins by median LISTING date (2026-03-25) into
EARLY vs LATE cohorts.

## Verdict table (best cell per hypothesis; full 72-cell table in W-Y1_results.json)

| Hypothesis | window | best cell | n | EV@25 | EV@40 | OOS early / late (EV@25) | MC p | VERDICT |
|---|---|---|---|---|---|---|---|---|
| H1 long continuation | young | h=1d s=8% (least bad) | 95 | -2.03% | -2.33% | -0.44% / -2.80% | 0.996 | **REFUTED** (every cell -2.0..-3.7%, p 0.97-1.00 = WORSE than random-timing long) |
| H1 control | mature | h=3d s=15% | 115 | +1.47% | +1.17% | +1.51% / -0.75% (late n=2) | 0.081 | not significant |
| H2 continuation short | young | h=5d s=20% (least bad) | 72 | -1.74% | -2.04% | -2.24% / -1.49% | 0.590 | **REFUTED** (all 12 cells negative) |
| H2 short control | mature | all cells | 63-78 | -1.7..-3.2% | worse | — | 0.60-1.00 | REFUTED too |
| H2 fade-LONG | young | h=1d s=15-20% | 90 | **+1.17%** | +0.87% | **+1.70% (n=26) / +0.95% (n=64)** | **0.011** | passes the pre-registered gate — see caveats |
| H2 fade-LONG control | mature | h=1d s=20% | 78 | **+1.63%** | +1.33% | +1.73% (n=76) / n=2 degenerate | **<0.001** | same edge, STRONGER |
| H3 drift | young | 10d nostop | 81 | +0.59% | +0.29% | **-0.47% / +1.67% (sign flip)** | 0.228 | **REFUTED** (OOS sign flip = noise) |

Fade-long robustness: 35 distinct coins; excluding the top contributor
(xyz:KIOXIA) mean stays +0.82%; monthly means positive 7 of 9 months
(only 2026-01 and 2025-11 negative). Wider stop beats tight (8% stop: +0.84%
vs 15-20%: +1.17%) — consistent with the sweep-stop-width lesson.

## The decisive comparison

The ONLY +EV structure in the young window (crash fade-long, next-day bounce)
exists in the mature window too, and is STRONGER there (+1.63% p<0.001 vs
+1.17% p=0.011). **Young-listing behavior has NO unique +EV structure.** The
young window is not better than mature on any hypothesis; it is strictly worse
on H1 (young movers mean-revert harder: -2.0% vs -0.3% mature at h=1).

The operator's motivating case — chase the young mover (xyz:ZHIPU +17.6%) —
is the single most refuted cell in the study: buying a young >= +8% day at next
open loses 2-3.7% per trade at every hold/stop, and MC p ~ 1.0 means it is
worse than entering the same young coins on random days. The floor is
protecting us from exactly that trade. The MINIMAX question resolves the other
way: crash continuation-short is -EV; the +EV response to a young -8% day is
the fade-LONG, hold ~1 day.

## Task 2 — realized counterfactual of the floor (PIT, from our own logs)

`history_floor_preflight` blocks in logs/trading_loop.log (2026-06-28 to
2026-07-09): 4,057 lines -> 46 (coin, day) episodes across 21 coins; 41
gradeable (5 from 07-09 lack a complete forward window). Forward returns from
first block timestamp of the day (next 1h bar open, log-local converted to UTC):

| horizon | n | mean | median | up-rate | if-LONG-all net (50bps RT) | if-SHORT-all net |
|---|---|---|---|---|---|---|
| 24h | 41 | +1.15% | +1.53% | 63% | **+0.65%/ep** | -1.65%/ep |
| 72h | 41 | **-4.20%** | -5.85% | 29% | **-4.70%/ep** | +3.70%/ep |

Read: the floor cost a small 24h scalp (+0.65%/ep net, sum ~ +27% notional-
episodes) and SAVED a large multi-day drawdown (-4.70%/ep net, sum ~ -193%
notional-episodes if held 72h). At the bot's actual multi-day holding style the
floor was net PROTECTIVE in this window. Big caveat: 40/41 episodes cluster in
Jun-28..Jul-01 — one pump-then-dump regime event in tokenized equities, so the
effective independent sample is ~4 days, not 41 episodes. This counterfactual
is evidence about ONE regime, not a law.

## VERDICT

**REFUTED as a young-listing lane.** H1 and H2-short and H3 are refuted
outright. H2 fade-long passes its pre-registered gate (+1.17% @25bps, both
listing cohorts positive, MC p=0.011) but (a) it does not survive a Bonferroni
across the 12 fade cells swept (0.011 x 12 ~ 0.13), (b) it is NOT young-
specific — the mature control is stronger — so it does not justify a special
young lane, and (c) the honest framing is a general xyz crash-fade-long edge
whose young instances the floor currently blocks. Do NOT relax
min_history_bars for momentum entries.

## Recorder spec (what ships instead of a live lane)

Shadow RECORDER `xyz_crash_fade_young` (shadow_ledger convention, graded by
`scripts/shadow_status.py`), so 60 more forward days can decide:

- Universe: coins with `dex == "xyz"`, daily-bar age in [2, 59] (the floor-
  blocked set; also record age >= 60 twins for the control, tagged `mature`).
- Signal (at UTC day close): completed daily return <= -8% AND day dollar
  volume >= $3M.
- Hypothetical action recorded: LONG at next daily open, exit close of the
  following day (hold 1d), stop 15% (record stop-hit path from 1h candles).
- One open episode per coin; max 1 concurrent young episode (record overflow).
- Record: ts, coin, age_bars, day_ret, dvol_usd, next_open, fwd close, stop
  hit y/n, net@25/side, plus the SAME fields for every mature-window signal.
- Decision rule after >= 60 forward days: go live small ($20-25, 1x, 15% stop,
  max 1 concurrent) ONLY if forward young EV@25 > 0 AND forward mature EV@25 > 0
  AND young n >= 15. Expected flow ~ 8-15 young signals/month at current
  listing pace.

## Caveats

- Survivorship: universe = today's xyz listings; any delisted xyz coin is
  invisible. 14 markets returned zero candles (index/commodity style:
  xyz:VIX, xyz:DXY, xyz:WHEAT, ...) and were excluded. Positive numbers are
  upper bounds.
- Volume proxy: dollar volume = base_volume x close from HL daily candles.
- xyz tokenized equities have flat/thin weekend bars; the $3M dollar-volume
  floor is what keeps signals on real trading days.
- 400-bar fetch window: oldest coin has 270 bars, so no young window is
  truncated in this dataset.
