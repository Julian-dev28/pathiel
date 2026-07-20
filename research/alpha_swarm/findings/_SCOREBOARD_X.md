# LANE X scoreboard — LIVE-BOOK DEEP DIVES (exit geometry, xs_momentum widening)

W-X1 exit_geometry: FRONTIER MAPPED — 65-70% win-rate target ALREADY met at validated baseline exits (F1 77.5/F2 76.0/F4 71.7% @25bps) at FULL EV; every win-rate-raising overlay (tight TP/trail) pays EV monotonically; breakeven locks are the anti-goal (crater win rate via -25bps scratches). Keep baseline exits.

W-X2 live_attribution (Part 1): EXECUTION GAP NOT SIGNAL GAP — settled knobs: pct_k(14)/equal-weight/no-overlay/low-vol-gate all confirmed by priors (ranker_ab, W-A5, A6, W-A6/D3/A16, C14). Live realized +$5.37/45d (long leg +$5.89, short -$0.52, 9/12 win) but the book NEVER ran as designed: shorts blocked 2 cycles ($50M->$20M floor), holds shredded to median 0.06d by main-engine DSL until e248c13 (07-19, AFTER the last rebalance — zero clean cycles yet), k=4/$50 legs. BUG: pnl_by_book.py:265 treats live xs_rebalance events as shadow (no 'shadow' key) -> xs fills invisible, attributed to main-engine.

W-X2-A xs_xyz_equities: **ROBUST (driver-named)** — 7d residual momentum vs xyz:XYZ100, k5/leg, H5, $250k floor: net25 +0.65%/rebal (ann +47%), OOS +0.18/+1.12 both +, null p=0.0055 (n=34), survives 50bps and $1M floor. Long leg +1.30%, short +0.31%. DRIVER: semis/memory supercycle (SNDK/INTC/AMD/MU longs vs HOOD/COIN/MSTR shorts); no-semis ablation collapses to +0.08% (p=0.25) — edge dies with the theme. pct_k14 does NOT transfer to equities (-0.24%). SPEC + pre-committed kill in findings (operator pre-authorized wiring). ~+$7/wk at current sizing.

W-X2-B xs_sector_buckets: REFUTED (confirms C14) — bucket rotation -0.16% net25 with OOS sign-flip; MEME-only dead (-0.22%, p=0.56); AI-only BLOCKED (4 names); DEFI noise. Side-finding MARGINAL: within-L1 +1.50% p=0.000 both halves + beats same-k control — read as liquid-majors-cleanliness not sector alpha; needs own pre-registered cell before any action.

W-X2-C xs_joint_universe: REFUTED (joint construction) — joint crypto+xyz book net25 +0.46% with OOS sign-flip (+1.09/-0.17), WORSE than both separates (crypto +0.83% Sh .288, xyz +0.65% Sh .217); crypto crowds xyz to ~18% of legs. Separate-books corr only +0.28 -> run BOTH separately for the diversification. Combo-never-beats-best-single again (A16/W-A6/D3).

W-X2-D xs_hold_sweep: MARGINAL-UPGRADE — every H in {3,5,10,20} +EV net25, both halves +, p<=0.0015 (n up to 111; strongest live-family re-confirmation to date). At live k=4, H10 DOMINATES H5: ann +118.8% vs +80.4%, Sharpe +0.589 vs +0.229, OOS +3.64/+2.89. H3 worst — never shorten. RECOMMEND: restore hold_days 10 (= the committed default; hot config drifted to 5). +$4.5/wk at current sizing for a 1-line config change.

W-X2-E stack_settled: VACUOUS BY PRIOR — no settled winner exists to stack (A6 vol-managed REFUTED, W-A5 weighting REFUTED, pct_k already settled). Live vol_managed.enabled=true contradicts A6 (currently inert, scalar 1.0) — flip OFF before history accumulates.
