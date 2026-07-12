# W-R — Honest replication of the "25/25 leaderboard" strategy FAMILIES

2026-07-12, Lane R. Executes the fallback in
`W-Q_25_strategy_leaderboard_audit.md`: the operator cannot supply the
leaderboard's source, so instead of reimplementing someone else's overfit we
test one honest, standard interpretation of each strategy FAMILY on our own
Hyperliquid data and issue per-family verdicts.

## PRE-REGISTRATION (written 2026-07-12 BEFORE any cell was scored)

Everything in this section was frozen before the backtest script produced a
single number. Grids are final; no cell may be added, moved, or re-scored
after the first run.

### Data

- **Hourly**: W-H0 cache (40 liquid HL coins, 1h x ~5000 bars,
  2025-12-13 02:00 → 2026-07-09 09:00 UTC, with volume). Copied verbatim to
  `W-R_cache_hourly.json`.
- **Daily**: `W-U_cache_daily.json` (90 HL coins, up to 2001 daily bars
  ≈ 2021 → 2026-07, OHLC only — no volume, so volume families are
  hourly-only). Daily EMA/momentum families restrict to coins with ≥400 bars.
- **BTC-dominance proxy**: R_t = BTC close index / equal-weight alt index
  (arithmetic EW daily-rebalanced, all non-BTC daily-cache coins with data,
  index valid only when ≥5 alts alive). Dominance falling ⇔ R falling.
- **No network**: both caches are pre-existing; zero fresh HL fetches.

### Global mechanics (all families, no exceptions)

- Signal computed at CLOSE of bar i → fill at OPEN of bar i+1 (next-open
  fills for entry and exit; no intra-bar fills, no stops → no intra-bar
  optimism; entry bar must be exactly contiguous with the signal bar).
- Costs: **25bps round trip** off every trade + **funding proxy** for holds
  ≥8h: longs pay 1.25e-5/hour (HL baseline 0.01%/8h). Caveat: real alt
  funding in bull phases runs above baseline, so this is generous to longs.
- LONG side only (every family name implies long).
- Per-coin dedup: no new entry while the coin holds a position for that
  family; cooldown families additionally wait N bars after exit. Basket
  families: globally no overlapping episodes.
- Trades still open at a split boundary or data end are discarded.
- **Splits by time on the BTC span of each timeframe**: train = first 50%,
  validate = next 25%, test = final 25%. Hourly boundaries ≈
  2026-03-26 / 2026-05-17. Cell selection uses TRAIN only; the best cell is
  FROZEN then scored once on validate and once on test.
- Selection rule: best train cell by mean net EV/trade with n_train ≥ 30;
  if no cell reaches 30 train trades the family is UNDERSAMPLED. Grading
  needs n ≥ 10 in each OOS window.
- **Nulls**: matched same-coin random-time entries (same coin mix, same
  hold, same weighting scheme, same count), 2000 MC resamples, computed on
  the OOS (validate+test) trades of the frozen cell.
  p = P(null mean ≥ observed mean).
- **Bonferroni**: declared cells = 9+9+6+9+9+9+9 = 60 across 7 families.
  Per-family corrected alpha = 0.05/9 ≈ **0.0056**; program-wide
  0.05/60 ≈ 8.3e-4 (reported for context).
- Verdicts: **VALIDATED** = frozen cell mean net EV > 0 in validate AND in
  test separately, and OOS null p < 0.0056. **MARGINAL** = OOS-positive
  combined but fails one of those. **REFUTED** = otherwise (or
  UNDERSAMPLED, which cannot promote).
- Survivorship caveat carried on every verdict: both caches are today's
  listings replayed backward; delisted coins are absent, biasing long-side
  EV UP. Any VALIDATED verdict is an upper bound.

### Family grids (frozen)

1. **vol_scaled_cooldown** (primary hourly; daily sensitivity, unselected):
   enter when close_i/close_{i−L} − 1 ≥ θ. Grid L ∈ {24,72,168}h ×
   θ ∈ {3%,5%,10%} (daily map: L ∈ {5,10,20}d). Hold fixed 24 bars (5d
   daily); cooldown 24 bars (5d) after exit. Vol scaling: weight
   w = clip(σ_target/σ_i, 0.25, 2.0), σ_i = pstdev of last 168 (30 daily)
   returns strictly before i; σ_target = train-window median σ (frozen
   constant). EV = Σ(w·r_net)/Σw; nulls use the same weights at random bars.
2. **dominance_timebox** (daily): R fell K consecutive days → long EW alt
   basket (all alts with ≥60 prior bars) next open, exit open after W days.
   Grid K ∈ {2,3,5} × W ∈ {3,5,10}. No overlapping boxes.
