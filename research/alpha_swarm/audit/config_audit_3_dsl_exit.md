# Config audit 3 — the DSL exit engine (`dsl_exit` block + loose exit keys)

READ-ONLY audit. Scope: the entire `dsl_exit` block plus the loose exit-related top-level
keys (`loss_cooldown_min`, `min_ai_close_hold_min`, `sl_atr_mult`, `backup_sl_max_frac_of_liq`).
Live config snapshot read from `.agent-config.json`. Every key was grepped in
`hermes_trader/agents/dsl_exit.py` and `hermes_trader/agents/executor.py`
(plus repo-wide for the loose keys).

**Headline: nothing in this block is dead. Every key is read and load-bearing.**
This is the live exit engine on real money. Do not remove anything here for "cleanup."
The only safe actions are CONSOLIDATE-adjacent notes and one just-off pair to leave alone.

---

## TL;DR — removable now (safe)

**NONE.** Every key in scope is read at runtime and changes exit behavior. There is no
dead key to delete in this block.

## TL;DR — DO NOT TOUCH (load-bearing / safety-critical)

Every one of these is a stop or a profit-lock on live positions. Removing or zeroing any
of them changes how real money exits:

- `max_loss_pct` — hard spot stop (safety-critical)
- `max_loss_roe_pct` — leverage-aware ROE stop (safety-critical, this is the real cap at high lev)
- `atr_stop{enabled,atr_mult,floor_pct,ceiling_pct}` — vol-scaled Phase-1 stop width (safety-critical when enabled; it IS enabled)
- `protect_pct` — Phase-1→Phase-2 arm threshold AND the never-armed cut threshold (safety-critical)
- `retrace_threshold` — base Phase-2 give-back floor (the tight floor)
- `phase2_tiers[]` — the VALIDATED loosened give-back ladder (the MANTA +94% ride; do not flatten)
- `hard_timeout_minutes` — emergency time stop
- `stale_flat_timeout_minutes` — cut never-armed drifters to free slots
- `sl_atr_mult` — server-side backup stop distance (safety-critical, fires between DSL ticks)
- `backup_sl_max_frac_of_liq` — caps the backup stop inside the liq buffer (safety-critical)
- `loss_cooldown_min` — anti-revenge re-entry block after a loss
- `min_ai_close_hold_min` — anti-churn min hold before the AI may close

## Just-off, NOT dead — leave in place

- `breakeven_trigger_pct = 0.0` and `breakeven_lock_pct = 0.0` — both READ every tick at
  `dsl_exit.py:307`, but the whole ratchet is guarded by `if pol.breakeven_trigger_pct > 0`.
  At 0.0 the breakeven ratchet is OFF, not dead. The code path is live; setting trigger
  >0 arms it with zero code change. Keep both keys as the on/off switch for the feature.

---

## Per-key table

| key | live config | read at (file:line) | controls | verdict |
|---|---|---|---|---|
| `max_loss_pct` | 2.5 | dsl_exit.py:217 (`spot_cap=pol.max_loss_pct`), 222; executor.py:1013, 712 | Hard spot-% stop below entry; floor of the min() with the ROE cap | KEEP — safety-critical |
| `max_loss_roe_pct` | 25.0 | dsl_exit.py:222 (`min(spot_cap, roe/lev)`), 265, 290; executor.py:1014, 713 | ROE/margin stop; `roe/lev` is the binding cap at high leverage | KEEP — safety-critical |
| `atr_stop.enabled` | true | dsl_exit.py:218, 503; executor.py:1024 | Switches the Phase-1 stop to a vol-scaled width (else fixed `max_loss_pct`) | KEEP — safety-critical (on) |
| `atr_stop.atr_mult` | 1.5 | dsl_exit.py:219, 504; executor.py:1025 (`atr_stop_mult`) | Stop width = `atr_mult x entry_atr_pct` | KEEP — safety-critical |
| `atr_stop.floor_pct` | 1.0 | dsl_exit.py:220, 504; executor.py:1026 (`atr_stop_floor_pct`) | Lower clamp on the ATR stop width | KEEP — safety-critical |
| `atr_stop.ceiling_pct` | 2.5 | dsl_exit.py:221, 505; executor.py:1027 (`atr_stop_ceiling_pct`) | Upper clamp on the ATR stop width | KEEP — safety-critical |
| `protect_pct` | 1.25 | dsl_exit.py:233 (never-armed cut), 270/295 (Phase-2 arm), 339, 358/367; executor.py:999, 1015 | Profit % that arms Phase-2; below it = Phase-1; also gates stale-flat cut | KEEP — load-bearing |
| `retrace_threshold` | 0.1 | dsl_exit.py:183 (default tier), 274/298 (floor calc); executor.py:1000, 1016 | Base Phase-2 give-back BEFORE any tier arms (the tight floor) | KEEP — load-bearing |
| `hard_timeout_minutes` | 1800 | dsl_exit.py:243; executor.py:1017 | Emergency time stop, flat after N min regardless of PnL | KEEP — safety-critical |
| `breakeven_trigger_pct` | 0.0 | dsl_exit.py:307, 310, 314; executor.py:1018 | Peak-% that arms the breakeven floor lock (0 = ratchet OFF) | KEEP — just-off, not dead |
| `breakeven_lock_pct` | 0.0 | dsl_exit.py:311, 315; executor.py:1019 | Locked floor % above/below entry once the ratchet arms | KEEP — just-off, not dead |
| `phase2_tiers[]` | [{8%,0.35},{15%,0.40}] | dsl_exit.py:184 (`_active_tier`), 339, 412/419; executor.py:1001, 1028 | Loosens the give-back at higher MFE so runners ride (arm +8%→35%, +15%→40%) | KEEP — VALIDATED, do not flatten |
| `phase2_tiers[].pct_above_entry` | 8 / 15 | dsl_exit.py:185, 339 | Profit % at which each looser tier activates (by PEAK) | KEEP |
| `phase2_tiers[].retrace_threshold` | 0.35 / 0.40 | dsl_exit.py:274/298 | Give-back fraction of peak gain for that tier | KEEP |
| `stale_flat_timeout_minutes` | 480 | dsl_exit.py:228; executor.py:1020 | Cut a position that never armed Phase-2 after N min (frees slots) | KEEP |
| `loss_cooldown_min` | 180 | executor.py:1596, 1716 | After a losing close, arms an extended per-coin re-entry block (anti-revenge) | KEEP |
| `min_ai_close_hold_min` | 25 | trading_loop.py:925 | Blocks the AI from closing a position younger than N min (anti-churn) | KEEP — read in the loop, not in dsl_exit/executor |
| `sl_atr_mult` | 1.5 | executor.py:211/216 (`_backup_sl_price`), 741, 1096, 1122 | Distance of the server-side backup stop = `sl_atr_mult x ATR` | KEEP — safety-critical |
| `backup_sl_max_frac_of_liq` | 0.6 | executor.py:1098, and `_backup_sl_price` max_frac_of_liq arg (line ~219) | Caps the backup stop inside `frac/lev` of the liq price so it can't sit past liquidation | KEEP — safety-critical |

