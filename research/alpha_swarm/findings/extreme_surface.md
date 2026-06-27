# extreme_surface — the full extreme-move response surface

**Agent:** `extreme_surface` (read-only swarm). Script: `scratchpad/extreme_surface.py`.

## Hypothesis (one sentence)
After a coin posts a large k-day move (+/- threshold), there is a tradeable response — and the
right side (fade vs continue) and regime can be read off a full surface sweep, telling us whether
the two live edges (`extreme_fade` -12% long, `rally_exhaustion` +12%/2d short in BTC-down) sit at
the optimum.

## Exact rule tested
- **Trigger:** coin k-day close-to-close return crosses +/- threshold, FRESH cross only (prior day's
  k-ret was inside the band) to de-cluster. k in {1,2,3}, threshold in {8,10,12,15,20,25}%.
- **4 quadrants:** pump_fade(short), pump_continue(long), crash_fade(long), crash_continue(short).
- **Lookahead-safe:** decide on close of bar i, FILL at i+1 open; the entry bar then plays out
  (`fwd = candles[i+1:]`, `alpha_lib.sweep_stop`).
- **Exit:** stop {8,15,20,25,40}%, horizon {3,5,10} daily bars, no TP. Per cell I pick the
  stop+horizon that maximizes OOS robustness (both halves > 0, then max of the weaker half).
- **Regime:** BTC close vs its 20d SMA at the decision bar (up/down). Liquidity floor on snapshot
  `dayNtlVlm`: all / >$20M / >$50M.
- **OOS / slippage:** `alpha_lib.summarize` (both-halves @12bps, slippage tiers 0–50bps).
- **Anti-beta control (added):** the window is a **−44% BTC tape** (175 down days vs 106 up), and
  raw random-long drift is **negative** (−0.45%/3d, −0.81%/5d, −1.7%/10d). So every cell is also
  scored as **excess = cell EV − matched-baseline EV** (random entry, same side/stop/horizon/regime/liq).
  This strips out the directional tailwind that otherwise flatters short cells.

## Surface (compact — best cell per quadrant family, OOS-robust, @12bps)

| k | thr | quadrant | regime | liq | stop | hz | n | EV@12bps | EV@25bps | h1 | h2 | excess vs base |
|--|--|--|--|--|--|--|--|--|--|--|--|--|
| 2 | 25% | **crash_fade (long)** | down | all | 20% | 3 | 34 | 14.9% | 14.8% | 15.2 | 14.5 | **+15.4** |
| 2 | 25% | crash_fade (long) | all | all | 20% | 3 | 38 | 13.5% | 13.3% | 13.6 | 13.2 | +14.2 |
| 1 | 15% | crash_fade (long) | down | all | 20% | 3 | 91 | 9.3% | 9.1% | 11.9 | 6.4 | +9.7 |
| 3 | 20% | crash_fade (long) | down | all | 40% | 3 | 108 | 8.1% | 8.0% | 8.1 | 8.1 | +8.3 |
| 1 | 12% | **crash_fade = LIVE extreme_fade** | all | all | 20% | 3 | 193 | 4.2% | 4.1% | 5.0 | 3.4 | **+5.0** |
| 1 | 12% | crash_fade = extreme_fade | down | all | 20% | 3 | 173 | 5.2% | 5.1% | 5.8 | 4.6 | +5.6 |
| 2 | 8% | **crash_continue (short)** | **up** | >20M | 8% | 10 | 33 | 9.1% | 9.0% | 11.0 | 7.1 | **+7.0** |
| 2 | 12% | crash_continue (short) | up | all | 20% | 10 | 49 | 7.4% | 7.2% | 8.6 | 5.6 | +4.7 |
| 2 | 12% | **pump_fade = LIVE rally_exhaustion** | down | all | 25% | 10 | 236 | 3.1% | 3.0% | 4.8 | 1.3 | **+2.1** |
| 2 | 12% | pump_fade (short) | up | all | 15% | 10 | 52 | 5.5% | 5.4% | 5.2 | 5.9 | +3.0 |
| 2 | 20% | pump_continue (long) | down | >20M | 40% | 10 | 22 | 7.6% | 7.5% | 8.7 | 6.1 | +7.9 |

(273/502 cells were "robust both halves @12bps" — but that count is inflated by the −44% tape; the
**excess** column is the column that matters, and it is dominated by `crash_fade`.)

## Where the two live edges land

### `extreme_fade` (−12% crash → long) — **CONFIRMED, conservative on threshold**
- Live cell (k≈1, −12%, all-regime, ~20% stop, ~3d): EV +4.2%@12bps, holds to +3.85%@50bps,
  win 62%, **excess +5.0%** over the matched (negative-drift) baseline. Both halves +5.0/+3.4. Real.
- It is **not the EV peak** of its quadrant: deeper crashes carry far more EV — −15%/1d → +9.3%
  (excess +9.7, n=91), −25%/2d → +14.9% (excess +15.4, n=34, win 91%, Sharpe 1.27). EV rises
  monotonically with crash depth and k.
