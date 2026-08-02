# W-TREND1 — trend_engine build: three nulls and two data traps

Date: 2026-08-02
Code: `services/trend_engine/`, tab `/trends`, tests `tests/test_trend_engine.py`
Reproduce: `python -m services.trend_engine.run --lane {hl,updown,politics,recorders}`

The `/trends` tab was built to answer "what has the market shown in the last
week, and what does that imply for next week". Building it produced five
results worth keeping. Three are nulls; two are data bugs that would have
manufactured fake edges on a dashboard.

---

## 1. NULL — 7-day trend extrapolation has no directional edge

`services/trend_engine/forecast.py::project` is a shrunken-drift model:
7-day OLS log-slope, shrunk 0.35x and weighted by sqrt(r2 x efficiency),
capped at 1.5 sigma, with a lognormal band.

Walk-forward, 26 HL coins, 400 daily bars, **non-overlapping** 7-day anchors
(`--backtest --days 400`):

| metric | value | null | verdict |
|---|---|---|---|
| directional hit | 49.66% (n=1317) | 50% | −0.25σ, **no edge** |
| p50 abs error | 9.35% | 8.90% (random walk) | **worse than naive** |
| 80% band coverage | 80.4% | 80% | **calibrated** |
| split-half | 47.3% early / 52.1% late | — | **sign flips** |

Two methodology notes that changed the answer:

- **Overlapping anchors inflate significance.** At `step=2` with a 7-day
  horizon the same outcome bars are reused, and the contrarian variant looked
  like +2.9σ. On non-overlapping anchors it is +0.25σ.
- **Split-half must use the same anchor grid.** Re-running the walk on
  truncated series shifts the anchor phase and manufactures a difference; the
  implementation buckets one grid by bar index instead.

Consequence for the product: the tab shows a calibrated *band* and prints the
coin-flip verdict in amber next to the direction column. Do not build a book on
7d trend continuation without new evidence.

## 2. NULL — BTC 5m up/down is unconditionally a coin flip

6,047 windows / 21 days of Binance 1m klines, on the market's 5-minute grid,
using Polymarket's own tie rule (`close >= open` resolves UP; 0.28% of windows
are exact ties, worth ~0.3pp of base rate).

Base UP rate **49.4%** (95% CI 48.2–50.7), p = 0.37 against a coin flip.

Seven conditioning families, each Bonferroni-corrected inside itself: hour of
day, session, day of week, prior direction, prior streak, prior magnitude,
volatility regime. **Zero buckets survived correction.** Nearest miss: after an
UP window, the next window is UP 47.4% (n=2988, p_bonf 0.062) — a mild
anti-persistence that is not significant.

## 3. POSITIVE — the *in-progress* window is calibrated and the market knows it

A driftless random walk from the elapsed move plus realised 1m vol, evaluated
at minute 3 of 5:

- Brier **0.160** vs 0.25 null → 36% skill
- calibration error **1.1pp**, every reliability bin's realised rate inside its CI
- stable across lookbacks (12–288 windows) and decision minutes (1–4)

And the live book prices essentially there: repeated live reads showed the
model within ~2pp of the executable price. So the model is right and there is
no free money — which is the useful conclusion. The tab watches for the case
where the book *does* diverge past a 5pp buffer.

## 4. TRAP — `clob.polymarket.com/midpoint` disagrees with the book

Observed live: `/midpoint` returned **0.325** while the book was **0.27 bid /
0.28 ask**. Comparing a model to that midpoint produced a 17–22pp "edge" that
did not exist. Fixed by reading `/book` and pricing the side that would be
lifted (UP costs the ask, DOWN costs 1 − bid).

Related: these markets settle on the **Chainlink BTC/USD data stream**, not
Binance (read off the market description). Any model mined from Binance carries
a feed-mismatch error, so `FEED_BUFFER = 0.05` must be cleared before a
deviation counts.

## 5. TRAP — the political "mean reversion" is a shared-endpoint artifact

Correlating last week's probability change with this week's shares the t−7d
price between the two windows. Noise in that single observation enters the two
changes with opposite signs, so the correlation is negative by construction.

Same 26 political markets, 2026-08-02:

| measurement | corr | z | reading |
|---|---|---|---|
| overlapping (t−14→t−7 vs t−7→now) | **−0.22** | — | "moves overshoot and fade" |
| gapped (t−21→t−14 vs t−7→now) | **+0.18** | 0.81 | martingale, not significant |

