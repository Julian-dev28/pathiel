# Alpha-method queue — hermes-trader swarm (3-lane continuous cycle)

Pull from the top of YOUR lane. Each entry = one test against `dataset.json` via `alpha_lib.py`,
obeying `SWARM-RULES.md` (lookahead-safe / OOS both-halves / slippage sweep 0-50bps /
stop-width sweep for any fade-or-squeeze / survivorship = positive is an UPPER BOUND).
Write `findings/<id>.md` with a VERDICT and append one line to your lane scoreboard.
**Refuting cleanly is a WIN. The −44% BTC tape means raw-long drift is negative — always score an
edge as EXCESS over a matched random-entry baseline (same side/stop/horizon/regime), like
extreme_surface did, or you will fool yourself.**

Status: ⬜ queued · 🔬 running · ✅ robust · ➖ marginal/shadow · ❌ refuted
Data: 🕯️ candles-only · 💰 needs data_logger funding/OI (~1-2wk) · 🌐 needs external feed

## WAVE 1 — done
- ❌ vol_compression · ❌ regime_basket · ❌ btc_leadlag · ❌ overnight_intraday
- ➖ seasonality (Thursday-short marginal) · ❌ xs_reversal
- ✅ extreme_surface — both live edges CONFIRMED; new shadow candidate `crash_continue_div_short`; deep-crash long lever

## PROMOTED (operator sign-off required before any LIVE flip — shadow only is self-approve)
- `crash_continue_div_short` — NEW cell, build a shadow logger.
- deep-crash long tier (−20/−25%, 20% stop, 3d) — satellite-size lever on live extreme_fade.

═══════════════════════════════════════════════════════════════════════
## LANE A — cross-sectional / factor / stat-arb  (agent `laneA`)
═══════════════════════════════════════════════════════════════════════
A1 ⬜ 🕯️ `pca_residual_reversion` — strip top 1-2 PCs (market+sector) from the daily return matrix, mean-revert the idiosyncratic residuals (long most-neg / short most-pos). The principled pairs. Sweep lookback{20,40,60}, n_pc{1,2,3}, hold{1,2,3}.
A2 ⬜ 🕯️ `tsmom` — time-series (absolute) momentum, each coin long if own trailing-L return>0 else short, vol-scaled. L{7,14,30,60}, hold{3,7,14}. Distinct factor from the live XS book — could be additive.
A3 ⬜ 🕯️ `lottery_skew_premium` — short top-decile MAX-daily-return / realized-skew, long bottom-decile, weekly rebal, market-neutral. Control for momentum overlap.
A4 ⬜ 🕯️ `low_beta_anomaly` — estimate beta-to-BTC, long low-beta / short high-beta, leverage-neutralized (BAB factor).
A5 ⬜ 🕯️ `idiosyncratic_vol_anomaly` — rank by residual vol after stripping BTC beta; low-idio-vol long / high short.
A6 ⬜ 🕯️ `vol_managed_momentum` — scale the live XS-momentum book exposure by inverse realized vol (Barroso momentum-crash protection). Measure Sharpe lift over un-scaled.
A7 ⬜ 🕯️ `momentum_of_momentum` — rank coins by the SLOPE of their own momentum (accelerating trends); long accelerating / short decelerating.
A8 ⬜ 🕯️ `dispersion_mean_reversion` — when cross-sectional return dispersion hits an extreme percentile it reverts; trade convergence (compress the spread between leaders and laggards).
A9 ⬜ 🕯️ `cointegration_triplets` — 3-coin cointegrated baskets (vs refuted 2-coin pairs); trade the basket residual's mean reversion. Watch survivorship hard.
A10 ⬜ 🕯️ `rsi_extreme_xs` — cross-sectional: long the basket of most-oversold RSI(14) coins / short most-overbought, daily rebal, regime-gated.
A11 ⬜ 🕯️ `connors_rsi_fade` — use the existing connors_rsi indicator concept; cross-sectional fade of CRSI extremes.
A12 ⬜ 🕯️ `beta_rotation` — high-beta basket in BTC-up regime, low-beta basket in BTC-down; measure vs static.
A13 ⬜ 🕯️ `relative_strength_drawdown` — long survivors trading X% off their N-day high while BTC up (drawdown-recovery), cross-sectional.
A14 ⬜ 🕯️ `granger_leadlag_network` — beyond BTC: estimate a lead-lag graph across all 40 coins, trade consistent followers of consistent leaders. Cost-brutal; report decay.
A15 ⬜ 🕯️ `carry_plus_trend` 💰 — combine funding-carry with price-momentum (the two strongest cross-sectional factors in perp markets). Needs data_logger.
A16 ⬜ 🕯️ `factor_ensemble` — combine the surviving Lane-A factors (momentum + skew + low-beta + carry) into one vol-weighted market-neutral book; test diversification lift over the best single factor.

