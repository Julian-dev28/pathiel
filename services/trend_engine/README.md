# trend_engine

Trend analysis across four lanes, rendered by the `/trends` dashboard tab.

The whole service follows one rule: **the trend is what the market has already
shown; the forecast is a labelled extrapolation that ships with its own
backtest.** Where the evidence says "coin flip", the tab prints coin flip in
amber. Nothing here trades, sizes, or persists capital state.

```
services/trend_engine/
  metrics.py            pure math (slope, efficiency, EMA, Wilson, binomial…)
  forecast.py           7-day price projection + the walk-forward that grades it
  flags.py              catalyst / structure / positioning flags
  hl_trends.py          LANE HL        — Hyperliquid 7d trend + regime
  updown_trends.py      LANE UPDOWN    — Polymarket BTC 5m base rates
  political_trends.py   LANE POLITICS  — political markets in probability space
  recorders.py          LANE RECORDERS — forward-graded P&L of every shadow book
  ai.py                 optional LLM pass (local Claude Code CLI, never an API)
  cache.py              disk cache + background refresh contract
  env.py                .env.local loader (state-dir correctness)
  run.py                CLI
```

## Quick start

```bash
python -m services.trend_engine.run --lane hl              # regime + per-coin table
python -m services.trend_engine.run --lane updown          # BTC 5m base rates
python -m services.trend_engine.run --lane politics        # political drift
python -m services.trend_engine.run --lane recorders       # what the books earned
python -m services.trend_engine.run --refresh-all          # what the scheduler runs
python -m services.trend_engine.run --backtest --save      # re-grade the forecaster
python -m services.trend_engine.run --lane hl --ai         # + optional AI read
```

Then open `/trends`.

## The dashboard contract

Every lane does live network work, so **none of it runs inside a request** —
the same rule the predictions board follows.

| path | what it does |
|---|---|
| `GET /api/dashboard/trends/{lane}` | pure read of `.state/trend_engine/<lane>.json` |
| `GET /api/dashboard/trends/updown/live` | in-progress 5m window only (2 HTTP calls, TTL 5s) |
| `POST /api/dashboard/trends/{lane}/refresh` | operator-gated background recompute |
| `POST /api/dashboard/trends/{lane}/ai` | operator-gated background LLM pass |
| `GET /api/dashboard/trends/job/result?job_id=` | poll either job |

A missing cache renders as `status: empty` carrying the command that fills it.

## Lane HL — Hyperliquid, 7 days

Per coin: returns (1/3/7/14/30d), OLS log-slope in %/day with R², Kaufman
efficiency, EMA stack, streak, ATR, range position, drawdown, beta/correlation
to BTC, residual momentum, funding APR, volume ratio.

Trend labels come from **shape, not size**: `STRONG_UP / UP / CHOP / DOWN /
STRONG_DOWN`. A coin up 20% on a round trip is CHOP, and the ranking says so.

Regime adds breadth, dispersion, trending share, alt strength (median residual
vs BTC), and mean funding — because a +5% BTC week at 30% breadth and the same
week at 80% breadth demand opposite books.

### What the forecast is worth (run it yourself)

```
python -m services.trend_engine.run --backtest --days 400
```

Measured 2026-08-02, 26 coins, 400 daily bars, non-overlapping 7-day anchors:

| metric | value | null | verdict |
|---|---|---|---|
| directional hit | 49.7% (n=1317) | 50% | **no edge** (−0.25σ) |
| p50 abs error | 9.35% | 8.90% random walk | **worse than naive** |
| 80% band coverage | 80.4% | 80% nominal | **calibrated** |
| split-half hit | 47.3% early / 52.1% late | — | **sign flips** |

So: the band is honest, the arrow is not. The tab prints that verdict next to
the forecast column instead of hiding it, and the AI prompt is fed the same
numbers so it cannot narrate a confident call over a coin flip.

## Lane UPDOWN — Polymarket BTC 5m

~6,000 resolved windows from 21 days of Binance 1m klines, on the market's own
5-minute grid, using **Polymarket's own tie rule** (`close >= open` resolves
UP; the scout's older backtest uses a strict `>`).

Conditioning families, each Bonferroni-corrected inside itself: hour of day,
session, day of week, prior direction, prior streak, prior magnitude,
volatility regime, and the HL daily trend the window sits inside.

Measured 2026-08-02: base UP rate **49.4%** (95% CI 48.2–50.7, p=0.37 vs a coin
flip) and **zero** conditionals survived correction. The nearest miss was
"after an UP window" at 47.4% (p_bonf 0.06).