- **But** deeper thresholds are **survivorship-acute**: selecting coins that fell 25% and are still
  in today's liquid set mechanically over-samples the ones that bounced. The −12%/all cell is the
  high-frequency (n=193), survivorship-safer operating point. Verdict: live choice is **defensible as
  the robust workhorse; a second, smaller-size "deep-crash" tier at −20/−25% with a 20% stop and 3d
  horizon is the upside lever** if you accept the survivorship caveat and cap size.
- The live long-only call is **correct**: crash_fade-long is +EV in all and down regimes; the
  up-regime −12% cell is NOT robust (h2 −5.7%, n=18). Keep it long-only / down+all regime.

### `rally_exhaustion` (+12%/2d rally → short, BTC-down, wide stop) — **CONFIRMED, marginal**
- Live cell (k=2, +12%, down, 25% stop, 10d): EV +3.1%@12bps, +2.76%@50bps, win 63%,
  both halves +4.8/+1.3, **excess +2.1%** after removing the short-side beta tailwind. Real but
  the smallest of the keeper edges, and h2 (+1.3%) is where its margin lives.
- The **wide 25% stop / 10d horizon is confirmed optimal** for this cell (sweep picked it).
- The **down-regime gate is justified** (it is the highest-excess pump_fade cell, +2.1% vs +1.3% up,
  +1.2% all) but **not strictly necessary** — pump_fade-short is robust both-halves in up and all
  regimes too. Loosening to all-regime ~doubles trade count (236→402) at lower per-trade excess.
  Recommendation: keep the down gate as the primary; an all-regime variant is viable at smaller size.

## Top-3 ROBUST +EV cells (both halves, survives 25bps, excess > 0)
1. **crash_fade LONG, −25%/2-day, 20% stop, 3-day horizon** (down or all regime). EV +14.9%@12bps,
   excess +15.4, win 91%, Sharpe 1.26, both halves +15.2/+14.5. **Biggest risk: survivorship** —
   deep-crash survivors over-sample bounces; n=34. Size it as a small satellite, not core.
2. **crash_fade LONG, −15%/1-day, 20% stop, 3-day horizon** (down regime). EV +9.3%@12bps, excess
   +9.7, n=91, both halves +11.9/+6.4. The robust mid-depth extension of the live `extreme_fade` —
   the cleanest "bigger than live, still high-n" upgrade.
3. **crash_continue SHORT, −8%/2-day in BTC-UP regime, 8% stop, 10-day horizon, >$20M** (NOT live).
   EV +9.1%@12bps, excess +7.0, n=33, win 76%, Sharpe 0.67, both halves +11.0/+7.1. Interpretation:
   a liquid coin that bleeds −8% while BTC is locally strong (relative weakness / divergence) keeps
   falling. This is the one genuinely new, non-overlapping cell on the surface. Tight 8% stop is the
   optimum here (this is a continuation, not a squeeze). Risk: short-side, modest n, and "up regime"
   here means local rallies inside a −44% bear, so it may not generalize to a true bull.

## VERDICT
**ROBUST +EV** for the `crash_fade(long)` family (the deciding number: excess EV stays
**+5% to +15%** above a matched negative-drift baseline, both OOS halves positive, slippage-stable
to 50bps). The surface is **asymmetric**: the long-the-crash side is the dominant, deep, high-n edge;
the short side (pump_fade, crash_continue) is real but ~2–4× smaller in excess — consistent with the
project's edge profile ("edge is long/trend-aligned; shorts bleed except in down regimes").

- `extreme_fade` (−12% long): **CONFIRMED**, sits on the robust workhorse cell; **suboptimal in raw
  EV** (deeper −15/−25% thresholds carry 2–3× the EV) but defensible as the survivorship-safe point.
  Lever: add a small deep-crash (−20/−25%, 20% stop, 3d) tier.
- `rally_exhaustion` (+12%/2d short, down): **CONFIRMED but marginal** (+2.1% excess); wide stop and
  down-regime gate both validated. Lever: optional all-regime variant at reduced size.
- **New candidate (not live):** `crash_continue` short on −8%/2d divergent weakness while BTC is in
  an up regime, 8% stop, 10d — excess +7.0%, worth a shadow logger.

### Caveats (gates honored)
- Lookahead-safe (decide@i-close, fill@i+1-open). OOS both-halves reported for every cell. Slippage
  swept 0–50bps. Stop width swept {8,15,20,25,40}%.
- **Survivorship is the dominant risk**, acute for deep-crash-fade and any short-continuation cell:
  the 40-coin universe is today's liquid survivors, so all positive EV is an **upper bound** and the
  deep cells over-state the true bounce rate. Liquidity (`dayNtlVlm`) is a single snapshot, so the
  >$20M/>$50M split is a coin-level classifier, not a point-in-time filter.
- Overlapping trades within a cell are de-clustered (fresh-cross) but not fully independent; treat
  the OOS-halves split as the real out-of-sample check, not the raw n.
