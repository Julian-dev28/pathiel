# Config audit — strategy books part 2 + misc (section B)

Read-only audit of `.agent-config.json` blocks: `crash_continue_div_short`,
`premium_fade_short`, `engulf_short`, `vol_breakout_long`, `hail_mary_short`,
`data_logger`, `ai_brain`. Date 2026-06-29. No files edited except this report.

## Wiring / fire status (ground truth)

All 7 blocks are imported and dispatched every loop cycle from
`scripts/trading_loop.py:58-65` (imports) and `:766-856` (dispatch). Every book's
`.<book>_ts` state file has a fresh mtime (today), so every book is actually
running. The executor reads every override key these books emit:
`leverage_override` (executor.py:630), `dsl_exit_override` (:639),
`strategy_book_notional` / `strategy_book_notional_usd` (:644-672),
`min_short_volume_usd_override` (:873-877), `backup_sl_pct_override` (:1100-1116),
`tp_scale_fraction_override` (:1152), `strategy_book` exemption (:1311,:1347).

Fire counts from `~/.hermes-trader-session-log.jsonl` (the live session log):

| Book | cycles logged | total signals | total opened | live? |
|---|---|---|---|---|
| hail_mary_short | 10 | 0 | 0 | live (shadow_only=false) |
| crash_continue_div_short | 8 | 0 | 0 | live |
| premium_fade_short | 7 | 26 | 0 | shadow_only=true |
| engulf_short | 7 | 2 | 2 (HYPE, SUI) | live |
| vol_breakout_long | 99 | 0 | 0 | live, $8 forward test |

`shadow_ledger/` on disk holds only `premium_fade_short.jsonl` (6.5KB, 26 signals).
`engulf_short` has actually opened 2 real shorts (`.engulf_short_live_seen.json` =
`{"HYPE":..., "SUI":...}`, both 2026-06-27). The other three live books have never
produced a single signal in the logged window.

---

## Per-block tables

### `crash_continue_div_short` (LIVE short)

| key | read at | controls |
|---|---|---|
| enabled | crash_continue_div_short_live.py:282 | master on/off |
| shadow_only | :292 | log-only vs execute (now false = live) |
| scan_interval_hours | :285 | throttle (6h) |
| entry_window_hours | :242 | freshness window after daily close (8h) |
| lookback_days | :225 | divergence return window (2d) |
| threshold_pct | :227 | min drop magnitude (8%) |
| btc_window | :229 | BTC-up regime lookback (20) |
| min_volume_usd | :228 | candidate dvol floor (20M) |
| executor_short_volume_floor_usd | :202 | short liquidity floor passed to executor |
| volume_window | :230 | trailing-dvol window (30) |
| hold_days | :179,:305 | hard-timeout hold (10d) |
| stop_pct | :177,:306 | backup SL + max_loss (20%) |
| notional_usd | :198 | position size ($20) |
| leverage | :178 | leverage_override (1x) |
| tp_scale_fraction | :201 | TP scale-out fraction (0 = none) |
| max_new_per_cycle | :333 | per-cycle open cap (1) |
| history_bars | :231 | candle fetch depth (40) |

Fires? 8 cycles, 0 signals. Gate is BTC-up tape AND a coin down >=8% over 2d AND
>=$20M dvol — a rare divergent cell. Backtested +7.0% excess / 76% win (survivor
universe). Live but dormant because the regime/divergence conjunction has not
occurred. **KEEP** — validated edge, all keys live, correctly inert by design
(BTC-up + sharp single-name divergence is uncommon). No cost while it waits.

### `premium_fade_short` (SHADOW)

| key | read at | controls |
|---|---|---|
| enabled | premium_fade_short_live.py:311 | master on/off |
| shadow_only | :321 | log-only (true = shadow) |
| scan_interval_hours | :314 | throttle (6h) |
| z_threshold | :252 | premium z entry threshold (2.0) |
| premium_lookback_days | :253 | premium z baseline window (30d) |
| btc_window | :254 | regime TAG only, not a gate (20) |
| min_volume_usd | :250 | candidate dvol floor (20M) |
| executor_short_volume_floor_usd | :229 | short liquidity floor to executor |
| volume_window | :251 | trailing-dvol window (30) |
| max_eval_coins | :256 | bounds funding-history fetches (60) |
| hold_days | :206,:332 | hold (5d) |
| stop_pct | :204,:333 | SL + max_loss (20%) |
| notional_usd | :225 | size ($20) |
| leverage | :205 | leverage_override (1x) |
| tp_scale_fraction | :228 | TP scale-out (0) |
| max_new_per_cycle | :353 | per-cycle cap (1) |
| history_bars | :255 | candle depth (40) |

