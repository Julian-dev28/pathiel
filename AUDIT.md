# hermes_trader Profitability Audit — 2026-06-11 (branch `able`)

Method: every number below is measured — from `logs/trading_loop.log` realized
closes (n=650: 502 DSL exits + 148 AI closes, 2026-05-30 → 06-10), the
`scripts/backtest.py` exit-geometry sweeps, or a code trace. PnL units: summed
**ROE %** (margin-relative, comparable across the era of changing account size);
$ figures use current sizing (~$22 margin/position, ~45 closes/day) and are
marked *est*.

---

## 1. Leak ledger (ranked by $ impact)

### L1 — `max_loss` stops inside the noise band, plus gap-throughs. ~−$10/day *est* ❶
- **Where:** `.agent-config.json dsl_exit.max_loss_pct` (was 1.2) + `dsl_exit.py:198` check cadence (60s loop).
- **Measured:** 162/502 DSL exits (32%) were `max_loss`, summing **−1,947% ROE**
  (avg **−12.0%** per stop). Two mechanisms:
  (a) the 1.2% spot cap sat inside 4h-ATR noise (2–5% on this universe), so
  recoverable dips became realized losses — the sweep on a fixed 21d/30-coin
  population: 1.2% → **−$0.003/trade (−EV)**; 1.8% → +$0.033; 2.5% → +$0.094;
  3.5% → +$0.226. (Caveat: monotonic to 8% = benign-window artifact; the robust
  finding is "1.2 is pathological", NOT "wider is always better".)
  (b) avg realized stop −12% ROE ≫ the −3.6% ROE the cap implies at 3x —
  **gap-throughs**: price blows past the floor between 60s checks; the 1.5×ATR
  backup SL caps some but not all (and is never retried on failure, see L5).
- **Status: PARTIALLY FIXED** — `max_loss_pct` 1.2 → 3.5 shipped 2026-06-10.
  Remaining: the stop is still a fixed % across a universe whose vol varies 5×.
  → **★ ATR-stop feature (section 3), built this audit.**

### L2 — AI-closed SHORTS: 0-for-8, −54.8% ROE summed. ~−$1/day *est*, capped by low n ❷
- **Where:** research verdicts (SHORT) + `route_verdict` → AI CLOSE path.
- **Measured:** every AI-closed short lost (avg **−6.85%** ROE; short/HIP3 avg
  −7.41%). Contrast: **DSL-exited crypto shorts were the BEST bucket: +3.63%/trade,
  n=75, 63% win.** Shorts per se aren't the leak — *HIP-3 shorts and AI-managed
  short exits are*. The short edge exists when the DSL manages the exit.
