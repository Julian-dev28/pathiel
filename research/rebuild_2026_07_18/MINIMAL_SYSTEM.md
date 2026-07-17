# MINIMAL_SYSTEM — the smallest defensible v2

**Date:** 2026-07-18. **Status:** DESIGN ONLY — nothing in this doc modifies live code or config.
**Context:** account $260 → $19. 72+ graded hypotheses in `research/alpha_swarm/findings/` refuted
nearly everything. v2 keeps only what measurably earns or measurably protects, and deletes the rest
with no successor.

---

## 1. The ground layer: `hermes_trader/client/` stays (1,651 lines, already rebuilt)

The data layer is NOT the problem. It was audited 2026-07-10 and hardened; v2 builds on it as-is.

| file | lines | what it gives v2 |
|---|---|---|
| `client/hl_client.py` | 495 | `_http_post` (all /info traffic through one token-bucket-metered, keep-alive `requests.Session`), `fetch_hl_candles` (90s TTL cache, `HERMES_CANDLE_CACHE_TTL_S`; retries transient non-list responses up to `HERMES_CANDLE_RETRIES=6` so a 429 never silently reads as "no signal"), `fetch_account_state` (per-dex equity + `queried_dexes`), `missing_material_dexes` (partial-dex degraded-read guard), `fetch_aggregate_contributions_since` (transfers don't fake PnL), `fetch_all_mids`, `fetch_funding_history` |
| `client/rate_limit.py` | 92 | `HL_LIMITER` token bucket: 300 burst + 15/s refill = 900 weight/min sustained for the loop, sized so loop + dashboard fit HL's 1,200/min per-IP budget. Per-endpoint weights (`candleSnapshot`=20, `allMids`=2, `clearinghouseState`=2, `fundingHistory` unlisted → default 20) |
| `client/universe.py` | 288 | `get_universe` (volume-ranked perp+spot+HIP-3, 24h disk cache in `~/.hermes/universe_cache/`), `list_hip3_dexes` (stale-serving on empty response — the 07-17 phantom-empty fix) |
| `client/exchange.py` | 748 | SDK signing, `place_hl_order` (IOC, L2-anchored cross price, `reduce_only` close semantics, `MIN_ORDER_USD=10.5` floor), `place_hl_trigger_order` (on-exchange SL/TP), `cancel_open_orders_for_coin`, `_cached_universe` meta cache + `prewarm_meta_cache`, `_set_session_timeout` (kills the SDK's `timeout=None` 15-min hangs), `get_all_hl_mids` with feed-freshness warnings |

Two client-audit truths that become v2 LAW, not code:

1. **Rate budget is per-IP, per-process buckets don't see each other.** v2 runs ONE loop process.
   No SDK call path that bypasses `HL_LIMITER` gets added (the SDK's own `Info.meta()` calls are
   the known bypass — v2 keeps them behind the 1h `_META_CACHE` only).
2. **Live must read COMPLETED bars.** The audit found live triggers reading the forming bar while
   every backtest graded completed bars. Every v2 book evaluates `candles[:-1]` on its signal
   timeframe. This is a contract test, not a convention.

---

## 2. What exists today (the delta v2 is measured against)

`hermes_trader/agents/`: **35 modules, 12,451 lines**, plus `scripts/trading_loop.py` (1,211 lines)
importing 20+ of them. Zero agents import `scripts/trading_loop` (grep confirms — only two comment
mentions), so the strategy layer is already severable from the loop.

Breakdown by fate:

| category | modules (lines) |
|---|---|
| **Survive as v2 books** | `funding_spike_short_live` (321), `extreme_fade_live` (358) + `extreme_fade` (95), `xs_momentum_live` (412) + `xs_momentum` (150) |
| **Survive as safety rails** | `dsl_exit` (739), `shadow_ledger` (351), `rebalancer_owned` claims registry (405), `risk_gates` kill switch (521, shrinks hard), `executor` backup-SL path (subset of 1,647) |
| **Die, no successor** | `ai_brain` (549), `research` (734), `system_prompt` (129), `perception` (535), `ta_filter` (197), `market_regime` (230), `news_catalyst` (469) + `news_catalyst_live` (257), `crypto_whale` (193) + `whale_flow_live` (103), `hyperfeed` (475), `rally_exhaustion_live` (350), `engulf_short_live` (381), `crash_continue_div_short_live` (401), `majors_swing_live` (386), `unlock_short_live` (220) + `unlock_recorder` (189), `young_listings_live` (340), `mover_recorders` (338), `memory` (421 — the AI's memory, not state files) |
| **Fold into one recorder** | `oi_logger` (51), `data_logger` (87) — funding/OI accrual IS the named frontier; keeps accruing |
| **Config plumbing, shrinks** | `config` (65), `config_store` (235), `sizing` (117) |

Net: ~12.5k lines of agents → **~3.5k lines in 7 modules**.

---

## 3. v2 spec — 7 modules on top of `client/`

```
hermes_trader/
  client/            # unchanged (4 files, above)
  v2/
    loop.py          # ONE process, ONE cadence
    books.py         # the 3 surviving signal generators
    executor.py      # entry + backup on-exchange SL + claims
    dsl_exit.py      # exit engine — verbatim survivor
    risk.py          # kill switch, caps, preflight, degraded-read guard
    ledger.py        # shadow ledger + grading + pnl-by-book — verbatim survivor
    recorder.py      # funding/OI accrual (the frontier data)
```

### One trading cadence

`loop.py` runs a **30-minute signal cycle** and a **60-second exit sub-cycle**:

- **60s exit pass:** `get_all_hl_mids` (weight 2) → `dsl_exit.advance()` on every open position →
  close via `place_hl_order(reduce_only=True)` when a floor breaches. This is protection, not
  trading; it is the only sub-30-min activity in the system.
- **30-min signal pass:** refresh account state (weight ~4), universe from cache, then ask each
  book for intents. Books that are internally slower (funding z on daily funding history, xs
  5-day rebalance, extreme_fade on completed daily bars) simply return nothing most cycles —
  the cadence is one; the books gate themselves on bar completion, not on private timers.
  This deletes today's zoo of `scan_interval_{min,hours,minutes}` timers (30min/1h/5min/6h all
  coexisting) and the 6h full-universe candle bursts the client audit flagged as saturating the
  IP for 4-5 minutes.

Rate math at steady state: exit pass ~2 weight/min; signal pass worst case (new daily bar:
extreme_fade re-reads top-40 daily candles = 40×20 = 800 weight, smoothed by the bucket over
~1 min) amortizes to <30 weight/min. Total <5% of budget on a normal day, one bounded burst per
UTC daily close. Everything else that used to burn budget — held-coin AI research refetches, news
polling, whale prints, hyperfeed — is gone.

### The 3 books (`books.py`) — and only these

Each book emits the same intent shape (coin, side, notional, stop_pct, hold_days, book name) and
records EVERY signal to the shadow ledger whether or not capital deploys (grading never stops when
capital goes on — the current `funding_spike_short_live.py` pattern).

1. **`extreme_fade`** — long a coin that closed ≤ −12% on the completed daily bar; 20% stop, 3d
   hold, deep tier at ≤ −20% gets 1.5× size. Evidence: `research/alpha_swarm/findings/extreme_surface.md`
   live cell +4.2%/ep @12bps, n=193, both OOS halves +5.0/+3.4, excess +5.0% over a matched
   negative-drift baseline on a −44% tape; long-only confirmed (up-regime cell NOT robust, so no
   regime gate needed — all-regime is the validated cell).
2. **`funding_spike_short`** — short when trailing-24h funding z ≥ 2 vs own 30d distribution;
   5d hold, 15% stop, episode-dedup until z < 1. Evidence: `findings/W-F2.md` +6.0%/ep net@25bps,
   n=25 deduped episodes, p=0.0027, both OOS halves +2.44/+8.98, and the short COLLECTS +0.19%/ep
   funding. Pre-committed kill: forward EV25 < 0 over 15 episodes.
3. **`xs_momentum`** — LB7 residual rank, long top-4 / short bottom-4, 5d rebalance,
   market-neutral. Evidence: `project_cross_sectional_momentum_edge` scrub, ~+1.41%/rebalance
   (modest, the long-SHORT spread — long-only variant is fragile and stays dead).
   **Equity-gated: OFF below $84 of headroom** — 8 legs × $10.50 HL minimum order = $84 gross,
   which is 440% of a $19 account. It arms automatically when `0.10 × equity ≥ $10.50` per leg
   fits inside the gross cap, i.e. around the $100 mark.

Sizing: `notional = clamp(frac × equity, MIN_ORDER_USD, book_cap)` with frac = 0.40 extreme_fade
(0.60 deep tier), 0.25 funding_spike, 0.10/leg xs. At $19 everything pins to the $10.50 exchange
minimum — that is the real floor of the whole design and the reason the account must not shrink
further: below ~$11 equity the minimum order exceeds equity and the system cannot trade at 1×
margin at all.

### Safety rails that survive VERBATIM

These are the components with measured protective value (`findings/W-G1_meta_alpha.md` gate
counterfactuals: the gates SAVE money — counter_regime −3.71%, trend −1.68%, giveback, reentry,
max_positions all negative-mean blocks) or proven catastrophe prevention:

1. **DSL exit engine** — `agents/dsl_exit.py` copied whole (739 lines): two-phase trailing floor,
   persisted tracker state (`.dsl-state.json`), `rehydrate_from_exchange` so a restart adopts any
   open position it finds, `queried_dexes`-aware so a dex timeout can't drop trackers. The tight
   profit-floor doctrine stands (KAITO n=296: every looser exit variant −EV).
2. **Backup on-exchange stop** — the `executor.py` path that places a reduce-only trigger SL
   (`place_hl_trigger_order`) immediately after every fill, capped by `backup_sl_max_frac_of_liq`
   (0.60) so the exchange flattens us even if the process dies. The Mac SLEPT for 194.8h over 15
   days (client audit); the on-exchange stop is the only rail that works while the loop is dark.
3. **Hard kill switch** — daily-loss flatten-all, equity>0 guarded. CHANGED IN ONE WAY: the
   threshold becomes `−15% × start-of-day equity` computed at SOD, not the fixed
   `max_daily_loss_usd: -100` that is unreachable on a $19 account (07-09 audit). v2 also fixes
   the SOD-reset laundering bug (`project_sod_reset_on_restart`): start-of-day equity persists
   keyed by UTC date, so a mid-day restart cannot re-baseline drawdown out of the kill switch.
4. **Claims registry** — `rebalancer_owned.ClaimsRegistry` (one book owns one coin, ever), with
   `_ACTIVE_CLAIM_BOOKS` pruned to `{extreme_fade, funding_spike_short, xs_momentum}`. New books
   MUST join the frozenset — that trap stays documented at the definition.
5. **Shadow ledger + grading** — `agents/shadow_ledger.py` + `scripts/shadow_status.py` verbatim,
   and the data at `.state/shadow_ledger/*.jsonl` (5,774 PIT records) is PRESERVED, not migrated.
   This is the organ that killed news_catalyst (−8.65%/sig forward after a positive-looking spec)
   and premium_fade (−11.47%/sig). It is the only reason v2's own books can be killed honestly.
   The standing operator orders bind v2: VALIDATED → live same day ($20/1×+kill); REFUTED →
   shadow same day.
6. **Rate limiter + degraded-read guards** — already in `client/` (§1): token bucket,
   `missing_material_dexes`, feed-freshness warnings, candle-retry-before-empty.
7. **Reduce-only close semantics + min-order floor** — `place_hl_order(reduce_only=True)` so a
   sub-$10 residual close can never flip the position.

`risk.py` keeps exactly these gates from `risk_gates.py`: kill switch, gross-notional cap (300%
of equity), margin preflight, per-book max-concurrent, reentry cap, liquidity floors ($20M short
floor / $5M long floor per `project_liquidity_floors_2026_06_28`). The AI-specific gates
(confidence floors, counter_regime_conf, runner gate, sidestep) die with the AI entry path —
they gated a signal source that no longer exists.

### What gets NO successor — each named, each justified

Forward ledger grades below are the `scripts/shadow_status.py` survey run for this design
(2026-07-18 06:21, PIT forward reads — stronger evidence than any backtest):

| dead thing | verdict source |
|---|---|
| **News (catalyst book + RSS/GDELT plumbing)** | Forward-REFUTED twice over: the live-flip window −8.65%/sig @12bps at n=34 (commit df3df37, past its own pre-committed review bar), and the full ledger −0.761%/sig @12bps over 2,214 resolved signals, negative in both halves (−0.45/−1.07). GDELT is additionally blind on small caps. No news veto either — there is no measured save from any news gate anywhere in the findings. |
| **Whale (crypto_whale + whale_flow book + whale override)** | Forward grade at n=116 resolved: MARGINAL +0.033%/sig @12bps, DIES at 25bps (−0.097), halves −1.46/+1.53 — no edge that survives costs. And the whale override's measured behavior is upgrading PASS→LONG at 0.78 conf — exactly the anti-calibrated long band (W-G1: 0.70–0.80 longs −2.13%/24h); `project_whale_override_pass_upgrade_watch` already logged it bleeding in chop. |
| **Premium fade** | Forward-REFUTED: −6.865%/sig @12bps at the full n=35, both halves negative (−3.50/−9.56); earlier read −11.47%/sig at n=34 said the same. Already disabled; v2 deletes the corpse. |
| **Neg-funding fade** | Ripped out 2026-07-12 (6916b85) after fwd −2.0%/ep net funding + debunked backtest; W-F3 independently corroborated. Operator refuted-rule: do not rebuild. |
| **AI-verdict-driven ENTRIES (research → LONG/SHORT/PASS pipeline)** | W-G1, n=4,644 episodes: mid-conf AI longs −2.13% @24h (n=609, both halves, WORSE than matched random timing); conf ≥0.75 did 1.53% worse than <0.70 (p=0.015) so a confidence floor selects the worst trades; PASS veto is not currently saving anything either (blocked ≈ executed, p=0.49). AI shorts were +0.33% excess at p=0.069 — marginal, and not worth keeping a 2,144-line entry pipeline (ai_brain+research+perception+ta_filter+system_prompt) plus its token cost and 402-outage failure mode alive for. |
| **thin_short_relax** | Structurally orphaned: its signal source IS the AI short verdict blocked by the thin floor. No AI entries → no signal. The W-G1 counterfactual (+1.12% blocked shorts) is real but only reachable by resurrecting the anti-calibrated pipeline; if funding/OI data later yields a mechanical short trigger, that's a new hypothesis for the ledger, not this module. |
| **rally_exhaustion, engulf_short, crash_continue_div_short, majors_swing, unlock_short, young_listings** | None cleared the validated bar: rally_exhaustion +2.1% excess is the weakest keeper-cell and its live plumbing inverted the stop (07-09 audit); majors_swing was explicitly NOT flipped for "no EV evidence" (07-10) and its forward ledger is 0/21 resolved; young_listings is now forward-REFUTED outright (−3.499%/sig @12bps, n=95 resolved, both halves negative — confirming W-Y1's p≈1.0 chase refute); unlock cells unvalidated (T-1d cell never confirmed; 2-6 ledger records each); engulf/crash_continue verdicts are SUSPECT under the broken-grader findings (07-09 audit: int() truncation, no funding, short-formula bugs) and have ≤4 resolved forward records. Rebuild-bar for any of them: fresh spec through the v2 ledger, ≥30 forward episodes, EV25>0. |
| **Mover/unlock/news recorders, hyperfeed, market_regime, AI memory** | Recorders for hypotheses that closed (mover-chase FLAT REFUTED across 624 cells, Lane M); market_regime's only consumers were dead books (the 3 survivors are regime-free by their validated specs); hyperfeed and `memory.py` serviced the AI brain. |

---

## 4. The AI brain, honestly

**Entries: nothing.** The measured record is unambiguous (W-G1 above). Every AI-entry variant is
either −EV, anti-calibrated, or statistically marginal, and the pipeline carried its own failure
modes (402 → silent PASS default, token cost, 429 amplification from held-coin research fetches).

**Closes: capability retained, but put on trial.** Two facts pull against each other:

- Standing operator order (`feedback_ai_close_required`): the AI must always be able to CLOSE a
  held position; never let token-saving skip research on held coins.
- There is NO measurement anywhere in the findings that AI closes add EV. Exit asymmetry is the
  "biggest untapped lever" (`project_edge_profile`) — but that claim was never separated from the
  DSL's own contribution.

v2 resolves this the only honest way: keep a minimal close-check (held book positions only, on the
30-min cycle, via **local Claude Code** per the CLAUDE.md LLM rule — no hosted API), whose verdict
can ONLY close, never extend, never enter. Every AI close writes a paired ledger record: realized
exit vs the counterfactual hold-to-DSL-exit. Pre-committed kill: 30 graded closes with negative
mean counterfactual delta → the close-check goes shadow, DSL becomes the sole exit authority, and
the operator is told the standing order now costs measured money. AI-down (402/timeout) degrades
to pure DSL — the AI is advisory on top of the floor, never the floor.

**News veto: no.** News is refuted as signal; there is no evidence it works as veto.

---

## 5. Migration — live money is never unmanaged

Invariants throughout: the v1 DSL monitor + on-exchange backup stops stay active for any open
position until the moment v2's monitor owns it; there is never a window where neither loop is
watching; every step has a one-config rollback.

**Phase 0 — freeze the dead books (day 0, config-only, hot-read, no restart).**
Set `enabled:false` / `shadow_only:true` for every non-survivor book in `.agent-config.json`.
Open positions from dead books keep their DSL trackers and backup stops and exit naturally.
Rollback: flip the flags back. Verify with heartbeat + `positions_snapshot` that nothing is
orphaned (per `feedback_validate_before_escalating`: cross-check live-fetch + ledger + dsl-state).

**Phase 1 — build v2 in a worktree; v1 untouched.**
`hermes_trader/v2/` with its own gate tests (<2s, pre-commit): completed-bar contract test,
claims-registry exclusivity, kill-switch SOD persistence, DSL floor math golden cases,
min-order/sizing floor arithmetic at $19. The 3 book signal functions are ports of already-live
logic, not rewrites: `extreme_fade_live.compute`, `funding_spike_short_live` z-episode logic,
`xs_momentum` ranker.

**Phase 2 — v2 shadow parity run (≥72h), v1 still trading.**
v2 loop runs `mode=SHADOW` from the same repo, same IP: full signal cycle, intents written to
`.state/shadow_ledger/` under `v2_`-prefixed book names, zero orders. Its exit sub-cycle computes
DSL verdicts for v1's open positions and DIFFS them against v1's actual verdicts in the session
log — floor divergence > rounding is a blocker. Rate budget check: combined loop + shadow must
show zero 429s over the window (both meter through the same `HL_LIMITER` process? no — two
processes, so the shadow run gets the dashboard's 60-cap/2-per-sec env budget from `restart.sh`
convention). Rollback: kill the shadow process; nothing depended on it.

**Phase 3 — cutover, one book at a time (survivor order: funding_spike_short first — it is
freshest-validated and cleanly episodic — then extreme_fade, then xs_momentum when equity
qualifies).**
Per book, in one config commit: v1 book `enabled:false`, v2 book live at $20/1× notional
(standing auto-flip sizing) with its pre-committed kill criterion armed. `.dsl-state.json` is
shared state; v2's `rehydrate_from_exchange` adopts any open position at first cycle, and the
on-exchange backup stops are position-scoped so they survive the handover untouched. Between the
v1 stop and v2 start the position is still covered by the exchange-side stop — that is the rail
that makes the gap safe. Rollback point per book: the config commit reverts; v1 loop re-adopts
via the same rehydrator.

**Phase 4 — decommission v1 (only after every live position was opened by v2 and ≥7 clean days).**
Tag the tree (`v1-final`), delete `scripts/trading_loop.py` + dead agents in one commit. The
shadow ledger, `.dsl-state.json`, and `.state/` recorder files carry over untouched — the graded
history IS the asset. Rollback: the tag redeploys in minutes.

Restart discipline: every phase change lists exact restart commands for Julien; the loop is never
left down after a config flip that requires one.

---

## 6. Expected weekly EV — measured numbers only, arithmetic in code

Inputs (every number cited above): extreme_fade +4.2%/ep @12bps, n=193/281d → 4.81 ep/wk;
funding_spike_short +6.0%/ep net@25bps (funding term included), n=25/90d → 1.94 ep/wk;
xs_momentum +1.41%/rebalance (net 10bps/name) on one-side notional, 1.4 rebal/wk. Sizing per §3;
HL minimum order $10.50 binds at $19.

| equity | extreme_fade | funding_spike_short | xs_momentum | **total/wk** |
|---|---|---|---|---|
| **$19** | 4.81 × 4.2% × $10.50 = **$2.12** | 1.94 × 6.0% × $10.50 = **$1.23** | OFF ($84 gross = 440% equity) | **≈ $3.35 (+17.6%/wk)** |
| **$100** | 4.81 × 4.2% × $40 = **$8.08** | 1.94 × 6.0% × $25 = **$2.92** | 1.4 × 1.41% × $42 = **$0.83** | **≈ $11.8 (+11.8%/wk)** |

Average concurrent notional: ~$36 at $19 (190% equity, ~$3 margin at 12×); ~$201 at $100 (201%
equity, ~$17 margin) — inside the 300% gross cap.

**Read these as upper bounds, and say so out loud.** Reasons, all measured elsewhere in this repo:
(a) the per-episode numbers are backtests on a single −44% regime with survivorship
(extreme_surface names survivorship its dominant risk; W-F2's h1 half is +2.4%, not +6.0 — expect
the +2.4% end in flat tape); (b) every weak edge this project graded FORWARD died — news
−0.76%/sig over 2,214 resolved, premium −6.87%/sig, young_listings −3.50%/sig, neg-funding
−2.0%/ep — so the honest planning posture is that forward reality has historically eaten 100% of
weaker edges; (c) the survivors' own forward grades are still open: extreme_fade sits at 231
ledger signals / verdict PENDING, and funding_spike_short has logged ZERO forward episodes since
its 07-10 wiring (no z≥2 spike in 8 days against an expected ~2/wk — either a quiet funding tape
or a recording gap; the migration's Phase 2 shadow run must confirm which); (d) episode arrival is
clustered (crash weeks deliver most extreme_fade episodes; many weeks deliver zero), so weekly
variance dwarfs weekly mean at this size — at $10.50/trade a single 20% stop-out is −$2.10,
±$8–10 weeks are normal at $19. The kill floor bounds the left tail at −15%/day of SOD equity.

The real product of v2's first 60 days is not the ~$3/wk — it is 40+ forward-graded episodes per
book through the untouched ledger, which is the only instrument that has ever told this project
the truth.
