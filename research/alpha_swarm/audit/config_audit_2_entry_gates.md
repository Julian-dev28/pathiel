# Config Audit 2 — Entry Gates / Thresholds

Scope: top-level entry gate keys in `.agent-config.json` (hot-read via `config_store.read_agent_config`).
Read-only. No live edits. Audited against `hermes_trader/`, `scripts/`, git log, and `research/alpha_swarm/liquidity/`.

## Headline

- ZERO dead keys. All 18 keys in this section are read at a live decision site.
- Config merge is deep (`config_store.merge_agent_config`, line 271). Live scalar/list values OVERRIDE `DEFAULT_CONFIG`. So an empty list in the live file is not "absence" — it actively replaces the default.
- One real contradiction: live `coin_blocklist: []` silently UNBLOCKS TON and TRX, which `DEFAULT_CONFIG` (config_store.py:68) blocks. Verify intent.
- Several live values sit BELOW their `DEFAULT_CONFIG` value (min_ai_confidence 0.62 vs 0.7, volume floors 700k vs 5M). All are validated loosenings, not drift, but the divergence is worth knowing.

## Removable now

Nothing is dead-code removable. The closest candidates:

- `coin_allowlist: []` — live value equals the default; deleting the key changes nothing. Keep only as the operator/MCP control surface.
- `hip3_dex_blocklist: []` — same: equals default, deleting it is a no-op. Keep as control surface.

Do NOT delete `coin_blocklist` or `hip3_dex_allowlist` from the live file — their live values DIFFER from the defaults and deletion would change behavior (see table).

## Table