A separate version of the same bug: Gamma lists some markets as open past their
own end date. Including them (they collapse to 0 or 1) gave corr −0.60,
z = −3.47 — a "significant" result driven entirely by expired rows.

Both are guarded: expired markets are dropped, the null test is gapped, and a
carry coefficient is only allowed into a forecast when it is significant AND
robust across a liquidity split AND measured on gapped windows AND n ≥ 25.

## 6. INFRA — a CLI entry point that skips `.env.local` reads the wrong ledger

`hermes_trader.agents.rebalancer_owned` freezes `_STATE_DIR` from
`HERMES_STATE_DIR` at import time, and that variable lives in `.env.local`.
The recorders lane first reported "2 books, 4 signals" against a tree holding
28 books, with no error anywhere — it was reading `<repo>/shadow_ledger`
instead of `<repo>/.state/shadow_ledger`. Fixed by
`services/trend_engine/env.py`, called before any `hermes_trader` import in
every entry point. Worth checking in any future service with a CLI.

---

## 7. CORRECTION — the advertised Polymarket fee is not the charged fee

Gamma's market payload for the 5m updown markets advertises
`takerBaseFee: 1000` / `makerBaseFee: 1000`. On the CLOB websocket, **79 of 79
executed trades reported `fee_rate_bps: "0"`** (2026-08-02).

This mattered: with the advertised 1000 bps and Polymarket's
`rate × min(p, 1−p) × shares` formula, a fee is up to 5¢/share, which turns any
one-tick crossed pair into a loss — the conclusion originally written here. At
the charged rate of 0, a 1¢ crossed pair is 1¢ of edge, and the only thing
standing between us and it is latency. `FEE_BPS_DEFAULT` is now 0 with the
advertised value explicitly distrusted, and `observed_fee_bps()` reads the live
rate off `last_trade_price` so the tape keeps the number honest.

Generalisable: price the fee off executions, never off the market metadata.

## 8. Latency — polling floor is geography, so stop polling

Measured medians from this machine: `gamma /markets?slug` 188 ms, `clob /book`
327 ms, `clob POST /books` (BOTH sides) 302 ms, curl-subprocess 323 ms vs
keep-alive session 297 ms. TLS handshake is not the bottleneck; RTT is.

Batching both books into one call plus caching the immutable slug→token lookup
took a quote from ~840 ms to ~245 ms. The CLOB market **websocket** takes it to
**0 ms**: 6,630 `price_change` events in 25 s for one pair, and each entry
carries `best_bid`/`best_ask` so no order-book reconstruction is needed.

## 9. TRAP — health measured in a process that exits is a permanent lie

The `/trends` arb card showed `websocket down · STALE · 0 events · reconnects 0`
for hours while the socket was fine. The status block came from the cached lane
payload, and that cache is written by `run --refresh-all`: a process that
subscribes, quotes in the same millisecond, snapshots `feed().health()`, and
exits. `reconnects 0` with an empty `last_error` was the tell — a socket that
*failed* would have retried; this one never got the chance.

Two consequences, both fixed: the cached `live_pair` was a REST read on every
refresh (`warm_feed()` now waits for both legs before quoting, 1.06s), and the
dashboard now reads socket health from `preflight()`, which runs in the
long-lived server process, tagged with `pid`. Verified after the fix: server pid
98510 reporting 7,244 events / 371 trades / `fee_rate_bps 0` on its own feed.

Generalisable: a health metric is only meaningful from the process that owns the
resource. Cache the *measurement*, never the *liveness*.

### What ships from this

- `/trends` tab, four lanes, every forecast next to its own backtest verdict.
- HL lane covers crypto AND HIP-3 (`xyz:`) as separate sectors with separate
  benchmarks — SP500 for equities, BTC for perps.
- Microstructure block: the fat tail (confirmed), the both-sides arb (real math,
  latency-bound), and the price-calibration question (needs the sampler).
- CLOB websocket feed: quotes served from memory at 0 ms with a REST fallback.
- 127 gate tests, offline, <1s.
- Scheduler jobs `trends-price` (30 min), `trends-recorders` (6 h), plus the
  book sampler as its own process (`scripts/restart.sh sampler`).
- No new capital, no new book. The HL lane's honest read is: the band is
  usable, the arrow is not.

### Open, accruing

The unbiased book sampler (~288 windows/day) is the only path to answering
claim 1 (price-bucket calibration) and the tradeable half of claim 2 (buying
the cheap side near the close). At n=25 the late-ticket EV interval still
straddles breakeven. Re-read `--edges` after a few days.
