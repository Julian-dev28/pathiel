---
name: hermes-trader-agent
description: Use when operating, maintaining, or debugging hermes-trader — the standalone autonomous Hyperliquid trading system that Hermes Agent drives through its MCP server. Covers the scan/research/execute pipeline, pluggable AI-brain providers, live EV+ strategy books, risk gates, MCP tool wiring, and Hyperliquid order-placement gotchas.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [trading, hyperliquid, mcp, autonomous, quant]
    related_skills: [hyperliquid-agent-wallets]
    homepage: https://github.com/Julian-dev28/hermes-trader
---

# Hermes-Trader Agent

`hermes-trader` is a **standalone Python trading system** for Hyperliquid
perpetual markets — both native crypto perps (BTC, ETH, etc.) **and HIP-3
tokenized-equity / commodity / index perps** (`xyz:NVDA`, `xyz:GOLD`,
`km:US500`, `xyz:CL`, etc.) when the `enable_hip3` flag is on. Hermes Agent
operates it through the **MCP server** registered in `~/.hermes/config.yaml`
(`mcp_servers.hermes-trader`) — that MCP boundary is the integration. The
trading engine itself has no Hermes-framework dependency; it is
Hermes-*operated*, not Hermes-*built*.

Repo: `/Users/julian_dev/Documents/code/hermes-trader`. The user may work on
dirty local branches and switch deploy branches between sessions. **Commit/push
only when the user asks, and never push directly to another branch without
explicit confirmation.**

## Architecture

A pipeline designed to keep AI token cost proportional to real opportunity:

1. **Scan** — fetch all mids (native + HIP-3 dexes when `enable_hip3=true`),
   evaluate 6 triggers per market (pctMoveSpike, volumeSpike, breakout,
   rangeCompression, trendStrength, momentumBurst). The candle-fetch budget
   is bucketed (default 45 core markets plus optional sweep): top-N crypto by volume + top-M crypto
   by `|24h%|` (movers) + top-K HIP-3 by volume, so HIP-3 tokenized equities
   and low-volume native big-movers each get scanned regardless of where
   they rank against the BTC/ETH volume leaders. `momentumBurst` bypasses
   the composite-score gate so explosive moves always surface. Every
   perception is persisted via `memory.record_perception`.

   Env knobs: `HERMES_MAX_MARKETS`, `HERMES_MAX_MARKETS_HIP3`,
   `HERMES_MAX_MARKETS_MOVERS`, `HERMES_UNIVERSE_SWEEP`,
   `HERMES_SCAN_WORKERS`, `HERMES_BATCH_SIZE`, `HERMES_BATCH_SLEEP`,
   `HERMES_MOVERS_VOL_FLOOR_USD`, `HERMES_HIP3_MOVERS_FLOOR_USD`.
2. **Pre-research cooldown** — `trading_loop.py` checks the most recent
   trade per coin and skips paid AI research if the coin is still inside its
   `cooldown_min` window. The execute-time `cooldown_gate` remains as the
   authoritative backstop.
3. **TA Filter** — `ta_filter.py` does multi-timeframe validation (1h/4h/1d
   EMA, RSI, ATR, ADX, volume) at zero AI cost. Only CONFIRMED perceptions
   (score ≥ 45) reach AI research; WEAK / REJECTED are dropped. A perception
   whose `momentumBurst` trigger fired bypasses the gate.
4. **AI Research** — deep AI analysis via the selected brain provider
   (`openrouter`, `claude_cli`, or `codex_cli`) on triggered candidates. The
   provider returns verdict text only; `parse_verdict()` and the executor path
   are shared.
5. **Execution** — ATR equal-risk sizing when `atr_risk_sizing.enabled=true`
   (current live path), with fraction-based sizing as the explicit fallback.
   Orders are clamped by per-trade notional and leverage caps, normalized to
   Hyperliquid coin precision before gates, signed through the SDK, protected
   by a server-side backup stop-loss, and registered with DSL dynamic exits.
   Blocked attempts are NOT written to `memory._trades` — only successful
   executions appear there, so `cooldown_gate` keys off real history rather
   than its own rejection log.

## Running

Use `scripts/restart.sh` as the canonical process manager for the loop and API
server:

