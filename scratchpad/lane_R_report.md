# Lane R report — leaderboard family replication (W-R)

2026-07-12. Executed the W-Q fallback: no source available, so each of the 7
requested strategy FAMILIES got one honest, standard, pre-registered LONG
interpretation on our own HL data. Full spec + results:
`research/alpha_swarm/findings/W-R_leaderboard_replication.md`.
Engine: `research/alpha_swarm/hypotheses/W-R1_leaderboard_replication.py`;
per-cell grids: `W-R1_results.json`.

## Discipline actually applied

- Pre-registered spec + frozen grids written to the findings doc BEFORE any
  scoring (one amendment, data-only: W-U daily cache has no BTC, fetched BTC
  1d once, 2s-paced, `W-R_cache_btc_daily.json`).
- Next-open fills both sides (no intra-bar optimism possible — no stops),
  25bps RT + baseline funding proxy for holds ≥8h, per-coin episode dedup +
  cooldowns, trades crossing split boundaries discarded.
- Train 50% / validate 25% / test 25% by time; selection on train only
  (n≥30), frozen cell scored once on validate and once on test.
- Matched same-coin random-time nulls, 2000 MC per family, on OOS trades.
- Bonferroni: alpha 0.05/9 = 0.0056 per family (60 declared cells total,
  program-wide 8.3e-4).
- Data: W-H0 hourly cache (40 coins x 208d, validated vs dataset.json) +
  W-U daily (90 coins, up to 5.5y). Survivorship caveat carried.

## Verdict table

| family | best-cell train | frozen validate | untouched test | null p | net EV25 (OOS) | VERDICT |
|---|---|---|---|---|---|---|
| vol_scaled_cooldown (1h L=24 θ=5%) | +0.22% n=728 | −1.24% n=351 | +0.28% n=397 | 1.000 | −0.57% | REFUTED |
| dominance_timebox (K=2 W=3d) | +0.48% n=105 | +0.89% n=53 | −1.53% n=60 | 0.563 | −0.39% | REFUTED |
| ema_oscillator_crossing (1d 8/21) | −0.13% n=436 | +6.53% n=541 | −4.66% n=829 | 0.215 | −0.24% | REFUTED |
| dominance_transition | +3.56% n=20 (no cell ≥30) | — | — | — | — | UNDERSAMPLED |
| ema_soft_alignment (1h x=2% H=24) | −0.07% n=1038 | −0.49% n=653 | −0.30% n=568 | 0.971 | −0.40% | REFUTED |
| burst_persistence (3σ H=12h) | −0.14% n=241 | −0.24% n=165 | −0.16% n=138 | 0.518 | −0.20% | REFUTED |
| vol_normalized_pressure (W=12 H=12h) | +0.43% n=629 | −0.17% n=363 | −0.38% n=347 | 0.685 | −0.27% | REFUTED |

0/7 VALIDATED. Nothing ships — no shadow recorder spec owed.

## One line per family

1. vol_scaled_cooldown — their #1 (Sharpe 7.07 / 2,311% claimed) earns
   −0.57%/trade OOS here and loses to random same-coin timing at p=1.000.
2. dominance_timebox — positive train AND validate, then test −1.53%/ep:
   the exact "final window looked great elsewhere" mirage W-Q flagged.
3. ema_oscillator_crossing — median trade −7% every window, win rate ~22%;
   mean swings are a few trend rides, not an edge.
4. dominance_transition — 9-21 flips in 5.5 YEARS; cannot reach
   significance on any realistic sample; their n is arithmetically suspect.
5. ema_soft_alignment — their +5,221% row: all 9 train cells negative, all
   OOS negative, p=0.97. Never worked here at any parameter.
6. burst_persistence — all 9 train cells negative; burst + confirm + buy =
   buying a local top net of fees.
7. vol_normalized_pressure — only family positive across the whole train
   grid; still flips negative in both OOS windows, and OOS gross ≈ 0 even
   at zero cost. Pure train-fit.

## Bottom line vs the screenshot

Not one family shows positive untouched-test EV; best null p anywhere is
0.215 vs the 0.0056 bar — and our universe is survivorship-biased IN the
strategies' favor. W-Q's "presumptively overfit" upgrades to empirically
contradicted. This closes the leaderboard question unless the operator
produces the actual source (in which case W-Q's reproduce-first protocol
applies).

## Housekeeping

- New files (all in scope, left uncommitted — the git index is shared with
  parallel lanes and the branch moved during my run; orchestrator commits):
  `research/alpha_swarm/findings/W-R_leaderboard_replication.md`,
  `research/alpha_swarm/hypotheses/W-R1_leaderboard_replication.py`,
  `W-R1_results.json`, `W-R_cache_hourly.json` (12MB copy of W-H0 cache),
  `W-R_cache_btc_daily.json`, `scratchpad/lane_R_report.md`.
- Network use: exactly one HL request (BTC 1d candles), 2s-paced.
- Nothing live touched. No config, no tests/ dir, no gate changes.

STATUS: DONE_WITH_CONCERNS — concern 1 (low): the 12MB hourly cache copy is
large for git; orchestrator may prefer to gitignore it and keep the
provenance note (verbatim copy of W-H0's validated cache). Concern 2 (low):
F4 verdict is UNDERSAMPLED, not refuted — signal fires too rarely to ever
promote, so it is closed for practical purposes.
