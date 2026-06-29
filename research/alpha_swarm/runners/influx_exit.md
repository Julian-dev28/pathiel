# Lane 3 — EXIT/ride study: the volume-influx runner play

Script: `research/alpha_swarm/runners/influx_exit.py` (read-only, run with the repo venv).
Dataset: `movers_5m.json` — 180 survivor perps, ~5000 5m bars each, OHLCV.

**Entry (fixed, not under study):** green 5m volume-influx candle (close>open AND
vol >= 1.5x trailing-6 mean of the PRIOR 6 bars). Enter at the i+1 open. Lookahead-safe.
12-bar (1h) cooldown per coin so one influx cluster = one trade and the entry set is
identical across every exit policy.

**Subset under study:** entries that reach **>= +5% MFE** within a 288-bar (24h) hold
horizon — i.e. a real move actually started. **n = 9,587.**

**Costs:** 12 bps round-trip on every policy; 18 bps on scale-out (extra exit leg).
**Lookahead safety:** the trailing floor/stop is computed from the running peak through
the PREVIOUS bar; the current bar's LOW is checked against it BEFORE the current bar's
high updates the peak. Fills at the floor/level price.

> **SURVIVOR-UNIVERSE CAVEAT = UPPER BOUND.** These 180 perps exist now. Coins that
> influx-pumped then bled out / delisted are absent, so every EV below is optimistic.
> Also note: the biggest move in this 5000-bar window is **+93.5% MFE** — there is no
> +100%/+200% monster in-sample, so a capped fixed-TP is *not* punished here the way it
> would be on a true 3x (e.g. the live MANTA +145% ROE). Weigh that against fixed-TP.

---

## MFE distribution on the reached-+5% subset (the ceiling)

| MFE threshold | % of subset | n |
|---|---|---|
| >= +5%  | 100.0% | 9587 |
| >= +10% | 31.9% | 3063 |
| >= +20% | 6.6% | 629 |
| >= +30% | 2.1% | 198 |
| >= +50% | 0.3% | 32 |
| >= +100% | 0.0% | 0 |

median MFE **+7.88%**, mean **+10.05%**, max **+93.53%**.

Reading: even conditional on a move starting, **two thirds die before +10%** and the true
runner (>= +50%) is **1 in ~300** of the reached-+5% set. The play lives or dies on the
fat right tail, so the exit must not cap it.

---

## Exit-policy table (whole reached-+5% subset, sorted by net EV)

`arm` = profit level at which the trailing floor engages. give-back = % of *peak gain*
surrendered (matches the live floor-breach semantics: MANTA peaked +145% ROE, closed
+94% = a 35%-of-gain give-back, exact).

| policy | n | EV | med capture | win | EV h1 | EV h2 |
|---|---|---|---|---|---|---|
| **floor 35% give-back, arm +5%** | 9587 | **+5.02%** | **65.0%** | 100% | +5.09% | +4.96% |
| floor 20% give-back, arm +10% | 9587 | +4.83% | 57.9% | 86.5% | +4.93% | +4.72% |
| **floor 35% give-back, arm +10%** | 9587 | +4.82% | 59.6% | 86.5% | +5.07% | +4.57% |
| fixed TP +10% | 9587 | +4.82% | 57.7% | 86.5% | +4.98% | +4.65% |
| fixed TP +20% | 9587 | +4.82% | 54.9% | 84.7% | +5.11% | +4.52% |
| fixed TP +30% | 9587 | +4.74% | 53.6% | 84.3% | +4.94% | +4.53% |
| volume-reversal, arm +2% | 9587 | +2.62% | 30.2% | 98.6% | +2.68% | +2.55% |
| floor 35% give-back, **arm +1%** | 9587 | +1.64% | 14.4% | 100% | +1.79% | +1.49% |
| floor 20% give-back, arm +1% | 9587 | +1.47% | 15.4% | 100% | +1.52% | +1.42% |
| floor 10% give-back, arm +1% | 9587 | +1.45% | 16.3% | 100% | +1.46% | +1.43% |
| scale-out 50% @ +10%, trail rest | 9587 | +1.39% | 16.3% | 100% | +1.40% | +1.37% |
| scale-out 50% @ +20%, trail rest | 9587 | +1.39% | 16.3% | 100% | +1.40% | +1.37% |
| scale-out 50% @ +5%, trail rest | 9587 | +1.37% | 16.3% | 100% | +1.39% | +1.34% |
| ATR-trail 3x | 9587 | +0.48% | 0.0% | 45.7% | +0.42% | +0.53% |

