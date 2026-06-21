# Lessons — 2026-06 (a hard session; what's actually true)

Hard-won during a session that started −21% intraday and recovered. Read before
touching config.

## 1. The #1 leak is OVERFITTING VIA RAPID ITERATION
~10 config changes in a few hours on n<30 single-regime samples = fitting noise,
not optimizing. Every "validated" change was a hypothesis on one 14-day chop
window. **Freeze the config; gather n≥50 clean closes; THEN optimize (walk-forward,
slippage-adjusted). Sooner = malpractice.**

## 2. Exits: TIGHT beats LOOSE (in chop)
Controlled backtest: scalp (protect 1.5/retrace 0.30) = 61% win / +$1518 vs
trend-ride (3.0/0.55) = 47% / −$757. Loose lets winners give it back. Live = scalp.
Regime-dependent trend-ride was tested as a historical experiment, but the live
config block has been removed. See `exit-engine.md`.

## 3. The AI is a GOOD FILTER set TOO STRICT — loosen, don't bypass
AI-replay (real verdicts, 4 modes): sidestep-the-AI = WORST (−$20/38%); force its
rejects = mediocre (adverse selection — it rejects them for a reason); **lower its
conf bar = BEST** (+$40/24h). AI PASS'd 87% of everything (16797/19218 verdicts).
Fix: `min_ai_confidence` 0.78→0.67 (live), broad force paths removed. And feed signals
INTO the prompt (`research.py`) — for months they only fed the executor, so the AI
decided blind. NEVER guarantee returns; never lever an unproven edge.

## 4. Leverage amplifies edge, it does NOT create it
High-lev cascade (15x, all-long) = a 2% dip → all positions stop together (the
−$23 morning). Cut to 10x (whole-system backtest peak). **High-lev + loose-stops
REFUTED** — loose stops lost at every leverage. The ROE cap (now 15%) means high
lev = tight spot-stop = noise-stops (15/12 = 1.25% spot) — which is exactly why
2026-06-21 widened the Phase-1 stop with `atr_stop` (lesson 8 below).

## 5. max_concurrent is a NO-OP; the GROSS-NOTIONAL cap binds
Portfolio backtest: max_concurrent 3→15 = identical (gross cap fills after ~3
positions, blocking 17/21 candidates). Deploy more via **CAPITAL, not leverage**
(gross leverage adds correlated-cascade tail risk a calm sample can't measure).

## 6. Slippage is unmodeled in backtests; thin books bleed
Live `fill_px` is the only truth. xyz HIP-3 thin books slip ~12.5bps median vs
~5bps crypto (max 176bps — eats an entire scalp move). Now instrumented
(`entry_slip_bps`). At n≥50 → per-coin slippage kill-list.

## 7. Operational
- **Full-universe scan must be FAST or it trips the 600s watchdog** (re-exec loop).
  Use the **rotating sweep** (`HERMES_UNIVERSE_SWEEP`): fast ~14s cycles, full
  coverage over ~22 cycles, storm-free. NOT one giant slow scan.
- **SOD logic is restart-safe** (persists, re-baselines only at UTC midnight) —
  don't "fix" it.
- **Position leverage is fixed at ENTRY** — config changes apply to new entries only.
- **Validate before you "fix"** — this session nearly "fixed" the SOD reset and a
  leverage "bug" that were both correct behavior.

## 8. Exits, refined (2026-06-21): WIDE stop + TIGHT trail
The early-June "tight stop" lesson was about the *trail*, not the Phase-1 *stop*.
The 0.4%/3% Phase-1 stop was itself a −EV noise-band leak (whipsawed volatile
movers: EIGEN stopped in 1min then ran +5%). Fix: `atr_stop` ON (1.5× ATR, 1–2.5%)
to ride through noise, AND tighten the trail to `retrace_threshold=0.10` to bank
give-backs. Validated live: AERO rode +10% on the wide stop; JUP banked +16%/+12%
ROE on serial 0.10-trail exits. **Wide stop (ride noise) + tight trail (bank the
give-back) is the stack.**

## 9. We miss parabolic low-liq runners ON PURPOSE — chasing them is −EV
The "why did we miss TNSR +70%?" autopsy (`scripts/edge_extension.py`): it's a
UNIVERSE/liquidity latency, not a gate bug — TNSR entered our scan universe only at
+44% (sub-$700k volume before its pump). And the backtest says momentum-continuation
is +EV ONLY in the 20–30% extension band (peak), −EV above 30% (so the 30% extension
cap correctly blocked the +70% chase). **Measured, not inferred:** low-liquidity coins
at 20–30% are **−1.27% GROSS** (they reverse — pump-and-dumps). So do NOT lower
`min_market_volume_usd` to catch them earlier; the floor filters a −EV pool. The
ONE +EV lever from this: `late_chase_relax` admits the liquid 20–30% pocket the
runner gate used to block (+0.15–0.20%/t, OOS-robust). Thin but real.

## 10. Capital-rotation is near-inert; early-runner has no tradeable tell
`scripts/edge_rotation.py`: rotating capital into capital-blocked movers is
inert at the safe threshold and −EV when forced (the blocked movers don't beat what
they'd evict). Held in SHADOW. `scripts/edge_runner_v2.py`: NO pre-breakout precursor
beats noise (thrust/coil/rising-vol all −EV, false-positive ~99.5%) — "no early tell,
only the breakout" is the honest finding. Don't rebuild either without genuinely new
features (order-flow/CVD, not more OHLCV math).
