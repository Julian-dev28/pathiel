# HTF "1.5x-previous-candle GREEN volume breakout" validation (1h / 4h / 1d)

**Date:** 2026-06-29
**Question:** is the 1.5x green-volume breakout a real, regime-robust +EV edge on higher timeframes? A 17-day resampled test looked striking (1h +0.37%/84%, 4h +1.15%/92%, +EV both OOS halves). Does it survive MONTHS of native data, a regime split, and a matched null?

**Verdict up front: NO. It is not an edge on any timeframe.** The apparent +EV is entirely produced by the tight-floor EXIT harvesting positive-skew noise on a survivor-biased universe. A random green candle with NO volume condition earns the same return. The 1.5x volume filter adds essentially nothing (excess over null: +0.01% at 1h, -0.05% at 4h, +0.29% at 1d). The 17-day result was a true positive about the *exit*, mislabeled as a signal — it never ran the matched null.

---

## Method

- **Universe:** top 120 perps by `dayNtlVlm`, HIP-3 excluded. Native (non-resampled) candles, `fetch_hl_candles(coin, iv, 5000)`.
- **Span:** 1h ~208d, 4h ~833d, 1d ~capped by listing age (oldest ~2140d). Survivor universe = today's liquid set, so every positive number is an **upper bound** (delisted coins absent).
- **Entry (operator rule):** green candle (close>open) whose volume >= 1.5x the *previous* candle's volume. Decide on complete bar i, fill bar i+1 open (lookahead-safe). 12-bar cooldown.
- **Exit (tight profit-floor):** arm at +1%, exit on 10% give-back from peak, hard stop 15%, 48-bar horizon.
- **Cost:** net of 12bps round-trip (then swept to 25 / 40bps).
- Scripts: `scratchpad/build_htf.py`, `scratchpad/htf_breakout_test.py`.

`ext` = median signal-candle body return (proxy for entry-extension). `r10/r20/r50` = share of trades whose MFE reached +10/20/50%.

---

## Per-timeframe results

### 1h  (208d, 120 coins, 34,312 signals)

| variant | n | EV | win | med | r10 | r20 | r50 | OOS h1/h2 |
|---|---|---|---|---|---|---|---|---|
| tight-floor @12bps | 34312 | **+0.23%** | 83% | +1.07% | 15% | 4% | 0% | +0.15/+0.31 |
| @25bps | 34312 | +0.10% | 83% | +0.94% | | | | +0.02/+0.18 |
| @40bps | 34312 | **-0.05%** | 83% | +0.79% | | | | -0.13/+0.03 (dies) |
| hold-to-horizon | 34312 | -0.64% | 43% | -0.88% | | | | -0.91/-0.36 (NEG) |
| wide-trail (25%gb/20%stop) | 34312 | -0.21% | 70% | +1.56% | | | | -0.37/-0.06 (NEG) |
| **matched null (random green)** | 34312 | **+0.22%** | 83% | +1.07% | 15% | 4% | 0% | +0.16/+0.28 |
| **EXCESS over null** | | **+0.01%** | -0pp | | | | | |

Median signal extension only +0.5%; raw forward return is negative (hold-to-horizon -0.64%). The +0.23% is pure exit-clip and **dies by 40bps**. Excess over null = +0.01% = zero.

### 4h  (833d, 120 coins, 26,349 signals)

| variant | n | EV | win | med | r10 | r20 | r50 | OOS h1/h2 |
|---|---|---|---|---|---|---|---|---|
| tight-floor @12bps | 26349 | **+0.96%** | 91% | +1.54% | 44% | 20% | 3% | +1.05/+0.87 |
| @25bps | 26349 | +0.83% | 91% | +1.41% | | | | +0.92/+0.74 |
| @40bps | 26349 | +0.68% | 91% | +1.26% | | | | +0.77/+0.59 |
| hold-to-horizon | 26349 | -1.26% | 39% | -3.66% | | | | -0.21/-2.31 (NEG) |
| wide-trail | 26349 | +0.14% | 84% | +1.95% | | | | +0.28/-0.00 (MIX) |
| **matched null (random green)** | 26349 | **+1.01%** | 91% | +1.53% | 44% | 20% | 4% | +1.04/+0.97 |
| **EXCESS over null** | | **-0.05%** | -1pp | | | | | |