═══════════════════════════════════════════════════════════════════════
## LANE B — time-series / volatility / regime  (agent `laneB`)
═══════════════════════════════════════════════════════════════════════
B1 ⬜ 🕯️ `hurst_regime_router` — per-coin Hurst/variance-ratio; route momentum to trending coins (H>0.5), reversion to reverting (H<0.5). Measure lift over un-routed.
B2 ⬜ 🕯️ `correlation_regime_gate` — rolling avg pairwise correlation; size the live XS book by 1/corr-regime (dispersion dies when everything moves together). Measure Sharpe lift.
B3 ⬜ 🕯️ `vol_term_structure` — short-RV/long-RV ratio as a switch: spike→fade, compression→trend. Use to route between the fade and momentum books.
B4 ⬜ 🕯️ `vol_targeting_overlay` — forecast next-period vol (EWMA/GARCH-lite), scale total book exposure to constant risk. Meta-overlay; measure drawdown + Sharpe vs flat sizing.
B5 ⬜ 🕯️ `realized_vol_mean_reversion` — vol itself is mean-reverting; trade the implied direction (size up after vol-spike fades, down into compression). Sizing edge, not direction.
B6 ⬜ 🕯️ `adx_gated_momentum` — only take momentum/trend entries when ADX>threshold (genuine trend present); measure dud-rate cut on the live entries.
B7 ⬜ 🕯️ `trend_ensemble_lookbacks` — ensemble of TSMOM lookbacks {1w,2w,1m,3m} voting; smoother than a single lookback. Test vs best single.
B8 ⬜ 🕯️ `momentum_12_1_reversal` — classic 12-1: long trailing-(L minus last-month), skipping the most recent window to dodge short-term reversal. Cross-sectional + time-series variants.
B9 ⬜ 🕯️ `vol_of_vol_regime` — second-order vol; does a vol-of-vol spike precede regime change / trend break? Use as a de-risk trigger.
B10 ⬜ 🕯️ `garch_jump_detection` — bipower/realized-range jump detector; classify bars as jump vs diffusion, trade the post-jump drift/reversion separately.
B11 ⬜ 🕯️ `drawdown_state_machine` — define market drawdown states (peak / correction / bear / recovery) from BTC equity curve; measure which live edge pays in which state. Router, not signal.
B12 ⬜ 🕯️ `half_life_OU_sizing` — fit Ornstein-Uhlenbeck to mean-reverting residuals, size by estimated half-life (faster reversion = bigger). Bolt onto pca_residual_reversion if it survives.
B13 ⬜ 🕯️ `realized_skew_timing` — market-level realized skew as a crash predictor; de-risk longs / arm the fade when aggregate skew goes extreme-negative.
B14 ⬜ 🕯️ `turn_of_month` — turn-of-month / first-N-days effect (institutional flows). Calendar; multiple-comparison-gate hard like seasonality did.
B15 ⬜ 🕯️ `regime_switch_HMM` — fit a 2-3 state HMM on BTC returns+vol; measure whether each live edge's EV concentrates in a state, enabling a regime-conditional size multiplier.
B16 ⬜ 🕯️ `funding_momentum` 💰 — funding-rate TREND predicts price (persistent funding = persistent pressure). Needs data_logger.

