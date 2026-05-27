# Signal vs Action Gap

Date: 2026-05-19 session  
Related code: perception.py, ta_filter.py, research.py, trading_loop.py, .agent-config.json

## Pattern observed
The perception engine produces 1-2 triggers most cycles (examples: AVAX, AAVE, KAITO, BCH, HBAR).  
Triggers commonly fire on:
- volumeSpike (11 σ moves)
- rangeCompression (Bollinger squeeze)
- trendStrength (ADX > 40)
- pctMoveSpike (fast sigma returns)

Yet the trading loop records almost no `execute` events and no net position changes.

## Diagnosis
1. `minAiConfidence: 0.5` in `.agent-config.json` (also exposed via `mcp_hermes_trader_config`).  
   The research step must return `confidence >= 0.5` before `maybe_execute` is ever attempted.

2. TA filter (`ta_filter.py:analyze_perception`) requires score >= 45 for `CONFIRMED`.  
   Anything below is dropped before the LLM call (except momentumBurst bypass).  
   A 5 m trigger often fails 1 h / 4 h / 1 d trend alignment and receives `REJECTED` or `WEAK`.

3. The model (grok-4.3 during this run) under the current system prompt defaults to "PASS" + confidence 0.0-0.25 when the setup is not overwhelmingly clean.

## Evidence from the session
- `feed.py --filter scan,research` shows repeated `research ... PASS conf 0.0-0.25`
- `status.py` shows 3 open positions with small unrealised PnL but zero new executions after restart.
- No `execute` or `order_id` lines appear in the last 30-40 log entries.

## Recommended actions (in priority order)
1. Quick lowering of the execution threshold:  
   `mcp_hermes_trader_config minAiConfidence=0.30` for a few cycles while watching the feed.  
   Re-raise after data collection.

2. Add a separate key `minConfidenceForExecution` in config and in `maybe_execute` so research confidence logging remains independent of trade gating.

3. Consider a one-session "research-only" mode that still runs full pipeline but blocks order placement, useful for prompt A/B testing.

## Actions taken 2026-05-19 (live tuning session)
- Lowered TA `CONFIRMED` threshold from 45 → 35 and `WEAK` from 30 → 25 in `ta_filter.py`.
- Relaxed system-prompt rule #5 from “score ≥ 80 + two hard conditions” to “composite_score ≥ 60 **OR** 4h EMA trend + ATR ≥ 0.4%”.
- Added explicit INFO logging in `analyze_perception` for every REJECTED/WEAK perception (lists which of the 6 indicators failed).
- Restarted trading loop after changes.

Observed outcome after one full cycle: still seeing `PASS conf 0.0–0.2` on ETHFI / similar names. The TA gate is now the remaining limiter; next useful experiment is to examine the per-indicator scores for a few rejected triggers.

## File pointers for future debugging
- perception.py: composite_score + momentumBurst bypass logic  
- ta_filter.py: analyze_perception scoring and verdict mapping  
- research.py: parse_verdict and system_prompt construction  
- trading_loop.py: the TA gate check before calling research  
- .agent-config.json: the current minAiConfidence value

This reference file exists so future sessions immediately recognise the gap instead of re-diagnosing it from scratch.

## Update 2026-05-28 — direction-asymmetric gap

A second flavor of this gap surfaced: the AI was generating roughly balanced
LONG/SHORT verdicts (48 LONG / 43 SHORT over 24h) but the executor was only
firing **11 LONGs vs 29 SHORTs**. Diagnosis: the `market_regime` gate was
blocking 60 LONG attempts as "counter-regime" because the BTC proxy was
on a slow trailing trend while alts were rallying.

Fixes applied:
- Regime classifier: 4h × 5-bar (20h) → **1h × 8-bar (8h)** so intraday
  rotations register (`market_regime.py`).
- `counter_regime_min_conf`: 0.85 → **0.65** (`.agent-config.json`).
- Counter-regime gate: added bypass when `composite_score ≥ 50` OR
  `momentumBurst` fired — own-coin momentum overrides a stale macro
  proxy (`risk_gates.market_regime_gate`).
- News-blackout gate: skipped for tokenized equity (their headlines
  always include earnings/Fed/SEC by definition).
- DSL: `protect_pct` 1.5% → **0.5%** so phase-2 ratchets engage earlier;
  `hard_timeout_minutes` 90 → **180** so slow winners aren't force-closed.

The diagnostic flow for "we're missing the gainers":
1. Session log: count `execute` events where `executed=false` by side,
   bucket `blocked_by` reasons.
2. If counter-regime dominates LONG blocks, check `regime_snapshot()` —
   the BTC/SP500 proxy may be lagging the actual market.
3. Check the LONG verdicts that the AI generated; if conviction is
   reasonable (>0.6) but blocked by the gate, relax either the
   confidence floor or trust own-coin momentum.