Survives cost (it's a structural clip, not a fragile signal) but the **null is identical and slightly higher**. The volume condition adds negative value here. Raw forward return again negative (hold -1.26%).

### 1d  (~2140d max, 120 coins, 4,559 signals)

| variant | n | EV | win | med | r10 | r20 | r50 | OOS h1/h2 |
|---|---|---|---|---|---|---|---|---|
| tight-floor @12bps | 4559 | **+3.31%** | 91% | +3.01% | 71% | 52% | 23% | +3.32/+3.31 |
| @25bps | 4559 | +3.18% | 91% | | | | | +3.19/+3.18 |
| @40bps | 4559 | +3.03% | 91% | | | | | +3.04/+3.03 |
| hold-to-horizon | 4559 | +0.24% | 23% | -15.12% | | | | +5.92/-5.44 (MIX) |
| wide-trail | 4559 | +1.99% | 88% | +3.04% | | | | +1.92/+2.07 |
| **matched null (random green)** | 4559 | **+3.02%** | 92% | +2.82% | 73% | 52% | 23% | +2.92/+3.13 |
| **EXCESS over null** | | **+0.29%** | -1pp | | | | | |

The largest *base* number, but ~91% of it (+3.02% of +3.31%) is reproduced by a random green daily candle. Run-rates (r10/r20/r50) are identical to the null. The +0.29% excess on a +3% base is inside survivor/sampling noise and the win-rate excess is negative. Daily green-candle + tight-floor on a survivor set with positive drift = the whole result.

---

## (a) BTC regime split (terciles of BTC trailing return at entry, tight-floor @12bps)

| TF | DOWN-tape | FLAT-tape | UP-tape |
|---|---|---|---|
| 1h | +0.18% (OOS +.15/+.22) | +0.18% (OOS -.22/+.58 MIX) | +0.33% (OOS +.35/+.30) |
| 4h | +1.03% (OOS +1.16/+.89) | +0.83% (OOS +.88/+.77) | +1.01% (OOS +1.05/+.98) |
| 1d | +3.04% (OOS +2.96/+3.13) | +3.03% (OOS +3.61/+2.44) | +3.86% (OOS +3.41/+4.32) |

The base number IS regime-robust (positive in down/flat/up on every TF) — but that is the *exit/survivor* effect, not the signal. The matched null is regime-robust for the exact same reason. Regime split does not rescue a signal that has no excess over null. (Mild up-tape tilt at 1d, expected from drift.)

## (b) Absolute $-volume floor sweep (signal-candle close*vol, tight-floor @12bps)

Raising an absolute $-vol floor nudges base EV up modestly (4h: +0.96% -> +1.41% at p90; 1d: +3.31% -> ~+3.6% mid) by selecting larger, more liquid candles. It does NOT create excess over null — the null benefits from the same survivor/liquidity drift, and the lift is small relative to the null base. No floor turns this into a signal; it just trades fewer, bigger candles.

## (c) Matched null — the decisive test

Random green candle, no volume condition, same coins/horizon/exit, matched count:

| TF | signal EV | null EV | **excess** |
|---|---|---|---|
| 1h | +0.23% | +0.22% | **+0.01%** |
| 4h | +0.96% | +1.01% | **-0.05%** |
| 1d | +3.31% | +3.02% | **+0.29%** |

The 1.5x-volume filter is indistinguishable from picking green candles at random. The edge is the exit, not the breakout.

## (d) Cost sensitivity

- 1h: +0.23% -> +0.10% (25bps) -> **-0.05% (40bps)** — fragile, dies at realistic small-cap slippage.
- 4h: +0.96% -> +0.83% -> +0.68% — survives cost (structural clip), but fails the null.
- 1d: +3.31% -> +3.18% -> +3.03% — survives cost easily, but fails the null.

## (e) Exit comparison

Tight-floor dominates on every TF; hold-to-horizon is **negative** at 1h/4h and a coin-flip at 1d (median -15% on 1d — the raw forward distribution of these entries is left-skewed). Wide-trail underperforms tight-floor. This confirms the result is an *exit-mechanics* artifact: the tight profit-floor banks the +1% arm on the ~83-91% of green candles that tick up at least 1% (trivial on a survivor universe), clips the right tail, and rarely eats the 15% stop. Same mechanic, applied to random green candles, yields the same return.

---

## One-line verdict per timeframe

- **1h — NO.** +0.23%@12bps but excess over null = +0.01% (zero), and it dies at 40bps cost. Not an edge.
- **4h — NO.** +0.96%@12bps survives cost but excess over null = -0.05% (negative). The volume filter adds nothing; pure exit/survivor artifact.
- **1d — NO.** +3.31%@12bps, regime-robust and cost-robust, but excess over a random-green null = only +0.29% on a +3% base (within survivor noise, win-rate excess negative). The daily result is tight-floor + positive-drift survivor universe, not the breakout.

**Bottom line:** the 1.5x-previous-candle green-volume breakout is NOT a real edge on 1h/4h/1d. The 17-day "striking" result measured the tight-floor exit's positive-skew clip on a survivor universe and mislabeled it as signal. Do not build a breakout book on this rule. If anything is worth keeping it is the tight-floor exit itself — but that is already the live exit, and it works on any long entry, not specifically on volume breakouts. Survivor bias means even these null-matched numbers overstate live performance (delisted/rugged coins absent from the universe).