```bash
scripts/restart.sh              # restart both trading loop + FastAPI server
scripts/restart.sh loop         # restart trading loop only
scripts/restart.sh server       # restart FastAPI server only
scripts/restart.sh stop         # stop both, don't start
scripts/restart.sh status       # show what's running
```

Logs land in `logs/trading_loop.log` and `logs/server.log`. The MCP server
(`scripts/hermes-mcp-server.py`) is intentionally NOT managed — it's a transient
stdio process respawned by Hermes Agent on each tool call. If MCP code is stale:
`pkill -f hermes-mcp-server.py` and the next tool call respawns fresh.

When `restart.sh` is run from Codex and background children are reaped by the
execution wrapper, launch the long-lived processes in `screen` instead:

```bash
screen -dmS hermes-server /bin/zsh -lc 'cd /Users/julian_dev/Documents/code/hermes-trader && HERMES_HL_RATE_REFILL_PER_SEC=5 HERMES_HL_RATE_CAPACITY=200 .venv/bin/python -m hermes_trader.server >> logs/server.log 2>&1'
screen -dmS hermes-loop /bin/zsh -lc 'cd /Users/julian_dev/Documents/code/hermes-trader && HERMES_STARTUP_GRACE_S=0 HERMES_META_PREWARM_TIMEOUT_S=3 .venv/bin/python scripts/trading_loop.py >> logs/trading_loop.log 2>&1'
screen -ls
curl -s -o /tmp/hermes-dashboard.html -w "%{http_code}\n" http://localhost:8000/
```

Manual foreground launch is only for debugging:

```bash
python scripts/trading_loop.py        # continuous scan -> research -> execute
python -m hermes_trader.server        # API server only
```

The `--env prod --daemon` flags are parsed but **informational only** — the
script does NOT actually fork or daemonize itself. The loop already has its own
`while True` with periodic sleeps; use `scripts/restart.sh` when it needs to
survive the terminal session.

Cadence is `HERMES_SCAN_INTERVAL` (default 60s). Or drive the steps individually
through the MCP `scan` / `research` / `execute` tools.

### Restarting the Trading Loop + Server

`scripts/restart.sh` handles stop (SIGTERM → SIGKILL fallback), verify,
background start with logs, and a status readout. `status` may show the process
group (`screen`/shell/python/`caffeinate`); the invariant is exactly one
`python ... scripts/trading_loop.py` process. If logs show overlapping scan
cadences, check for an orphan with `ps ax | rg "scripts/trading_loop.py"` and
stop the older process before trusting live behavior.