3. **ema_oscillator_crossing** (both timeframes in grid): EMA(f) crosses
   above EMA(s) → long next open; exit on cross-down, next open. Grid
   (f,s) ∈ {(8,21),(12,26),(20,50)} × timeframe ∈ {1h,1d} = 6 cells.
4. **dominance_transition** (daily): SMA_s(R) crosses below SMA_l(R)
   (dominance rising→falling flip) → long EW alt basket H days. Grid
   (s,l) ∈ {(5,20),(10,30),(20,60)} × H ∈ {5,10,20}.
5. **ema_soft_alignment** (primary hourly; daily sensitivity, unselected):
   EMA8>EMA21>EMA55 and |close/EMA8 − 1| ≤ x at close i → long next open,
   hold H. Grid x ∈ {0.5%,1%,2%} × H ∈ {12,24,72}h (daily map
   H ∈ {2,5,10}d). Cooldown = H.
6. **burst_persistence** (hourly only — needs volume): bar i has
   ret_i ≥ Nσ·σ_i (σ = 168-bar pstdev strictly before i, ret_i > 0) AND
   vol_i ≥ 3× rolling 168-bar median volume; persistence check
   close_{i+1} ≥ close_i; enter open of bar i+2, hold H. Grid
   Nσ ∈ {2,3,4} × H ∈ {4,12,24}. Cooldown = H.
7. **vol_normalized_pressure** (hourly only): per-bar pressure
   p = ((c−l)−(h−c))/(h−l)·v (0 if h=l); S_i = rolling sum over W bars;
   z_i = (S_i − mean)/std of the previous 168 S-values (strictly before i).
   Long when z ≥ 2 (threshold fixed by assignment), hold H. Grid
   W ∈ {6,12,24} × H ∈ {6,12,24}. Cooldown = H.

Script: `W-R1_leaderboard_replication.py`; results:
`W-R1_results.json` (same dir). Seed 20260712.

AMENDMENT (2026-07-12, before any cell was scored — the first run crashed on
`KeyError: BTC` with no numbers produced): the W-U daily cache has no BTC
(unlock-study universe). One 2s-paced fetch of BTC 1d x 2000 bars
(2021-01-19 → 2026-07-11) cached to `W-R_cache_btc_daily.json`; it supplies
the daily master calendar and the dominance numerator. No grid, threshold,
or rule changed.

---

RESULTS AND VERDICTS ARE APPENDED BELOW AFTER THE RUN — nothing above this
line may change once scoring starts.

---

## RESULTS (scored 2026-07-12, single run, seed 20260712)

Split boundaries as computed: hourly train ≤ 2026-03-27 05:30, validate ≤
2026-05-18 07:15, test ≤ 2026-07-09 09:00; daily train ≤ 2023-10-15,
validate ≤ 2025-02-26, test ≤ 2026-07-11. All EVs are NET of 25bps RT +
funding proxy, per trade. "null p" = matched same-coin random-time MC
(2000 resamples) on the frozen cell's validate+test trades; Bonferroni
threshold 0.0056.

### Verdict table

| family | frozen cell | best-cell train | frozen validate | untouched test | null p | net EV25 (OOS) | VERDICT |
|---|---|---|---|---|---|---|---|
| 1 vol_scaled_cooldown | 1h, L=24, θ=5% | +0.22% (n=728) | −1.24% (n=351) | +0.28% (n=397) | 1.000 | −0.57% (n=748) | **REFUTED** |
| 2 dominance_timebox | K=2, W=3d | +0.48% (n=105) | +0.89% (n=53) | −1.53% (n=60) | 0.563 | −0.39% (n=113) | **REFUTED** |
| 3 ema_oscillator_crossing | 1d, EMA 8/21 | −0.13% (n=436) | +6.53% (n=541) | −4.66% (n=829) | 0.215 | −0.24% (n=1370) | **REFUTED** |
| 4 dominance_transition | none (no cell ≥30 train eps; best n=20) | +3.56% (n=20) | — | — | — | — | **UNDERSAMPLED** (cannot promote) |
| 5 ema_soft_alignment | 1h, x=2%, H=24 | −0.07% (n=1038) | −0.49% (n=653) | −0.30% (n=568) | 0.971 | −0.40% (n=1221) | **REFUTED** |
| 6 burst_persistence | 3σ, H=12h | −0.14% (n=241) | −0.24% (n=165) | −0.16% (n=138) | 0.518 | −0.20% (n=303) | **REFUTED** |
| 7 vol_normalized_pressure | W=12, H=12h | +0.43% (n=629) | −0.17% (n=363) | −0.38% (n=347) | 0.685 | −0.27% (n=710) | **REFUTED** |

