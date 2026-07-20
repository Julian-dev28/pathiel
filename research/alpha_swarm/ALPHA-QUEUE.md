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
## LANE D — DATA FRONTIER (funding/OI)  (agent `laneD`) — the post-candle pivot
═══════════════════════════════════════════════════════════════════════
Candle-space is saturated. THE frontier is data the candle agents never had. HL exposes REAL hourly
funding history → `funding.json` (built via lib/build_funding_dataset.py, loader `funding_lib.py`).
Load price via `alpha_lib`, funding via `funding_lib`, align by timestamp. OI is still snapshot-only
(data_logger accruing) → OI items stay BLOCKED-DATA. Same gates: lookahead-safe, OOS both-halves,
slippage sweep, EXCESS over matched null, mc_null p-value. Funding is small per-hour — a carry edge must
clear fees; report net. Survivorship still applies (today's liquid set = upper bound).
D1 ⬜ 💰✅ `funding_carry` — market-neutral: short top-positive-funding / long top-negative-funding coins,
  vol-weighted, rebal each funding epoch / daily. Collect the carry while delta-neutral. The canonical perp trade.
D2 ⬜ 💰✅ `funding_momentum` — does the funding TREND (persistent positive/negative) predict the next price move?
  Persistent funding = persistent directional pressure. Long persistent-neg-funding / short persistent-pos.
D3 ⬜ 💰✅ `carry_plus_trend` — combine funding-carry with price-momentum (the two strongest perp factors). Does
  the combo beat either alone (diversification / confirmation)? This was A15, finally unblocked.
D4 ⬜ 💰✅ `funding_extreme_reversion` — extreme funding = crowded positioning. Does a funding spike (top decile
  |rate|) precede a price REVERSAL (fade the crowded side)? Funding as a contrarian sentiment gauge.
D5 ⬜ 💰✅ `basis_premium_signal` — the `premium` field (perp vs oracle) as a basis signal distinct from funding;
  trade premium extremes expecting convergence.
D6 ⬜ 💰✅ `funding_price_divergence` — price up + funding down (shorts paying) or price down + funding up =
  positioning/price divergence; does the funding side win?
D7 ⬜ 💰 `oi_divergence` / `oi_buildup` — data accrued (logger running since 2026-06-26); SUPERSEDED by the pre-registered W-F4 quadrant protocol — run frozen `hypotheses/W-F4.py` on 2026-07-30, do NOT peek early.

═══════════════════════════════════════════════════════════════════════
## TIER 4 — parked (needs feeds not wired): macro_event_drift 🌐 · news_catalyst_reaction 🌐 (free_signals_suite exists) · perp_spot_basis · gex_maxpain_crypto · liquidation_heatmap
═══════════════════════════════════════════════════════════════════════

### Cycle protocol
Each lane agent works its list top-to-bottom, ONE item at a time, writes findings/<id>.md +
appends to findings/_SCOREBOARD_<lane>.md, then moves on WITHOUT keeping prior scripts in context.
When a lane empties or the agent comes to rest, the orchestrator synthesizes, generates fresh
hypotheses from what survived, refills the lane, and re-dispatches. ✅ robust → shadow-deploy
proposal (operator sign-off before any live flip). This loop is meant to run for hours.

## Wave W-E/F/G/H follow-ups (2026-07-09 creative swarm)
- **W-F4 OI x price quadrants**: PRE-REGISTERED, frozen thresholds — re-run `hypotheses/W-F4.py` UNCHANGED on 2026-07-30 (comfortable 2026-08-19). No tuning allowed.
- **crash_continue_hourly** (Lane H spec): hourly idio-flush SHORT continuation arm — post-hoc inversion (mc_p 0.048, n=23), needs a shadow recorder before any trust. Spec in findings/W-H3.md.
- **W-E2 open-reversal MOMO-RESID** (MARGINAL shadow-wire candidate, both MC nulls <0.05 but thin @25bps): consider a shadow recorder next wave; spec in findings/W-E2_open_reversal.md.
- **W-E1 weekend gap-fade**: MARGINAL at n=31 weekends — accrues ~1 episode/week passively; re-score after ~15 more weekends (~2026-10-20).
- **Lane G follow-ups**: AI long anti-calibration (0.70-0.80 band −2.13%@24h) — candidate config lever AFTER a second window confirms (raise long conf floor / down-weight AI longs vs shorts); killswitch counterfactual weakly negative (n=29, p=0.156) — re-examine at n>=60.
- **WIRED 2026-07-09**: funding_spike_short shadow book (W-F2A VALIDATED, +6.2%/ep, p=0.0027); thin_short_relax shadow recorder in executor (W-G1, +1.12% counterfactual, p=0.001; promotion bar >=30 entries +EV net 25bps).

## W-N3 clean-epoch verdict — due 2026-07-26 — DECIDED EARLY 2026-07-16
Outcome: REFUTED (-7.33%/sig, 42 resolved, both OOS halves negative) ->
news_catalyst.shadow_only=true flipped same day per standing order; EDGAR stays unbuilt.
News-catalyst ledger RESTARTED 2026-07-12 (relevance bug fixed 18d596c: symbol
presence mandatory, equity queries for xyz:). Every pre-fix read is tainted —
archived at hypotheses/W-N_tainted_news_ledger_pre_20260712.jsonl, NOT to be
graded as evidence. Decision on 2026-07-26 (or sooner at n>=15 breaking reads):
- EV25(breaking) > 0 AND > EV25(non-breaking) -> news book keeps its live arm
  AND build the EDGAR full-text layer for xyz equities (8-K/earnings triggers,
  free, ~half-day; spec sketch: poll EDGAR full-text search API for the xyz
  ticker set, map filings to coins, record + gate exactly like breaking reads).
- Otherwise -> news_catalyst.shadow_only=true and EDGAR stays unbuilt.

## W-P public-record latency (EDGAR -> xyz perps) — 2026-07-19
- **W-P1 EDGAR acceptance-timestamp latency: MARGINAL** (findings/W-P1_edgar_latency.md,
  pre-registered). Reaction ROBUST: xyz perps move ~2.3x same-coin random-time null
  around 8-K acceptance (+1h/+4h/+24h all p=0.0005, both OOS halves, session-matched
  null too; n=308 events, 95% after-hours, median entry gap 0.82h). Signed capture
  REFUTED: long-all and first-reaction-momentum both net-negative @25bps and OOS
  sign-flip — direction is not in the timestamp. 6-K bucket dead (routine paperwork).
  Facts bank: 52/87 live xyz tickers EDGAR-covered (coverage table in
  W-P1_cache_cik_map.json); data.sec.gov acceptanceDateTime is TRUE UTC (histogram-
  verified); xyz DOES trade 24/7 — universe.py:42 off-hours claim is wrong, fix docs.
  Recorder: NO-GO (history refetchable, mechanical rules refuted).
- **W-P2 scheduled-catalyst playbook: REFUTED, playbook build NO-GO**
  (findings/W-P2_scheduled_catalyst.md, pre-registered, run 2026-07-19).
  Systematic enumeration — ALL 2,325 floor votes 2024-01->2026-07 (clerk XML +
  GovTrack/LIS) through a pre-committed keyword filter with bill-title acronym
  enrichment -> 89 hits -> 23 events (16 congressional, 5 SEC orders, 2
  executive; marquee list fell out naturally, filter ADDED the DeFi-broker
  CRA pair + failed Crypto-Week rule vote; zero crypto floor votes in 2026).
  Outcome-signed BTC+ETH basket net 25bps NEGATIVE at every horizon
  (+1h -0.28% / +4h -0.16% / +24h -0.59% / +72h -1.11%; already -0.34%
  at ZERO cost @24h) and — deeper — NO abnormal unsigned movement vs 2000x
  random-time null (p_unsig 0.21-0.69): scheduled US crypto policy resolves
  into majors price continuously, not at the gavel. Odds-conditioned primary
  cell (surprise vs Polymarket T-24h) INCONCLUSIVE by locked n<8 rule:
  vote-level markets existed for only 4/23 events (n=2 contested: +1.16%
  @24h, p=0.23, weaker under press timestamps). Anti-signal on the 4
  bullish-BLOCKED events (shorts -4.1%/ep @24h, market rallied through
  every policy failure) is macro-regime coincidence (2025-05-08 ETH squeeze
  ate the cloture-fail short), n=4, exploratory only — do NOT invert-trade
  it. Parked forward-recorder spec in findings (CLARITY Senate vote H2-2026,
  PM ~0.43 = the first genuinely contested liquid instance, single-shot
  shadow recording only if operator wants). Caches: hypotheses/W-P2_cache_*
  (full House/Senate roll metadata 2024-26 + 770 bill titles reusable).
  Fact bank: HL prunes 1h candles at ~5000 bars — 2024 backtests on majors
  need an external 1h source (Binance spot used here, gap-free).
- **W-P3 LLM-signed EDGAR direction: REFUTED** (findings/W-P3_llm_signed_edgar.md,
  pre-registered, run 2026-07-19). Claude CLI (fable-5, no tools/web) read all 308
  W-P1 filing texts at acceptance: 84% SKIP, 49 signed in the honest post-cutoff
  cell (L31/S18). EV25 negative at EVERY horizon (-0.58/-0.99/-0.45% at
  +1h/4h/24h), raw uncosted EV also negative, no better than 2000x sign-shuffled
  null (best p=0.08 — in the wrong direction). LLM sign matches the first-bar
  reaction sign 60%: by first-bar entry the content direction is already priced
  and gives back. Items-1.01/2.02/8.01 diagnostic cell also negative.
  Contamination split moot (24/25 pre-cutoff events skipped, n=1). LANE-P CLOSED:
  reaction real (W-P1), direction not capturable at >=1h latency even with the
  text; a minutes-scale intra-bar entry would be a NEW execution cell, not a
  re-run. Recorder NO-GO, zero capital, do not rebuild. Caches:
  hypotheses/W-P3_cache_* + W-P3_cache_texts/ (all 308 primary docs, reusable).
- **W-P4 PEAD (post-earnings drift, day horizons): REFUTED**
  (findings/W-P4_xyz_pead.md, pre-registered, run 2026-07-20, zero data spend —
  all W-P1/W-P3 caches). 66 earnings events (61 8-K items-2.02 + 5 6-K via a
  locked text-regex classifier; TSM monthly-revenue 6-Ks deliberately excluded),
  daily bars from the 1h cache, horizons +3/5/10/21d, 25bps, 2000x same-coin
  random-time null. Rule A (first-day reaction sign, classic PEAD proxy): EV25
  positive at all horizons but best +5d +0.87% p=0.15 — fails even MARGINAL;
  halves sign-flip at +10d/+21d. Rule B (W-P3 cached LLM signs): structurally
  starved — the LLM SKIPped 91% of earnings shells (numbers live in unseen
  EX-99), n=6 signed (all LONG), one SNDK outlier; B-ALL308 diag at +21d
  +1.35% p=0.45 with OOS flip — the signs do NOT redeem at day scale. REAL
  FACT BANKED: earnings-filing unsigned |r| beats null through +5d (6.05% vs
  4.44% p=0.0045 @3d; 7.78% vs 5.77% p=0.0040 @5d), dead by +10d, and the
  non-earnings complement shows nothing — earnings filings are a ~5-day VOL
  event with no recoverable direction. Lane P now closed at every horizon
  tested (1h -> 21d). Recorder/live NO-GO. Do not rebuild PEAD without a real
  surprise measure (consensus-vs-actual), absent from all free caches.

## W-W whale/exposure quality-reads program (started 2026-07-12)
INTERIM 2026-07-17 (shadow_status --book whale_flow): REFUTED lean at n=82 resolved —
-0.492%/sig @12bps, OOS halves -1.68 / +0.60 (not both positive). Formal bar stays
2026-07-26; if the verdict holds, (2) Deribit GEX lane and (3) FINRA rewire do NOT
proceed per the gates below.
Goal: EV+/PnL+ validation, not UW feature parity.
1. whale_flow recorder LIVE (c1df51a): Binance >=100k taker prints on scan
   candidates, biased sides vs balanced control. VERDICT BAR: >=30 eps/side,
   EV25>0 both halves, biased beats control. Due ~2026-07-26. If VALIDATED ->
   standing auto-flip order applies ($20/1x). If control ties biased ->
   whale flow carries no information here; disable, do not rebuild.
2. Deribit GEX/flip (crypto market-exposure): free API is CURRENT chains only
   (no deep history) -> build a data-logger lane first (hourly net GEX + flip
   level for BTC/ETH into the warehouse), correlate forward after >=3 weeks
   of accrual. Build when a session has budget; NOT before whale_flow's
   verdict unless idle.
3. FINRA short-vol rewire: only if (1) validates (same read-quality class).

## W-N4 AI-imported news + WSS (operator ask 2026-07-13)
1. AI news sweep: periodic web-search brain call ("top market-moving crypto/
   macro headlines last hour -> JSON with source+date"), citations mandatory
   (fabrication guard), feeds the macro tape. Cost ~1 call/30min. Build with
   eval in the same commit; verdict = does it beat the RSS tape on freshness
   (median headline age) over 1 week of side-by-side logging.
2. WSS low-latency news: Tree of Alpha (news.treeofalpha.com) free websocket
   is the known-good crypto news wire. Research terms/stability first, then a
   listener -> warehouse -> recorder (same bars as news_catalyst). This is
   the real answer to "the second it hits" latency; RSS is minutes behind.

## W-V follow-up: xyz ticker->company news aliases (found 2026-07-13)
Lane V: the ENTIRE tokenized-equity class is news-dark — _coin_query("xyz:SKHX")
searches "SKHX" but headlines say "SK Hynix"; only 2/126 xyz reads ever had
news. Build a small xyz ticker->company map in news_catalyst.py (SKHX->SK
Hynix, SNDK->SanDisk, BE->Bloom Energy, CRCL->Circle, KIOXIA->Kioxia, ...) used
by _coin_query + _title_relevant for xyz coins. Without it the news_ta_quadrant
recorder accrues ~zero xyz rows and SKHX-class conflicts stay unmeasurable.

## P0 BUG: DSL tracker entry_px goes stale on position ADDS (found 2026-07-13) — FIXED 2026-07-17
Evidence (SKHY, manual short + add): fills avg entry 158.73, close 158.90 =
-0.1% spot / -$0.29 realized. DSL tracker kept the FIRST fill entry (159.83),
computed +0.64% spot / +6.44% ROE, showed a green win, and ran its profit
floor off the wrong basis. Symmetric risk: an add above tracked entry delays
the stop beyond intended. FIX: on any size increase for a tracked coin
(heartbeat/rehydrate sees position size > tracked size), refresh entry_px to
the exchange's avg entryPx and recompute floors; ALSO close events should
carry realized closedPnl from fills when available so the feed shows exchange
truth. Tests: add-below-entry short (SKHY replay) + add-above-entry long.
Owner: next session / Codex (dsl_exit.py + dashboard close model).
STATUS 2026-07-17: FIXED — DSLTracker tracks last-seen size; rehydrate_from_exchange
detects any material size increase, refreshes entry_px to the exchange avg, and clamps
peak_px to the new basis (partial closes update size only; legacy size-0 state adopts
silently). SKHY replay + 8 more cases green in tests/test_dsl_add_refresh.py. The
close-event realized-PnL half was already served by the dashboard's pnl_source="fill"
path. Commit: see git log.

## W-P2 PARKED LIVE OPTION (operator-approved 2026-07-19)
CLARITY Senate vote (H2-2026, Polymarket ~0.43 / $1.9M): when a floor vote is
SCHEDULED, build the single-shot zero-capital shadow playbook per
findings/W-P2_scheduled_catalyst.md spec — brain writes branches T-24h
(conditioned on PM odds), deterministic watcher records the counterfactual at
resolution. One event, no capital. Watch: congress.gov S.### actions + PM odds.

## W-X4 meme-exclusion revert check — due at xs rebalance 6 post-wiring (~2026-09-18 at H10)
Pre-committed (b02276b): re-run the W-X4 harness on forward data — if the
cumulative counterfactual delta (filtered vs unfiltered book) < 0 after 6
forward rebalances, REMOVE xs_momentum.exclude_coins the same day. The
dominance gate passed 4/4 but paired-t was 1.36 — the forward A/B decides.
ANCHOR CORRECTION (W-X5 2392c4e): offset-0 backtests are phase-flattered
~+1.4pp; grade forward xs performance against the PHASE-MEAN expectation
+2.2%/rebal (~$6.5/wk at 0.10-frac), NOT the +3.68% offset-0 number.
A forward read near +2.2% is ON TARGET, not underperformance.

## Ledger sweep 2026-07-20 (grader min-n 8; mover settle bars stay pre-registered n=30)
- **mover_pass INTERIM REFUTED-lean at n=17**: −6.36%/sig @12bps, halves −6.85/−5.93, win 17.6%.
  W-M4's "PASS veto forfeits +4.48%" is inverted forward — the AI PASS veto is SAVING ~6.4%/sig
  on researched movers in this tape. Zero capital, keep recording, settle at n=30. Do NOT loosen
  the runner-gate/PASS veto off the W-M4 backtest claim while this read stands.
- **mover_b15_up INTERIM REFUTED-lean at n=10**: −6.45%/sig, halves −10.93/−1.98 — the W-M1
  Bonferroni-failed cell is behaving like grid luck forward. Settle at n=30.
- **extreme_fade unconditional read n=6: −10.19%/ep @12bps, 1/6 win — NOT the live policy.**
  6/7 unique episodes fired DISARMED (W-B2 skew-arm blocked live entry; the arm dodged ~−7.9%/ep
  on the disarmed 5). The one armed episode (CASHCAT open 07-16, skew −0.001, 9d-old listing)
  was taken live and lost $4.42 (−61% ROE, closed externally 07-17). RULE: never flip the live
  book off the unconditional ledger — grade the live policy with
  `shadow_status.py --book extreme_fade --meta armed=true` (flag added + tested this sweep).
  PRE-REGISTERED at n≥8 per subset: do <60d-history listings (CASHCAT-class) underperform the
  rest of the fade ledger? If yes, add a min_history gate to the book. Not tuned today — n=2
  CASHCAT episodes is not evidence.
- whale_flow: formal bar 2026-07-26 (cron armed) — not peeked. All other books below min-n.

## W-X5 xs implementation frontier — SETTLED 2026-07-20 (4 cells, no live changes)
Findings: W-X5_{depth_hold,tranches,hysteresis,xyz_hardening}.md. Verdicts:
- depth_hold REFUTED-as-upgrade: k4/H10 meme-excluded is the frontier (0/5 dominate;
  phase-mean per-day EV monotone-declining in k and H). Growth ladder pre-committed:
  k4/H10 at $65/$150/$300 — capital goes into leg size, never depth/tranches.
- tranches REFUTED (−30.6% rel EV drag). SIDE-FINDING THAT RECALIBRATES EVERYTHING:
  offset-0 is the best of all 10 rebalance phases — phase-mean is +2.24%/rebal, not
  +3.68%. GRADE ALL FORWARD xs EXPECTATIONS AGAINST ~+2.2%/rebal (~$6.5/wk), incl.
  the W-X4 revert check above, or healthy books will read as underperforming.
- hysteresis MARGINAL (gate-pass, phase-fragile): b=2 dominant 4/4 at offset 0, but
  phase-mean paired delta −0.125%/rebal (4/10 offsets). DO NOT WIRE; rank_buffer=2
  spec + revert recorded in findings if fee pressure ever demands a turnover cut.
- xyz_hardening: LIVE CONFIG STANDS (0/11 neighbors dominate resid7/k5/H5/XYZ100;
  most phase-stable positive cell). Re-sweep at ~12 forward rebalances (H10 column).
Book-integrity guard from the capital table: funding-dex equity < ~$35 silently drops
3x-cap legs (ACE-class) below the $10.50 min order — treat <$40 as an integrity alert.
