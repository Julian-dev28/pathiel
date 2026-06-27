B1 hurst_regime_router: REFUTED — router lift -0.66% EV vs best un-routed baseline (mom-only), worse than random; VR routing mis-routes
B2 correlation_regime_gate: MARGINAL — sit-flat-on-top-quartile-corr-days lifts +0.16 annSharpe (helps weak h2 1.16->2.26) but cuts return, no DD reduction; inv-corr sizing inert (+0.01)
B3 vol_term_structure: MARGINAL — vol-ratio router is the ONLY OOS-robust mode (+0.124 EV lift over mom-only, +0.61 vs random) but h2 EV +0.003% (break-even); routing stitches two one-sided legs
B4 vol_targeting_overlay: MARGINAL — EWMA vol-targeting on neutral XS book is inert: Sharpe lift -0.087 for +1.7pp maxDD reduction (-24.0->-22.3%); helps h2 hurts h1
B5 realized_vol_mean_reversion: REFUTED — size-up-after-spike is wrong direction (-0.566 Sharpe lift); book likes CALM tape (low-vol tercile Sharpe 4.37 vs high 2.45); inverse only +0.14
B6 adx_gated_momentum: MARGINAL — ADX>25 lifts EV +0.162% and turns ungated momentum (sign-flip) into OOS-robust (h2 +0.16); but dud rate FLAT 47.4% (claimed dud-cut refuted)
B7 trend_ensemble_lookbacks: REFUTED — ensemble Sharpe lift -0.053 vs best single; edge is all in lb7 (1wk TSMOM, +0.779% EV OOS-robust, +0.76 vs random), longer LBs noise/negative dilute it
B8 momentum_12_1_reversal: REFUTED — 12-1 skip adds no consistent lift; XS Sharpe change inconsistent across L, TS robust in only 1/9 cells (multiple-comparison chance); positive=pre-existing XS edge not the skip
B9 vol_of_vol_regime: REFUTED — vov spike does NOT precede regime breaks (high-vov fwd5d BTC move 3.34% < low-vov 4.48%, opposite of claim); de-risk overlay only +0.09 Sharpe/+2.4pp DD = generic risk-off
B10 garch_jump_detection: MARGINAL — only up-jump-fade short robust (EV +1.29%, excess +2.56, both halves) = re-derives live rally_exhaustion via vol-scaled trigger, no NEW alpha; down-fade/continuation refuted
B11 drawdown_state_machine: INCONCLUSIVE — no robust state-conditional router (high-EV peak/correction states rare n=22/34 & OOS-fragile); clean sub-finding: XS book +EV in ALL states, bear-stable both halves 0.318/0.323
B12 half_life_OU_sizing: REFUTED — market-residual reversion is -EV (Sharpe -3.59, both halves neg); cross-section CONTINUES not reverts; half-life 'lift' +1.35 just shrinks a loser
B13 realized_skew_timing: ROBUST (fade-arming) — neg market-skew regime lifts extreme_fade EV +5.20->+7.85% (win 64->74%, both halves robust, 129/177 events); BUT crash-predictor sub-claim REFUTED (neg skew = BEST fwd bucket +0.42%). Survivor upper bound
B14 turn_of_month: REFUTED — ToM window at median of random DoM windows (empirical p=0.512); ToM long-market -0.22% EV, sign-flip; calendar noise
B15 regime_switch_HMM: MARGINAL — XS-book EV concentrates ~9x in turbulent HMM state, HOLDS OOS (test turb 0.907% vs calm 0.102%); but concentration ~= vol-scaling (Sharpe lift << EV ratio), turb state rare; up-size-in-turbulence candidate
B16 funding_momentum: BLOCKED-DATA — only a funding SNAPSHOT in dataset (BTC=-2.4e-06), trend needs data_logger time series (~1-2wk); stub+self-test built (3 cases PASS), ready for the feed
