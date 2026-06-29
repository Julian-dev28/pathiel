# Config Audit 1 — Core Sizing / Risk

Scope: top-level core sizing + risk keys in `.agent-config.json` (hot-read each cycle).
Read-only analysis. No live behavior changed. Date: 2026-06-29.

## Removable / value-inert NOW (highest-value finds)

Nothing in this section is strictly DEAD (every key is read somewhere live). But two keys
are **value-inert under the current live config** — they are read, but the value cannot
affect any trade as the config stands:

- **`equity_fraction_per_trade` (0.2)** — read ONLY in the legacy `else` branch at
  `executor.py:762`, which is unreachable while `atr_risk_sizing.enabled = true`
  (current live). Sizing today runs the `primary_stop` ATR path (`executor.py:710+`),
  which never reads this key. The value 0.2 currently sizes nothing. KEEP the key (it is
  the real fallback if ATR sizing is ever disabled) but know the number is doing nothing
  right now. Do NOT tune it expecting a position-size change.
- **`asset_notional_multiplier` ({crypto:1.0, hip3:1.0})** — the multiplier is applied
  only when `< 1.0` (`executor.py:772`). Both legs are 1.0, so it is a no-op every cycle.
  KEEP the machinery (it is the per-asset-class haircut lever) but the current value
  changes nothing.

Neither should be deleted — both are load-bearing levers at non-default values. They are
flagged so nobody spends time tuning a number that is currently inert.

## Table