OOS halves agree for every top policy (h1 ~ h2), so the ranking is not a regime fluke.

### The single most important result: arm threshold, not policy family

The spec'd tight floor (**arm +1%**) is the leak. Armed at +1%, a give-back floor stops
on the first noise wiggle long before the move develops, capturing only **14-16%** of
MFE and netting **+1.45-1.64%**. Delay the arm to **+5%/+10%** and the *same* 35% floor
jumps to **+5.02% / +4.82%** EV and **65% / 60%** capture. Scale-out adds nothing — it
arms at +1% too, so the trailing half leaks identically; banking a partial just locks a
sliver. ATR-trail 3x is unusable here (stop distance dwarfs a 5-8% move → holds to a
horizon give-back, 0% capture).

---

## EV by MFE bucket — which exit wins ON the runs that actually run

| policy | 5-10% | 10-20% | 20-50% | **>=50%** |
|---|---|---|---|---|
| fixed TP +10% | +2.44% | +9.88% | +9.88% | +9.88% |
| fixed TP +30% | +2.44% | +7.29% | +18.06% | +29.88% |
| floor 35%, arm +1% | +1.45% | +1.95% | +2.30% | +4.26% |
| floor 35%, arm +5% | +4.20% | +6.06% | +9.21% | +16.20% |
| **floor 35%, arm +10%** | +2.44% | +8.72% | +13.51% | **+31.90%** |
| scale-out @ +20% | +1.33% | +1.50% | +1.56% | +1.54% |
| volume-reversal, arm +2% | +2.39% | +2.92% | +3.64% | +6.35% |
| ATR-trail 3x | +0.34% | +0.61% | +1.21% | +4.52% |

bucket counts: 5-10% = 6524, 10-20% = 2434, 20-50% = 597, **>=50% = 32**.

### Median capture (realized / MFE) by MFE bucket

| policy | 5-10% | 10-20% | 20-50% | **>=50%** |
|---|---|---|---|---|
| fixed TP +10% | 47.1% | 79.0% | 38.1% | 13.6% |
| fixed TP +30% | 47.1% | 62.9% | 72.4% | 40.7% |
| floor 35%, arm +1% | 16.7% | 9.3% | 4.5% | **1.8%** |
| floor 35%, arm +5% | 65.0% | 41.3% | 23.7% | 9.4% |
| **floor 35%, arm +10%** | 47.1% | 65.0% | 48.0% | **36.5%** |
| scale-out @ +20% | 18.6% | 10.0% | 5.0% | 1.9% |
| volume-reversal, arm +2% | 34.8% | 19.8% | 9.8% | 4.2% |
| ATR-trail 3x | 0.0% | 0.1% | 0.2% | 1.3% |

The live-style arm-+1% give-back captures **1.8%** of a >=50% runner's MFE — it stops out
on an early leg and the monster's peak arrives later, unowned. MANTA rode clean and
monotonic; the *typical* big-MFE event on the survivor set is choppy, which is why one
clean win flatters the policy. On the tail, **floor 35% arm +10%** captures **36.5%** of
MFE and is the only trailing exit with **no upside cap** — it beats even fixed TP +30%
on the >=50% bucket (+31.9% vs +29.88%) and would pull further ahead on a real 3x that
this in-sample window simply doesn't contain.

---

## VERDICT

**To maximize capture on the events that actually run: 35%-of-peak-gain trailing floor,
armed at +10% (not +1%)** — +31.9% EV and 36.5% MFE-capture on the >=50% runners,
uncapped, while costing nothing on the body (+2.44% on 5-10%, identical to fixed TP);
the spec'd tight arm-+1% floor captures only 1.8% of a runner and is the real leak.
(If the goal is blended EV across all reached-+5% trades rather than the tail, arm at
+5% instead: +5.02% EV / 65% capture, but it clips the monsters to 9.4%.)
