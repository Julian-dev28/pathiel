# Alpha Hunt — Validated Edges & Implementation Architecture

Systematic search for +EV signals. **Every method is held to the same bar:** lookahead-safe
(signal from data ≤ t, enter t+1), cost-aware (≥10bps round-trip), survivorship-free (tested on
the whole liquid universe incl. failures), and **OOS-robust** (both halves of the trade stream
positive — fragile/regime-dependent cuts do NOT qualify). Only edges that clear ALL of these get
wired, and only after shadow validation. Backtests live in `scripts/edge_*.py`.

Goal: assemble ≥10 validated +EV helpers, wire the keepers (alone or stacked), keep looping.

## 💰 EV LEDGER (running sum — updated each wave)
Honest accounting. EVs live in different units (%/rebal, %/trade, %/day) → they DON'T naively sum; this
tracks validated edges + their EV, a portfolio estimate, and a per-wave scorecard. Refuted tests add $0
tradeable EV but add KNOWLEDGE (de-risking), counted separately. **ALL 11 edges now WIRED (builders 1–4).
vol-dispersion went LIVE 2026-06-23 23:49** — 6 real positions (long WLD/XMR/XPL, short BTC/SOL/XRP, ~$15/leg,
market-neutral, each with DSL + backup-SL + TP). The rest are wired + OFF/shadow (flip via config). ⚠️ Going
live surfaced + we FIXED a CRITICAL bug: executor.py read `coin` before assignment (~L516 vs L576, since the
2026-06-22 sizing fix) → it UnboundLocalError'd EVERY gate-passing trade + every external_alpha open for 2 days
(silently throttling the whole bot). Fixed (bind coin early), verified live (opens now fill). xs_momentum reverted
to shadow (we run ONE book on $60 — vol-dispersion is the higher-EV pick per Y1).

### Validated edges & EV (paper, pre-live)
| Edge | EV (natural unit) | Wired? | Note |
|---|---|---|---|
| xs-momentum (residual, LB7/hold10) | ~+0.6–1.3%/rebal (mkt-neutral spread) | SHADOW | R3 corrected DOWN from +2.4% on the longer 261d window |
| pairs stat-arb | +1.08%/trade (V4: entry-z 2.5 → +1.98%) | not wired | orthogonal → small allocation; corr-clustering + Bollinger/RSI cross-sectional REFUTED (V4) |
| vol-scaled momentum | +0.9–1.7%/rebal (steadier) | fold into rebalancer | variant of core |
| vol-regime gate | lifts momentum to +3.45% in low-vol | in rebalancer | enhancement, not standalone |
| day-of-week (Mon+/Thu−) | Mon +0.78% / Thu −1.64% | not wired | calendar tilt; multiple-testing caveat |
| extreme-fade | +0.23–0.59% | not wired | marginal overlay |
| ★ vol-dispersion (idio-vol, W1+V1) | +5.56%/rebal (30d/K8, within-β-tercile; robust ALL perturbations) | WIRING (shadow) | NEW family; corr +0.40 to momentum (NOT orthogonal — both bleed in a crash); bear-gate; equal-weight |
| ★ Sortino-ranked (V2) | +3.66%/rebal (within-β-tercile, beta-neutral) | not wired | NEW signal; corr +0.07 mom / +0.37 idio-vol; HOLDS in down-regime (+2.24%) — more regime-stable than idio-vol |
| correlation-regime gate (V3) | mom Sharpe 4.95→8.36 (low-corr) / vol-disp 9.06→13.27 (high-corr) | enhancement | ONLY regime gate passing permutation; size mom UP in low-corr, vol-disp UP in high-corr |
| vol-managed momentum (W6) | Sharpe +0.27, maxDD −4.6%→−32% | WIRING (shadow) | fold vol-scaling into rebalancer (target-vol 0.01–0.02) |
| Amihud illiquidity (W6) | +2.33%/rebal but lumpy (2/4 q neg) | not wired | BORDERLINE; orthogonal to mom (−0.11); needs a down-regime quarter |
| kurtosis (HIGH, V2) | +1.71%/rebal (within-β-tercile) | not wired | GENUINE but modest; bleeds in down-regime; partial overlap w/ vol-disp family |

