# Hermes-Trader
> Autonomous multi-market trading agent for Hyperliquid — crypto perps, equity perps (TSLA, NVDA, AAPL), and commodities (NATGAS, SILVER, COPPER). A standalone Python system built with FastAPI and a pluggable AI brain (OpenRouter default; Claude/Codex CLI optional), operated by [Hermes Agent](https://github.com/NousResearch/hermes-agent) through an MCP server.

**What it does:** Scans every Hyperliquid market (500+ perps + spot), fires statistical triggers on price/volume/breakout signals, runs a cheap pre-AI technical analysis filter, and only calls AI on CONFIRMED setups. Executes real trades with DSL-managed dynamic exits — no human in the loop.

---

## CLI Quick Start

```bash
# 1. Start/restart autonomous trading loop + dashboard API
scripts/restart.sh

# 2. Confirm there is one loop process and one API server
scripts/restart.sh status

# 3. Monitor
tail -f logs/trading_loop.log
python3 scripts/status.py
```

Dashboard served at `http://localhost:8000` (port from `HERMES_PORT`).

---

## The problem it solves

Trading signals appear constantly — 5-minute spikes, hourly trends, daily breakouts. Most systems call expensive AI on every signal, burning tokens on noise. Hermes-Trader solves this by separating cheap statistical analysis from expensive AI reasoning:

1. **Scan** — 500+ markets in parallel with volume pre-filtering and rate-limit-aware batching
2. **TA Filter** — multi-timeframe indicators (EMA, RSI, ATR, ADX, volume) — zero AI cost
3. **AI Research** — only on CONFIRMED signals, plus any fired momentum burst, through the selected AI brain provider
4. **Execution** — ATR equal-risk sizing, Hyperliquid-valid order normalization, and DSL dynamic exits (loss protection → profit locking)
5. **Discovery** — built-in Hyperfeed Discovery replicates Smart Money leaderboards and whale signals

This architecture reduced daily AI costs from $8-$52 to $3-$10 while improving signal quality.

---

## Architecture

```
+---------------------------------------------------------------+
|          hermes-trader — autonomous trading pipeline          |
|                                                               |
|  Scan ➜ TA Filter ➜ AI Brain ➜ Risk Gates ➜ Execute ➜ DSL Monitor ──▶ Auto-Close
|        (cheap)          (expensive)     (11 gates)            (per-tick, 2-phase)
│                       |
│                  Only CONFIRMED
│                  signals proceed
├───────────────────────────────────────────────────────────────┤
│               Hyperfeed Discovery                             |
│  Leaderboard • Whale Flow • OI Anomaly • Whale Tracking       |
+---------------------------------------------------------------+
```

### Pipeline

```
┌─────────────┐    ┌──────────────┐    ┌─────────────────┐    ┌──────────┐    ┌──────────┐
│  Perception │───>│  TA Filter   │───>│    AI Brain     │───>│  Risk    │───>│  Executor│
│   Scanner   │    │  (TA Filter) │    │ provider seam   │    │  Gates   │    │ (HL + DSL)│
│ 5m/1h/4h    │    │  EMA/RSI/ATR│    │ Verdict + Price │    │  11 gates│    │ SL/TP    │
│ Volume-N    │    └──────────────┘    └─────────────────┘    └──────────┘    └──────────┘
└─────────────┘
     │
     ├── Hyperfeed Discovery (leaderboard, whale index, OI anomaly)
     │     ↳ whale_concentration(), oi_funding_anomaly()
     │     ↳ discovery_get_top_traders(), leaderboard_get_trader_positions()
     └── Rate-Limit Pipeline (1200 weight/min — batch + cache)
```

---

## Key Features

### Rate-Limit-Aware Scan Pipeline
- **Volume pre-filtering**: Top-N markets by 24h notional volume plus mover and rotating-sweep slots
- **Parallel batch scanning**: Workers fan out within batches, sleep between
- **TTL caching**: 5m candles are cached inside the scan interval; 1h enrichment is cached longer and only fetched for surfaced markets
- **Configurable**: `HERMES_SCAN_INTERVAL`, `HERMES_MAX_MARKETS`, `HERMES_SCAN_WORKERS`, `HERMES_BATCH_SIZE`, `HERMES_BATCH_SLEEP`

### Pluggable AI Brain
- **Single seam**: `research._call_ai()` delegates to `hermes_trader/agents/ai_brain.py`; every provider returns verdict text for the same `parse_verdict()` contract.
- **Providers**: `openrouter` (default), `claude_cli`, and `codex_cli`.
- **Hot switch**: set `AI_BRAIN_PROVIDER` or `.agent-config.json` → `ai_brain.provider` to switch without code changes. Config is read on each research call.
- **Failure-safe**: provider failure, timeout, empty output, or CLI JSON-less output returns `""`, which becomes `ai_down=True` and a PASS that cannot be upgraded by the TA sidestep path.
- **OpenRouter preserved**: the 402 affordability retry still downgrades `max_tokens` once before failing closed.

### DSL (Dynamic Stop-Loss) Exit Engine
- **Phase 1 — Loss Protection**: Hard stop at the tighter of `max_loss_pct` or `max_loss_roe_pct / leverage` in spot terms, optionally widened to a volatility-scaled `atr_stop` (ATR×mult, clamped to floor/ceiling). Current live new-entry config is `2.5%` spot / `15%` ROE with `atr_stop` enabled (1.5× ATR, 1.0–2.5% clamp).
- **Phase 2 — Profit Locking**: Activated once price moves `protect_pct` in your favor. Current live new-entry config arms at `1.25%`, trails with a tight `retrace_threshold=0.10` (banks give-backs early), then loosens via `phase2_tiers` at `+8%` (0.35) and `+15%` (0.40) so proven runners get room; the floor ratchets one-way and never gives back locked profit.
- **Hard/stale timeout**: `hard_timeout_minutes` is the maximum hold horizon; `stale_flat_timeout_minutes` exits positions that never reach the profit-lock phase.
- **Auto-registration**: Every executed position is registered for DSL tracking
- **Persisted across restarts**: Tracker state (peak, floor, breach counter) is written to `.dsl-state.json` on every advance, so a daemon restart doesn't reset the ratchet
- **Exchange reconciliation**: Each scan tick, trackers are reconciled with live exchange positions — manually-opened or externally-closed positions stay in sync; positions opened before the engine shipped are synthesized from `entryPx`
- **Auto-close**: When a tick trips a floor/stop/timeout, the trading loop market-closes the position and logs a `dsl_exit` event to the session log. No human in the loop

### Risk & Resilience Gates
- **Regime-aware gating**: trades are scored against the asset's applicable trend regime — BTC for native crypto, own-trend-first for tokenized equities, and own trend for commodities. Aligned trades clear at `aligned_min_conf`; counter-regime trades need `counter_regime_min_conf`. `block_counter_trend_bypass` stops weak own-coin trigger bypasses from sneaking longs into a downtrend.
- **Short-specific liquidity floor**: shorts require deeper 24h volume (`min_short_volume_usd`) than longs — thin markets squeeze.
- **Free-margin floor**: `min_available_margin_pct` blocks new entries once free margin gets thin, capping over-leverage and correlated stacking.
- **Correlation cap**: `max_crypto_long_correlated` limits simultaneous correlated crypto exposure.
- **Self-healing watchdog**: the loop re-execs itself if a scan cycle hangs; the watchdog is armed *before* startup network I/O so it also covers startup hangs.
- **Partial-dex degraded-read guard**: a HIP-3 dex that fails to fetch no longer drops its equity from the aggregate — prevents false "huge loss" reads from poisoning memory or tripping the kill switch.
- **Re-entry backstop**: a DSL-registry check prevents position stacking when a live read flakes (restart / 429 window).

### Hyperfeed Discovery (Native, no MCP)
Replicates the Hyperfeed MCP plugin's data directly from HL API:
- `leaderboard_get_markets(limit)` — top markets by OI + volume
- `market_get_funding_regime()` — LONG_CROWDED / SHORT_CROWDED / NEUTRAL analysis
- `whale_concentration()` — identifies assets with whale accumulation
- `oi_funding_anomaly()` — OI spike + negative funding + flat price = accumulation signal
- `discovery_get_top_traders(...)` — trader rankings with win rates
- `market_get_asset_data(asset)` — candles + funding + OI for any coin

---

## Core Modules

| Module | Purpose |
|--------|---------|
| `hermes_trader/agents/perception.py` | Multi-market volume-pre-filtered scanner with parallel batch scanning |
| `hermes_trader/indicators/triggers.py` | Trigger engine — composite scoring across signal types |
| `hermes_trader/agents/ta_filter.py` | Pre-AI technical analysis — multi-TF (1h/4h/1d) EMA, RSI, ATR, ADX, volume confirmation |
| `hermes_trader/agents/research.py` | AI research pipeline — fetches candles, builds context, dispatches to the configured AI brain |
| `hermes_trader/agents/ai_brain.py` | Pluggable verdict providers: OpenRouter HTTP, Claude CLI, Codex CLI |
| `hermes_trader/agents/risk_gates.py` | 11 independent risk gates: confidence, notional caps, daily loss, cooldown, correlation, news blackout, etc. |
| `hermes_trader/agents/executor.py` | ATR/fallback sizing + Hyperliquid precision normalization + EIP-712 order signing + DSL exit registration |
| `hermes_trader/agents/dsl_exit.py` | Two-phase trailing stop engine — disk-persisted (`.dsl-state.json`), reconciled with exchange positions each tick |
| `hermes_trader/agents/hyperfeed.py` | Hyperfeed Discovery API — leaderboard, whale index, OI/funding context |
| `hermes_trader/agents/memory.py` | Persistent file-backed state (`.agent-memory.json`, `.agent-config.json`) |
| `hermes_trader/agents/config_store.py` | Config persistence layer |
| `hermes_trader/agents/system_prompt.py` | Dedicated system prompt for the trading agent |
| `hermes_trader/client/hl_client.py` | Hyperliquid REST + WebSocket client (mids, candles, account state) |
| `hermes_trader/client/ws_client.py` | Persistent WebSocket connection for sub-second mids |
| `hermes_trader/client/universe.py` | Volume-ranked market loader with 24h caching |
| `hermes_trader/client/cache.py` | LRU + TTL memoization with in-flight dedup |
| `hermes_trader/client/lock.py` | fcntl lock with stale-PID recovery for scan coalescing |
| `hermes_trader/client/parallel.py` | Concurrency-bounded fan-out for independent API calls |
| `hermes_trader/client/daemon.py` | Long-lived scan scheduler with tick timeouts + graceful shutdown |
| `hermes_trader/client/exchange.py` | Order placement, leverage setting, trigger orders (SL/TP) |
| `hermes_trader/indicators/math.py` | TA indicators: EMA, SMA, ATR, RSI, ADX |
| `hermes_trader/models/types.py` | Shared data type: `Candle` (OHLCV) |
| `hermes_trader/server.py` | FastAPI server — 22 REST routes for frontend/dashboard |

---

## Configuration

There are two config files, and they bootstrap **differently**:

| File | Ships with repo? | You… | Read |
|------|------------------|------|------|
| **`.agent-config.json`** | **Yes — tracked**, comes pre-populated with the live strategy | **edit** it (don't create) | fresh on every trade — no restart |
| **`.env.local`** | **No — gitignored** | **create** it: `cp .env.local.example .env.local`, fill in keys | at process start — restart to apply |

So on a fresh clone: `.agent-config.json` is already there (tweak the values); `.env.local` does not exist until you copy the example and add your credentials. If `.agent-config.json` is ever missing or malformed, the loader falls back to the tuned default config with `"mode": "OFF"` (analyse-only, no orders) — it fails safe, never trades blind. Partial configs are normalized against the same defaults, and bad nested strategy blocks are ignored rather than wiping stop/gate settings.

### `.env.local` — credentials & runtime

Copy `.env.local.example` → `.env.local` and fill in:

```bash
# ── AI brain / OpenRouter (default research provider) ─────────
AI_BRAIN_PROVIDER=openrouter              # openrouter | claude_cli | codex_cli
OPENROUTER_API_KEY=sk-or-...your-key      # required when provider=openrouter
OPENROUTER_MODEL=x-ai/grok-4.3            # optional — this is the OpenRouter default
# OPENROUTER_MAX_TOKENS=2048              # optional; 402 retry can shrink this once
# AI_BRAIN_TIMEOUT_S=120                  # CLI providers are capped at 120s
# CLAUDE_CLI_COMMAND=claude               # optional override for provider=claude_cli
# CLAUDE_CLI_MAX_TURNS=1                  # cheap mode default
# CODEX_CLI_COMMAND=codex                 # optional override for provider=codex_cli

# ── Hyperliquid ──────────────────────────────────────────────
HYPERLIQUID_WALLET_ADDRESS=0x...          # required — the signing (agent) wallet
HYPERLIQUID_PRIVATE_KEY=0x...             # required — that wallet's key
# HYPERLIQUID_MASTER_ADDRESS=0x...        # optional — set for an agent-wallet
#                                           setup; the master holds the funds

# ── News (optional) ──────────────────────────────────────────
# BRAVE_API_KEY=BSA...                    # optional — enables news headlines
#   in AI research and the news-blackout risk gate. Without it, research runs
#   with news_context = "no news" and that gate is inert.

# ── Scan tuning (optional — defaults shown) ──────────────────
HERMES_SCAN_INTERVAL=60        # seconds between scan cycles
HERMES_MAX_MARKETS=45          # top-vol+movers candle-fetch budget per scan
HERMES_MAX_MARKETS_HIP3=18     # of that budget, slots reserved for HIP-3
HERMES_UNIVERSE_SWEEP=0        # >0 = ALSO rotate N extra tail markets/cycle so the
#                                FULL universe is covered over ceil(N_universe/N)
#                                cycles (top-vol+movers still scanned every cycle).
#                                Keep total (MAX_MARKETS+SWEEP) within the rate budget.
HERMES_SCAN_WORKERS=8          # max concurrent market scans per batch
HERMES_BATCH_SIZE=10           # markets per parallel batch
HERMES_BATCH_SLEEP=1.0         # seconds between batches (raise to pace a wider scan)
HERMES_WATCHDOG_TIMEOUT_S=600  # re-exec the loop if a scan/cycle makes no progress
#                                for this long. A scan slower than this (too many
#                                markets / too much batch_sleep) trips it — keep
#                                MAX_MARKETS+SWEEP fast enough that a cycle stays well under.
# HERMES_PORT=8000             # FastAPI server port
```

Keep `MAX_MARKETS + UNIVERSE_SWEEP` within HL's ~1200 weight/min budget — a wider per-cycle scan must be paced (`BATCH_SLEEP`) or it 429-storms AND trips the watchdog. For full-universe coverage prefer the **rotating sweep** (fast cycles, full coverage over time) over one giant slow scan. See [Rate Limit Math](#rate-limit-math).

When `enable_hip3=true`, the budget splits into `(HERMES_MAX_MARKETS - HERMES_MAX_MARKETS_HIP3)` crypto slots + `HERMES_MAX_MARKETS_HIP3` HIP-3 slots, each sorted by 24h volume independently. Without this split, BTC/ETH/SOL/etc. dominate the single sorted list and tokenized-equity perps (e.g. `xyz:CRCL` $34M, `xyz:DRAM` $22M) never get candles fetched — so their +20% / −8% swings never surface a signal.

### `.agent-config.json` — trading behaviour & risk

The live trading knobs. Read fresh on **every trade**, so edits take effect on the
next cycle — no restart. Keys are read tolerantly: `snake_case` or `camelCase`
both resolve (`max_trade_notional_usd` ≡ `maxTradeNotionalUsd`).

```json
{
  "mode": "LIVE",
  "ai_brain": {
    "provider": "openrouter",
    "timeout_s": 120,
    "claude_cli": { "command": "claude", "max_turns": 1 },
    "codex_cli": { "command": "codex" }
  },
  "enable_crypto": true,
  "enable_hip3": true,
  "equity_fraction_per_trade": 0.2,
  "leverage": 12,
  "max_trade_notional_usd": 350,
  "tp_scale_fraction": 0.5,
  "max_concurrent": 10,
  "max_total_notional_pct": 10.0,
  "max_daily_loss_usd": -30,
  "daily_giveback_halt_pct": 0.35,
  "daily_giveback_min_peak_usd": 25.0,
  "min_available_margin_pct": 0.10,
  "min_market_volume_usd": 700000,
  "min_hip3_volume_usd": 700000,
  "min_short_volume_usd": 50000000,
  "cooldown_min": 30,
  "min_ai_confidence": 0.67,
  "counter_regime_min_conf": 0.8,
  "block_counter_trend_bypass": true,
  "max_crypto_long_correlated": 3,
  "coin_allowlist": [],
  "coin_blocklist": [],
  "dsl_exit": {
    "max_loss_pct": 2.5,
    "max_loss_roe_pct": 15.0,
    "atr_stop": { "enabled": true, "atr_mult": 1.5, "floor_pct": 1.0, "ceiling_pct": 2.5 },
    "protect_pct": 1.25,
    "retrace_threshold": 0.1,
    "phase2_tiers": [
      { "pct_above_entry": 8.0, "retrace_threshold": 0.35 },
      { "pct_above_entry": 15.0, "retrace_threshold": 0.4 }
    ],
    "stale_flat_timeout_minutes": 480
  },
  "atr_risk_sizing": {
    "enabled": true,
    "risk_per_trade_pct": 0.02,
    "sizing_basis": "primary_stop"
  },
  "ta_sidestep_force_execute": true,
  "override_max_daily_extension_pct": 30.0,
  "override_volume_confirm": { "enabled": true, "min_ratio": 1.2 },
  "trend_filter_200ma": { "enabled": true, "period": 200, "allow_daily_mover_long_bypass": true },
  "runner_entry_gate": {
    "enabled": true,
    "allow_shorts": false,
    "require_daily_mover_longs": false,
    "shock_day_fresh_impulse": true,
    "min_confidence": 0.67,
    "min_crypto_composite": 20.0,
    "min_hip3_composite": 32.0,
    "mover_min_composite": 20.0
  },
  "late_chase_relax": { "enabled": true, "min_ext_pct": 20.0, "max_ext_pct": 30.0, "min_volume_usd": 5000000 },
  "capital_rotation": { "enabled": true, "min_candidate_composite": 40.0, "min_hold_minutes": 30, "protect_winner_roe_pct": 3.0 },
  "xs_momentum": { "enabled": true, "ranking": "pct_k", "lookback_days": 7, "hold_days": 5, "k_per_leg": 4 },
  "extreme_fade": { "enabled": true, "crash_pct": -0.12, "max_new_per_cycle": 2, "scan_interval_min": 30 },
  "rally_exhaustion": { "enabled": true, "threshold_pct": 12.0, "min_volume_usd": 20000000, "leverage": 1 },
  "hail_mary_short": { "enabled": true, "shadow_only": true, "min_breadth_bearish_pct": 0.55, "min_volume_usd": 20000000 }
}
```

The snippet above is the current live strategy shape, not a guarantee that those
values are optimal in future market regimes. Missing keys are filled from
`hermes_trader.agents.config_store.DEFAULT_CONFIG`; keep the tracked
`.agent-config.json` explicit so reviews show intentional strategy changes.

| Key | What it does | Fallback/default |
|-----|--------------|---------|
| `mode` | `OFF` = analyse only, no orders · `LIVE` = place real orders | `OFF` |
| `equity_fraction_per_trade` | Fraction of perp equity committed as margin per trade when ATR risk sizing is disabled — see [Trade Sizing](#trade-sizing) | `0.01` |
| `leverage` | Leverage **ceiling** — each trade uses `min(this, the coin's own max)`. Coin maxes differ (BOME 3×, BTC 40×). Set high (e.g. 40) to ride each coin's max. Also multiplies position notional. | `5` |
| `min_ai_confidence` | Minimum AI confidence for a LONG/SHORT to execute | `0.8` |
| `max_concurrent` | Max simultaneous open positions | `3` |
| `max_trade_notional_usd` | Hard ceiling on a single trade's notional | `350` |
| `asset_notional_multiplier` | Optional asset-bucket sizing scale applied after risk sizing. Defaults neutral; use it only for controlled risk experiments, not as the primary alpha fix. | `{"crypto": 1.0, "hip3": 1.0}` |
| `max_total_notional_pct` | Ceiling on combined open notional, as a multiple of equity | `1.0` |
| `max_daily_loss_usd` | Daily-loss kill switch (negative number) | `-100` |
| `daily_giveback_halt_pct` | **Give-back breaker**: once the day peaks ≥ `daily_giveback_min_peak_usd`, halt NEW entries if it retraces more than this from peak (existing positions ride their stops; resets at UTC roll). Locks green days from round-tripping | `0` (off) |
| `daily_giveback_min_peak_usd` | Arm threshold for the give-back breaker — stays disarmed until the day's peak PnL reaches this | `20` |
| `tp_scale_fraction` | Fraction auto-banked at the TP target (server-side reduce-only trigger at ~1 ATR); rest rides the trail. Captures profit instead of round-tripping | `0.5` |
| `crowded_with_min_conf` | **Squeeze caution**: a with-the-crowd aligned trade (short into `SHORT_CROWDED` / long into `LONG_CROWDED`) must clear this conf or it's blocked `via:crowded_squeeze` | `0` (off) |
| `min_available_margin_pct` | Block new trades when free margin drops below this fraction of equity — caps over-leverage/stacking. Lower = deploys more aggressively | `0.10` |
| `min_market_volume_usd` | Skip markets below this 24h volume | `5_000_000` |
| `min_short_volume_usd` | Extra 24h-volume floor for **shorts only** — thin markets squeeze, so shorts need deeper liquidity | `0` |
| `cooldown_min` | Minutes before re-trading the same coin | `60` |
| `counter_regime_min_conf` | Confidence bar for a trade **against** the regime (e.g. long in a downtrend) | `0.7` |
| `aligned_min_conf` | Confidence bar for a trade **with** the regime (trend-aligned) — typically lower than the counter-regime bar | _unset_ |
| `block_counter_trend_bypass` | When `true`, own-coin trigger bypasses cannot override the counter-regime gate — stops long-into-downtrend bleed | `false` |
| `override_max_daily_extension_pct` | Max positive 24h move allowed for PASS→LONG TA sidestep. Blocks parabolic chase entries (e.g. TNSR +70%, which backtests −EV above 30% extension). `0` disables | `30` |
| `backup_sl_max_frac_of_liq` | Caps server-side backup stop distance to this fraction of the approximate liquidation buffer | `0.60` |
| `max_crypto_long_correlated` | Cap on simultaneous correlated crypto positions (concentration guard) | `2` |
| `coin_allowlist` | If non-empty, **only** these coins are tradeable | `[]` (all) |
| `coin_blocklist` | Coins that are never traded | `[]` |

**Nested blocks** (all in `.agent-config.json`, all hot-read for new entries):

- **`ai_brain`** — verdict provider selection. `provider` can be `openrouter`,
  `claude_cli`, or `codex_cli`; `AI_BRAIN_PROVIDER` overrides it. CLI providers
  run in cheap/headless mode by default and must emit the same final-line verdict
  JSON as OpenRouter. On any provider failure the result is `ai_down=True` PASS.
- **`dsl_exit`** — trailing-stop engine. `max_loss_pct` and `max_loss_roe_pct`
  are the hard stop, with the tighter spot-equivalent value binding; the optional
  `atr_stop` sub-block widens it to a volatility-scaled stop (`atr_mult` × ATR,
  clamped to `floor_pct`/`ceiling_pct`). Current live new-entry values are
  `max_loss_pct=2.5`, `max_loss_roe_pct=25`, `atr_stop` enabled (1.5×, 1.0–2.5%),
  `protect_pct=1.25`, and a tight `retrace_threshold=0.10`. `phase2_tiers` is the
  profit-scaled give-back ladder (loosens the trail on proven runners: +8%→0.35,
  +15%→0.40). `stale_flat_timeout_minutes` (480) exits positions that never reach
  the profit-lock phase. Tracker state → `.dsl-state.json` (override
  `HERMES_DSL_STATE_FILE`). Existing open positions keep the policy captured at
  entry; config edits affect new entries and synthesized trackers.
- **`atr_risk_sizing`** `{enabled, risk_per_trade_pct, sizing_basis}` —
  equal-risk position sizing: target risk = `risk_per_trade_pct × equity`, converted
  to notional from the configured stop distance. Current live uses
  `risk_per_trade_pct=0.2` and `sizing_basis="primary_stop"`. This overrides the
  flat `equity_fraction_per_trade` path; volatile/wide-stop coins get smaller size.
- **`ta_sidestep_force_execute`** — the only remaining PASS upgrade path. It can
  upgrade an AI PASS to LONG only on composite>=runner minimum or momentumBurst,
  never on slow-burn alone, never when AI is down, and never above
  `override_max_daily_extension_pct`. The upgraded LONG still must pass the
  normal runner gate.
- **`late_chase_relax`** `{enabled, min_ext_pct, max_ext_pct, min_volume_usd}` —
  narrows the runner gate's "late trend-only chase" block:
  trend-aligned entries with no fresh breakout are admitted ONLY on liquid coins
  (vol ≥ `min_volume_usd`) inside the `[min_ext_pct, max_ext_pct]` daily-extension
  band — the one pocket backtested +EV / OOS-robust (20–30% ext, +0.15–0.20%/t).
  Low-liquidity and out-of-band chases stay blocked.
- **`capital_rotation`** `{enabled, min_candidate_composite,
  min_hold_minutes, protect_winner_roe_pct}` — when a strong fresh candidate is
  blocked purely by capital (book full / notional cap), evicts the weakest
  non-winner (roe < `protect_winner_roe_pct`, held ≥ `min_hold_minutes`) to make
  room.
- **`xs_momentum`** — live cross-sectional momentum book using the validated
  `pct_k` rank.
- **`extreme_fade`** — live long-only crash-fade book, crash-bar deduped and
  cadence-throttled by `scan_interval_min` so it cannot block later hooks every
  scan.
- **`rally_exhaustion`** — live short-only post-rally exhaustion book, isolated
  from crash-fade long logic.
- **`hail_mary_short`** — AI/semis HIP-3 short basket, currently shadow-only.
  It logs when basket breadth and proxy trend are bearish, then waits for fresh
  daily breakdowns before it would become tradeable. Do not set
  `shadow_only=false` until the shadow/backtest sample is EV+.

Trigger internals (weights, sigma thresholds, candle interval) live separately in
`hermes_trader/agents/config.py` — edit there to tune the scan itself.

**TL;DR — where to set what:** strategy/risk knobs → `.agent-config.json` (live,
no restart); credentials + scan/infra env → `.env.local` (restart to apply); scan
trigger internals → `hermes_trader/agents/config.py` (restart).

---

## Quick Start

### Prerequisites
- Python 3.11+
- Hyperliquid wallet with private key
- OpenRouter API key ([openrouter.ai](https://openrouter.ai)) for the default `openrouter` brain, or non-interactive Claude/Codex CLI auth for `claude_cli` / `codex_cli`
- (Optional) Brave Search API key for news

### Setup
```bash
git clone https://github.com/Julian-dev28/hermes-trader
cd hermes-trader

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies (editable, with dev extras: pytest + ruff)
pip install -e ".[dev]"

# Configure
cp .env.local.example .env.local
# Edit .env.local with your keys
```

## Running

### Trading Loop + API Server
```bash
# Start or restart both long-running processes
scripts/restart.sh

# Check process status
scripts/restart.sh status

# Follow logs
tail -f logs/trading_loop.log
```
The API is available at `http://localhost:8000`. Health check: `GET /` returns `{"service": "Hermes-Trader", "version": "0.3.0", "status": "running"}`.

`scripts/restart.sh` manages the autonomous trading loop and the FastAPI server,
including stop/verify/start and log files under `logs/`. The MCP stdio server is
not managed by this script; Hermes Agent respawns it on tool calls.

### Manual Process Launch
```bash
# Foreground loop, useful while debugging
python scripts/trading_loop.py

# API server only
python -m hermes_trader.server
# or: uvicorn hermes_trader.server:app --host 0.0.0.0 --port 8000
```

The `--env prod --daemon` flags are informational only; they do not fork the
process. Use `scripts/restart.sh` for normal operation.

**Trading Loop Behavior:**
- Scans the top ~45 markets (by 24h volume) plus mover slots and a rotating universe sweep, every 60 seconds
- Each tick, reconciles DSL trackers with live exchange positions and runs an exit pass — market-closes anything whose dynamic floor, hard stop, or timeout has tripped
- Runs the TA filter on each trigger — only CONFIRMED signals (or fired momentum bursts) reach AI research
- Researches qualifying signals with the selected AI brain provider (`openrouter`, `claude_cli`, or `codex_cli`)
- Executes trades that clear all 11 risk gates
- Runs continuously until stopped

---

## Testing

```bash
pytest                          # offline unit tests — fast, no network, CI-safe
pytest -m online                # read-only tests against the live Hyperliquid public API
HERMES_E2E=1 pytest -m live      # real-money e2e: places a tiny order, calls the LLM
```

`online` and `live` tests are deselected by default. The `live` suite spends
real funds (a ~$14 round-trip order plus a billable AI-brain call) and is
additionally gated behind `HERMES_E2E=1` so it can never run by accident.

### Backtests and Grid Sweeps

Logged-replay backtests use the real saved AI verdicts from `.agent-memory.json`
and route them through the current gates/exits:

```bash
.venv/bin/python scripts/backtest_logged.py --hours 168 --summary-only \
  --mode sidestep --force-bar 30 \
  --apply-runner-gate --regime-mode neutral --slippage-bps 5

.venv/bin/python scripts/strategy_grid_search.py --hours 168 --profile blend \
  --mode sidestep --force-bar 30 \
  --regime-mode neutral --slippage-bps 5
```

Treat these as replay diagnostics, not proof of future profit. The live outcome
store, with slippage/funding/hold-time capture, is the source of truth once the
sample is large enough.

---

## MCP Integration

hermes-trader is a standalone Python application; **Hermes Agent operates it through this MCP server** — that is the whole integration boundary. The agent calls the tools below; the trading engine itself has no Hermes-framework dependency.

The MCP server (`scripts/hermes-mcp-server.py`) exposes 99 tools over stdio transport. The 16 primary tools are listed below; the remainder are Hyperliquid data passthroughs (some are placeholders pending SDK wiring).

| Tool | Description |
|------|-------------|
| **Trading Core** | |
| `scan` | Scan all HL markets (volume-filtered), return triggered candidates |
| `research` | Deep AI analysis on a coin with the configured AI brain provider |
| `submit_verdict` | Store an agent-authored verdict as an analysis for MCP-native brain mode |
| `execute` | Execute trade through risk gates + DSL registration |
| `close_position` | Close a coin through the same reduce-only executor close helper used by loop exits |
| `state` | Get full agent state (mode, equity, positions, trades) |
| `config` | Get/set agent configuration (mode, risk caps, thresholds, `ai_brain`) |
| **Hyperfeed Discovery** | |
| `leaderboard_get_markets` | Top markets by OI + volume |
| `leaderboard_get_top_traders` | Trader rankings with win rates |
| `leaderboard_get_trader_positions` | Positions for a specific trader |
| `discovery_get_top_traders` | Discovery top traders (alias) |
| `discovery_get_trader_state` | Full trader state from discovery |
| **Market Data** | |
| `market_get_asset_data` | Candles + funding + OI for any coin |
| `market_get_funding_regime` | LONG_CROWDED / SHORT_CROWDED / NEUTRAL |
| `market_list_instruments` | All tradeable instruments |
| `market_get_mids` | Real-time mid prices |

Configure in Hermes Agent's `config.yaml`:
```yaml
mcp_servers:
  hermes-trader:
    command: python3
    args:
      - /path/to/hermes-trader/scripts/hermes-mcp-server.py
    cwd: /path/to/hermes-trader
    timeout: 60
    env:
      OPENROUTER_API_KEY: ${OPENROUTER_API_KEY}
```

---

## Operating via Hermes Agent

With the skill loaded and the MCP server registered (see [MCP Integration](#mcp-integration)),
you operate hermes-trader by prompting your Hermes Agent in plain language — the agent
calls the MCP tools for you. Restart your Hermes session first so the skill and MCP
server are picked up.

| Goal | Prompt to give Hermes |
|------|-----------------------|
| **Check state** | *Load the hermes-trader skill and show me its current state — mode, equity, open positions, recent trades.* |
| **Configure** (`OFF` analyzes only, `LIVE` places real orders) | *Set hermes-trader to LIVE mode with a max trade size of $20.* |
| **Scan** | *Scan the markets with hermes-trader and list what triggered, with composite scores.* |
| **Research** | *Research the top candidate and tell me the verdict, side, and confidence.* |
| **Run one full cycle** | *Run a hermes-trader cycle: scan, run the TA filter, research the best candidate, and execute it if the verdict is LONG or SHORT. Tell me what happened.* |
| **Start continuous trading** | *Start the hermes-trader trading loop in the background, then confirm it is running.* |
| **Stop continuous trading** | *Stop the hermes-trader trading loop.* |
| **Monitor (in session)** | *Check hermes-trader's status and tell me if anything changed since the last report.* |

"Start continuous trading" should use `scripts/restart.sh loop`, which starts
the same scan -> TA-filter -> research -> execute loop on its own every
`HERMES_SCAN_INTERVAL` seconds, independent of the Hermes session.

For **hands-off monitoring**, resume the hourly status cron job (zero AI cost — it
just runs `status.py`; see [`references/cron-jobs.md`](skills/hermes-trader-agent/references/cron-jobs.md)):
```bash
hermes cron list            # find the "Hermes Trader Hourly Report" job id
hermes cron resume <job-id> # start hourly status delivery
```

---

## Trade Sizing

The current live path uses ATR equal-risk sizing in `executor.py`:

```
target_risk_usd = perp_equity × atr_risk_sizing.risk_per_trade_pct
raw_notional    = target_risk_usd / primary_stop_distance_pct
trade_notional  = clamp(raw_notional, max_trade_notional_usd, leverage caps)
```

Then the executor converts the target notional into the exact Hyperliquid-valid
coin size before risk gates run. That prevents a small intended trade from
passing gates and then being silently enlarged by exchange minimum-order logic.

Relevant knobs live in `.agent-config.json`:

| Key | Meaning | Example |
|-----|---------|---------|
| `atr_risk_sizing.enabled` | Use stop-distance-based equal-risk sizing | `true` |
| `atr_risk_sizing.risk_per_trade_pct` | Fraction of equity risked at the primary stop | `0.02` = 2% |
| `atr_risk_sizing.sizing_basis` | Stop source for sizing | `primary_stop` |
| `leverage` | Leverage ceiling — each trade uses `min(this, coin's own max)`; pushed to the exchange via `set_leverage` | `10` = up to 10× |
| `max_trade_notional_usd` | Hard cap on a single trade's notional | `350` |
| `equity_fraction_per_trade` | Fallback margin fraction when ATR sizing is disabled | `0.20` = 20% |

When ATR sizing is disabled, the fallback formula is:

```
trade_notional = perp_equity × equity_fraction_per_trade × leverage
```

Caps that bound both sizing paths: `max_concurrent`, `max_total_notional_pct`,
`max_trade_notional_usd`, exchange max leverage, available margin, and the
coin-specific precision/minimum order. Config keys are read tolerantly —
`snake_case` or `camelCase` both work.

Defaults if the keys are absent: `equity_fraction_per_trade = 0.01`, `leverage = 5`.

---

## Design Decisions

### Why volume pre-filtering?
HL's API rate limit is **1200 weight/minute**. A single candle fetch costs **weight 20**. Scanning all 500+ markets naively requires 10,000+ weight -> instant 429. Volume pre-filtering to 45 core markets plus a small rotating sweep leaves room for mids, HIP-3 metadata, dashboard/account calls, and occasional 1h enrichment. Sustained usage is roughly `1200 * markets / interval` weight/min, so the safe rule is **markets plus sweep should stay below the scan interval in seconds**.

### Why DSL exit engine?
Static SL/TP orders don't adapt to price action. The DSL engine implements a two-phase design: Phase 1 protects your capital (hard stop), Phase 2 locks in profits (trailing floor with tiered retrace thresholds). The floor only moves up — it never gives back locked profit. State is persisted on disk so a daemon restart doesn't reset the ratchet, and the registry is reconciled against the exchange each tick so manually-opened or externally-closed positions stay coherent. This pattern is inspired by senpi-skills' DSL dynamic stop-loss engine.

### Why pluggable AI brain?
The research prompt and verdict parser are stable; only the transport changes.
Keeping OpenRouter, Claude CLI, and Codex CLI behind one provider interface lets
the operator switch the decision-maker without adding another executor or another
prompt/parse path. LONG/SHORT/CLOSE verdicts still flow through the same risk
gates, kill switch, close helper, and DSL exit engine.

### Why Hyperfeed Discovery?
The HL leaderboard and whale tracking aren't exposed through the public API. This module reconstructs the same data patterns (leaderboard rankings, smart money concentration, OI anomalies) from the raw HL endpoints we already call. No external MCP dependency needed.

### Why pure Python?
Rewritten from TypeScript/Next.js to enable simpler deployment, MCP integration with Hermes Agent, and native testability without a headless browser.

---

## Rate Limit Math

| Operation | Weight | Notes |
|-----------|--------|-------|
| `allMids` | 2 | Real-time prices |
| `metaAndAssetCtxs` | 20 | Universe + volume + OI (perp) |
| `spotMetaAndAssetCtxs` | 20 | Universe + volume + OI (spot) |
| `candleSnapshot` (per coin) | 20 | Plus per-item weight |
| **Total per scan cycle** | ~900-1,100 | Top 45 markets plus a small sweep, one 5m candle fetch each |

With `HERMES_MAX_MARKETS=45`, a small `HERMES_UNIVERSE_SWEEP`, and a 50s candle-cache TTL, each 60s scan fetches fresh 5m candles while keeping room for mids, HIP-3 metadata, dashboard/account calls, and occasional 1h enrichment. The cache TTL is deliberately kept just below the scan interval so the scanner never reacts to a stale snapshot — raising it would re-introduce that lag.

The crypto/HIP-3 budget split (`HERMES_MAX_MARKETS_HIP3`) is a *partition* of the same scan budget, not extra calls. If 429s or data gaps show up, lower `HERMES_UNIVERSE_SWEEP` or increase `HERMES_BATCH_SLEEP` before tightening strategy gates.

When HIP-3 is enabled, `fetch_account_state(user, include_hip3=True)` issues one extra `clearinghouseState` POST per registered HIP-3 dex (~8 dexes × weight 2 = ~16 weight). The aggregated path is used by the dashboard, the trading-loop heartbeat, and the MCP `state`/`portfolio` handlers. MCP `close_position` delegates to `executor.close_position_market()`, so closes share the same reduce-only order path, DSL cleanup, trigger-order cancellation, and loss-cooldown behavior as loop exits.

---

## Project Structure

```
hermes-trader/
├── hermes_trader/                  # Pure Python agent
│   ├── __init__.py
│   ├── __main__.py                # Entry point
│   ├── server.py                  # FastAPI server — 22 routes
│   ├── agents/                    # Core agent logic
│   │   ├── config.py              # Agent configuration model
│   │   ├── config_store.py        # Config persistence
│   │   ├── ai_brain.py            # Pluggable AI brain providers
│   │   ├── executor.py            # ATR/fallback sizing + order execution + DSL registration
│   │   ├── memory.py              # File-backed state
│   │   ├── perception.py          # Volume-filtered parallel scanner
│   │   ├── research.py            # AI research pipeline
│   │   ├── risk_gates.py          # 11 risk gates
│   │   ├── system_prompt.py       # Agent system prompt
│   │   ├── ta_filter.py           # Pre-AI TA filter
│   │   ├── dsl_exit.py            # Two-phase trailing stop engine
│   │   └── hyperfeed.py           # Discovery API (leaderboard, funding regime, OI context)
│   ├── client/                    # External API clients
│   │   ├── exchange.py            # HL order placement
│   │   ├── hl_client.py           # HL REST + WebSocket client
│   │   ├── ws_client.py           # Persistent WebSocket for real-time mids
│   │   ├── universe.py            # Volume-ranked market loader with caching
│   │   ├── cache.py               # LRU + TTL memoization
│   │   ├── lock.py                # fcntl lock with stale-PID recovery
│   │   ├── parallel.py            # Concurrency-bounded fan-out
│   │   └── daemon.py              # Long-lived scan scheduler
│   ├── indicators/                # TA math
│   │   ├── math.py                # EMA, SMA, ATR, RSI, ADX
│   │   └── triggers.py            # Trigger detection + composite scoring
│   └── models/                    # Shared data types
│       └── types.py               # Candle (OHLCV)
├── scripts/
│   ├── hermes-mcp-server.py       # MCP server (stdio, 99 tools)
│   └── trading_loop.py            # Continuous trading loop
├── skills/hermes-trader-agent/    # Hermes Agent skill
├── tests/                         # pytest suite — offline / online / live e2e
└── docs/
    ├── AI_BRAIN_OPERATOR_WIRING.md # Codex/Claude/Hermes/OpenClaw brain wiring
    └── journal-schema.md          # Trade journal schema
```

---

## Built With

- FastAPI — Python web framework
- OpenRouter / Claude CLI / Codex CLI — AI research brain providers
- Hyperliquid Python SDK — perpetual futures DEX
- Brave Search API (optional, for news signals)
- Prometheus (`prometheus-client`) — `/metrics` instrumentation + observability
- Kubernetes (kind + kube-prometheus-stack) — local deployment & Grafana dashboards (see [`k8s/`](k8s/README.md))

It is **operated by** [Hermes Agent](https://github.com/NousResearch/hermes-agent)
through the MCP server — Hermes Agent is not a build dependency; the trading
engine is plain Python.

---

**Note:** Project trunk is `main` (Python). The legacy TypeScript/Next.js implementation lives on archived branches.
