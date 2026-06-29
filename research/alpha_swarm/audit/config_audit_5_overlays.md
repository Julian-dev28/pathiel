# Config audit 5 — overlay / surface blocks

Read-only audit of seven overlay blocks in `.agent-config.json`. Evidence window:
`logs/trading_loop.log` spans 2026-06-26 00:31 → 2026-06-29 10:27 (~3.5 days, LIVE,
9 fresh entries executed). Firing counts are grep-counted from that log plus
`logs/server.log` and `.backups/`. Every block's keys are READ somewhere (no fully
dead block), so the live question is FIRES vs INERT.

## Per-block table

| Block | Read at (file:line) | What it does | Fires live? (3.5d evidence) | Verdict |
|---|---|---|---|---|
| `capital_rotation` | `executor.py:892`, `trading_loop.py:286/298/388/429` | When a strong fresh candidate is blocked PURELY by capital, close the weakest stale non-winner and retry once. Also: `enabled=true` SUPPRESSES the `max_positions_reached` preblock (`trading_loop.py:393 …and not rotation_live`) and lets margin-blocked candidates fall through the margin preflight (`:431`). | **0 firings.** No `rotation][LIVE`, `should_rotate`, or `evicted` line in trading_loop.log, server.log, OR any backup. Matches the memory finding (near-inert, 0 rotations in 11d sim). | **REMOVE (inert, net-negative)** |
| `gex_signal` | `executor.py:1429` (entry veto), `research.py:138` (warms cache + prompt) | HIP-3-only LONG veto: suppress a forced override when dealers are long-gamma and spot is pinned within `caution_near_wall_pct`% under the call wall. | **24 firings** (BB, HIMS, LLY pin-traps blocked), incl. a pre-research block. NOT the 0x of the earlier audit — the cache is now warmed by research so the veto bites. | **KEEP** |
| `runner_mover_surface` | `perception.py:158` | Surface large liquid 24h movers to the AI as a weight-0 `dailyMover` trigger so an orderly runner isn't dropped once the fresh-spike bar passes. | **Live** — `daily_mover` evaluated 605x; feeds `structured_daily_mover` + the daily-mover long bypass. | **KEEP** |
| `trend_filter_200ma` | `executor.py:185` (bypass), `executor.py:1308` (block) | PTJ 200d-MA regime filter: block entries that fight the daily trend; daily-mover long bypass exempts the 10-30% ext pocket. | **39 firings** ("long fights the daily 200d-MA downtrend"). | **KEEP** |
| `override_volume_confirm` | `executor.py:371` | On the AI PASS→LONG sidestep override path, require volume ≥ `min_ratio`× avg before upgrading — kills the documented no-volume-override leak. | **150 firings** (MANTA etc. blocked). Heaviest-firing overlay; +EV per ledger (no-vol −$24/n=28 vs vol-confirmed +$16/n=12). | **KEEP** |
| `late_chase_relax` | `executor.py:160` (def), `:1445` (call) | Admit a trend-aligned late-chase long ONLY in the liquid 20-30% daily-extension pocket (validated +EV, edge_extension.py). | **31 ADMITs** (JTO 15, S 7, AAVE 4, IP 5). Active, narrow, working as the validated pocket. | **KEEP** |
| `reentry_cap` | `executor.py:442`, `trading_loop.py:457` (+`risk_gates.py:176`) | Block the (cap+1)-th entry on one coin in a rolling window — anti-churn / fee-bleed guardrail. | **17 firings** (JTO 2-in-24h blocked). Live guardrail. | **KEEP** |

## Remove lists

### Remove (dead — key read nowhere)
None. Every block is read in live code.

### Remove (inert — enabled but no live effect, or net-negative)
- **`capital_rotation`** — 0 rotations in 3.5d LIVE + 0 across all backups + 0 in the prior 11d sim. It never displaces a position. Worse than harmless: `enabled=true` suppresses the cheap `max_positions_reached` preblock (`trading_loop.py:393`) and the margin-preflight short-circuit (`:431`), so margin-blocked candidates that rotation will never actually rescue still fall through to PAID AI research before dying at the notional cap anyway. So the block costs research spend and complexity for zero realized benefit. Why it can't fire: `min_candidate_composite=40` is above the runner-gate composite floors (crypto 20 / hip3 32), and eligible evictees must be non-winners past `min_hold_minutes` — the margin-blocked movers it targets die upstream at the preflight (memory: "architecturally can't reach the movers we miss"). **Impact if removed:** none to trade behavior; the `max_concurrent` preblock returns and a few capital-blocked candidates get cheaply rejected before research instead of after it (small cost saving). Disable via `enabled:false` (reversible) before deleting the block.

### Keep (live and firing, EV-justified)
- `gex_signal` (24), `runner_mover_surface` (605 evals), `trend_filter_200ma` (39), `override_volume_confirm` (150), `late_chase_relax` (31), `reentry_cap` (17).

## Notes / minor consolidation
- `reentry_cap` is checked twice — `trading_loop.py:457` (pre-research, saves AI spend) and `executor.py:442` (backstop at order time). Intentional defense-in-depth, both hot-read the same block. Not a consolidation target; leave as is.
- Default drift (cosmetic, not a bug): `gex_signal.caution_near_wall_pct` is `8.0` in `.agent-config.json` but `15.0` in `config_store.py:118`; the executor passes the config value (8.0), so the live 8% wall is what fires. `runner_mover_surface.min_volume_usd` is `5_000_000` in both config and store (the in-code fallback default of `3_000_000` at `perception.py:175` is never reached). No action needed.
- `capital_rotation` removal is the single highest-value cleanup here: it is the only overlay that adds cost (extra paid research on candidates it never rescues) while producing zero observed action.

## Verdict
1 inert block to remove (`capital_rotation`), 6 keepers all firing with EV support, 0 dead
blocks. Analysis only — no files edited.