**Portfolio estimate** if the validated stack ran live (momentum-primary + small pairs, after R3's
down-correction + costs): realistically **~+0.6–1.3%/rebalance** on a market-neutral book — LUMPY
(expect multi-month flat stretches), bear-regime UNTESTED. Not yet realized (all shadow/unwired).

**RECOMMENDED LIVE ALLOCATION (Y1 `edge_portfolio.py`):** vol-dispersion = PRIMARY book; momentum ~30–40%
gross as the genuine diversifier (corr only +0.22 to vol-disp, −0.02 to Sortino); deploy AT MOST ONE of
{vol-disp, Sortino} — they're the SAME family (corr +0.52), vol-disp higher raw EV / Sortino more
regime-stable; PAIRS deferred until main funded (fragile + low-$ at this size). Gates = per-book SIZING
only, NOT on the combined book (gating the combination removes good days, no Sharpe gain). The combination
does NOT beat vol-disp-alone (combining dilutes the strongest factor). ⚠️ Y1's Sharpe figures (+13 to +19)
are NOT real — inflated by daily mark-to-market alignment + single 8-mo bull window + 29-coin universe + no
market impact; trust the RELATIVE ranking (vol-disp > blend > momentum-alone on this frame), NOT the absolute magnitudes.

### Wave scorecard (EV ADDED = NEW validated tradeable edge)
| Wave | Tests | New validated | Refuted/marginal | EV added | Knowledge gained |
|---|---|---|---|---|---|
| Pre-swarm | ~15 | momentum·residual·vol-scaled·vol-gate·pairs·day-of-week·extreme-fade | ~11 | **the 3 core families** | price-entries are the leak; relative frames work |
| Wave 1 (R1–R4) | 8 | **0** | 6 refuted + 1 marginal (blend) + 1 beta-suspect (idvol/skew) | **$0 new** | momentum est. corrected +2.4→+0.6-1.3%; beta-trap on idvol/skew caught; LB7/daily reconfirmed |
| Wave 2 (W1–W6) | ~14 | **1 new family** (vol-dispersion/idio-vol) + 1 enhancement (vol-mgd sizing) | 4 refuted + 1 borderline (Amihud) | **vol-dispersion; vol-mgd Sharpe lift** | beta-trap REVERSED (idio-vol is real alpha); pairs/reversal/PCA/52wk/FIP closed |
| Wave 3 (V1–V4) | ~12 | **1 new signal** (Sortino) + correlation-regime sizing gate; pairs entry-z 2.5 (V4) | redundant: downside-dev/MDD (=idio-vol); refuted: beta-rotation, BTC-dom, ETH-BTC, corr-clustering, Bollinger/RSI | **Sortino (HOLDS down-regime); corr-gate; vol-disp HARDENED 30d/K8** | kurtosis modest; vol-disp corr +0.40 to mom |
| Wave 4 (X1–X4) | ~14 | **0 new families** — 2 actionable rebalancer refinements: vol-regime gate (permutation-confirmed) + illiquidity×momentum tilt | refuted: weekend/intramonth/time-of-day, CUSUM, ETH/dual/vol-wt/EWMA-beta mom-refinements, Parkinson(=idio-vol) | **vol-state gate Sh+2.06/maxDD-halved/p=0.02; momentum +2.55% in MID-liquidity tier** | simple BTC-neutral mom beats ALL refinements; corr-regime dominates all gates |

**Running total: 3 core families + 2 new beta-neutral families (vol-dispersion, Sortino) + 2 sizing gates
(correlation-regime primary, vol-regime secondary) + illiquidity-tilt + vol-mgd enhancement; Amihud/kurtosis
borderline · GOING LIVE (owner ×3+).** ⚠ SEARCH IS SATURATING — Wave 4 found 0 new standalone families (only
rebalancer refinements); the candle-testable space is largely mapped. Higher-value frontiers now = (1) WIRE +
GO LIVE on what's validated, (2) deeper/different data (OI/liquidations/funding-history/order-flow — all 🔒),
(3) bear-regime validation. Executing the wire + live-flip at SMALL sizes; −$100 kill + all safety gates intact;
vol-dispersion/Sortino window-validated (no bear) → minimal size, bear-gate documented. Was $0 realized; going live.

