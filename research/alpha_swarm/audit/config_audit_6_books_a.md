# Config Audit — Strategy Books Part 1 (xs_momentum, extreme_fade, rally_exhaustion)

Read-only audit. Config source: `.agent-config.json` (hot-read). Date 2026-06-29.
Scope: top-level `strategy_book_notional_usd`, `strategy_book_equity_frac`, `short_notional_usd`,
plus the full `xs_momentum`, `extreme_fade`, `rally_exhaustion` blocks.

All three books are wired and called every loop cycle from `scripts/trading_loop.py`:
- `_xs_maybe_rebalance(...)` at scripts/trading_loop.py:753
- `_ef_maybe_run(...)` at scripts/trading_loop.py:766
- `_rally_exhaustion_maybe_run(...)` at scripts/trading_loop.py:778

Live realized PnL (scripts/pnl_by_book.py, last 14d): only 4 of 232 episodes are book-attributed.
- extreme_fade: 1 episode, net -1.01
- rally_exhaustion: 1 episode, net -0.62
- xs_momentum: 0 attributed episodes (its longs flow to main-engine attribution; short leg never executes — see below)

---

## Top-level sizing keys

| key | value (live) | read at | controls | verdict |
|-----|-------|---------|----------|---------|
| strategy_book_notional_usd | 50 | executor.py:659, executor.py:672 | Absolute per-trade USD ceiling for strategy_book trades. With equity_frac=0 it is the DIRECT cap (fallback path, executor.py:672). | KEEP. Active. It is the real size cap for xs_momentum + extreme_fade today. |
| strategy_book_equity_frac | 0 | executor.py:656 | Fraction of FUNDING-account equity x leverage to size each strategy_book trade. 0 = disabled -> code falls back to strategy_book_notional_usd. | RECONSIDER. Live=0 disables the equity-frac sizing path (config_store default is 0.1). Not dead (read every strategy_book execute), but currently a no-op branch. Operator chose flat $50 over equity-scaled sizing. Intentional, flag only. |
| short_notional_usd | 100 | executor.py:679 | Dedicated USD cap applied when analysis side==short. min()'d into the notional cap. | KEEP but NON-BINDING for these 3 books. rally_exhaustion sets its own $20 (min(20,100)=20); xs_momentum short leg falls back to $50 (min(50,100)=50). It only bites discretionary/other short books with larger base size. Active globally, just never the binding constraint here. |

Sizing interaction worth noting: because `strategy_book_equity_frac=0`, both xs_momentum and
extreme_fade (neither sets an explicit `strategy_book_notional` in its analysis) size at the flat
`strategy_book_notional_usd=50` via the fallback at executor.py:672. rally_exhaustion is the exception:
it sets `strategy_book_notional=20` directly (rally_exhaustion_live.py:187) so it bypasses the
equity-frac branch entirely.

---

## Block: xs_momentum (LIVE momentum rebalancer — FIRES, short leg blocked)

Engine: hermes_trader/agents/xs_momentum_live.py. Rebalances every hold_days.
Last live rebalance 2026-06-26 21:26 UTC: `target 4L/4S; open 4L+4S, close 0`.
The 4 shorts were ALL blocked by the executor $50M short floor (ENA/NEAR/TAO/kPEPE, thin markets).
`.xs_momentum_positions.json` currently `{"longs": [], "shorts": []}`.

| key | value | read at | controls | verdict |
|-----|-------|---------|----------|---------|
| enabled | true | xs_momentum_live.py:282 | master on/off for the rebalancer | KEEP |
| lookback_days | 7 | xs_momentum_live.py:159 | trailing-return window for ranking | KEEP |
| hold_days | 5 | xs_momentum_live.py:284-286 | rebalance interval (gate on _last_ts) | KEEP |
| k_per_leg | 4 | xs_momentum_live.py:160 | names per long/short leg | KEEP |
| universe_top_n | 50 | xs_momentum_live.py:138 | candidate universe size by volume | KEEP |
| min_volume_usd | 5000000 | xs_momentum_live.py:137 | eligibility volume floor (falls back to min_market_volume_usd) | KEEP |
| residual | true | xs_momentum_live.py:180 | rank on BTC-neutral residual vs total return | KEEP (validated upgrade, edge_sweep4) |
| ranking | "pct_k" | xs_momentum_live.py:162 | ranker family (raw / z_ext / pct_k) | KEEP |
| zext_window | 14 | xs_momentum_live.py:163 | channel window for pct_k/z_ext | KEEP |
| beta_window | 30 | xs_momentum_live.py:161 | window for residual beta | KEEP |
| vol_gate | true | xs_momentum_live.py:311 | go flat in high BTC-vol regime | KEEP (validated dead-regime gate) |
| vol_short | 14 | xs_momentum_live.py:312 | short window for BTC vol regime | KEEP |
| vol_long | 90 | xs_momentum_live.py:312 | long window for BTC vol regime | KEEP |
| vol_managed.enabled | true | xs_momentum_live.py:293 | Moreira-Muir exposure scalar | RECONSIDER. enabled=true LIVE (config_store default is false). When on, scales notional by target_vol/realized_vol. Verify history (`.xs_volmgd_history` currently tiny: 2 bytes) is long enough; with near-empty history the scalar can be unstable. Flag for operator. |
| vol_managed.target_vol | (default 0.02) | xs_momentum_live.py:299 | per-period vol target | KEEP (only read when vol_managed on) |
| vol_managed.cap | (default 2.0) | xs_momentum_live.py:300 | max exposure scalar | KEEP (only read when vol_managed on) |