═══════════════════════════════════════════════════════════════════════
## LANE C — microstructure / behavioral / event / exotic  (agent `laneC`)
═══════════════════════════════════════════════════════════════════════
C1 ⬜ 🕯️ `oi_divergence` 💰 — price↑+OI↑=continuation, price↑+OI↓=short-covering fade. The NEW-DATA frontier. Needs data_logger; until then stub the logic + unit-test on synthetic.
C2 ⬜ 🕯️ `liquidation_cascade_fade` — 5m forced-liquidation signature (violent wick + volume spike + range>N×ATR); fade the overshoot. Intrabar cousin of extreme_fade. Sweep entry delay + stop width.
C3 ⬜ 🕯️ `volume_divergence` — price-trend vs volume-trend divergence; standalone entry AND as a dud-filter bolted on the live momentum/fade entries.
C4 ⬜ 🕯️ `wick_rejection` — large lower-wick rejection→long / upper-wick→short on 1h/4h; sweep wick/body ratio + stop. Run the MC null.
C5 ⬜ 🕯️ `nday_high_breakout` — slow positional 52-wk-high analog: long new N-day high {20,50,100} with WIDE stop + BTC-up gate. Distinct from the refuted intraday breakout.
C6 ⬜ 🕯️ `round_number_magnet` — reversion toward / rejection at psychological round levels (power-of-ten, whole-dollar). Behavioral; high prior of refute.
C7 ⬜ 🕯️ `opening_range_breakout` — define a daily UTC session-open range, trade the break with regime gate.
C8 ⬜ 🕯️ `vwap_reversion` — intraday VWAP deviation mean-reversion on 5m; cost-brutal, report decay vs slippage.
C9 ⬜ 🕯️ `engulfing_reversal_xs` — candlestick engulfing/3-bar-reversal as a cross-sectional ranking signal; almost certainly refuted, prove it.
C10 ⬜ 🕯️ `nr7_range_compression` — NR7/NR4 range-compression then directional follow-through; sweep direction by regime.
C11 ⬜ 🕯️ `gap_fill` — daily-boundary "gaps" from low-liquidity hours; probability and EV of the gap filling vs running.
C12 ⬜ 🕯️ `entropy_predictability_filter` — permutation-entropy per coin; only signal on low-entropy (predictable) coins. Meta-filter; measure dud-cut on a Tier-1 edge.
C13 ⬜ 🕯️ `obv_vpt_slope` — on-balance-volume / volume-price-trend slope ranking as a cross-sectional flow proxy.
C14 ⬜ 🕯️ `sector_rotation` — hand-tag the 40 coins by sector (L1/meme/DeFi/AI/infra), trade intra-sector relative value + sector momentum.
C15 ⬜ 🕯️ `sympathy_followthrough` — when a sector LEADER makes a big move, does the laggard follow next bar? Event-study + tradeable rule.
C16 ⬜ 🕯️ `montecarlo_null_harness` — NOT an alpha: build the reusable shuffled-label / block-bootstrap null every test bolts on. Attacks the "p on the multiple-comparison edge" weakness directly. Import it from then on.

═══════════════════════════════════════════════════════════════════════
## WAVE 2 — refill (re-dispatched as lanes empty)
═══════════════════════════════════════════════════════════════════════
### Lane B Wave-2 (vol/regime — Wave-1 found B13 skew-arm ROBUST; combine survivors + new angles)
W-B1 ⬜ 🕯️ `survivor_stack` — combine the Wave-1 survivors into ONE overlay on the live XS-momentum book:
  skew-regime arm (B13) + turbulence-upsize (B15 HMM) + ADX>25 gate (B6). Does the STACK beat the best
  single overlay and the un-overlaid book on OOS Sharpe? Watch for overfitting (3 gates on one sample).
W-B2 ⬜ 🕯️ `skew_arm_forward_spec` — pin down B13 exactly: the precise neg-skew threshold + lookback that
  maximizes the within-universe regime split (neg vs pos), and whether it's robust to the skew window. This
  becomes the shadow-wire spec for a skew filter on extreme_fade.
W-B3 ⬜ 🕯️ `semivol_risk_targeting` — risk-target the book on DOWNSIDE semideviation instead of total vol;
  does penalizing only downside vol beat symmetric vol-scaling on Sharpe/drawdown?
W-B4 ⬜ 🕯️ `efficiency_ratio_gate` — Kaufman efficiency ratio (net move / path length) as a trend-QUALITY gate
  on momentum entries; cut choppy-path names. Measure dud-rate + Sharpe lift over ADX gate.
W-B5 ⬜ 🕯️ `regime_age_timing` — measure BTC up/down regime PERSISTENCE (run-length distribution); does entry
  timing by regime AGE (fresh vs stale regime) change momentum/fade EV? Survivor-safe event study first.
W-B6 ⬜ 🕯️ `cross_asset_vol_spillover` — does BTC realized vol lead alt realized vol (vol clustering across the
  cross-section)? If so, a BTC-vol-based sizing signal pre-positions the book. Sizing edge, not direction.
W-B7 ⬜ 🕯️ `turbulence_upsize_spec` — pin B15: is the high-vol-state EV concentration real Sharpe lift or just
  vol-scaling restated? Build the size multiplier, compare Sharpe vs plain inverse-vol sizing. If it's only
  vol-scaling, REFUTE the "turbulence alpha" framing explicitly.

