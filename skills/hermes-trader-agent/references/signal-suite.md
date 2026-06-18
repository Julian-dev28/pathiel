# Free Signal Suite + Backtest Tools (2026-06)

Our own builds of Unusual-Whales-style analytics — **no paid feed, no extra keys**
(except the optional Etherscan one, since removed). Pure parsers + thin TTL-cached
fetches. Modules in `hermes_trader/agents/`, CLIs in `scripts/`.

## The signals

| Signal | Module / CLI | Source (free) | Use |
|---|---|---|---|
| **GEX / max-pain / gamma walls** | `options_gex.py` / `scripts/gex.py` | CBOE delayed options JSON | xyz equity perps: call wall = resistance/ride-target, put wall = support, regime = pin (mean-revert) vs negative-gamma (squeeze-prone) |
| **FINRA short volume** | `short_volume.py` / `scripts/short_volume.py` | FINRA daily Reg SHO files | high ratio = crowded short = squeeze fuel for longs |
| **News catalyst** | `news_catalyst.py` / `scripts/news.py` | GDELT 2.0 + RSS wires | breaking-headline / coverage-surge detector (solves "catch the headline live, not via Twitter") |
| **Crypto whale (order-flow)** | `crypto_whale.py` / `scripts/whale.py` | Binance public aggTrades (rolling window) | net aggressive large prints → whale buying/selling pressure |

On-chain exchange-netflow (Etherscan) was built then **removed** — aggTrades is
the better fit for the (mostly non-ERC-20) universe.

## How they're wired (`shadow_signals.py`)

- **`shadow_signals` config** → `run_shadow_async` logs every signal per candidate
  on a daemon thread (ZERO hot-path latency). Pure observation.
- **`signal_enforcement` config** → `enforce_signals` acts on the **forced-override
  path only** (never AI-conviction trades, LONG-only). **CACHE-ONLY** (`allow_fetch=
  False`) so it never fetches on the execute path; the async advisor warms the cache;
  cold cache = fail-open. VETO: GEX pin-trap (xyz) / whales dumping (crypto). BOOST:
  breaking news / whale buying / crowded short → lowers the override bar.
- **Entry-context capture** stores each signal's value + slippage + regime + funding
  + hold at entry → merged into the outcome store at close, for the forward backtest.

**Critical wiring fact:** signals feed the EXECUTOR *and* (since 2026-06-16) the AI
RESEARCH PROMPT (`research.py _signals_block`). For months they only fed the
executor — the AI decided blind and PASS'd rippers. See `signal-vs-action-gap.md`.

## Backtest tools (`scripts/`)

| Tool | What it does |
|---|---|
| `backtest.py` | Strategy on historical candles, **heuristic** entries (no LLM) |
| `backtest_logged.py` | Replays the **real logged AI verdicts** through current gates/exits. Useful flags: `--mode {ai,lowconf,force,sidestep}`, `--force-bar`, `--sidestep-min-slow-burn`, `--apply-runner-gate`, `--regime-mode`, `--loss-cooldown-min`, `--slippage-bps`, `--leverage`, `--roe-cap`, `--max-loss`, `--protect`, `--retrace` |
| `backtest_portfolio.py` | **Portfolio-level**: shared equity, concurrency + gross-notional + margin gates (the only one that models capital contention / correlated drawdown). Useful flags: `--sweep-concurrent`, `--max-notional-pct`, `--mode`, `--force-bar`, `--sidestep-min-slow-burn`, `--apply-runner-gate`, `--loss-cooldown-min`, `--slippage-bps` |
| `strategy_grid_search.py` | Grid-search wrapper over logged replay. Profiles include `sizing`, `gate`, `exit`, and `blend`; modes include `ai`, `force`, and `sidestep`. Use it to test candidate config families before editing live risk knobs. |
| `reentry_backtest.py` | Exit-mode + re-entry comparison (scalp vs trend-ride vs regime) |
| `signal_backtest.py` | Forward read: realized PnL by signal-at-entry, from the outcome store |

**Discipline:** backtests are slippage-free + often small-n + single-regime — they
OVERSTATE edge. The live realized outcome store (with the new slippage/funding
capture) is ground truth. Optimize only at n≥50 clean. See `lessons-2026-06.md`.