Note: live block omits `target_vol`/`cap` keys (relies on code defaults 0.02 / 2.0). Harmless;
add them explicitly if the operator wants to tune.

Book verdict: FIRES but HALF-INERT. Long leg attempts (no block warnings -> likely fills, attributed to
main-engine). SHORT leg is structurally dead in this universe: a market-neutral book that can never open
its short side is running net-LONG, not market-neutral. The $50M executor short floor vs the 5M eligibility
floor is the conflict — every short candidate the ranker surfaces is too thin to short. Either lower the
short floor for this book (validate first) or accept it is a long-only momentum book in practice.

## Block: extreme_fade (LONG-after-crash overlay — FIRES daily, blocked by long volume floor)

Engine: hermes_trader/agents/extreme_fade.py + extreme_fade_live.py. Signal module reads `crash_pct`
at extreme_fade.py:56.

| key | value | read at | controls | verdict |
|-----|-------|---------|----------|---------|
| enabled | true | extreme_fade_live.py:164, extreme_fade.py:52 | master on/off | KEEP. Note config_store default is False; operator enabled it live. |
| crash_pct | -0.12 | extreme_fade.py:56,64 | prior-day return threshold for the long leg (-12%) | KEEP |
| max_new_per_cycle | 2 | extreme_fade_live.py:202 | cap opens per cycle | KEEP |
| scan_interval_min | 30 | extreme_fade_live.py:168 | min minutes between scans | KEEP |
| entry_window_hours | (default 6.0) | extreme_fade_live.py:174 | freshness window after the crash bar | KEEP (read; not in live block, uses code default) |

Book verdict: FIRES, mostly BLOCKED. Today (2026-06-29) it flagged FOGO(-14.5%) on two cycles
(09:41, 10:12) and both were rejected: `market 24h volume $0.22M below floor $0.70M`. This is the
generic LONG market_volume floor ($0.70M), not a book-specific key — extreme_fade has no own volume
floor, so it inherits the global long floor. The signal works; the only realized episode is -1.01.
KEEP enabled but understand it rarely clears the floor on the micro-cap crashes it targets.

## Block: rally_exhaustion (SHORT — FIRES, mostly signals=0, btc-gated)

Engine: hermes_trader/agents/rally_exhaustion_live.py. Reads its sub-block via `cfg = config.get("rally_exhaustion")`.

| key | value | read at | controls | verdict |
|-----|-------|---------|----------|---------|
| enabled | true | rally_exhaustion_live.py:268 | master on/off | KEEP (default False; operator enabled live) |
| scan_interval_hours | 6 | rally_exhaustion_live.py:271 | min hours between scans | KEEP |
| entry_window_hours | 8 | rally_exhaustion_live.py:231 | freshness window after signal bar | KEEP |
| lookback_days | 2 | rally_exhaustion_live.py:214 | rally-measurement window | KEEP |
| threshold_pct | 12.0 | rally_exhaustion_live.py:215 | rally size that triggers a short | KEEP |
| btc_window | 20 | rally_exhaustion_live.py:216 | BTC regime window (gate to BTC-down) | KEEP |
| min_volume_usd | 20000000 | rally_exhaustion_live.py:217, 192 (fallback) | candidate volume floor AND fallback for the short floor override | KEEP but redundant-with-next (see below) |
| executor_short_volume_floor_usd | 20000000 | rally_exhaustion_live.py:191 -> executor.py:873 | per-book override of the executor short floor | RECONSIDER. Same value (20M) as min_volume_usd in this block; the code already falls back to min_volume_usd when this is absent (line 191-192). Carrying both at identical values is redundant within the book. |
| volume_window | 30 | rally_exhaustion_live.py:218 | bars for avg-volume calc | KEEP |
| hold_days | 5 | rally_exhaustion_live.py:168 -> hard_timeout_minutes | max hold (timeout) | KEEP |
| stop_pct | 25.0 | rally_exhaustion_live.py:166 -> backup_sl_pct_override (executor.py:1100) + max_loss/roe | wide spot stop | KEEP (validated: wide stop banks the squeeze, per memory) |
| notional_usd | 20.0 | rally_exhaustion_live.py:187 -> strategy_book_notional (executor.py:644) | per-trade size; bypasses equity-frac branch | KEEP |
| leverage | 1 | rally_exhaustion_live.py:167 -> leverage_override (executor.py:630) | leverage | KEEP |
| tp_scale_fraction | 0.0 | rally_exhaustion_live.py:190 -> tp_scale_fraction_override (executor.py:1152) | TP scale-out fraction (0 = none) | KEEP |
| max_new_per_cycle | 1 | rally_exhaustion_live.py:286 | cap opens per cycle | KEEP |
| history_bars | 40 | rally_exhaustion_live.py:219-220 | daily-candle history depth | KEEP |
| protect_pct | (default 1000.0) | rally_exhaustion_live.py:196 | DSL protect threshold (effectively off) | KEEP (read; uses code default) |
| retrace_threshold | (default 1.0) | rally_exhaustion_live.py:197 | DSL retrace threshold | KEEP (read; uses code default) |

