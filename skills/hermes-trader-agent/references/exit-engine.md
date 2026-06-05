# Exit Engine — DSL trail + server-side brackets (2026-06-04/05 overhaul)

Every executed position gets three exit layers, all set at entry. This was the
fix set for the documented **"we had it all and gave it back"** round-trips.

## 1. DSL trailing stop (`hermes_trader/agents/dsl_exit.py`) — primary, 60s tick

- **Phase 1 (loss):** exit at `min(max_loss_pct, max_loss_roe_pct/lev)`. Config:
  `max_loss_pct=1.2` spot, `max_loss_roe_pct=18`. Caps each loss ~−1.2% spot.
- **Phase 2 (profit lock):** arms at `protect_pct` (1.0%). Floor =
  `entry ± peak_range × (1 − retrace)`, ratchets one-way (never gives back).
- **Retrace ladder — the give-back control.** `_active_tier` picks the retrace
  by PEAK profit. It must TIGHTEN with profit. Current (post-fix):
  - default `retrace_threshold = 0.40` (was a loose 0.65)
  - `phase2_tiers`: `+3%→0.30, +6%→0.22, +10%→0.15, +20%→0.12` (was a
    *loosening* `0.30→0.60` default that round-tripped winners)
  - **`phase2_tiers` is wired from config** in BOTH builders (`executor.py`
    entry-time `ExitPolicy(...)` + `dsl_exit._policy_from_config`). Before
    2026-06-04 it was silently ignored — only the hardcoded class default applied.
    Changing tiers needs a **loop restart** (code path), not just config.
- **Breakeven ratchet:** once peak ≥ `breakeven_trigger_pct` (1.0), floor clamps
  to ≥ `breakeven_lock_pct` (0.6) — a winner can't round-trip to flat.
- **Hard timeout:** `hard_timeout_minutes` (1800 = 30h). Scalp/swing horizon.

## 2. Backup stop-loss trigger (server-side)

`place_hl_trigger_order(is_buy, size, sl_px, "sl", coin)` at `sl_atr_mult`=1.5 ATR,
placed at entry. Fires on the exchange between 60s ticks and is the ONLY
protection while the host sleeps or the loop restarts.

## 3. Take-profit scale-out (server-side) — the offensive fix

`tp_scale_fraction` (default 0.5) of the position gets a reduce-only TP trigger at
`TP_ATR_MULT`=1 ATR past entry. **Banks half at target automatically**; the rest
rides the DSL trail. Validated live 2026-06-05: auto-banked an ADA half, runner
rode to +66% ROE. HL accepts a 100%-SL + 50%-TP reduce-only bracket (150% total)
without "would increase position" rejects.

## Trigger hygiene

`close_position_market` calls `cancel_open_orders_for_coin(coin)` after a market
close to clear the now-stranded SL/TP bracket. Without it, stale reduce-only
orders accumulate and reject a later reduce-only order on that coin
(`reduce only order would increase position`). HL also auto-cancels reduce-only
triggers when a position flattens, so the call is a safe backstop (usually a no-op).

## Why all this

Root cause of the round-trips (audited 2026-06-04): the strategy's edge was
positive (6W/4L, +77.6% summed ROE) but `tp_px` was computed and never acted on,
the retrace was loose, and `max_trade_notional_usd` was unbounded → **bigger bets
on losers** + winners bled back. The counterfactual with the $600 cap flipped a
−$4.2 closed-trade day to +$4.6. The fixes: clamp notional, tighten the ladder,
and actually take profit at target.
