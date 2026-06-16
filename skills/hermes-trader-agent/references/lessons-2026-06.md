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
Regime-dependent (trend-ride wins in *sustained* up-trends) → `regime_aware` exists
but is gated OFF until a trend sample proves it. See `exit-engine.md`.

## 3. The AI is a GOOD FILTER set TOO STRICT — loosen, don't bypass
AI-replay (real verdicts, 4 modes): sidestep-the-AI = WORST (−$20/38%); force its
rejects = mediocre (adverse selection — it rejects them for a reason); **lower its
conf bar = BEST** (+$40/24h). AI PASS'd 87% of everything (16797/19218 verdicts).
Fix: `min_ai_confidence` 0.78→0.65, `composite_force_execute` OFF. And feed signals
INTO the prompt (`research.py`) — for months they only fed the executor, so the AI
decided blind. NEVER guarantee returns; never lever an unproven edge.

## 4. Leverage amplifies edge, it does NOT create it
High-lev cascade (15x, all-long) = a 2% dip → all positions stop together (the
−$23 morning). Cut to 10x (whole-system backtest peak). **High-lev + loose-stops
REFUTED** — loose stops lost at every leverage. The ROE cap (18%) means high lev =
tight spot-stop = noise-stops (18/15 = 1.2% spot).

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