Note: `max_bar_age_hours` is read at :261 (default 48h staleness cap) but is NOT
present in the config block — runs on the code default. Harmless, but the knob
exists un-surfaced.

Fires? 7 shadow cycles, 26 signals, 0 opened (correct — shadow). This is the one
book actively collecting forward-validation data (the only file in
`shadow_ledger/`). p=0.0002 vs beta-matched null, 82% orthogonal to the other
shorts. **KEEP in shadow** — it is doing exactly its job: gathering the up-regime
forward sample before any live flip. Do not flip without `scripts/shadow_status.py`
VALIDATED + operator sign-off (per the module docstring).

### `engulf_short` (LIVE short — actually trading)

| key | read at | controls |
|---|---|---|
| enabled | engulf_short_live.py:281 | master on/off |
| shadow_only | :291 | live (false) |
| scan_interval_hours | :284 | throttle (6h) |
| entry_window_hours | :240 | freshness window (8h) |
| min_body_ratio | :229 | engulf body-size filter (1.0) |
| btc_window | :230 | regime TAG only (20) |
| min_volume_usd | :227 | dvol floor (20M) |
| executor_short_volume_floor_usd | :206 | short liquidity floor |
| volume_window | :228 | trailing-dvol window (30) |
| hold_days | :183,:302 | hold (1d) |
| stop_pct | :181,:303 | SL + max_loss (20%) |
| notional_usd | :202 | size ($20) |
| leverage | :182 | leverage_override (1x) |
| tp_scale_fraction | :205 | TP scale-out (0) |
| max_new_per_cycle | :323 | per-cycle cap (2) |
| history_bars | :231 | candle depth (40) |

Fires? 2 signals -> 2 opened (HYPE, SUI, 2026-06-27). The only live short here that
has actually put on risk. 1-day hold so both have closed; no per-book PnL was
computed in this audit. **KEEP, watch** — it is the live keeper of this group and
the only one generating forward live evidence. Pull PnL via
`scripts/pnl_by_book.py` before any size change. Risk: it shorts in a down-tape and
the up-regime generalization is still unconfirmed (per docstring).

### `vol_breakout_long` (LIVE $8 forward test)

| key | read at | controls |
|---|---|---|
| enabled | vol_breakout_long_live.py:295 | master on/off |
| shadow_only | :305 | live (false) |
| scan_interval_minutes | :298 | 5m throttle |
| entry_window_minutes | :269 | freshness window (7m) |
| vol_window | :263 | trailing-mean vol window (6) |
| breakout_vol_x | :264 | breakout candle vol multiple (1.5) |
| confirm_vol_x | :265 | follow-through candle vol multiple (1.0) |
| confirm_require_green | :267 | require 2nd candle green (true) |
| require_new_high | :266 | gate on new W-bar high (false = pure influx) |
| min_mover_pct | :196 | 24h mover pre-filter (0.0 = off) |
| min_volume_usd | :197 | dvol floor (5M) |
| max_scan_coins | :198 | bounds 5m fetches (40) |
| history_bars | :268 | candle depth (25) |
| hold_hours | :219,:316 | hold (8h) |
| retrace_threshold | :220 | TIGHT profit-floor (0.10) |
| protect_pct | :221 | profit-floor protect (1.0) |
| stop_pct | :217 | SL + max_loss (15%) |
| notional_usd | :240 | size ($8) |
| leverage | :218 | leverage_override (1x) |
| tp_scale_fraction | :243 | TP scale-out (0) |
| max_new_per_cycle | :336 | per-cycle cap (1) |
| max_book_positions | :341 | per-book concurrency cap (3) |

All keys read. Fires? 99 cycles (every 5m), 0 signals, 0 opened. Just retuned
(`require_new_high=false`, `min_mover_pct=0.0`) to loosen toward the pure
volume-influx variant. The module is HONEST that even the best config nets ~-0.10%
in backtest — this is a deliberate small live probe to gather real fills, not a
proven edge. **DISABLE candidate / RECONSIDER.** It is the weakest economic case in
this group: a self-described -EV trigger spending real money ($8/1x, capped 3
positions). It is bounded and reversible (`shadow_only=true`, hot-read), so KEEP is
defensible AS a time-boxed forward test, but set an explicit stop date / fill count
— an indefinitely-running -EV probe is the kind of slow leak the memory log warns
about. Verdict: **RECONSIDER (time-box it or revert to shadow).**