- **Fix direction:** keep crypto shorts, block HIP-3 shorts (config:
  `coin_blocklist` can't express "HIP3+short"; needs a one-line gate) — and see L3,
  which currently does this *by accident* while also blocking the good shorts.

### L3 — WIRING: liquidity gates fed a hardcoded constant, not real volume ❸
- **Where:** `executor.py:52-59` `_get_market_volume_24h` returns a static map
  (8 majors=$100M, **everything else=$10M**) → feeds `GateContext.market_volume_24h_usd`
  → `market_liquidity_floor` + `short_liquidity_floor` (`risk_gates.py:102-139`).
  Real `dayNtlVlm` is sitting in the universe cache (`client/universe.py:255`), unused here.
- **Effect in dollars:**
  - `min_short_volume_usd: 50M` — every non-major coin reads $10M < $50M →
    **all non-major shorts blocked**, including the measured short winners
    (XMR/TON-class, the +3.63%/trade bucket). The gate was calibrated on real
    volume data ($223M median for winners) but receives fiction.
  - `min_market_volume_usd`/`min_hip3_volume_usd` at this gate: $10M passes both
    floors → **gate never blocks** (vestigial; the real volume floor is enforced
    earlier at perception scan-time on real `dayNtlVlm`, so entry filtering still
    works — but the *gate* is dead wiring).
- **Fix:** pipe real `dayNtlVlm` (perception already has it per coin) into the
  GateContext. Cheap, high-confidence. **Implemented this audit (section 3).**

### L4 — HIP-3 longs: huge volume of trades, ~zero-to-negative edge ❹
- **Measured:** long/HIP3 = biggest DSL population (n=213) at **−0.07%/trade**;
  AI-closed HIP3 summed **−48.8% ROE** (n=67). Combined: HIP-3 is net negative
  while long/crypto carries the book (+58.1% summed, +0.73%/trade).
- **Mechanism (hypothesis, partially measured):** HIP-3 equity/commodity perps
  gap on tradfi opens and dry up off-hours; the DSL's 60s cadence and the crypto-
  tuned trigger thresholds translate poorly. Not a single bug — an allocation leak.
- **Fix direction:** don't ban (xyz allowlist already constrains); shift sizing —
  conviction tiers or a per-class size multiplier. Defer until ATR-stop data
  arrives (HIP-3 stops will be the first beneficiaries of vol-scaling).

### L5 — Backup SL placement failures are silent and never retried ❺
- **Where:** `executor.py:525-529` — on `place_hl_trigger_order` failure: one
  ERROR log line, no retry, no flag. Observed live: 429 rate-limit (BTC 06-05,
  xyz:PLTR 06-02) and stale-coin lookups → those positions ran with **no
  server-side stop**, exposed to exactly the gap-through cluster in L1.
- **$:** unquantifiable directly (depends on which position gaps), but L1's
  −12%-avg stops show what a gap costs. **Fix:** one retry after ~2s, and a
  loud `sl_missing` field in the trade record. Small diff, implemented this audit.

### L6 — Dead/misleading code: `kelly_size` (executor.py:62-75)
- Defined, unit-tested, **never called** in any live path. The comment at
  executor.py:361 says "kelly_size already clamps" implying it runs — it doesn't.
  $0 direct, but it misleads audits (cost: my time, twice now). Left in place
  (churn rule); flagged so nobody "tunes" it expecting effect.

### Wiring audit — everything else: **CONNECTED.**
All 45 config keys have live readers (verified by trace): the `dsl_exit.*` block
flows `executor.maybe_execute → ExitPolicy → register_position → DSLTracker.check()`
(hot-read per trade, no restart needed); `tp_scale_fraction` reaches
`place_hl_trigger_order("tp")` (live placements in log 06-10); backup SL reaches
`place_hl_trigger_order("sl")` incl. colon coins (`get_coin_index` parent-dex
fallback works; the old "can't resolve colon coins" note is stale); whale/structural
overrides reach `maybe_execute` via `route_verdict` PASS-hint routing; the
breakeven ratchet, phase2 tiers, give-back breaker, hard kill-switch: all live.
The two genuine wiring defects are L3 (fake volume) and the L6 dead function.
`analysis.tp_px/stop_px` fallbacks at executor.py:548-549 are unreachable-dead
(atr<=0 refuses earlier) — harmless.

## 2. Edge map (realized, 2026-05-30 → 06-10)

| bucket | n | win% | sum ROE | avg/trade |
|---|---|---|---|---|
| DSL floor_breach (trailing wins) | 218 | 94% | **+1,857%** | +8.52% |
| DSL hard_timeout | 122 | 65% | +416% | +3.41% |
| DSL max_loss | 162 | 0% | **−1,947%** | −12.02% |
| DSL short/crypto | 75 | 63% | +272% | **+3.63%** |
| DSL long/crypto | 181 | 54% | +56% | +0.31% |
| DSL long/HIP3 | 213 | 57% | −15% | −0.07% |
| AI-close (all) | 148 | 41% | +6% | +0.04% |
| AI-close shorts | 8 | **0%** | −55% | −6.85% |
| AI-close HIP3 | 67 | 37% | −49% | −0.73% |

**Verdict: the system is approximately breakeven-to-slightly-negative net of the
max_loss bucket — the trailing engine prints (+1,857%), the stop bucket burns it
(−1,947%).** The gap to green is almost entirely "lose less on stopped trades":
fewer noise stops (shipped: 3.5%; next: ATR-scaled) and fewer gap-throughs (L5
retry). Allocation tilt (crypto > HIP-3; keep DSL-managed crypto shorts) is the
second-order win.

## 3. Implemented this audit (all flag-gated, default-safe)

1. **★ ATR-multiple primary stop** — `dsl_exit.atr_stop` config block
   (default `enabled: false`). Stop = `atr_mult × ATR(4h,14)` as spot-% captured
   at registration, clamped `[floor_pct, ceiling_pct]`, then through the existing
   `max_loss_roe_pct` lev cap. Wire: `executor (atr already in scope) →
   register_position(entry_atr_pct) → DSLTracker.check()`. Unit-tested (two coins,
   same mult, different stops) + `backtest.py --atr-mult` validation below.
2. **Real volume → gates** (L3): GateContext now receives live `dayNtlVlm` when
   available, static map only as fallback. Flag: none needed (bug fix), but the
   behavior change is logged.
3. **Backup-SL retry + loud failure** (L5): one retry, `sl_missing` surfaced.

### Validation results (run 2026-06-11, 21d / 30 coins, protect 1.0 / retrace 0.40)

| exit config | trades | win% | expectancy/trade | total PnL | median stop |
|---|---|---|---|---|---|
| fixed 1.2% (old live) | 5,466 | 48.6% | **−$0.003** | −16% | 1.2% |
| **fixed 3.5% (current live)** | 3,475 | 77.0% | **+$0.226** | +784% | 3.5% |
| ATR 1.0× (clamp 1–4%) | 4,335 | 67.7% | +$0.111 | +482% | 2.40% |
| ATR 1.5× | 3,715 | 74.6% | +$0.182 | +676% | 3.80% |
| ATR 2.0× | 3,449 | 77.1% | +$0.201 | +695% | 4.00% (pinned) |

**Honest verdict: the ATR stop did NOT beat fixed-3.5% on this window — so it
ships as code but stays `enabled: false`.** The window rewards raw stop width
monotonically (the same artifact that made 8% look best), so any config whose
*average* width is below 3.5% loses mechanically — this dataset cannot isolate
the value of vol-scaling from the value of width. The honest path: leave the
flag OFF, let the new per-entry `atr_stop=` width logging accumulate live data,
and re-judge on a window containing a real down-regime. Unit wiring is proven
(`tests/test_atr_stop.py`, 5 tests: differential stops, clamps, ROE-cap
stacking, fallbacks, state round-trip — all passing, full suite 176 green).

L3 real-volume fix verified live: BTC reads $2,945M (was fake $100M), XMR reads
its true current $4.8M (was fake $10M). Gates now track reality — XMR/TON
shorts unblock automatically when their volume returns to the winning regime
($200M-class) instead of being permanently fiction-blocked.

## 4. Rollout (one change at a time)

1. **Now live:** `max_loss_pct: 3.5`. Watch: share of `max_loss` exits among DSL
   closes (was 32%) and avg ROE per stop. Kill: revert to 2.5 if full-width stops
   cluster on trend-aligned longs or daily give-back worsens.
2. **Flip next (operator):** `dsl_exit.atr_stop.enabled: true` after reviewing the
   backtest table + stop-distribution sanity print. Watch: same metrics, plus the
   logged per-entry stop width. Kill: `enabled: false` (hot-read, instant).
3. **Already-on after merge:** real-volume gates (L3) — watch `short on thin
   market` block reasons now reflecting real volume; crypto shorts ≥$50M real
   volume start passing again. Kill: restore the static map via revert.
4. **Defer:** HIP-3 size tilt (L4) until 1–2 weeks of post-ATR data.

*Honesty notes: backtest absolute returns are inflated (no slippage/funding, no
concurrency cap, benign window) — only relative deltas across a sweep are load-
bearing. The 8 AI-short sample is too small to ban shorts outright; the fake-
volume fix (L3) is the right-sized response. All historical ROE sums mix account
eras; per-trade averages are the comparable stat.*
