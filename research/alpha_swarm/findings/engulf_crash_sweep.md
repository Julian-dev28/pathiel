# engulf_crash_sweep — config sweeps through the live grader

Same method as premium_fade: replay each book's live recording over the dataset, grade every
config via shadow_ledger.classify, time-OOS via sorted records.

## engulf_short — robustly VALIDATED, regime-robust (NO change)
Every config VALIDATED (n=781). Live (body1.0/hold1/all): m25 +1.15, OOS +2.16/+0.39.
KEY: STRONGER in BTC-UP (+2.01, OOS +3.08/+1.20) than down (+0.80) -> resolves the Lane C2
"down-tape-only" risk; the edge is regime-robust. hold 2-3 looks marginally better (+1.76/+2.67)
but Lane C2 flagged hold-2 fragility, so keep hold=1 (don't churn a thin-but-solid edge).

## crash_continue_div_short — BTC-up gate essential; stop 8->20 (CHANGED)
- The BTC-UP gate is LOAD-BEARING: up-regime all VALIDATED (+6..+13); down-regime mostly REFUTED
  (-0.1..-2.4). Shorting -8% drops only works while the market RISES (divergent weakness). Validates
  the live gate.
- The tight 8% stop is suboptimal: 20% beats 8% in EVERY up cell (-8%/h10 +6.26->+7.60; -12%/h10
  +10.24->+13.30) — the stop-width lesson ([[feedback_sweep_stop_width]]): tight stop is shaken out
  before the continuation. => changed live shadow config stop_pct 8->20 (hot-read, reversible).
  (Note: extreme_surface originally specified 8% for this cell; this broader sweep + the stop-width
  lesson favor 20. Reversible shadow change; forward shadow decides.)
- Deeper threshold stronger but thinner (-12% +13.30 n=87 vs -8% +7.60 n=295); kept -8% for coverage.

Both still survivor upper bounds, shadow-only. See [[project_engulf_short]], [[project_crash_continue_div_short]].