### `hail_mary_short` (LIVE equity-basket short — largest param surface)

Every one of the 28 keys is read — nothing dead:

| key | read at | controls |
|---|---|---|
| enabled | hail_mary_short_live.py:445 | master on/off |
| shadow_only | :455 | live (false) |
| names[] (34) | :82-89 via `_names` | equity-ticker watchlist |
| dex_allowlist | :107 | which HIP-3 dexes are tradeable (xyz, vntl) |
| proxy_coins | :129 | risk-off proxies (xyz:SMH/SP500/XYZ100) |
| require_proxy_down | :301 | gate entries on proxy bearish |
| scan_interval_hours | :448 | throttle (6h) |
| entry_window_hours | :328 | freshness window (10h) |
| min_volume_usd | :275,:373 | liquid-watchlist floor (20M) |
| executor_short_volume_floor_usd | :414 | short liquidity floor to executor |
| min_breadth_bearish_pct | :300 | basket breadth gate (0.55) |
| breakdown_lookback_days | :238 | prior-low window (20) |
| breakdown_buffer_pct | :240 | breakdown tolerance (0.0) |
| ema_fast | :220 | fast EMA (8) |
| ema_slow | :221 | slow EMA (21) |
| ema_trend | :222 | trend EMA (50) |
| min_history_bars | :212 | min bars for trend stats (24) |
| history_bars | :260 | candle depth (90) |
| drawdown_lookback_days | :231 | drawdown high-ref window (20) |
| min_basket_drawdown_pct | :243 | bearish drawdown threshold (6%) |
| recent_drop_days | :235 | recent-return window (5) |
| min_recent_drop_pct | :332 | downside-continuation threshold (6%) |
| hold_days | :388 | hold (10d) |
| stop_pct | :386 | SL + max_loss (12%) |
| notional_usd | :410 | size ($20) |
| leverage | :387 | leverage_override (1x) |
| tp_scale_fraction | :413 | TP scale-out (0) |
| max_new_per_cycle | :496 | per-cycle open cap (1) |
| max_attempts_per_cycle | :497 | per-cycle attempt cap (1) |

Fires? 10 cycles, 0 signals, 0 opened. Root cause of the inertness is structural,
not a bug: the watchlist is equity tickers (NVDA, SMCI, ...) that only exist on the
xyz/vntl HIP-3 equity dexes, and the `min_volume_usd=20M` liquid filter (:276) plus
the `>=55%` breadth gate plus `require_proxy_down` almost never all clear on thin
HIP-3 equity perps. `liquid_count` collapses to near zero -> `breadth_pct=0` ->
`risk_off=false` -> zero signals. This is the largest, most complex block in the
whole config and it has produced nothing. **DISABLE (inert) / RECONSIDER.** It is
not dead at the key level (every knob is wired and tested) but it is dead weight in
practice: the biggest maintenance surface for zero output. Two honest options:
(a) DISABLE (`enabled=false`) until the HIP-3 equity venue has the volume to clear
the gates, or (b) if kept, lower `min_volume_usd` for the HIP-3 equity context so
the breadth computation has a non-empty liquid set — but that is a research task,
not a config nudge, and shorting equity baskets at 1x/$20 is a thin-edge bet to
begin with. Recommend **DISABLE** pending a dedicated HIP-3-equity volume study.

### `data_logger` (read-only collector)

| key | read at | controls |
|---|---|---|
| enabled | data_logger.py:46 | master on/off |
| interval_hours | data_logger.py:48 | snapshot throttle (1h) |