| key | read at file:line | gates / controls | verdict | why / impact |
|---|---|---|---|---|
| `mode` (LIVE) | `executor.py:284` (OFF→`mode_off`, non-LIVE→`mode_not_live`); banner/dashboard `dashboard.py` | Master kill: gates the entire executor. Anything but `LIVE` blocks every real trade | KEEP | Load-bearing master switch. Removing it = bot always trades or never trades. Must stay. |
| `enable_crypto` (true) | `executor.py:315`; `perception.py:284` | Allows native HL perp (no-colon) scan + execution | KEEP | Asset-class on/off. Removing = no way to disable crypto leg without code edit. |
| `enable_hip3` (true) | `executor.py:309`; `perception.py:285`; `exchange.py:71`; `server.py:158` | Allows HIP-3 (colon `xyz:*`) scan + execution + name resolution | KEEP | Asset-class on/off, also gates per-dex POST cost in perception. Load-bearing. |
| `leverage` (12) | `executor.py:629/635` (requested lev), `:744` (`config_max_leverage` cap into sizing) | Sets requested exchange leverage AND the operator leverage cap; also feeds `_stop_frac = max_loss_roe/lev` in primary-stop sizing | KEEP | Two roles on one key (request + cap). Drives stop fraction in current sizing path → directly affects position size and ROE-based stop. Critical. |
| `max_trade_notional_usd` (200) | sizing clamp `sizing.py:71` + `executor.py:641`; gate `risk_gates.py:469` | Hard per-trade $ ceiling. Sizing clamps DOWN to it; risk gate also rejects above it (belt + suspenders on the same key) | KEEP | Primary per-trade size bound. The two reads are not duplicate keys, just clamp + gate on one value. Removing = unbounded per-trade notional (a known prior leak, see `min_margin_floor` memory). |
| `asset_notional_multiplier` ({1.0,1.0}) | `executor.py:243/249/771` | Per-asset-class notional haircut, applied only when `<1.0` | KEEP (value inert) | Machinery live, value a no-op at 1.0/1.0. See removable list. No impact if left; do not delete. |
| `tp_scale_fraction` (0.5) | `executor.py:1152-1153` (overridable per-analysis) | Fraction of position banked as a server-side reduce-only TP scale-out at 1 ATR | KEEP | THE offensive take-profit lever (see `take_profit_scaleout` memory — `tp_px` was computed-but-unused before this). Removing = no TP scale-out, winners run unbanked. |
| `max_concurrent` (10) | gate `risk_gates.py:62-65/468` | Hard cap on simultaneous open positions | KEEP | Book-size cap. Note: this + the notional cap are the known "94% of missed movers die here" constraint (memory: capital saturation). Load-bearing guardrail. |
| `max_total_notional_pct` (10.0) | gate `risk_gates.py:277/491`; sizing room `executor.py:703` | Aggregate notional cap = `equity × 10.0` (i.e. 1000% of equity); also caps ATR-sizing `_room` | RECONSIDER-VALUE | Code default is 1.0 (100%). Live is 10.0 = 1000%. For a ~$148 account that is ~$1480 aggregate notional — effectively non-binding given `max_concurrent 10 × max_trade 200 = $2000` ceiling sits just above it. The historical 300% (3.0) cap was the binding constraint memory repeatedly cites as the missed-mover throttle; raising to 10.0 removes that throttle but also removes the concentration brake. Not wrong, but the gate barely bites now. Confirm 10.0 is intentional vs a leftover from a larger-account era. KEEP the key. |
| `max_daily_loss_usd` (-40) | gate `risk_gates.py:470` (hard kill-switch) | Daily realized+unrealized loss floor; breach FLATTENS all positions (hard, per `hard_killswitch` memory) | KEEP | Capital-preservation hard floor. Value scales to account size (~$148 eq). Removing = no daily stop. Critical. |
| `daily_giveback_halt_pct` (0.45) | gate `risk_gates.py:473` | Halt new entries after giving back 45% of the day's peak profit | KEEP | Profit-protection. Pairs with `min_peak` below. Removing = no give-back brake. |
| `daily_giveback_min_peak_usd` (25.0) | gate `risk_gates.py:474` | Floor before the give-back rule arms (peak must exceed $25 first) | KEEP / CONSOLIDATE-candidate | Only meaningful as the companion to `daily_giveback_halt_pct`; the two are one mechanism in two keys. Not redundant (different units) but should always be reasoned about together. Removing = give-back gate fires on tiny noise peaks. |
| `min_available_margin_pct` (0.1) | preflight `executor.py:583` | Require ≥10% of the funding account's margin free before opening | KEEP | Margin-saturation guard on the funding (per-dex) account. Memory (`min_margin_floor_005`, `sizing_against_funding_account`) shows low floors spawned oversized concentrated legs. Load-bearing. |
| `equity_fraction_per_trade` (0.2) | `executor.py:762` (legacy `else` branch only) | Legacy fallback sizing `equity × frac × lev` when ATR sizing is OFF | KEEP (value inert) | Unreachable while `atr_risk_sizing.enabled=true`. See removable list. Keep as the fallback; value does nothing today. |

## Notes / cross-checks

- **No dead keys in this section.** Every audited key resolves to a live read. The two
  "inert" keys (`equity_fraction_per_trade`, `asset_notional_multiplier`) are gated off by
  current config values, not absent from the code.
- **One genuine value concern:** `max_total_notional_pct = 10.0` (1000% of equity). This
  contradicts the repeatedly-cited "300% cap is the binding missed-mover constraint"
  finding in memory. At the current account size the aggregate gate is essentially
  non-binding (the per-trade cap × max_concurrent ceiling sits just above it). Flagging for
  operator confirmation — not proposing a change.
- **No true duplicates.** `max_trade_notional_usd` appears as both a sizing clamp and a
  risk gate, but that is intentional defense-in-depth on a single key, not two keys doing
  the same job. `daily_giveback_halt_pct` + `daily_giveback_min_peak_usd` are one mechanism
  split across two keys (pct + arming floor) — reason about them as a pair.
- **Sizing path reality check:** live sizing = `atr_risk_sizing` primary-stop path. The two
  inputs that actually move position size today are `leverage` (via the ROE stop fraction)
  and `max_trade_notional_usd` / `max_total_notional_pct` (the caps). `equity_fraction_per_trade`
  is bypassed.

Status: DONE.