The in-progress window is different. A driftless random walk from the elapsed
move and realised 1m vol is **calibrated**: Brier 0.160 vs 0.25 null (36%
skill), calibration error 1.1pp, every reliability bin's realised rate inside
its CI. That is the only model the live price may be compared to.

**Two traps this lane exists to avoid:**

1. Comparing a next-window base rate to the current window's price invents a
   ~20pp edge — the market already contains the move that happened.
2. `clob.polymarket.com/midpoint` has been observed quoting **0.325 against a
   live 0.27/0.28 book**. Everything here reads the book and prices the side
   you would have to lift (UP costs the ask, DOWN costs 1 − bid).

The market settles on **Chainlink BTC/USD**, not Binance, so a `FEED_BUFFER`
(5pp) must be cleared before a deviation is called actionable.

## Lane POLITICS — probability space

Everything in percentage POINTS: current probability, 1d/7d change, pp/day
slope (linear OLS — a log fit distorts moves near 0 and 1), realised hourly
vol, days to resolution, and a martingale forecast band.

**The null test is gapped.** The obvious version correlates last week's move
with this week's, but both windows share the t−7d price: noise in that single
observation enters the two changes with opposite signs and manufactures mean
reversion. Measured 2026-08-02 on the same 26 markets:

| measurement | corr | verdict |
|---|---|---|
| overlapping (t−14→t−7 vs t−7→now) | **−0.22** | looks like overshoot-and-fade |
| gapped (t−21→t−14 vs t−7→now) | **+0.18**, z=0.81 | martingale, not significant |

The apparent effect was the artifact. Only a `usable` carry (significant AND
robust across a liquidity split AND gapped AND n≥25) is ever allowed to bend a
forecast; otherwise today's price is the forecast.

Markets Gamma still lists as open past their own end date are dropped — they
cannot move and they poison every drift statistic (an earlier run read −0.60,
z=−3.47 purely from expired markets collapsing to zero).

## Lane RECORDERS — the scoreboard

Forward-grades every shadow book through
`hermes_trader.agents.shadow_ledger.grade_records` (real candles, net of
funding) — the same path `scripts/shadow_status.py` uses, so the numbers agree.

Per book: signals, resolved, pending, EV%/signal at 12bps and 25bps, win rate
with a Wilson interval, **first-half vs second-half EV** (a positive average
built on a negative second half is flagged `decaying`), and the
VALIDATED / MARGINAL / PENDING / REFUTED verdict.

Plus the Polymarket paper ledger, per lane, never pooled: realised PnL per $1
position, win rate, and our Brier against the market's on the same resolved
questions. The `updown_5m` lane is resolved **offline** from the klines lane 2
already caches — ~900 rows graded with zero Gamma calls.

This lane takes minutes to grade, which is exactly why it lives behind the
cache with a 6-hour staleness window.

## The AI pass

Optional, additive, and structurally incapable of inventing a number: the
prompt contains only figures computed above (including the lane's own backtest
verdict), and the system prompt forbids computing new ones. Routed through the
**local Claude Code CLI** on the operator's subscription, never a hosted API.
A failure leaves the tab unchanged except for a status line.

Model: `TREND_AI_MODEL` (default `claude-opus-4-8`), timeout
`TREND_AI_TIMEOUT_S`.

## Tests

```bash
python -m pytest tests/test_trend_engine.py -q      # gate: offline, <2s
```

Covers the math identities, the forecaster's nulls, every flag predicate, the
tie rule, Bonferroni correction, random-walk calibration, executable-edge
pricing, the gapped null test and its guards, the cache contract, and the
dashboard routes. No network: fixtures and injected `getter` / `runner`
callables throughout.

## Gotchas worth keeping

- **State directory.** `hermes_trader.agents.rebalancer_owned` freezes
  `HERMES_STATE_DIR` at import time and that variable lives in `.env.local`.
  A CLI entry point that skips `env.load()` silently reads a *different*
  shadow-ledger directory than the bot writes (first symptom: "2 books, 4
  signals" against a tree holding 28 books). Every entry point calls it first.
- **HL rate limits.** The scan runs 4 workers, no more. This repo has burned
  that budget before.
- **The walk-forward is not part of a scan.** It pulls 400 daily bars per coin,
  so it runs on its own daily cadence and is attached to the scan from
  `.state/trend_engine/hl_eval.json`.
