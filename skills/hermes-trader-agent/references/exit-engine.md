# Exit Engine — DSL trail + server-side brackets

Every executed position gets three exit layers, all set at entry. Originally the
fix set for the **"we had it all and gave it back"** round-trips; materially
re-tuned 2026-06-16 (see "Scalp vs trend-ride" below) and tightened again in the
2026-06-18 PnL audit.

## 1. DSL trailing stop (`hermes_trader/agents/dsl_exit.py`) — primary, 60s tick

- **Phase 1 (loss):** exit at `min(max_loss_pct, max_loss_roe_pct / lev)`, optionally
  widened to a volatility-scaled `atr_stop` (`atr_mult`×ATR clamped floor/ceiling).
  Current live new-entry config: `max_loss_pct=2.5` spot, `max_loss_roe_pct=15`,
  `atr_stop` ON (1.5× ATR, 1.0–2.5% clamp). At 12x the 15% ROE cap = 1.25% spot but
  the atr_stop widens it toward ~2.5% on volatile movers. **2026-06-21: widened from
  the old 0.4%/3% fast-invalidation stop** — that tight stop was whipsawing volatile
  movers out of trend (EIGEN entered→stopped in 1min→ran +5%; AERO rode +10% on the
  wide stop). The fast stop was a measured −EV leak (noise-band).
- **Phase 2 (profit lock):** arms at `protect_pct`. Floor = `entry ± peak_range ×
  (1 − retrace)`, ratchets one-way (never gives back).
- **Retrace ladder (`phase2_tiers`)** = the give-back control; tighter = bank faster.
  Wired from config in BOTH builders (`executor.py` entry-time `ExitPolicy(...)` +
  `dsl_exit._policy_from_config`). Hot-read for new entries.

## Scalp vs trend-ride — the 2026-06-16 finding (the exit lever)

Legacy heuristic backtest (`scripts/reentry_backtest.py`, same lev/coins/period,
only exit params vary; confirm current decisions with logged/portfolio replays):
```
scalp      (protect 1.5 / retrace 0.30):  61% win  +$1518   <- 2026-06-16 live baseline
trend-ride (protect 3.0 / retrace 0.55):  47% win  -$757
```
**Tight (scalp) beats loose (trend-ride) hard in chop** — loose lets winners give
it all back. Current live new-entry config keeps the scalp profile, tightened
further on 2026-06-21: `protect_pct=1.25`, `retrace_threshold=0.10` (was 0.20 —
banks give-backs earlier; validated live, JUP banked +16%/+12% ROE in serial
trail exits), `phase2_tiers=[{8.0,0.35},{15.0,0.40}]` so proven runners still
breathe. The wider Phase-1 stop (above) + the tight 0.10 trail is the current
stack: ride through noise, bank the give-back. Trend-ride was originally shipped after
validating on ONE up-trend day — it rides rippers but bleeds in chop, the dominant
regime. Caveat: scalp can amputate the fat-tail winners the edge depends on —
`tp_scale_fraction` lets a runner ride (below).

- **Hard timeout / stale-flat:** `hard_timeout_minutes` (1800); and
  `stale_flat_timeout_minutes` (480) flattens a position that never reaches
  `protect_pct`.

## 2. Backup stop-loss trigger (server-side)

`place_hl_trigger_order(is_buy, size, sl_px, "sl", coin)` at `sl_atr_mult`=1.5 ATR,
placed at entry. Fires on the exchange between 60s ticks — the ONLY protection
while the host sleeps or the loop restarts. `[executor] Backup SL FAILED` =
escalate (position has no server-side stop).

## 3. Take-profit scale-out (server-side) — keeps the right tail

`tp_scale_fraction` (0.5) of the position gets a reduce-only TP trigger at
`TP_ATR_MULT`=1 ATR past entry. Banks half at target; the rest rides the DSL
trail. This is what stops scalp from fully amputating the fat tail — verify it's
firing (`Placed TP scale-out` log). HL accepts a 100%-SL + 50%-TP reduce-only
bracket without "would increase position" rejects.

## Trigger hygiene

`close_position_market` calls `cancel_open_orders_for_coin(coin)` after a market
close to clear the stranded SL/TP bracket, else stale reduce-only orders pile up
and reject later reduce-only orders (`reduce only order would increase position`).

## Execution-quality capture (2026-06-16)

Each close logs `entry_slip_bps`/`exit_slip_bps` (fill vs arrival mid),
`funding_cost_usd`, `hold_minutes`, `regime_at_entry`, `is_hip3` → the cost data
the backtests omit. Thin HIP-3 books slip materially (xyz median ~12.5 vs crypto
~5 bps); at n≥50 build a per-coin slippage kill-list (>~50bps).
