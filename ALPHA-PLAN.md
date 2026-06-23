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

### 2. Extreme-move fade — MARGINAL  (`edge_sweep.py`)
After a single-day move > |12–18%|, fade it next day (big up→short, big down→long): +0.23–0.59%,
robust but OOS-2nd-half ~flat. Real but thin — best as a small overlay / confirmation, not standalone.

---

## ❌ REFUTED (−EV, tested + rejected — do not revisit without a new angle)
price breakout / oversold bounce / volume-momentum / trend-filtered breakout (`edge_movers.py`) ·
Williams bar patterns (`edge_williams_patterns.py`) · daily news catalyst surge (`edge_catalyst.py`) ·
funding-rate extremes (`edge_funding.py`) · short-term cross-sectional reversal · low-vol anomaly
(`edge_sweep.py`). Common cause: on daily data, by the time an *absolute* single-coin signal is
visible, the move already happened — we're structurally late. The cross-sectional (relative) frame
is what broke through.

---

## 🔬 CANDIDATE QUEUE (toward ≥10 — test next)
3. xs-momentum **vol-scaled** (weight legs by inverse realized vol — usually sharpens the spread)
4. xs-momentum **with a market/BTC-regime filter** (only run net-long tilt when BTC trend up)
5. **dual momentum** (xs rank + absolute trend filter on each leg)
6. **time-of-day / weekend** seasonality (needs intraday cache)
7. **pairs / correlation reversion** (cointegrated majors)
8. **OI/price 4-quadrant** (the OI logger has been collecting — `oi_quadrant_backtest.py`)
9. xs-momentum on **4h** bars (faster rebalance — more turns, check cost drag)
10. **basis/spot-perp** dislocation (market-neutral carry)
11. **liquidation-cascade fade** (buy forced-sell wicks)
12. **stacking**: xs-momentum core + extreme-fade overlay + regime gate — test the combination vs each alone

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

## STATUS
- Validated: 2 edges (xs-momentum strong, extreme-fade marginal). 8+ refuted.
- **xs_momentum REBALANCER — build in progress (validate-first):**
  - ✅ Pure engine `agents/xs_momentum.py` (rank_universe + rebalance_plan) + 6 unit tests (green).
  - ✅ Shadow runner `scripts/xs_momentum_run.py` — builds the live target book + plan, no orders.
  - ✅ **Universe filter fixed** (exclude `@` spot/index markets — shadow caught untradeable shorts
    @109/@144/@155) and **edge RE-VALIDATED on tradeable perps only**: LB=7d/hold=10d +2.37%,
    OOS +2.49/+2.25 robust (most configs still robust; LB=30/hold=5 now borderline).
  - ⏳ NEXT: loop integration — timer-based rebalance (every hold-days) + executor diff-execution
    (close drops / open adds, BOTH legs incl. shorts), SHADOW mode default → live small-gross on sign-off.
- Note: the `able` branch has ~10 PRE-EXISTING test_cleanup failures (executor news/shadow-signals
  path) unrelated to this work — flagged, not introduced here.