---

## phase2_tiers — the Lane 3 validation (why this is load-bearing, not cosmetic)

`research/alpha_swarm/runners/influx_exit.md` (Lane 3 EXIT/ride study, n=9,587 reached-+5%
influx longs, OOS halves agree, lookahead-safe) ranks exit policies by net EV:

- The spec'd **tight floor armed at +1%** captures only **14-16%** of MFE → **+1.45-1.64% EV**.
  On the >=50% runners it captures **1.8%** of the move. That is the leak.
- **35%-of-peak give-back armed at +10%** captures **36.5%** of MFE on the >=50% bucket,
  **+31.9% EV**, uncapped — it beats even fixed-TP+30% on the tail (+31.9% vs +29.88%).
- This is exactly the MANTA case: peaked +145% ROE, closed +94% = a 35%-of-gain give-back,
  the move the live `phase2_tiers` ladder is built to ride.

The live ladder (`{pct_above_entry: 8, retrace: 0.35}`, `{15, 0.40}`) plus the tight base
`protect_pct=1.25` / `retrace_threshold=0.1` is the deliberate shape Lane 3 endorses:
**tight floor in the body (give back only 10% sub-+8%), then loosen to 35%/40% give-back
once a real move is underway.** Flattening or removing the tiers reverts to the validated
leak. Do not touch them.

`protect_pct` / `retrace_threshold` = the tight floor that protects the body before the
first tier arms. `phase2_tiers` = the loosened give-back that lets the runner run. They
are a matched pair; tune together if ever, not in isolation.

---

## Findings / concerns (no edits made)

1. **Config nesting is correct on the LIVE path.** The entry-time policy that every fresh
   position gets is built in `executor.py:1013-1029`, which reads the NESTED `atr_stop`
   block (`atr_mult`/`floor_pct`/`ceiling_pct`) correctly. Verified `register_position`
   is called with that policy at `executor.py:1033`.

2. **Reconcile-path inconsistency (code bug, not a config issue).** The fallback builder
   `_policy_from_config()` at `dsl_exit.py:533-560` — used to SYNTHESIZE a tracker after a
   blackout/exchange reconcile — does NOT read the `atr_stop` block at all (it omits
   atr_stop_*, so a reconciled position silently falls back to `atr_stop_enabled=False`
   and the fixed `max_loss_pct` stop). It also omits the nested mapping the executor does.
   Net effect: a position rebuilt after a blackout gets a TIGHTER fixed stop than a fresh
   entry, not the configured ATR stop. This is a correctness gap in the reconcile path,
   not a reason to remove any key. Flagging for a follow-up fix (mirror the executor's
   nested `atr_stop` read into `_policy_from_config`). Severity: medium — only fires on the
   post-blackout reconcile path, and it errs toward a tighter stop (safer, not looser).

3. **`min_ai_close_hold_min` lives outside this engine.** It is read in
   `scripts/trading_loop.py:925`, not in dsl_exit/executor. It is in scope as a loose
   exit-related key and is live (anti-churn gate on AI closes). Keep, just note its home.

4. **Default-value drift between call sites (harmless, worth knowing).** The `.get()`
   fallbacks disagree across builders: e.g. `max_loss_pct` defaults to 2.5 at
   executor.py:1013 but 2.0 at executor.py:712; `max_loss_roe_pct` defaults 50.0 at
   executor.py:1014, 40.0 at executor.py:713, 50.0 in the dataclass, while live config = 25.0.
   Because the live config sets every key explicitly, the fallbacks never fire, so behavior
   is unaffected. If a key were ever removed from config, these stale defaults would
   silently loosen the stop. Another reason the answer here is KEEP-everything-explicit.

---

## Verdict summary

- REMOVE (dead): none.
- CONSOLIDATE: none structurally; `protect_pct`+`retrace_threshold`+`phase2_tiers` are a
  matched exit-shape unit — document/tune as one, do not split.
- RECONSIDER: none of the keys; one CODE follow-up (finding 2, atr_stop missing from the
  reconcile-path policy builder).
- KEEP: every key in scope. Most are safety-critical stops on live capital.

This block is the live exit engine. The completeness move here is to leave it intact and
fix the reconcile-path atr_stop gap separately, not to prune.
