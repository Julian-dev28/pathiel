# ranker_ab — decision-grade A/B: pct_k vs z_ext vs raw-residual (live xs_momentum book)

**Question.** Live book runs `ranking="pct_k"` (so `residual:True` is a NO-OP — pct_k ignores
the BTC benchmark). Lane W-A4/A2 claimed BTC-beta-RESIDUAL momentum (`ranking="raw"` + bench)
tests better (Sharpe +0.63 vs pct_k +0.30 @H10) and removes a down-beta confound. Settle it.

## Method (what makes this honest)
- Imported the **EXACT live scorers** from `hermes_trader/agents/xs_momentum.py`
  (`rank_universe`, `zext_score`, `pctk_score`, `residual_score`, `trailing_return`). Bars
  converted to dicts because the live `candle_val()` reads keys/attrs not list indices.
- Built a **continuous market-neutral L/S DAILY-return series** per ranker. Book decided on
  the close of day `i` (i-close approximation, allowed for slow daily signals), effective from
  day `i+1`, rebalanced every `hold` days. Book daily return = `0.5·(mean_long_ret − mean_short_ret)`
  (dollar-neutral, gross=1). Turnover-based slippage charged on each rebal day.
- **Matched universe across all three rankers each rebal** (a coin is eligible only if it has
  enough history for ALL three scorers), so the paired (residual − pct_k) DIFFERENCE is robust
  to survivorship even though absolute Sharpe levels are upper bounds (today's 39-coin survivor set).
- Swept lb {7,10,14,20} (7 = live), k {4,6,8}, hold {5,7,10} (10 = live). **Live config = lb7/k8/H10.**
- **n = 270 aligned daily bars** (full common series after the 31-bar warmup; master axis = 301
  BTC daily bars). This is the full history, NOT the truncated 188-bar subset W-A4 used.
- Paired-difference significance via a **block sign-flip permutation** (block = hold, preserves
  the within-hold autocorrelation; null = zero difference). The repo's `block_bootstrap_p`
  recenters on the series mean → ~0.5 by construction for a diff series, so it is the wrong null
  for a paired test; sign-flip is the correct one. Naive t-stats shown too (they overstate, because
  hold=10 makes daily obs autocorrelated → ~27 independent rebal periods).

## Headline — PAIRED DIFFERENCE (residual minus pct_k), net@12bps, n=270
| lb | k | H | mean diff (bp/day) | naive t | block-signflip p (2-sided) | reading |
|---|---|---|---|---|---|---|
| **7** | **8** | **10** *(LIVE)* | **−10.35** | **−1.76** | **0.114** | residual directionally WORSE, n.s. |
| 10 | 8 | 10 | −1.43 | −0.25 | 0.799 | tie |
| 14 | 8 | 10 | +0.56 | +0.09 | 0.937 | dead tie |
| 14 | 8 | 5 | −10.37 | −1.72 | 0.113 | residual worse, n.s. |
| 20 | 8 | 10 | −14.17 | −2.10 | 0.064 | residual significantly WORSE |
| 14 | 6 | 10 | +0.03 | +0.00 | 0.997 | tie |
| 14 | 4 | 10 | +0.02 | +0.00 | 0.999 | tie |

Gross (0bps) is essentially identical (live: −10.31bp, t=−1.75) → **not a turnover/cost artifact.**
**Residual NEVER paired-beats pct_k.** Best case (lb14) it's a tie; at the live lb7 and at lb20
it is directionally-to-significantly worse. The W-A4 "+0.63 vs +0.30 @H10" lift was an artifact of
n=15 non-overlapping rebals on a 188-bar subset; it does not survive on the full daily series.

z_ext − pct_k (net@12bps): −3.81 bp/day, t=−0.81, signflip p=0.48 → z_ext also slightly behind
pct_k but the closest of the three (not significant either way).

## Beta table — does residual remove the down-beta confound? (net@12bps)
| config | ranker | full beta (book on BTC) | down-day beta (BTC<0) |
|---|---|---|---|
| lb7 k8 H10 (LIVE) | pct_k | −0.109 | −0.198 |
| | z_ext | **−0.079** | **−0.155** |
| | residual | −0.085 | −0.144 |
| lb14 k8 H10 | pct_k | −0.109 | −0.198 |
| | z_ext | −0.079 | −0.155 |
| | residual | −0.112 | **−0.247** |

All three books are already near-neutral (|full beta| ≤ 0.11). Residualizing the *score* does NOT
guarantee a neutral *portfolio*: at the live lb7 residual cuts down-beta only marginally
(−0.198→−0.144), and at lb14 it is WORSE than pct_k (−0.247). The "removes the down-beta confound"
claim does not hold up. **z_ext has the cleanest beta of all three at the live config.**
Context: BTC drifted −20.6 bp/day over the 270-day window (mild down-regime), so beta matters here —
and residual still doesn't reliably win on it.

## Per-ranker OOS (annualized Sharpe, net@12bps; survivorship = upper bound, RELATIVE comparison valid)
| config | ranker | full Sh | h1 Sh | h2 Sh | mean bp/day @12 | @25 |
|---|---|---|---|---|---|---|
| lb7 k8 H10 (LIVE) | **pct_k** | **+3.99** | +5.78 | +2.02 | **+26.8** | +25.9 |
| | z_ext | +3.51 | +4.30 | +2.67 | +23.0 | +22.1 |
| | residual | +2.46 | +3.46 | +1.29 | +16.5 | +15.5 |
| lb14 k8 H10 | pct_k | +3.99 | +5.78 | +2.02 | +26.8 | +25.9 |
| | z_ext | +3.51 | +4.30 | +2.67 | +23.0 | +22.1 |
| | residual | +3.67 | +3.75 | +3.65 | +27.4 | +26.6 |

All three are OOS-robust (both halves +). pct_k has the highest Sharpe at the live config and the
highest h1; z_ext is the steadiest across halves (smallest h1→h2 decay) and the cleanest beta;
residual only catches up to pct_k if you ALSO move lb 7→14 (and even then ties, with worse beta).
(pct_k/z_ext are lb-independent — they use only the 14d channel window — confirming correct wiring;
only residual responds to lb.)

## VERDICT: **KEEP-PCTK**
Deciding numbers: at the **LIVE config (lb7/k8/H10)** the residual-minus-pct_k paired daily diff is
**−10.35 bp/day (block-signflip p=0.114, gross −10.31)** — residual is directionally WORSE, and it is
**never** significantly better at any swept config (best case lb14 is a dead tie, p=0.94). The
beta-cleanliness rationale also fails: residual's down-day beta is −0.144 (lb7, barely better than
pct_k's −0.198) and −0.247 at lb14 (worse than pct_k). **No robust paired edge → do not switch.**

**z_ext standing** (the code's "validated upgrade"): nearly ties pct_k (−3.81 bp/day, p=0.48), has the
**cleanest beta** of the three (full −0.079 / down −0.155) and the **steadiest OOS halves**. It is the
only defensible shadow-A/B candidate if beta-cleanliness is the goal — but it does not beat pct_k on
Sharpe, so there's no case to flip live off pct_k. The thing to NOT do is switch to raw-residual.

**Biggest caveat:** absolute Sharpes (~+4 annualized) are inflated by survivorship AND by hold-window
autocorrelation deflating daily vol — treat them as upper bounds. The RELATIVE (paired) result is the
load-bearing one and it says pct_k ≥ residual everywhere.