**Keep-awake (laptop hosts):** `start_loop` now launches a `caffeinate -i -m -w $pid` alongside the loop so the host can't idle/maintenance-sleep mid-run. On a sleeping Mac the whole process freezes — you'll see `[watchdog] no progress for N s — HUNG` re-execs where N is *minutes-to-hours* (that's the sleep gap, NOT a code hang; the watchdog re-execs correctly on wake). During sleep only the **server-side SL/TP brackets** protect open positions. `caffeinate -i` stops idle sleep but a closed lid on battery can still clamshell-sleep → keep on AC for true 24/7. See `references/daemon-investigation.md`.

**Pitfall:** `python3 scripts/trading_loop.py --env prod --daemon` does NOT daemonize — the `--daemon` flag is parsed but has no effect. Use the restart script for persistent operation; only fall back to a manual launch if the script is unavailable.

## Asset-class toggles

`.agent-config.json` carries two boolean flags that control what the scanner
and executor will trade:

- `enable_crypto` (default `true`) — scan native HL perps (BTC, ETH, SOL, ...).
- `enable_hip3` (default `true`) — scan HIP-3 perpDexes (xyz / vntl / km / ...).

Both false = no-op scan (logged loudly). Single-class runs hand the full
candle budget to that class. The executor enforces the same gating at
execute-time so stale perceptions can't sneak through if the flag flips
mid-cycle (`hip3_disabled` / `crypto_disabled` reasons).

## AI Brain Providers

Research has one provider seam:

```
research._call_ai(system_prompt, user_message)
  -> hermes_trader.agents.ai_brain.get_brain(provider).complete(...)
  -> parse_verdict(...)
```

Valid providers are:

- `openrouter` — default HTTP completion provider. Keeps the 402 affordability
  retry that shrinks `max_tokens` once when OpenRouter reports an affordable
  token budget.
- `claude_cli` — `claude -p --output-format json` headless provider. The
  handler parses the envelope, rejects `is_error`, and passes `.result` to the
  shared verdict parser.
- `codex_cli` — `codex exec --sandbox read-only --ephemeral -` provider. The
  handler passes stdout to the shared verdict parser.

Selector precedence: `AI_BRAIN_PROVIDER` env var, then
`.agent-config.json` → `ai_brain.provider`, then `openrouter`. The selector is
hot-read per research call; code changes still require a loop restart.

Failure contract is load-bearing: provider failure, timeout, non-zero exit,
empty stdout, or output without verdict JSON returns `""`. That becomes
`ai_down=True` PASS and the TA sidestep override must not upgrade it.

CLI provider auth is outside Hermes. Verify `claude -p` / `codex exec` works
non-interactively in the loop's actual environment before switching live.

## MCP Integration

The server (`scripts/hermes-mcp-server.py`, stdio, 99 tools) is registered in
`~/.hermes/config.yaml`:

```yaml
mcp_servers:
  hermes-trader:
    command: python3
    args:
      - /Users/julian_dev/Documents/code/hermes-trader/scripts/hermes-mcp-server.py
    cwd: /Users/julian_dev/Documents/code/hermes-trader   # recommended
    timeout: 60
    connect_timeout: 30
    env:
      OPENROUTER_API_KEY: ${OPENROUTER_API_KEY}
```

Primary tools: `scan`, `research`, `submit_verdict`, `execute`,
`close_position`, `state`, `config`. Adding tools and the audit invariant: see
`references/mcp-server.md`. After editing the server, restart it:
`pkill -f hermes-mcp-server.py` (the next call respawns it fresh).

`close_position` delegates to `executor.close_position_market()`. Do not
re-implement close logic in MCP handlers; that helper owns reduce-only close
orders, DSL tracker cleanup, stale trigger-order cancellation, realized PnL
capture, and loss-cooldown arming.

`submit_verdict` is the MCP-native verdict-authority seam: external operators
such as Codex, Claude Code, Hermes Agent, or OpenClaw submit their own
PASS/LONG/SHORT/CLOSE analysis, then call `execute(analysisId)` to route it
through the existing gates and close helper.

## State Files

Project state — not Hermes memory (all gitignored):
- `.agent-config.json` — mode (OFF/LIVE), AI brain provider, risk caps, thresholds
- `.agent-memory.json` — perceptions, analyses, trades, cooldowns
- `.data_funding_oi.jsonl` / `.data_logger_ts` — live funding/OI data logger
- `.extreme_fade_state.json` / `.extreme_fade_live_ts` — crash-bar dedup and
  cadence throttle for the live fade book
- `.hail_mary_short_ts` / `.hail_mary_short_seen.json` — shadow/live cadence and
  dedup for the AI/semis HIP-3 short basket
- `.rally_exhaustion_live_ts` / `.rally_exhaustion_live_seen.json` — live
  rally-exhaustion cadence and dedup
- `.rebalancer_claims.json` — cross-book claim registry
- `.xs_momentum_positions.json`, `.xs_rebalance_ts`, `.xs_volmgd_history` —
  live cross-sectional momentum state
- `logs/trading_loop.log` and `logs/server.log` — process logs

Claim registry invariant: only active claim books may own `.rebalancer_claims.json`
entries. Current owners are `xs_momentum`, `rally_exhaustion`, and
`hail_mary_short`; stale owners from deleted books are auto-scrubbed by the
registry and surfaced by `status.py`.
If a new live book uses `get_claims_registry()`, add its book name to
`rebalancer_owned.active_claim_books()` coverage before enabling it live.

## Live EV+ Books

Only validated EV+ methods should be enabled live. Current live books:

- `xs_momentum` — cross-sectional momentum rebalance, live, claim-scoped.
- `extreme_fade` — long-only crash fade, live, crash-bar deduped and cadence
  throttled.
- `rally_exhaustion` — short-only rally exhaustion, live, cadence/dedup gated.

Shadow / research book:

- `hail_mary_short` — AI/semis HIP-3 short basket (`NVDA`, `AMD`, `MSFT`,
  `GOOGL`, `MU`, etc.). It is watchlist-driven but trigger-gated by basket
  breadth, proxy trend, and fresh daily breakdowns. Current live config keeps it
  `shadow_only=true`; do not promote it to live capital until its shadow/backtest
  sample is EV+.

`gex_signal` is not standalone alpha. It is a HIP-3 option gamma guardrail that
vetoes longs under nearby call-wall risk.

Removed/refuted/shadow methods should stay gone from config, docs, tests, and
MCP tools. Do not reintroduce shadow logging or disabled alpha paths without a
fresh EV+ audit and explicit user request.

## Risk Gates (independent, no short-circuiting)

Every gate is evaluated; results are collected even when one blocks:
confidence, max_concurrent, per_trade_notional_cap, daily_loss_killswitch,
**daily_giveback**, market_liquidity_floor (+ HIP-3 split), short_liquidity,
coin_allowlist / coin_blocklist, cooldown, opposite_direction_guard,
correlation_cap, equity_risk_cap, news_blackout, market_regime.

Notes on specific gates:
- **daily_giveback** (give-back breaker, 2026-06-06): once the day's PnL peaks
  ≥ `daily_giveback_min_peak_usd`, blocks NEW entries if it retraces >
  `daily_giveback_halt_pct` from that peak (existing positions ride their stops;
  resets at UTC roll). Locks green days from round-tripping. Uses the TRUE
  aggregate account PnL (not main-dex-only — that bug spuriously halted). `0` = off.
- **market_regime**: blocks counter-trend trades unless `confidence ≥
  counter_regime_min_conf` OR `composite_score ≥ 50` ("via composite") OR a
  binary momentum trigger fired. Aligned trades clear at the lower
  `aligned_min_conf`. **`block_counter_trend_bypass`** (currently `true`)
  disables ONLY the binary-trigger bypass — the composite≥50 path stays open, so
  strong-momentum counter-trend trades (e.g. an alt long in a down regime via
  composite strength) still pass. **Crowded-squeeze caution** (`crowded_with_min_conf`):
  a with-the-crowd aligned trade (short+`SHORT_CROWDED` / long+`LONG_CROWDED`) no
  longer gets the free "aligned" pass — must clear that conf or it's blocked
  `via:crowded_squeeze` (those are the entries that get squeezed). Regime is
  computed from **1h candles, 8-bar lookback** each scan (fresh, not stale).
- **short_liquidity**: a SEPARATE, deeper 24h-volume floor for SHORTS only
  (`min_short_volume_usd`, $50M) — thin markets squeeze. Distinct from the
  long/general `market_liquidity_floor`.
- **correlation**: `max_crypto_long_correlated` caps simultaneous correlated
  crypto exposure (concentration guard).
- **news_blackout**: skipped for tokenized-equity perps. Crypto + commodity gated.

**Surfacing layer (what reaches research)** — beyond the weighted composite gate,
several weight-0 "surfacing bypasses" bring a coin to the AI even below the gate;
the AI + risk gates then adjudicate:
- `uptrendMomentum` / `downtrendMomentum` — sustained intraday trend (both
  directions; the down side is what lets us short selloffs).
- `dailyMover` — large liquid 24h movers. Raw `daily_move_pct` is carried into
  analysis so TA sidestep can block parabolic PASS upgrades.

**Entry-side guardrails (2026-06, backtest-gated):**
- **`late_chase_relax`** (LIVE) — narrows the runner gate's "late trend-only
  chase; no fresh breakout/burst" block. Admits trend-aligned no-breakout entries
  ONLY on liquid coins (vol ≥ `min_volume_usd`, default $5M) inside the
  `[min_ext_pct, max_ext_pct]` daily-extension band (20–30%) — the one pocket
  retained in the live config. Low-liquidity and out-of-band chases stay blocked.
- **`capital_rotation`** (LIVE) — when a strong fresh candidate is blocked purely by
  capital (book full / notional / margin), evicts the weakest non-winner (roe <
  `protect_winner_roe_pct`, age ≥ `min_hold_minutes`) for it. Wired at both the
  executor stage and the pre-research margin preflight.
- **`gex_signal`** (LIVE, HIP-3 only) — option gamma call-wall guardrail for
  tokenized equities. It is a veto, not standalone alpha: longs jammed under a
  nearby long-gamma call wall are blocked at the configured wall distance.

**Config keys are read tolerantly** — current gates accept snake_case and the
legacy camelCase form for common knobs. Prefer snake_case in `.agent-config.json`
and MCP writes so diffs stay predictable.

## Trade Sizing

Current live sizing uses `atr_risk_sizing`: target risk is
`equity × risk_per_trade_pct`, converted to notional using the primary stop
distance (`sizing_basis=primary_stop`) and then clamped by
`max_trade_notional_usd`, configured leverage, and the coin's max leverage.
When ATR sizing is disabled, the fallback is
`equity_fraction_per_trade × equity × leverage`.
It is bounded by `max_concurrent`,
`max_total_notional_pct`, and `max_trade_notional_usd`.

**Per-trade notional CLAMPS, not rejects** (`executor.py`): the computed
`trade_notional` is clamped down to `max_trade_notional_usd` so an oversized
candidate is sized down to the cap and can still be taken. The executor then
normalizes to the exact exchange-valid coin size before gates; tiny precision
dust around the cap is tolerated, while real overshoots are still blocked.

Free-margin floor: the executor refuses if `available / equity <
min_available_margin_pct` (config; currently **0.10**).
`available` is `accountValue - totalMarginUsed` (matches HL "Available to
Trade"). A defensive `equity_unavailable` reason fires when HL returns
`equity=0` (transient outage) instead of sending an unsized order.

For HIP-3 trades the executor runs a per-dex preflight (queries that
specific dex's clearinghouse) and refuses with `hip3_dex_underfunded` if
the target dex has < $1 — HIP-3 dexes are separate clearinghouses and
agent wallets cannot transfer between them.

## Exit Engine (DSL + server-side brackets)

Every executed position gets THREE layers of exit, all set at entry:

1. **DSL trailing stop** (`dsl_exit.py`, primary, re-evaluated each 60s tick):
   - **Phase 1 — loss cap:** `max_loss_pct` (current live 2.5% spot) AND
     `max_loss_roe_pct/lev` (current live 25% ROE, whichever is tighter),
     optionally widened to a volatility-scaled `atr_stop` (current live ON:
     1.5× ATR clamped 1.0–2.5%). The wider stop was validated to stop whipsawing
     volatile movers out of trend (the EIGEN/AERO leak).
   - **Phase 2 — profit lock:** engages at `protect_pct` (current live 1.25% for new entries).
     Floor =
     `entry ± peak_range × (1 − retrace_threshold)`, ratchets one-way.
   - **`retrace_threshold` + `phase2_tiers` ladder** — give-back control.
     Current live retrace is a tight **0.10** (banks give-backs early; validated
     live — JUP banked +16%/+12% ROE), loosening via tiers at +8% (0.35) and
     +15% (0.40) so proven runners breathe. `phase2_tiers` is wired from config
     at entry and on state synthesis.
   - **Breakeven ratchet:** once peak ≥ `breakeven_trigger_pct`, floor can't drop
     below `breakeven_lock_pct` — guarantees a winner can't round-trip to flat.
   - **Hard timeout:** `hard_timeout_minutes` (30h) — a scalp/swing horizon, NOT
     a multi-week hold.
2. **Backup stop-loss trigger** (server-side, `place_hl_trigger_order(...,"sl")`
   at `sl_atr_mult`=1.5 ATR): fires on the exchange instantly between 60s ticks,
   and is the ONLY protection while the host sleeps or the loop is restarting.
3. **Take-profit scale-out** (server-side, the "we had it all and gave it back"
   fix): a reduce-only TP trigger banks `tp_scale_fraction` (0.5) of the position
   at `TP_ATR_MULT`=1 ATR past entry — **half locks at target automatically**, the
   rest rides the DSL trail. Validated live (auto-banked ADA half; runner to +66%
   ROE). HL accepts a 100%-SL + 50%-TP reduce-only bracket (150% total) fine.

**Trigger hygiene:** `close_position_market` calls `cancel_open_orders_for_coin`
after a market close to clear the stranded SL/TP bracket — otherwise stale
reduce-only orders accumulate and reject a later reduce-only order ("reduce only
order would increase position"). See `references/exit-engine.md`.

## Unified Accounts

On a Hyperliquid unified account the agent wallet signs orders while the master
account holds funds; `resolve_user_address()` picks `HYPERLIQUID_MASTER_ADDRESS`
first, else `HYPERLIQUID_WALLET_ADDRESS`. Equity reads come from the master.
For agent-wallet setup and the `approveAgent` flow, see the
`hyperliquid-agent-wallets` skill.

## User Rules

- Real orders only — no simulation or dry-run; mode is `OFF` or `LIVE`.
- Full autonomy — do not ask permission for individual trade decisions.
- Token-cost aware — a flat market with 0 triggers = $0 AI-brain spend = correct behavior.

## Market Coverage & Scan Scope

Scanner uses a **bucketed budget** (default 45 core candle fetches per scan):
- `HERMES_MAX_MARKETS_HIP3` (18) HIP-3 markets by 24h volume
- `HERMES_MAX_MARKETS_MOVERS` (10) crypto markets by `|24h%|` above a
  `HERMES_MOVERS_VOL_FLOOR_USD` ($300k) floor
- Remainder (17) crypto markets by 24h volume

This catches three regimes: high-volume majors, tokenized equities, and
low-volume native-crypto big movers (the IO/SEI/DYDX/GRASS cohort). Without
the movers slot, BTC/ETH/SOL dominate the volume cut and every +10% midcap
rally goes unscanned.

To force coverage of a specific coin not in the buckets:
- Call `research` directly on the symbol via MCP (confirm it's in
  `get_perp_markets` first), or
- Bump `HERMES_MAX_MARKETS_MOVERS` if it's a momentum candidate.

## HIP-3 Tokenized Equity / Commodity Perps

HIP-3 markets are namespaced as `<dex>:<symbol>` and live on separate
clearinghouses (`xyz`, `km`, `vntl`, `flx`, `hyna`, `abcd`, `cash`, `para`).
`enable_hip3` requires a loop restart because the universe is loaded once at
startup. Dashboard/status reads use `fetch_account_state(..., include_hip3=True)`
for aggregated equity; the executor still checks the target dex before placing a
HIP-3 order and refuses underfunded dexes. See
`references/hip3-tokenized-equity-handoff.md` for the exact five threaded entry
points, `queried_dexes` DSL safety, and SDK gotchas.

**Pitfall:** assuming "we scanned everything" when the log says "50 markets".
Check MCP market-list tools when the user mentions an asset that was not reported.

## Funding-Regime Bias

`risk_gates.py::market_regime_gate` applies the funding-regime overlay
symmetrically: trades aligned with the current funding crowd use the normal bar,
while trades against it face the elevated `counter_regime_min_conf` / composite
bar. With current live settings, broad binary-trigger force paths stay blocked
for counter-regime trades. Do not solve regime bias by raising
`min_ai_confidence` globally.

When the user asks "regime?", "short or long?", or similar, answer from a fresh
`market_get_funding_regime` call. Do not rely on session memory. See
`references/short-regime-bias.md` for code locations, exact thresholds, prompt
template, and pitfalls.

### Testing the regime gate

`tests/test_cleanup.py` has the regime + funding-regime overlay test suite
(`test_market_regime_gate_*` and `test_funding_regime_*`). **Every test
that touches `market_regime_gate` MUST mock BOTH the trend regime and the
funding regime**, or the live API call from inside the gate will hit
production and randomize the result:

```python
from hermes_trader.agents import market_regime, hyperfeed
monkeypatch.setattr(market_regime, "detect_regime", lambda c: "up")
monkeypatch.setattr(hyperfeed, "market_get_funding_regime",
                    lambda: {"regime": "NEUTRAL", "assets": []})
```

For cache-behavior tests, also monkeypatch `_funding_regime_cache` to
`None` and patch `_compute_funding_regime` (not `market_get_funding_regime`
itself — that's the cache wrapper).

## Common Pitfalls

| Issue | Fix |
|-------|-----|
| Dashboard equity ≠ HL UI total | The dashboard reads aggregated (`fetch_account_state(include_hip3=True)`). If the loop is running old code that uses main-only, restart it. |
| Daily PnL inflated after a deposit/transfer | Contribution-aware tracking subtracts spot↔perp transfers + external deposits/withdrawals automatically. If still inflated, baseline may be stale — reset with the snippet in `references/restart-sequence.md`. |
| Executor blocks LONG with "insufficient_free_margin" while HL UI shows plenty | `available` is `accountValue - totalMarginUsed` (matches HL UI). If they differ, the loop is on stale code — restart. |
| Logs show overlapping scan cycles or doubled cadence | There is likely an orphan loop. Run `scripts/restart.sh status` and `ps ax \| rg "scripts/trading_loop.py"`; keep exactly one Python loop process. |
| Most blocked LONGs are "counter-regime" | Regime proxy is slow; raise `counter_regime_min_conf` floor or rely on the own-coin-momentum bypass (composite_score≥50 or momentumBurst). |
| MCP `config` tool dropping a key | FIXED 2026-06-05 and pruned later — the tool exposes the current risk-knob set in snake_case. Removed experiment knobs are intentionally absent. Older builds took a narrow camelCase schema, silently dropped keys, and wrote dup keys; if you see that, the MCP is on stale code → `pkill -f hermes-mcp-server.py`. |
| CLI brain returns PASS for every coin | Check whether the research event has `ai_down`/empty reasoning or logs show CLI timeout/non-zero exit. Provider failures intentionally return `""`; fix CLI auth/env or switch `AI_BRAIN_PROVIDER=openrouter`. Do not loosen TA sidestep to compensate. |
| `[watchdog] no progress for N s — HUNG` re-execs (N = minutes/hours) | NOT a code hang — the host (MacBook) idle/maintenance-slept and froze the process; the watchdog re-execs correctly on wake. Confirm with `pmset -g log \| grep -iE "Sleep\|Wake"`. Fix: `caffeinate` (now auto-launched by `restart.sh`); keep on AC for closed-lid. Positions are held by server-side brackets during sleep. |
| `reduce only order would increase position` reject | Stranded SL/TP trigger orders from a prior closed position. `close_position_market` now auto-cancels them (`cancel_open_orders_for_coin`); if on old code, cancel manually or restart. |
| Day PnL baseline looks stale/reset after a restart | A mid-day restart can re-baseline `startOfDayEquity` to current equity (loses the true SOD) if persisted memory loaded zeroed — would launder a pre-restart drawdown out of the kill-switch. Known issue; verify `dayStartTs` vs UTC midnight before trusting daily PnL. |
| HIP-3 position shows "no DSL" indefinitely | `get_all_hl_mids` must be called with `include_hip3=True` so trackers receive a mid. If a HIP-3 dex query times out, trackers are preserved via `queried_dexes` until the next successful query. |
| Coin lookup fails on HIP-3 (`XYZ:MU` → not found) | Use `_norm_coin()` in MCP handlers — only the symbol uppercases, the lowercase dex prefix stays. |
| `@` coins as noise in scan results | Spot pairs are filtered in `perception.py`; if they appear, the filter regressed. |
| "perception not found" on research | Send the full perception object inline, not just a `perceptionId`. |
| Order rejected on price/size | Hyperliquid `szDecimals` ≠ `pxDecimals` — see `references/hyperliquid-gotchas.md`. |
| MCP tool runs stale code after a fix | The server is a separate process — `pkill -f hermes-mcp-server.py` to respawn. |
| Scan returns 0 triggers | Often correct (quiet market). Lower minScore only to widen deliberately. |
| Scanner fires triggers but zero executes | See `references/signal-vs-action-gap.md`. First bucket the feed by `entry_preflight`, `ta_skip`, `research`, and `execute.detail`; do not lower thresholds or re-enable removed methods without fresh EV evidence. |

## Bundled Scripts

Runnable tooling shipped with this skill (`scripts/`) — all read-only, no orders
or writes. `audit_mcp_server.py` and `feed.py` are stdlib-only; `status.py` also
does a read-only Hyperliquid query for live equity:

- `scripts/audit_mcp_server.py` — validates MCP tool wiring (`TOOLS` /
  `tool_handlers` / `_STUB_RESPONSES` consistency). Run before and after editing
  the server; parses via `ast` (no import, no execution) and exits non-zero on
  drift.
- `scripts/status.py` — plain-text snapshot showing BOTH the cached state from
  `.agent-memory.json` and LIVE state pulled directly from Hyperliquid. The
  live read uses the repo's `hl_client` and `.env.local` for credentials, so
  drift between the two surfaces a broken loop heartbeat immediately. Falls
  back to cache-only when no wallet env var is set.
- `scripts/feed.py` — human-readable activity feed from the session log. The
  trading loop appends every scan / heartbeat / preflight / skip / research /
  execute / error event; `feed.py` renders them with timestamps and symbols.
  Examples:
  ```bash
  python3 scripts/feed.py                     # last 20 events
  python3 scripts/feed.py -n 50               # last 50
  python3 scripts/feed.py --since 30m         # last 30 minutes
  python3 scripts/feed.py --follow            # tail -f forever
  python3 scripts/feed.py --filter execute,error  # only those types
  ```
  Event types emitted by the loop: `loop_start`, `loop_stop`, `loop_heartbeat`
  (per-cycle equity/positions sync), `scan` (trigger count + coins),
  `entry_preflight` (deterministic live gate skipped paid AI), `ta_skip`
  (TA filter, held/cooldown/research throttle, or pre-research runner skip),
  `research` (verdict + confidence + `ai_brain_provider` when present),
  `execute` (order outcome), `error`. `status.py` also prints TA verdict counts
  (CONFIRMED / WEAK / REJECTED + avg composite score over the last 30) so you
  immediately see if the statistical gate is over-filtering early signals.

## Visibility / "What is the bot doing right now?"

MCP tools are request/response — there is no streaming progress mid-call.
The right way to watch the trading system is:

1. **Tail the feed:** `python3 scripts/feed.py --follow` in a terminal.
2. **One-off snapshot:** `python3 scripts/status.py` for cached + live state.
3. **Hourly auto-report:** Hermes cron job `8a82eaa567fe` (`hermes-trader-status.sh`)
   runs `status.py` + `feed.py --since 60m` every hour and delivers the
   combined report to the originating chat (no LLM cost — `no_agent=true`).
   - Pause:  `hermes cron pause 8a82eaa567fe`
   - Resume: `hermes cron resume 8a82eaa567fe`
   - Run now: `hermes cron run 8a82eaa567fe`

## Loop Heartbeat (live equity sync)

`trading_loop.py` calls `_sync_account_state()` at the top of every cycle:

1. Resolves the user address via `resolve_user_address()` (master else wallet).
2. Calls `fetch_account_state(user, include_hip3=True)` — aggregated equity
   across main + every HIP-3 dex; returns `queried_dexes` so DSL rehydrate
   can scope its stale check.
3. Fetches net USDC contributions since UTC midnight via
   `fetch_aggregate_contributions_since(user, sod_ts_ms)` — subtracts
   deposits / spot↔perp transfers from the equity diff so daily PnL only
   reflects trading gains.
4. Calls `memory.track_daily_pnl(equity, contributions)` +
   `memory.update_open_positions(...)`.
5. Calls `memory.flush()` so `.agent-memory.json` always reflects current state.
6. Appends a `loop_heartbeat` event with equity / available / daily_pnl /
   positions + the live config snapshot (mode, frac, lev, slots, cap,
   crypto:on/off, hip3:on/off).

If `status.py` shows `cached equity: $0` while LIVE is non-zero, the
heartbeat is broken or the loop hasn't completed one cycle yet.

## Scheduled Operation

An hourly Hermes cron job (`no_agent`, zero LLM cost) runs `status.py` and
delivers the snapshot. It ships paused — `hermes cron resume 8a82eaa567fe` to
start it. See `references/cron-jobs.md`.

## References

- `references/mcp-config.md` — MCP server config and tool list.
- `references/mcp-server.md` — server structure, adding tools, the audit invariant.
- `../../docs/AI_BRAIN_OPERATOR_WIRING.md` — Codex/Claude/Hermes/OpenClaw brain-provider and MCP-operator wiring.
- `references/hyperliquid-gotchas.md` — order-placement gotchas (decimals, tick size, $10 min, singletons).
- `references/cron-jobs.md` — Hermes cron wiring for the hourly status report.
- `references/signal-vs-action-gap.md` — current gate-first diagnostic flow for "scanner fires, trader stays silent".
- `references/restart-sequence.md` — `scripts/restart.sh` usage + baseline-reset snippet.
- `references/trading-mode.md` — execute-first reporting contract when the user is in active trading mode.
- `references/daemon-investigation.md` — historical note on the no-op `--daemon` flag; superseded by `restart.sh`.
- `references/hip3-tokenized-equity-handoff.md` — current HIP-3 production wiring (all 5 entry points, queried_dexes safety, sizing semantics).
- `references/exit-engine.md` — DSL trail + tighter retrace ladder + breakeven, server-side SL/TP brackets, take-profit scale-out, trigger hygiene (the 2026-06-04/05 round-trip-fix overhaul).
- `references/short-regime-bias.md` — regime-aware bias, counter-trend gating, and symmetric up/down trend surfacing.