---

## ✅ VALIDATED EDGES (keepers)

### ★ UPGRADE — RESIDUAL (BTC-neutral) momentum is the CORRECT core  (`edge_sweep4.py`)
Ranking on each coin's return MINUS beta×BTC return (the idiosyncratic residual) both RAISES the
return AND SMOOTHS it across regimes — directly fixing the lumpiness the audit found:
total momentum +1.53% (quartiles +2.71/−0.79/−0.55/+4.77, 2/4 negative) → **residual momentum
+2.47% (quartiles +2.71/+2.48/+0.34/+4.37, ZERO negative quarters)**. ⇒ **the rebalancer should
rank on the residual, not total return** (add a trailing-beta-neutral score to rank_universe).
Acceleration (momentum-of-momentum) REFUTED (−0.68%, 3/4 neg).

### 1. Cross-sectional momentum — STRONG  (`edge_xsectional.py`)
Rank the liquid universe by trailing return each rebalance; **LONG the top-8, SHORT the bottom-8**
(market-neutral). Long-short spread is robust +EV at **every** lookback/hold tested:

| lookback | hold | spread/rebal | win | OOS |
|---|---|---|---|---|
| 7d | 10d | **+2.50%** | 61% | +2.57 / +2.42 ✓ |
| 14d | 5d | **+2.01%** | 63% | +1.87 / +2.16 ✓ (most balanced) |
| 14d | 10d | +2.20% | 61% | +1.41 / +2.98 ✓ |
| 21d | 5d | +0.93% | 55% | +0.50 / +1.37 ✓ |

**Key:** the edge is the **long-SHORT spread** (relative strength). Long-only is fragile
(+0.3% to −0.5%). Capturing it REQUIRES both legs — a market-neutral book, not the per-coin loop.
**Recommended config: LB=14d, hold=5d** (most balanced OOS).

### 2. Pairs / cointegration stat-arb — STRONG, INDEPENDENT FAMILY  (`edge_pairs.py`)
Mean-reversion of a market-neutral log-spread between co-moving coins (corr>0.6): trade z>2
divergence, exit on reversion. **+1.08%/trade, n=2413, OOS +1.10/+1.06 (rock-solid)**. ORTHOGONAL
to momentum (profits from relative mean-reversion, not trend) → stacking the two diversifies.
Shape: 45% win / neg median → many small losses + fewer big convergence wins (size for that).

### 3. Momentum variants/enhancements (same family as #1)  (`edge_sweep2.py`)
- **vol-scaled xs-momentum** (inverse-vol legs): +0.92–1.70%, OOS +0.89/+0.95 — MORE STABLE than
  plain. **Fold this weighting into the live rebalancer.**
- skip-momentum (12-1, LB=14/skip=3): +0.81% robust · TSMOM absolute (LB=40/thr=5%): +0.53% robust
  (directional, has market beta). Both robust but weaker than the xs core.

### 4. Day-of-week seasonality — INDEPENDENT (calendar) family  (`edge_sweep3.py`)
Cross-coin mean daily return by weekday: **Monday +0.78% (OOS +0.87/+0.68 robust)**, **Thursday
−1.64% (robust negative)**, Sat +0.27%. Tradeable as a long-Mon / flat-or-short-Thu bias (net of a
daily round-trip ~+0.6% Mon). ⚠ CAVEAT: 7 weekdays tested → multiple-comparisons risk; the OOS
consistency (both halves agree) lends some confidence but treat as a tilt, validate forward. Calendar
edge ⇒ orthogonal to momentum + pairs.