Book verdict: FIRES, currently DORMANT-by-design. Recent cycles (06-28/06-29) log
`btc_down=True signals=0 opened=0`. It opened 1 historically (2026-06-27 08:07, signals=1 opened=1),
net -0.62. The book is correctly gated (BTC-down required) and just has no qualifying rally in a down
tape. Not dead, not -EV on the evidence (n=1). KEEP and keep accumulating.

---

## DEAD KEYS (read nowhere)

None. Every key in all three blocks is read by its live module or the executor. The only "inert" keys
are conditional-branch keys whose enclosing feature is off:
- `strategy_book_equity_frac=0` makes its executor branch (executor.py:656) a no-op; the key is still
  read every strategy_book execute, so not dead, just disabled.
- `vol_managed.target_vol` / `vol_managed.cap` are only read when `vol_managed.enabled=true` (it is),
  but the live block omits them and relies on code defaults.

## INERT / IMPAIRED BOOKS

- xs_momentum: short leg STRUCTURALLY INERT. Market-neutral book whose short side is 100% blocked by
  the $50M executor short floor (every ranked short is sub-floor thin). Running long-only in practice.
  Highest-priority finding: this is not what a "market-neutral" rebalancer is supposed to do.
- extreme_fade: signal FIRES daily but micro-cap crash targets (FOGO $0.22M) routinely fail the global
  $0.70M long volume floor. Real but rarely-executing overlay. 1 episode, -1.01.
- rally_exhaustion: FIRES, dormant-by-design (BTC-down + 12% rally rarely co-occur). 1 episode, -0.62.
  Not impaired, just selective.

None warrant DISABLE on this evidence (all n<=1 realized, none shown -EV with significance). The
actionable impairment is xs_momentum's dead short leg, which is a floor-conflict, not a config-delete.

## CONSOLIDATE DUPLICATES (shared short-book defaults)

Confirmed identical values across ALL live short books (rally_exhaustion, crash_continue_div_short,
hail_mary_short, engulf_short, premium_fade_short):

| key | value (all books) | recommendation |
|-----|-------|----------------|
| min_volume_usd | 20000000 | hoist to a shared `short_book_defaults.min_volume_usd`; per-book override only when it differs |
| executor_short_volume_floor_usd | 20000000 | same; also redundant WITH min_volume_usd inside each book (code falls back min_volume_usd) |
| volume_window | 30 (4 books; hail_mary omits) | shared default |
| btc_window | 20 (4 books; hail_mary omits) | shared default |

Keys that legitimately VARY per book (keep per-book, do NOT consolidate):
- stop_pct: rally 25 / crash 20 / hail 12 / engulf 20 / premium 20
- threshold_pct, lookback_days, entry_window_hours, scan_interval_hours, notional_usd, leverage,
  history_bars, hold_days, tp_scale_fraction, max_new_per_cycle — book-specific by design.

Proposed shape: a top-level `short_book_defaults` block holding {min_volume_usd, executor_short_volume_floor_usd,
volume_window, btc_window}; each book's loader merges defaults then per-book overrides. That removes 16-20
duplicated lines across 5 books and makes a floor change a one-line edit instead of five. This is a
CODE change (loader merge), so analysis-only here — flag for the operator, do not edit live.

## Cross-book sizing note

`short_notional_usd=100` (executor.py:679) is dead-weight for these three books specifically: rally caps
itself at 20, xs short leg falls to 50, extreme_fade is long-only. It is the binding short cap only for
the OTHER short books / discretionary shorts that size larger. Keep, but the operator should know it does
not constrain any book audited here.
