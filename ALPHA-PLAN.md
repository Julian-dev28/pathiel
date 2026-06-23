# Alpha Hunt — Validated Edges & Implementation Architecture

Systematic search for +EV signals. **Every method is held to the same bar:** lookahead-safe
(signal from data ≤ t, enter t+1), cost-aware (≥10bps round-trip), survivorship-free (tested on
the whole liquid universe incl. failures), and **OOS-robust** (both halves of the trade stream
positive — fragile/regime-dependent cuts do NOT qualify). Only edges that clear ALL of these get
wired, and only after shadow validation. Backtests live in `scripts/edge_*.py`.

Goal: assemble ≥10 validated +EV helpers, wire the keepers (alone or stacked), keep looping.

---

## ✅ VALIDATED EDGES (keepers)

### 1. Cross-sectional momentum — STRONG  (`edge_xsectional.py`)
Rank the liquid universe by trailing return each rebalance; **LONG the top-8, SHORT the bottom-8**
(market-neutral). Long-short spread is robust +EV at **every** lookback/hold tested:

| lookback | hold | spread/rebal | win | OOS |
|---|---|---|---|---|
| 7d | 10d | **+2.50%** | 61% | +2.57 / +2.42 ✓ |
| 14d | 5d | **+2.01%** | 63% | +1.87 / +2.16 ✓ (most balanced) |
| 14d | 10d | +2.20% | 61% | +1.41 / +2.98 ✓ |
| 21d | 5d | +0.93% | 55% | +0.50 / +1.37 ✓ |

**Key:** the edge is the **long-SHORT spread** (relative strength). Long-only is fragile
(+0.3% to −0.5%). Capturing it REQUIRES both legs — a market-neutral book, not the per-coin loop.
**Recommended config: LB=14d, hold=5d** (most balanced OOS).

### 2. Pairs / cointegration stat-arb — STRONG, INDEPENDENT FAMILY  (`edge_pairs.py`)
Mean-reversion of a market-neutral log-spread between co-moving coins (corr>0.6): trade z>2
divergence, exit on reversion. **+1.08%/trade, n=2413, OOS +1.10/+1.06 (rock-solid)**. ORTHOGONAL
to momentum (profits from relative mean-reversion, not trend) → stacking the two diversifies.
Shape: 45% win / neg median → many small losses + fewer big convergence wins (size for that).

### 3. Momentum variants/enhancements (same family as #1)  (`edge_sweep2.py`)
- **vol-scaled xs-momentum** (inverse-vol legs): +0.92–1.70%, OOS +0.89/+0.95 — MORE STABLE than
  plain. **Fold this weighting into the live rebalancer.**
- skip-momentum (12-1, LB=14/skip=3): +0.81% robust · TSMOM absolute (LB=40/thr=5%): +0.53% robust
  (directional, has market beta). Both robust but weaker than the xs core.

### 4. Day-of-week seasonality — INDEPENDENT (calendar) family  (`edge_sweep3.py`)
Cross-coin mean daily return by weekday: **Monday +0.78% (OOS +0.87/+0.68 robust)**, **Thursday
−1.64% (robust negative)**, Sat +0.27%. Tradeable as a long-Mon / flat-or-short-Thu bias (net of a
daily round-trip ~+0.6% Mon). ⚠ CAVEAT: 7 weekdays tested → multiple-comparisons risk; the OOS
consistency (both halves agree) lends some confidence but treat as a tilt, validate forward. Calendar
edge ⇒ orthogonal to momentum + pairs.

### 5. VOL-REGIME FILTER for momentum — ENHANCEMENT (high value)  (`edge_sweep3.py`)
The xs-momentum edge CONCENTRATES in low volatility: **+3.45% (win 65%, OOS +2.78/+4.12) when BTC
trailing-vol is BELOW median**, vs a fragile +0.54% (OOS +1.17/−0.08) above. ⇒ **gate the rebalancer
on BTC vol** (run / up-size only in low-vol regimes). Materially lifts the wired edge.

### 6. Extreme-move fade — MARGINAL  (`edge_sweep.py`)
After a single-day move > |12–18%|, fade it next day: +0.23–0.59%, robust but OOS-2nd-half ~flat.
Thin — small overlay / confirmation at best.

---

## ❌ REFUTED (−EV, tested + rejected — do not revisit without a new angle)
price breakout / oversold bounce / volume-momentum / trend-filtered breakout (`edge_movers.py`) ·
Williams bar patterns (`edge_williams_patterns.py`) · daily news catalyst surge (`edge_catalyst.py`) ·
funding-rate extremes (`edge_funding.py`) · short-term cross-sectional reversal · low-vol anomaly ·
BTC lead-lag (alts don't follow BTC's prior-day move) (`edge_sweep.py`/`edge_sweep2.py`). Common
cause: on daily data, by the time an *absolute* single-coin signal is visible, the move already
happened — we're late. The RELATIVE frames (cross-sectional momentum, pairs spread) broke through.

---