### 5. VOL-REGIME FILTER for momentum — ENHANCEMENT (high value)  (`edge_sweep3.py`)
The xs-momentum edge CONCENTRATES in low volatility: **+3.45% (win 65%, OOS +2.78/+4.12) when BTC
trailing-vol is BELOW median**, vs a fragile +0.54% (OOS +1.17/−0.08) above. ⇒ **gate the rebalancer
on BTC vol** (run / up-size only in low-vol regimes). Materially lifts the wired edge.

### 6. Extreme-move fade — MARGINAL  (`edge_sweep.py`)
After a single-day move > |12–18%|, fade it next day: +0.23–0.59%, robust but OOS-2nd-half ~flat.
Thin — small overlay / confirmation at best.

---

## ❌ REFUTED (−EV, tested + rejected — do not revisit without a new angle)
price breakout / oversold bounce / volume-momentum / trend-filtered breakout (`edge_movers.py`) ·
Williams bar patterns (`edge_williams_patterns.py`) · daily news catalyst surge (`edge_catalyst.py`) ·
funding-rate extremes (`edge_funding.py`) · short-term cross-sectional reversal · low-vol anomaly ·
BTC lead-lag (alts don't follow BTC's prior-day move) (`edge_sweep.py`/`edge_sweep2.py`) ·
**4h residual momentum** (`edge_4h_momentum.py`, R1 2026-06-23): NO config OOS-robust after cost —
short LBs cleanly neg (noise), best 4h gross paper-thin (LB=48h/H=24h +0.36% @10bps → ~flat @20bps)
vs daily's 11× margin; lumpier not smoother (Q3 crater). Caveat: only 40d of 4h data exists
(insufficient OOS) — but evidence points to dead-end; daily is the right timeframe. Common
cause: on daily data, by the time an *absolute* single-coin signal is visible, the move already
happened — we're late. The RELATIVE frames (cross-sectional momentum, pairs spread) broke through.

---

## 🟡 CANDIDATE UNDER AUDIT — high-vol / positive-skew alts (R4, `edge_factors.py`)
Cross-sectional sweep surfaced TWO robust-LOOKING long-short factors that INVERT the classic equity
anomalies (long HIGH idio-vol +4.41%/rebal OOS +2.72/+6.10; long POS-skew +3.81% OOS +3.44/+4.18; both
4/4 quartiles positive, all windows). Factor-score corr to momentum near-zero (idvol +0.06, skew +0.18)
⇒ NOT momentum in disguise. Downside-beta & consistency REFUTED. BUT two reasons this is NOT yet an edge:
1. **Same phenomenon, not two edges** — return-stream corr +0.45; residualize either vs the other and
   BOTH go fragile. ONE family.
2. ⚠ **LIKELY A BULL-REGIME BETA BET, not market-neutral alpha.** Long-high-vol/short-low-vol is
   dollar-neutral but NOT beta-neutral → net-long-beta, which prints +EV in ANY bull/choppy tape (our
   only data, Mar–Jun 2026). Near-zero MOMENTUM corr does NOT rule out MARKET-BETA exposure. "4/4
   quartiles positive" = consistency within ONE bull regime, not across regimes. R4 itself: a real bear
   "would likely flip this completely."
**W1 AUDIT RESULT (2026-06-23, `edge_beta_neutral_factor.py`):** spreads DO carry net beta (idio-vol
+0.45, skew +0.28) — BUT beta-neutralizing makes EV go UP, not to zero (a pure beta bet would vanish).
Conservative WITHIN-β-TERCILE control survives ROBUSTLY: **idio-vol +5.68% (OOS +4.21/+7.15), skew +2.70%
(OOS +1.84/+3.57)**. ⇒ the "pure beta bet" hypothesis is (at least partly) WRONG — there's a real
beta-independent vol-DISPERSION component; idio-vol even rises on down-DAYS (+13.12%, small-sample).
REMAINING CAVEATS before wiring: (1) still ONE ~6mo bull/choppy window — down-DAYS ≠ a sustained BEAR
regime (untested); (2) the beta-weighted magnitude (+6.68%) may be method-inflated → trust the
within-tercile numbers; (3) idio-vol≈skew (r=0.45) = ONE family → deploy idio-vol ONLY. STATUS: upgraded
"suspected beta" → **"promising NEW family"**. ✅ MY METHOD SPOT-CHECK PASSED (audited
edge_beta_neutral_factor.py): lookahead-safe; the within-tercile neutralization is beta-neutral
BY CONSTRUCTION so the +5.68%/+2.70% is legit (use THAT, not the +6.68% beta-weighted which inflates
gross by scaling the short leg up; the +13.12% down-day is on the inflated method + n=45 = directional
only). REMAINING GATE before live: bear-regime / longer-window proof (cache can't provide — needs forward
validation or a deeper fetch). ⇒ the swarm's FIRST genuinely new beta-neutral edge family (vol-dispersion),
window-validated; wire SHADOW-first alongside momentum (independent, 36–45% leg overlap) once bear-proofed.

