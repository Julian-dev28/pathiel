# Funding-Regime Bias (symmetric)

Use when the user wants the bot to follow the market-wide funding regime more
strictly. The pattern is **direction-agnostic**: when the regime is
SHORT_CROWDED, longs face the elevated bar; when it flips to LONG_CROWDED,
shorts face the same elevated bar. Same code path, same thresholds, just
keyed off `funding_regime` vs `trade_side`.

## Core principle

> Enforce regime discipline by default, but never *hinder* aligned trades and
> never hard-block on a clear individual setup.

- Trades **aligned** with the funding regime → normal bar (no friction added).
- Trades **against** the funding regime → elevated bar.
- Bypass triggers (momentumBurst / slow_burn / whale_signal) preserved on
  BOTH sides — those are explicit "regime proxy is stale" signals.

Separate the "counter-regime is hard" knob (`counter_regime_min_conf`) from
the overall activity knob (`min_ai_confidence`). **Never** solve regime bias
by globally raising `min_ai_confidence` — it just kills volume.

## Implementation location

The funding-regime overlay lives inside
`hermes_trader/agents/risk_gates.py::market_regime_gate`. It:

1. Calls `detect_regime(ctx.coin)` for the trend regime (BTC / SP500 / own).
2. Calls `market_get_funding_regime()` (cached 5 min in
   `hermes_trader/agents/hyperfeed.py`) for the funding regime.
3. Computes `against_funding` symmetrically:
   ```python
   against_funding = (
       (funding_regime == "SHORT_CROWDED" and trade_side == "long") or
       (funding_regime == "LONG_CROWDED"  and trade_side == "short")
   )
   ```
4. When `against_funding`, raises the effective bar:
   - `effective_min_conf = max(counter_regime_min_conf, 0.85)`
   - `effective_min_score = 60.0` (vs default 50)
5. Bypass triggers (momentumBurst / slow_burn / whale_signal) still pass —
   do not weaken them. The user explicitly called this out.

## Live config (`.agent-config.json`) recommended values

```json
{
  "min_ai_confidence": 0.5,
  "counter_regime_min_conf": 0.85,
  "leverage": 10,
  "equity_fraction_per_trade": 0.07
}
```

Apply by editing `.agent-config.json` directly — the MCP `config` tool does
NOT accept `counter_regime_min_conf` writes (it silently drops anything not
in its narrow schema). Always restart the trading loop after editing the
file.

## Funding-regime cache

`market_get_funding_regime()` in `hyperfeed.py` is cached for 5 minutes
(`_FUNDING_REGIME_TTL_S = 300`). Funding rates settle hourly, so the regime
can't flip faster than that. Without the cache, every risk-gate evaluation
would refetch the full universe.

If you need a fresh read (e.g. operator testing a flip): clear the module
global `_funding_regime_cache` or wait out the TTL.

## Prompt template for sub-agents (Claude, hip3, etc.)

```
Current market regime: <SHORT_CROWDED | LONG_CROWDED | NEUTRAL>.

The system must follow the funding regime with high priority and SYMMETRICALLY.
Apply the elevated bar to trades AGAINST the current crowd, regardless of
direction. When the regime flips, the same logic applies in reverse — no
direction-specific hardcoding.

Requirements:

1. In a crowded regime, trades against the crowd must face a higher bar
   (close to a hard block unless the signal is exceptionally strong).
2. Trades aligned with the regime use the normal bar — never raise friction
   for aligned trades.
3. Preserve momentumBurst / slow_burn / whale_signal bypasses on BOTH sides.
4. Trade frequency is secondary to regime alignment.

Constraints:
- Do NOT change max_daily_loss_usd.
- Do NOT weaken or remove the binary trigger bypasses.
- Do NOT create new bypasses just to increase trade count.
- Do NOT solve this by raising min_ai_confidence globally.

Suggested changes:
- Raise counter_regime_min_conf to 0.85 in .agent-config.json (not via MCP).
- Make market_regime_gate in risk_gates.py stricter for against-funding-regime
  trades (require either high confidence OR high composite score OR a binary
  bypass trigger).
- Cache market_get_funding_regime so the gate doesn't refetch per call.
- Restart the trading loop to pick up code + config changes.

Files to review:
- hermes_trader/agents/risk_gates.py (market_regime_gate)
- hermes_trader/agents/market_regime.py
- hermes_trader/agents/hyperfeed.py (market_get_funding_regime)
- .agent-config.json (live config)
```

## Pitfalls

- **Asymmetric implementations are wrong.** Earlier drafts treated
  "short-crowded → easy shorts, hard longs" as a special case. When the
  regime flips, that logic doesn't migrate. The symmetric `against_funding`
  check covers both states from one code path.
- **MCP `config` tool silently drops `counter_regime_min_conf`** — edit
  `.agent-config.json` directly and restart the loop. Trying to push
  through the MCP tool wastes a turn and looks like the bot ignored you.
- **Don't kill the binary-trigger bypasses.** The user has repeatedly
  refused to weaken `momentum_burst` / `slow_burn` / `whale_signal` bypasses
  — those exist because the regime proxy is slow and a strong individual
  signal should win.
- **Verify regime via `market_get_funding_regime`** before assuming. The
  user explicitly asks "regime?" / "short or long?" — answer with a fresh
  tool call, not from session memory. The regime can flip; don't cache it
  in your head across sessions.
- **The aligned + funding-neutral case must still pass cleanly.** The gate
  fall-through for `regime == "neutral" and not against_funding` is the
  default path for most trades; if you add new short-circuits, preserve it.
- **Cross-asset-class leak.** `market_get_funding_regime` only scans
  crypto perps. HIP-3 equity (`xyz:ARM`, `xyz:META`, `xyz:TSLA`) and
  commodity (`xyz:CL`, `xyz:SILVER`, `xyz:GOLD`) markets have their own
  funding markets that are NOT in the regime scan. Result: a `xyz:CL` long
  whose own commodity trend regime is "up" will pass the
  `aligned and not against_funding` branch because `against_funding`
  only reflects the crypto crowd. **When the user asks "how did this
  HIP-3 long sneak through during SHORT_CROWDED?" — this is the answer.**
  Don't redesign the symmetric overlay; the right fix (if the user wants
  one) is one of:
  1. Make the overlay crypto-only (cleanest; preserves intent).
  2. Block binary bypasses for cross-asset-class trades (requires explicit
     user approval — they have refused to weaken bypasses before).
  3. Scan HIP-3 namespaced assets too with their own per-class threshold.
  Confirm which fix the user wants before patching.
- **Test mocking gotcha.** Every test that exercises `market_regime_gate`
  must mock BOTH `market_regime.detect_regime` AND
  `hyperfeed.market_get_funding_regime`. The gate calls both, and an
  unmocked `market_get_funding_regime` will hit the live HL API during
  pytest runs — making tests order-dependent and randomly failing when
  the regime flips on the live market. Pattern:
  ```python
  from hermes_trader.agents import market_regime, hyperfeed
  monkeypatch.setattr(market_regime, "detect_regime", lambda c: "up")
  monkeypatch.setattr(hyperfeed, "market_get_funding_regime",
                      lambda: {"regime": "NEUTRAL", "assets": []})
  ```
  For cache-behavior tests, monkeypatch `_funding_regime_cache` to `None`
  and patch the internal `_compute_funding_regime` (not the public
  `market_get_funding_regime`, which is the cache wrapper).