| key | read at file:line | gates | verdict | why / impact if removed |
|---|---|---|---|---|
| crowded_with_min_conf (0.8) | risk_gates.py:377,495; config_store.py:57 | With-the-crowd trade (short into SHORT_CROWDED / long into LONG_CROWDED) needs conf >= value; 0=off | KEEP | Squeeze guard. Matches default 0.8. Remove -> falls back to 0.8, no change. Value not independently backtested but conservative. |
| cooldown_min (30) | risk_gates.py:235,488; trading_loop.py:875,952; config_store.py:59 | Min minutes between trades on a coin (re-entry / re-research throttle) | KEEP | Live operational throttle against churn. Matches default. Memory "Re-entry churn fix" shows re-entry is fee-dominated -EV -> cooldown is load-bearing. |
| held_research_interval_min (10) | trading_loop.py:881,907; config_store.py:60 | Held position re-researched only every N min (LLM cost throttle) | KEEP | Pure cost/cadence knob. Matches default. Remove -> default 10, no change. |
| min_ai_confidence (0.62) | executor.py:384; trading_loop.py:268; risk_gates.py:455; config_store.py:61 | Floor on AI confidence to enter / upgrade to LONG | KEEP / RECONSIDER-VALUE | Live 0.62 is BELOW default 0.7. Consistent with the "entry latency is the real leak" finding (AI vetoes confirmed breakouts -> loosen). Load-bearing. Flag: the loosened value is not pinned to a specific commit/backtest in-tree; confirm 0.62 vs 0.70 was a deliberate set. |
| counter_regime_min_conf (0.8) | risk_gates.py:359,362,493; config_store.py:62 | Confidence bar for a trade AGAINST the funding regime (elevated to 0.85 when proxy-stale) | KEEP | Matches default 0.8. Counter-trend is the documented bleed (Edge profile memo) so a high bar here is correct. |
| max_crypto_long_correlated (3) | risk_gates.py:490; config_store.py:63 | Cap on simultaneous correlated crypto longs | KEEP | Correlated-cascade guard (see "Correlated-gross shadow" memo: ~9 simultaneous stops on a dip). Matches default. |
| min_market_volume_usd (700000) | risk_gates.py:478; perception.py:366; trading_loop.py:438; xs_momentum_live.py:137; config_store.py:64 | 24h $ volume floor for native-coin LONG entries + scan sweep | KEEP | VALIDATED. floor_long.py verdict = KEEP the $700k floor (edge sits ABOVE 700k, lower bands die to slippage). Live 700k is below default 5M but is the swarm-validated floor. Do NOT lower (Extension/latency memo). |
| min_hip3_volume_usd (700000) | risk_gates.py:479; perception.py:367; trading_loop.py:438; config_store.py:65 | 24h $ volume floor for HIP-3 entries + scan sweep | KEEP | VALIDATED same swarm (commit db066d1: long/hip3 KEEP). Separate semantic from crypto floor — not a duplicate. |
| min_short_volume_usd (20000000) | risk_gates.py:482; executor.py:873-877; config_store.py:66; per-book overrides in *_short_live.py | Extra 24h volume floor for SHORTS (squeeze risk) | KEEP | VALIDATED. Lowered 50M->20M, commit b8b854c, operator sign-off + swarm (db066d1: "short can go $20M"). Live 20M < default 50M is intentional. Strategy books override via executor_short_volume_floor_usd (also 20M). |
| min_history_bars (60) | risk_gates.py:162,449; config_store.py (default 24); trading_loop.py:449 | Preflight: block coins younger than N completed DAILY bars | KEEP | VALIDATED. Added commit cd6eaee; memory "Liquidity floors swarm" identified the real HIP-3 gap = new listings trading on ~6 bars. Live 60 > default 24 is the deliberate fix. Disabled if <=0. Note hail_mary_short carries its own min_history_bars:24 (separate book gate). |
| coin_allowlist ([]) | risk_gates.py:483-485; trading_loop.py:356; config_store.py:67 | If non-empty, restrict trading to listed coins only | KEEP (control surface) | Empty = allow all. Equals default. Removable with zero impact, but it is the operator/MCP whitelist surface — keep it explicit. |
| coin_blocklist ([]) | risk_gates.py:486; trading_loop.py:355; config_store.py:68 (default ["TON","TRX"]) | Coins to never trade | RECONSIDER-VALUE | LOAD-BEARING and CONTRADICTS default. Live [] OVERRIDES default ["TON","TRX"] -> TON/TRX are currently tradeable. If TON/TRX were blocked for a reason, the live empty list quietly re-enabled them. Confirm intent; if blocking is still wanted, set the list. Do not delete the key thinking it is a no-op. |
| hip3_dex_allowlist (["xyz"]) | perception.py:344; config_store.py:69 | Scan ONLY these HIP-3 dexes | KEEP | Load-bearing — restricts HIP-3 scan to xyz. Equals default. Note: hail_mary_short.dex_allowlist is ["xyz","vntl"] (book-local), so that one book reaches vntl while the main scan does not — intentional asymmetry, flagged for awareness. |
| hip3_dex_blocklist ([]) | perception.py:345; config_store.py:70 | Scan all HIP-3 dexes EXCEPT these | KEEP (control surface) | Empty = no exclusions. Equals default. Removable no-op; keep as operator surface. Paired with allowlist (allowlist takes precedence when set). |
| ta_sidestep_force_execute (true) | executor.py:328,1234; config_store.py:85 | Let a TA-confirmed sidestep candidate force-execute past the AI veto | KEEP | Core of the entry-latency fix (AI vetoes confirmed breakouts). Matches default true. Turning off re-introduces the documented latency leak. |
| override_max_daily_extension_pct (30.0) | executor.py:146; config_store.py:86 | Block override entries on coins already extended > N% on the day | KEEP | VALIDATED ceiling. Extension/latency memo: momentum +EV only in 20-30% band, -EV above 30% (cap validated; chasing TNSR=loss). 30.0 = the validated edge. Do not raise. |
| block_counter_trend_bypass (true) | risk_gates.py:407,494; config_store.py:87 | Force counter-regime trades to clear conf/score instead of a binary trigger bypass (momentumBurst/slow_burn) | KEEP | VALIDATED 2026-06-01 (memory: stops the long-into-down ~-7% drawdown bleed). Matches default true. Watch via "blocked_bypass" in logs. |
| trend_surface_enabled (true) | perception.py:286,221,499; config_store.py:88 | Surface trend-only candidates below composite threshold (unblocks shorting downtrends) | KEEP | Matches default true. Feeds the short books / downtrend entries. Read path is live. |

## Cross-cutting notes

1. coin_blocklist divergence (most important): live `[]` vs default `["TON","TRX"]`. Because the merge replaces lists wholesale, the live file is the source of truth and TON/TRX are NOT blocked. Either the unblock is intended (then fine) or a prior config write wiped the list. One-line operator confirm.

2. Live-vs-default loosenings that ARE validated (no action, documented for the record): min_short_volume_usd 20M (was 50M), min_market_volume_usd / min_hip3_volume_usd 700k (default 5M), min_history_bars 60 (default 24, a tightening), min_ai_confidence 0.62 (default 0.7).

3. No duplicates within this key set. min_market_volume_usd vs min_hip3_volume_usd look identical (both 700k) but gate different asset classes through different code paths — keep separate.

4. Volume floors also appear inside each strategy book (min_volume_usd, executor_short_volume_floor_usd at 20M). Those are book-scoped and out of this section's scope, but they track the top-level short floor (20M) consistently.

5. Every key in this section is also exposed through the MCP tool schema (hermes-mcp-server.py ~lines 232-257, 984-999), so they are part of the operator control contract — that is a second reason not to delete the redundant-but-harmless empty lists.

## Verdict summary

- KEEP: 16 keys (all validated or load-bearing operational/control-surface).
- RECONSIDER-VALUE: 2 keys — `coin_blocklist` (confirm TON/TRX unblock is intended) and `min_ai_confidence` (confirm 0.62 was a deliberate loosening vs 0.70 default).
- REMOVE(dead): 0 keys.