---

## 🔬 CANDIDATE QUEUE (the infinite quest continues)
DONE: ✅ vol-scaled xs · ✅ skip-momentum · ✅ TSMOM · ✅ pairs stat-arb · ❌ BTC lead-lag · ❌ funding · ❌ low-vol · ❌ 4h-momentum (R1) · ❌ multi-LB composite (R2 — no gain over LB=7) · ➖ Sharpe-optimal blend (R2 — w*=0.6 max-Sharpe only, cuts raw $) · ❌ regime-switch Hurst/autocorr (R3 — both destroy value) · 🟡 idio-vol & skew factors (R4 — robust-LOOKING but likely bull-beta, see candidate block) · ❌ downside-beta & consistency (R4)
### 📚 STRATEGY CATALOG — the backlog to exhaust (✅validated ❌refuted 🟡audit ⏳testable-now 🔒needs-data)
**A. Momentum / relative-strength** (core validated; variants cluster — diminishing returns, low priority):
✅ xs-momentum · ✅ residual(BTC-neutral) · ✅ vol-scaled · ✅ skip(12−1) · ✅ TSMOM · ➕ longer-horizon 60/90d
momentum (W4 side-find — +3.23% OOS-robust; variant not new family; head-to-head vs LB=7 untested) · ❌ multi-LB composite ·
❌ acceleration · ❌ 4h · ❌ 52-week-high (W3 — George-Hwang inverts/refuted; n=70 cache-depth-starved) · ❌ frog-in-the-pan (W3 —
refuted; FIP-tilt = momentum disguise corr +0.72) · ✅ Sortino-ranked (V2, beta-neutral — see family C) · ❌ ETH-neutral / dual BTC+ETH / vol-weighted / EWMA-beta residual
(X3 — NONE beat simple BTC-neutral, all Δ t<1.4 = noise; simple LB=7 wins again) · 🔒 intermediate 7–12mo · 🔒 overnight/gap momentum
**B. Reversal / mean-reversion** — FAMILY COMPREHENSIVELY REFUTED (crypto = pure-momentum regime this window):
✅ pairs stat-arb (static) · ❌ Kalman/OU dynamic-hedge upgrade (W2 — static +1.53% beats Kalman −14.9% / HL-filter
+1.17%; Kalman chases divergences) · ❌ short-term total reversal · ❌ RESIDUAL short-term reversal (W4 — residual did
NOT rescue it, all NEG) · ❌ medium-horizon 1–3mo reversal (W4) · ❌ distance-from-MA reversion (W4 — reversion −EV;
"continuation" = momentum in disguise, corr −0.69) · ❌ multivariate cointegration baskets (W5 — refuted, −1.65% gross; multiple-testing/6-leg cost) ·
⏳ Bollinger/RSI-extreme · ⏳ correlation-clustering pair discovery · 🔒 long-horizon (DeBondt 3–5yr) · 🔒 overnight rev
**C. Volatility / risk-based**:
❌ low-vol anomaly · 🟢 HIGH-idio-vol (W1 — NOT pure beta: within-β-tercile +5.68% OOS-robust; PROMISING new
vol-dispersion family, see audit block; caveat bear-regime untested) · 🟢 pos-skew (W1 — same family as idio-vol,
r=0.45, deploy idio-vol ONLY) · ✅ vol-managed momentum (W6 — ENHANCEMENT: Sharpe +0.27, maxDD −4.6%→−32% at tv 0.01–0.02; fold into rebalancer) · ⏳ beta-rotation timing · ⏳ kurtosis factor ·
⏳ trailing-max-drawdown factor · ⏳ downside-deviation/Sortino factor · ⏳ realized-vol breakout/clustering · 🔒 VRP
**D. Seasonality / calendar**:
✅ day-of-week (Mon+/Thu−; Sat marginal +0.13% net — X1, don't wire alone) · ❌ turn-of-month (X1 re-confirms: multiple-testing
artifact) · ❌ weekend effect (X1 — Sat-only, no weekend-as-a-class effect) · ❌ intramonth drift (X1 — coarse/fine grids contradict) ·
🔒 time-of-day (X1: 40d of 4h too thin, all neg after cost — needs 6mo+ 4h) · 🔒 funding-settlement (8h) · 🔒 month/quarter (needs years)
**E. Microstructure / flow** (mostly need non-candle data):
❌ funding extremes · 🟡 Amihud illiquidity (W6 — BORDERLINE: long-illiquid +2.33%, orthogonal to mom −0.11, but 2/4 quarters neg; needs down-regime) · 🟡 volume-spike CONTINUATION (X4 — robust +0.4-0.8% but corr +0.38 mom/+0.63 $vol-trend = momentum proxy) · 🟡 $vol-trend
(X4 — robust +0.5%, +0.43 mom; residualize first) · ❌ range/Parkinson-vol (X4 — = idio-vol, corr +0.85) · 🟢 ILLIQUIDITY×MOMENTUM
tilt (X4 — momentum +2.55% MID-liquidity vs +0.30% fragile in mega-caps → rebalancer should tilt AWAY from top-$vol tier) ·
⏳ basis / spot-perp (basis_gap wired) · 🔒 OI/price 4-quadrant (OI logger collecting) ·
🔒 liquidation-cascade fade · 🔒 funding carry/momentum · 🔒 order-flow imbalance · 🔒 taker buy/sell ratio
**F. Statistical / ML / math**:
❌ Hurst regime-switch · ❌ PCA eigenportfolios + residual reversion (W5 — refuted, −0.88% GROSS; 28 coins too thin, PC1 dominates) · ⏳ OU half-life
mean-reversion timing · ❌ change-point/CUSUM regime (X2 — de-risk-post-break doesn't hold, p>0.05) · ✅ vol-state gate on
momentum (X2 — HMM-proxy; LOW-vol upsizes mom, Sh+2.06/maxDD-halved/p=0.02; = edge #5 now permutation-confirmed; independent
of corr-regime, keep SECONDARY, don't combine) · ⏳ random-matrix-theory corr-matrix filtering · ⏳ entropy/information signals ·
⏳ wavelet/Fourier cycle · ⏳ DFA/fractal · ⏳ meta-labeling ensemble (Lopez de Prado)
**G. Cross-asset / relative**:
❌ BTC-dominance / alt-season (V3 — temporal artifact, p>0.05) · ❌ ETH/BTC ratio regime (V3 — refuted) · ✅ correlation-regime
gate (V3 — VALIDATED, see EV ledger) · ❌ dispersion timing (W6 — LOW-disp better, non-monotone) · ⏳ relative-strength vs sector
median · ❌ lead-lag network (Y2 — permutation p=0.72, WORSE than random leaders; N²-pairs noise) · 🔒 sector rotation (needs sector tags)
- ✅ **STACK tested** (`edge_stack.py`): momentum + pairs daily streams are UNCORRELATED (corr +0.05,
  reconfirmed −0.006 by R2/`edge_blend.py`), but momentum's Sharpe dominates (gross ann ~4.95 vs pairs
  ~1.27) so 50/50 ≈ momentum. ⇒ momentum = primary book, pairs = SMALL uncorrelated allocation.
  Sharpes are GROSS of rebalance cost (optimistic); the orthogonality is the durable result.
- ➖ **Sharpe-optimal blend weight** (R2/`edge_blend.py`): swept w·mom+(1−w)·pairs; w*=0.60 maxes Sharpe
  (+4.976 vs +4.760 mom-alone, +0.217, OOS-robust) — but via VOL-REDUCTION (σ 2.67%→1.68%), not mean:
  raw daily mean DROPS +0.666%→+0.436%. ⇒ for max $-EV keep w=1 (mom-alone); w=0.6 only if maximizing
  risk-adjusted return to run higher leverage. Multi-LB composite (z-avg of LB 3/7/14/30) REFUTED — +EV
  & robust but +1.86% < single LB=7 +2.29% (blending dilutes the strongest signal). LB=7 stays.
### Active wave (disjoint, daily-cache-testable, cache-only)
[WAVE 2 ✅] vol-dispersion family + vol-mgd sizing. [WAVE 3 ✅] Sortino (NEW, regime-stable) + correlation-regime gate; pairs entry-z 2.5.
[WAVE 4 ✅] 0 new families — vol-regime gate (permutation-confirmed) + illiquidity×momentum tilt; rest refuted ⇒ candle-space SATURATING.
[WAVE 5 ✅] Y1 portfolio construction → vol-disp PRIMARY + momentum diversifier (combination does NOT beat vol-disp-alone;
Sharpes inflated — trust ranking not magnitude); Y2 lead-lag network REFUTED (permutation p=0.72).
[WAVE 6 — FINAL candle wave, exhausts the space] Z1 RMT-denoised stat-arb + entropy · Z2 spectral/wavelet/DFA + sector
relative-strength. ⇒ AFTER THIS: candle-testable space DONE → new edges need DATA-COLLECTION (OI/funding/liquidation loggers,
~1-2wk), not more backtests. Recommend the swarm pivots to (a) go-live forward-validation + (b) standing up those loggers.
BUILD/GO-LIVE (parallel track): builder-1 ✅ wired vol-dispersion + vol-mgd (shadow). builder-2 wiring Sortino / pairs /
corr-gate / Amihud / day-of-week / extreme-fade (shadow). ⚠ **SIZING-GAP found in review:** rebalancer `_analysis` doesn't
set `external_alpha_notional` → executor:582 sizes trades at the full $350 cap, not the small ext-alpha cap. MUST fix
(pass `external_alpha_notional_usd` through, or executor fallback) BEFORE any live-flip — else ~6× oversized on the $60 main.
Then flip vol-dispersion live SMALL (k=1). Capital constraint: ~$60 main caps live to ONE small book; fund main (xyz→main, operator-only) to run the stack.
PENDING (deeper data, DEFERRED — competes with live loop API): bear-regime proof of vol-dispersion/Sortino + fair PCA + 52wk-high.

---

## 🏗 IMPLEMENTATION ARCHITECTURE (how the keepers get wired)
The proven edge (#1) is a **market-neutral cross-sectional rebalancer**, structurally different from
the current per-coin scan→research→execute loop. Plan:

- **`xs_momentum` engine**: each rebalance (every `hold` days), rank the liquid universe by trailing
  `LB`-day return; target **+K longs / −K shorts**, equal-risk per name (reuse the executor's
  structural sizing). Diff vs current holdings → close drops, open adds. Market-neutral (gross ≈ 2×,
  net ≈ 0).
- **Short leg**: requires the short path (currently gated by `min_short_volume`); the top-50-by-volume
  universe clears it. The bottom-K shorts are liquid majors, not microcaps.
- **Cadence**: rebalance on a timer (not the 60s scan); between rebalances, only manage stops.
- **Overlay (#2)**: extreme-fade as a small satellite or an entry-timing nudge on the rebalance.
- **Validate-first**: build → backtest the live wiring → SHADOW (log target book vs actual) → LIVE
  with small gross, scale as forward data confirms. Same discipline as the Williams rebuild.
- **Risk**: keep the bare safety gates (kill-switch, margin floor, per-name + gross caps). Market-
  neutral lowers directional risk but adds short-squeeze tail — cap per-name short size.

## AUDIT LOG (truth-check — re-run + STRESS-TEST, confirm robustness not just reproduction)
- 2026-06-23 #1: xs-momentum +2.37% / pairs +1.08% reproduce EXACTLY. No drift.
- 2026-06-23 #2 (`edge_audit.py`, perturbation stress-test of the WIRED xs-momentum):
  - ROBUST to cost (+1.97% even at 30bps/name), K (+EV at 4/8/12), universe (+EV top-20/30/40). ✓
  - long-only FRAGILE (+0.23%, OOS −3.33/+3.77) ⇒ edge IS the market-neutral spread, not beta. ✓
  - ⚠ **FRAGILITY: regime-dependent.** 4 sub-period quartiles = +5.28% / +0.14% / −0.14% / +4.12%.
    The edge is LUMPY — ~2-month flat/negative stretches (Q2/Q3). The 2-half OOS masked it. ⇒ the
    **vol-regime gate is NECESSARY** (those dead periods ≈ high-vol/choppy), and expect multi-month
    drawdowns live. The stack-test Sharpe (~4.95) is OPTIMISTIC (gross + smoothed); real Sharpe lower.
  - ⚠ ALL backtests cover ONE ~6-month window (Mar–Jun 2026) — not proven across a bear/crash regime.
- 2026-06-23 #3 (R3/`edge_regime_switch.py`, OUT-OF-WINDOW check — cache now extends to 261d,
  2025-10→2026-06, 28 coins): total-return momentum STAYS robustly +EV on the longer sample
  (LB=7/hold=5 +0.61%, OOS +0.85/+0.37; LB=7/hold=10 +1.27%, OOS +0.36/+2.17) — but at ~HALF the
  Mar–Jun magnitude. ⇒ direction + robustness CONFIRMED on a longer window; magnitude was flattered by
  the recent regime — **set live expectations to ~+0.6–1.3%/rebal, not +2.4%.** (Still no bear/crash in
  sample.) Regime-switching (Hurst/autocorr) REFUTED: the classifier label anti-correlates with where
  momentum works (momentum +1.52% in "reverting" windows, −0.15% in "trending"); reversal never +EV.

## STATUS
- **3 INDEPENDENT robust edge families validated** (the real prize, not 10 momentum lookalikes):
  (1) **Momentum** — xs core +2.37%, vol-scaled +1.7% (steadier); **concentrates in LOW BTC-vol
      (+3.45%)** ⇒ add the vol-regime gate. Directional-relative.
  (2) **Pairs stat-arb** +1.08% — relative mean-reversion, ORTHOGONAL → stack for diversification.
  (3) **Day-of-week seasonality** — Mon +0.78%/Thu −1.64% robust (calendar; multiple-testing caveat).
  + extreme-fade overlay (marginal). ~11 refuted (price patterns, Williams, catalysts, funding,
  reversal, low-vol, lead-lag, turn-of-month).
- **xs_momentum REBALANCER — wired + SHADOW-deployed + VERIFIED** (logged 8L/8S target book, 0 orders):
  - ✅ Pure engine `agents/xs_momentum.py` (rank_universe + rebalance_plan) + 6 unit tests (green).
  - ✅ Shadow runner `scripts/xs_momentum_run.py` — builds the live target book + plan, no orders.
  - ✅ **Universe filter fixed** (exclude `@` spot/index markets — shadow caught untradeable shorts
    @109/@144/@155) and **edge RE-VALIDATED on tradeable perps only**: LB=7d/hold=10d +2.37%,
    OOS +2.49/+2.25 robust (most configs still robust; LB=30/hold=5 now borderline).
  - ⏳ NEXT: loop integration — timer-based rebalance (every hold-days) + executor diff-execution
    (close drops / open adds, BOTH legs incl. shorts), SHADOW mode default → live small-gross on sign-off.
- Note: the `able` branch has ~10 PRE-EXISTING test_cleanup failures (executor news/shadow-signals
  path) unrelated to this work — flagged, not introduced here.