**0 of 7 families validated. 6 REFUTED, 1 UNDERSAMPLED.** No shadow
recorder ships. Full per-cell grids in `W-R1_results.json`.

### One line per family

1. **vol_scaled_cooldown** — their #1 (claimed Sharpe 7.07 / 2,311%): the
   honest version loses −0.57%/trade OOS and null p = 1.000 — in 2000/2000
   resamples, RANDOM same-coin timing beat the momentum entries. Daily
   sensitivity shows the same regime artifact (validate +0.85% → test −1.09%).
2. **dominance_timebox** — their #2 (Sharpe 6.37): train and validate both
   positive, then the untouched test window gives it all back (−1.53%/ep);
   a live replica of W-Q red flag #4 (OOS-looks-better is regime luck).
3. **ema_oscillator_crossing** — win rate 15-25% and median trade −7% in
   EVERY window; the validate +6.53% mean is a handful of trend rides
   (median still −7.1%), test −4.66%. Trend-following EMA crosses on this
   venue are fee/chop-dominated, consistent with
   [[project_williams_patterns_neg_ev]].
4. **dominance_transition** — dominance SMA flips happen 9-21 times in 5.5
   YEARS per cell. A family this slow cannot reach significance on any
   sample the leaderboard could have used — their n is arithmetically
   impossible unless their "transitions" fire orders of magnitude more
   often (i.e., a different, noisier signal).
5. **ema_soft_alignment** — their #5 (claimed 5,221%!): all 9 train cells
   NEGATIVE, all OOS windows negative, p=0.97. The honest version of their
   biggest-return row never made money here at any parameter.
6. **burst_persistence** — all 9 train cells negative; confirming a burst
   with a hold-the-move bar then buying is buying a local top net of fees.
7. **vol_normalized_pressure** — the only family with a genuinely positive
   full train grid (+0.43% best, all 9 cells ≥ 0 train) and it still flips
   negative in BOTH OOS windows. Even at ZERO cost the frozen cell's OOS
   gross is ≈ −0.01%/trade — there is no fee story to rescue, the signal
   itself decayed.

### Direct comparison with the screenshot's claims

Our numbers say the leaderboard is describing a different universe. Their
#1, "Vol-scaled cooldown", claims Sharpe 7.07 and +2,311% with max DD
−5.36%; the honest, cost-loaded, next-open-filled version of that family on
real Hyperliquid data earns **−0.57% per trade out of sample and is beaten
by random entry timing at p = 1.000**. Their #5, "EMA soft alignment",
claims +5,221%; the honest version has never had a single positive
parameter cell even IN sample. Across all seven families, not one shows a
positive untouched-test EV, and the best OOS null p anywhere is 0.215 —
nowhere near the 0.0056 Bonferroni bar. The two patterns that recur are
exactly the failure modes pre-registered in W-Q: (a) train/validate luck
evaporating in the final window (families 2, 3, 7 — the same "final 30%
looked great" shape their leaderboard sorts on), and (b) fee-dominated
high-turnover indicator entries (families 1, 3, 5, 6). And this is with a
survivorship-biased, longs-favoring universe and a generous baseline
funding proxy — reality is worse than these numbers. The leaderboard's
claims are not just unreproduced; the families themselves are refuted on
this venue under honest accounting. Verdict of W-Q ("presumptively
overfit") upgrades to **empirically contradicted**.

### Caveats

- Survivorship: both caches replay today's listings backward; long-side EV
  is overstated. Every REFUTED verdict survives this bias a fortiori.
- Funding proxy is HL baseline (0.01%/8h); real alt funding in the 2024-25
  bull phases ran higher, which would make longs worse.
- Hourly sample is 208 days (one macro regime arc, Dec-2025 top → 2026
  drawdown); daily sample is 5.5 years. Families 1, 5, 6, 7 were selected
  on hourly train per pre-registration; families 1 and 5's daily
  sensitivities agree: positive-looking until the final window, then
  negative (F1 daily test −1.09%, F5 daily test −2.25%).
- F4's UNDERSAMPLED is a data-length statement, not evidence of absence —
  but it cannot be promoted, and its 9 cells disagree on sign across the
  grid.

### Repro

```
.venv/bin/python research/alpha_swarm/hypotheses/W-R1_leaderboard_replication.py
```

Inputs: `W-R_cache_hourly.json` (verbatim copy of W-H0's validated cache),
`W-U_cache_daily.json`, `W-R_cache_btc_daily.json` (one paced fetch,
2026-07-12). Runtime ≈ 8 min, zero network.