### Lane A Wave-2 (factor — Wave-1 found A13 relative-strength-drawdown ROBUST but 0.7-corr w/ live book)
W-A1 ⬜ 🕯️ `a13_orthogonality` — **THE decider.** Is A13 (long nearest-50d-high / short deepest-drawdown,
  market-neutral) NEW capacity or a re-expression of the live XS-momentum book? Regress A13 per-rebal returns
  on the XS-momentum book returns → residual alpha (report t-stat) + return correlation + combined-book Sharpe
  vs each alone, OOS both halves. VERDICT new-capacity ONLY if combined Sharpe > best single AND residual
  alpha > 0 both halves. If not, A13 is the same factor wearing a different hat — say so.
W-A2 ⬜ 🕯️ `proximity_high_decomp` — A13's long leg (nearest-50d-high) and Lane-C `nday_high_breakout` both flag
  proximity-to-high. Decompose A13: long-leg-only vs short-leg-only vs combined. Is the edge the long
  proximity-to-high signal, the short deep-drawdown signal, or only the spread? This tells us which half to wire.
W-A3 ⬜ 🕯️ `short_leg_is_beta` — is shorting the deepest-drawdown basket +EV on its own or just down-beta in the
  −44% tape? Score the short leg as EXCESS over matched random-short; if it's beta, the L/S book's short side is
  a regime bet not alpha (mirror the A2/A4 short-tilt caveat).
W-A4 ⬜ 🕯️ `idio_momentum_residual` — XS-momentum on BTC-beta-RESIDUALIZED returns (strip market beta first).
  Does pure idiosyncratic momentum beat raw-return momentum on Sharpe + reduce the down-beta confound?
W-A5 ⬜ 🕯️ `rank_weighting_schemes` — implementation alpha: rank-weight vs equal-weight vs inverse-vol-weight the
  live XS-momentum book. Which weighting maximizes OOS Sharpe net of turnover/fees? Cheap, directly actionable.
W-A6 ⬜ 🕯️ `factor_combo_v2` — combine the genuine survivors (XS-momentum + A13-residual-if-orthogonal + B13
  skew-arm) into one vol-weighted market-neutral book; report diversification lift over the best single. Only
  include A13 if W-A1 says it's orthogonal — else this is double-counting one factor.

### Lane C Wave-2 (microstructure — Wave-1 found C9 engulfing ROBUST + C5 nday-high MARGINAL; harden them)
W-C1 ⬜ 🕯️ `engulf_spec` — pin C9 into a shadow-wire spec: best engulf definition (body-ratio, prior-bar
  overlap, optional gap), best hold (1 vs 2 days), and whether a volume-confirm filter sharpens it. Output the
  exact entry rule + the MC p-value at the chosen spec.
W-C2 ⬜ 🕯️ `engulf_leg_decomp` — split C9 into long-bullish-engulf-only vs short-bearish-engulf-only. Score
  each as EXCESS over a matched SAME-SIDE null (the −44% tape flatters shorts). Which leg is real, which is beta?
W-C3 ⬜ 🕯️ `engulf_orthogonality` — is C9 additive to the live XS-momentum book, or a fast 1-day momentum
  restatement? Return correlation + residual-alpha t-stat vs the live book (same method as W-A1).
W-C4 ⬜ 🕯️ `candle_pattern_family` — is engulf special or is the whole 2-bar reversal family alive? Test harami,
  piercing/dark-cloud, hammer/shooting-star cross-sectional under the same null. If only engulf survives, that's
  a flag for overfitting; if the family survives, it's a real reversal effect.
W-C5 ⬜ 🕯️ `entropy_on_engulf` — bolt C12's permutation-entropy filter onto C9 (Lane-C suggested this); does
  restricting to low-entropy names lift the engulf edge at the now-higher n? Measure dud-rate cut.
W-C6 ⬜ 🕯️ `engulf_1h` — does the engulf edge exist on 1h candles (≫ samples) or only daily? More n = a cleaner
  p-value, but watch fees (1h re-trades more). Report net-of-25bps EV + OOS halves.

═══════════════════════════════════════════════════════════════════════
## TIER 4 — parked (needs feeds not wired): macro_event_drift 🌐 · news_catalyst_reaction 🌐 (free_signals_suite exists) · perp_spot_basis · gex_maxpain_crypto · liquidation_heatmap
═══════════════════════════════════════════════════════════════════════

### Cycle protocol
Each lane agent works its list top-to-bottom, ONE item at a time, writes findings/<id>.md +
appends to findings/_SCOREBOARD_<lane>.md, then moves on WITHOUT keeping prior scripts in context.
When a lane empties or the agent comes to rest, the orchestrator synthesizes, generates fresh
hypotheses from what survived, refills the lane, and re-dispatches. ✅ robust → shadow-deploy
proposal (operator sign-off before any live flip). This loop is meant to run for hours.