## 🔬 CANDIDATE QUEUE (the infinite quest continues)
DONE: ✅ vol-scaled xs · ✅ skip-momentum · ✅ TSMOM · ✅ pairs stat-arb · ❌ BTC lead-lag · ❌ funding · ❌ low-vol
NEXT (independent structures preferred — momentum variants cluster, diminishing returns):
- **time-of-day / day-of-week / turn-of-month** seasonality (needs intraday cache)
- **OI/price 4-quadrant** (OI logger collecting — `oi_quadrant_backtest.py`)
- xs-momentum on **4h** bars (faster rebalance — check cost drag)
- **liquidation-cascade fade** (buy forced-sell wicks — needs liq data)
- **Kalman/OU dynamic-hedge pairs** (improve the validated pairs edge: dynamic beta vs ratio)
- **Hurst-regime switch** (run momentum when trending, pairs when mean-reverting)
- ✅ **STACK tested** (`edge_stack.py`): momentum + pairs daily streams are UNCORRELATED (corr +0.05,
  confirms orthogonality), but momentum's Sharpe dominates (gross ann ~4.95 vs pairs ~1.27) so 50/50 ≈
  momentum. ⇒ momentum = primary book, pairs = SMALL uncorrelated allocation (not equal-weight).
  Sharpes are GROSS of rebalance cost (optimistic); the orthogonality is the durable result.
NEXT: OI-quadrant · 4h-momentum · liq-fade · Kalman/OU pairs · Hurst regime-switch · Sharpe-optimal blend weights

---

## 🏗 IMPLEMENTATION ARCHITECTURE (how the keepers get wired)
The proven edge (#1) is a **market-neutral cross-sectional rebalancer**, structurally different from
the current per-coin scan→research→execute loop. Plan:

- **`xs_momentum` engine**: each rebalance (every `hold` days), rank the liquid universe by trailing
  `LB`-day return; target **+K longs / −K shorts**, equal-risk per name (reuse the executor's
  structural sizing). Diff vs current holdings → close drops, open adds. Market-neutral (gross ≈ 2×,
  net ≈ 0).
- **Short leg**: requires the short path (currently gated by `min_short_volume`); the top-50-by-volume
  universe clears it. The bottom-K shorts are liquid majors, not microcaps.
- **Cadence**: rebalance on a timer (not the 60s scan); between rebalances, only manage stops.
- **Overlay (#2)**: extreme-fade as a small satellite or an entry-timing nudge on the rebalance.
- **Validate-first**: build → backtest the live wiring → SHADOW (log target book vs actual) → LIVE
  with small gross, scale as forward data confirms. Same discipline as the Williams rebuild.
- **Risk**: keep the bare safety gates (kill-switch, margin floor, per-name + gross caps). Market-
  neutral lowers directional risk but adds short-squeeze tail — cap per-name short size.

## AUDIT LOG (truth-check — re-run + STRESS-TEST, confirm robustness not just reproduction)
- 2026-06-23 #1: xs-momentum +2.37% / pairs +1.08% reproduce EXACTLY. No drift.
- 2026-06-23 #2 (`edge_audit.py`, perturbation stress-test of the WIRED xs-momentum):
  - ROBUST to cost (+1.97% even at 30bps/name), K (+EV at 4/8/12), universe (+EV top-20/30/40). ✓
  - long-only FRAGILE (+0.23%, OOS −3.33/+3.77) ⇒ edge IS the market-neutral spread, not beta. ✓
  - ⚠ **FRAGILITY: regime-dependent.** 4 sub-period quartiles = +5.28% / +0.14% / −0.14% / +4.12%.
    The edge is LUMPY — ~2-month flat/negative stretches (Q2/Q3). The 2-half OOS masked it. ⇒ the
    **vol-regime gate is NECESSARY** (those dead periods ≈ high-vol/choppy), and expect multi-month
    drawdowns live. The stack-test Sharpe (~4.95) is OPTIMISTIC (gross + smoothed); real Sharpe lower.
  - ⚠ ALL backtests cover ONE ~6-month window (Mar–Jun 2026) — not proven across a bear/crash regime.

## STATUS
- **3 INDEPENDENT robust edge families validated** (the real prize, not 10 momentum lookalikes):
  (1) **Momentum** — xs core +2.37%, vol-scaled +1.7% (steadier); **concentrates in LOW BTC-vol
      (+3.45%)** ⇒ add the vol-regime gate. Directional-relative.
  (2) **Pairs stat-arb** +1.08% — relative mean-reversion, ORTHOGONAL → stack for diversification.
  (3) **Day-of-week seasonality** — Mon +0.78%/Thu −1.64% robust (calendar; multiple-testing caveat).
  + extreme-fade overlay (marginal). ~11 refuted (price patterns, Williams, catalysts, funding,
  reversal, low-vol, lead-lag, turn-of-month).
- **xs_momentum REBALANCER — wired + SHADOW-deployed + VERIFIED** (logged 8L/8S target book, 0 orders):
  - ✅ Pure engine `agents/xs_momentum.py` (rank_universe + rebalance_plan) + 6 unit tests (green).
  - ✅ Shadow runner `scripts/xs_momentum_run.py` — builds the live target book + plan, no orders.
  - ✅ **Universe filter fixed** (exclude `@` spot/index markets — shadow caught untradeable shorts
    @109/@144/@155) and **edge RE-VALIDATED on tradeable perps only**: LB=7d/hold=10d +2.37%,
    OOS +2.49/+2.25 robust (most configs still robust; LB=30/hold=5 now borderline).
  - ⏳ NEXT: loop integration — timer-based rebalance (every hold-days) + executor diff-execution
    (close drops / open adds, BOTH legs incl. shorts), SHADOW mode default → live small-gross on sign-off.
- Note: the `able` branch has ~10 PRE-EXISTING test_cleanup failures (executor news/shadow-signals
  path) unrelated to this work — flagged, not introduced here.