Both keys read; dispatched at trading_loop.py:856; `.data_logger_ts` fresh (10:18).
Appends funding/OI snapshots for the still-unvalidated OI strategy (per MEMORY: "OI
strategy still unvalidated, logger collecting ~1-2wk"). **KEEP** — cheap, read-only,
and it is the data-frontier collector the alpha plan depends on.

### `ai_brain`

| key | read at | controls |
|---|---|---|
| provider | ai_brain.py:77 (`selected_ai_brain_provider`), research.py:515 | which completion backend |
| timeout_s | ai_brain.py:261 (`_cli_timeout_s`) | CLI subprocess timeout (clamped <=120s) |
| codex_cli.command | ai_brain.py:229 | path to codex binary |
| claude_cli.command | ai_brain.py:193 | path to claude binary |
| claude_cli.max_turns | ai_brain.py:187 | claude `-p` max-turns (clamped 1-20) |

All keys read. `timeout_s=120` equals the clamp ceiling `MAX_CLI_TIMEOUT_S`.
`codex_cli`/`claude_cli` blocks are only consulted when `provider` selects them, but
they are live wiring (CodexCliBrain / ClaudeCliBrain are real backends). **KEEP** —
but see the provider contradiction below.

---

## ai_brain provider contradiction (flagged)

`ai_brain.provider = "openrouter"`. CLAUDE.md ("LLM access — local Claude Code, not
the API"): *"do NOT use an LLM API ... unless Julien explicitly instructs it. Route
the call through the local Claude Code."*

What the code actually does:
- `get_brain()` with provider=openrouter returns `OpenRouterBrain`, which POSTs to
  `https://openrouter.ai/api/v1/chat/completions` (ai_brain.py:90-176) using
  `OPENROUTER_API_KEY` and model `x-ai/grok-4.3` — a hosted third-party API.
- The repo already ships the compliant backends: `ClaudeCliBrain` (shells the local
  `claude` binary, ai_brain.py:179-218) and `CodexCliBrain` (:221-240). Setting
  `provider="claude_cli"` would satisfy CLAUDE.md directly.
- Mitigating context: commit e615706 ("research via host-harness sampling — route
  through the caller, not OpenRouter") added an INJECTED-brain path in research.py
  (the `brain` arg, :305-308). When the loop runs under the MCP host harness, an
  injected brain is preferred and `provider` is only the FALLBACK. But when the
  standalone `scripts/trading_loop.py` runs with no injected brain,
  `selected_ai_brain_provider()` resolves to `openrouter` and the bot calls the
  hosted OpenRouter API for every research verdict.

Net: the configured fallback violates the letter of CLAUDE.md. This is a config
choice, not a code defect (the compliant path exists and is one string away). The
fix is `provider: "claude_cli"` (or `codex_cli`) unless Julien has explicitly
green-lit OpenRouter. Flag for operator decision — do not change it in this
read-only pass.

---

## Summary lists

DEAD keys (read nowhere): **none.** Every key in all 7 blocks is read.

INERT / DISABLE candidates (running, costing maintenance, ~zero output):
- `hail_mary_short` — 0 signals in 10 cycles; structurally gated out on thin HIP-3
  equity volume; largest param surface in the config for zero output. **DISABLE**
  pending a HIP-3-equity volume study.
- `vol_breakout_long` — self-described -EV probe, 0 fills in 99 cycles. **RECONSIDER:
  time-box the forward test or revert to `shadow_only=true`.**
- `crash_continue_div_short` — 0 signals but validated edge correctly waiting on a
  rare regime; KEEP (not a leak — no cost while dormant).

KEEP:
- `premium_fade_short` (shadow, actively collecting the forward sample — the one
  doing real validation work)
- `engulf_short` (live, the only short that has actually traded; watch its PnL)
- `data_logger` (cheap read-only collector feeding the OI frontier)
- `ai_brain` (all keys live) — subject to the provider note below.

CONSOLIDATE (code-level, not config):
- `crash_continue_div_short_live.py`, `engulf_short_live.py`,
  `premium_fade_short_live.py`, `rally_exhaustion_live.py` (section A) and
  `hail_mary_short_live.py` share ~90% identical boilerplate (`_bar_t`, `_val`,
  `_completed_bars`, `_held_coins`, `_execute_opened`, `_execute_block_detail`,
  ts/seen IO, the shadow/live `maybe_run` skeleton, the `_analysis` override dict).
  The config blocks should stay distinct (different triggers, different EV), but the
  modules are begging for a shared `short_book_base`. Not a config-audit action;
  logged here as the obvious refactor.

PROVIDER CONTRADICTION:
- `ai_brain.provider="openrouter"` calls a hosted API on the fallback path, against
  CLAUDE.md's "route through local Claude Code." Compliant backends
  (`claude_cli`/`codex_cli`) already exist. Operator decision: flip to `claude_cli`
  unless OpenRouter is explicitly sanctioned.
