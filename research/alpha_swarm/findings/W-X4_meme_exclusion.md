# W-X4 — meme_exclusion_overlay: drop declared MEME names from the live book's eligible set

## Hypothesis
W-X3 traced the W-X2-B "+1.50% within-L1" side-finding to meme-exclusion, not liquidity.
Test the exclusion directly ON the live recipe: does the live book get strictly better when
SECTOR_MAP MEME names are removed from the eligible set before ranking?

## Exact rule (pre-registered in hypotheses/W-X4_meme_exclusion.py docstring before first run)
- Data/engine: `W-X2_cache_daily.json` (2026-07-20, 401 bars), shared W-X2 engine imported.
  All books = the live recipe verbatim: pct_k(14), k=4/leg, H=10, top-50, >=61-bar
  eligibility, start=66, ungated sim (baseline reproduces W-X2-D/W-X3 to the digit).
- BASELINE: full top-50 eligible set. PRIMARY: the 20 SECTOR_MAP MEME names (declared before
  W-X2 cell B ran) dropped from the eligible set BEFORE ranking, both legs. 8 are currently
  in the top-50: CASHCAT, DOGE, FARTCOIN, PUMP, TRUMP, VINE, kBONK, kPEPE. Unmapped names
  (ACE, SUSHI, GRAM, AZTEC) stay eligible. SENSITIVITY (asymmetric): memes excluded from the
  LONG leg only; short leg = bottom-4 of the full universe (asserted identical to the
  baseline short leg).
- Costs 0/12/25/50 bps RT turnover-scaled; OOS halves; 2000-draw matched nulls (diagnostic —
  the decision gate is dominance). Asym null: longs drawn from the meme-free pool, shorts
  from the full pool minus drawn longs.
- DECISION GATE (pre-registered; a modification of the live book, so the bar is STRICT
  DOMINANCE): wire-worthy iff the variant beats BASELINE on net25 EV AND net25 Sharpe-like
  in BOTH halves.
- DECOMPOSITION (pre-registered): per-leg contribution identity (long +0.5*fwd/k, short
  −0.5*fwd/k; sums exactly to EV, asserted) → forfeited meme-short EV vs avoided meme-long
  bleed, plus the realized variant−baseline net25 delta split exactly into
  long-leg + short-leg + fee deltas (identity asserted).
- Selftests green: baseline/overlay/asym selection exact on synthetic trends, asym shorts ==
  baseline shorts, decomposition identities and signs, asym matched null, dominance gate
  (strictly-shifted book passes, self-vs-self fails).

## Results (n=33 rebalances; Sharpe = net25 mean/pstdev; $/wk at $76.8/leg = 0.10-frac x 12x)
| book | gross | net25 | net50 | net25 h1/h2 | Sharpe full (h1/h2) | null p | turn | $/wk |
|---|---|---|---|---|---|---|---|---|
| BASELINE live pct_k14 k4 H10 | +3.46% | +3.25% | +3.05% | +3.64 / +2.89 | +0.589 (0.635/0.546) | 0.000 | 0.83 | +$14.00 |
| **PRIMARY meme-excluded (both legs)** | +3.88% | **+3.68%** | +3.48% | **+4.34 / +3.06** | **+0.636 (0.677/0.606)** | 0.000 | 0.81 | **+$15.83** |
| SENS long-leg-only exclusion | +3.60% | +3.39% | +3.18% | +3.98 / +2.83 | +0.576 (0.613/0.547) | 0.000 | 0.83 | +$14.57 |

Dominance gate:
- **PRIMARY: 4/4 WIN — STRICT DOMINANCE.** h1 EV +4.34 vs +3.64, h1 Sh 0.677 vs 0.635,
  h2 EV +3.06 vs +2.89, h2 Sh 0.606 vs 0.546. Also dominates at net50 both halves
  (+4.13/+2.86 vs +3.43/+2.68). 19/33 rebalances improved, 7 unchanged, 7 hurt.
- SENS long-only: FAILS (h1 Sharpe 0.613 vs 0.635 LOSE; h2 EV +2.83 vs +2.89 LOSE).
  The half-measure is worse than the full exclusion AND fails to beat the baseline.

## Decomposition — where the net comes from (the surprise)
Inside the BASELINE book (33 rebalances):
- Meme LONG legs: 11 legs, **−0.260%/rebal** (−8.6pp total) → exclusion avoids real bleed.
  Per-coin: PUMP L −7.6pp, FARTCOIN L −4.5pp, kPEPE L −1.5pp; but TRUMP L +3.7pp and
  DOGE L +1.1pp — meme longs are net toxic, not uniformly toxic.
- Meme SHORT legs: 19 legs, **+0.157%/rebal** (+5.2pp total) → the exclusion genuinely
  FORFEITS earning shorts (kPEPE S +6.8pp, kBONK S +3.7pp, VINE S +2.3pp — consistent with
  the live observation that meme shorts have been paying; though FARTCOIN S −3.4pp and
  PUMP S −3.1pp cut the other way).

Realized delta (variant − baseline, %/rebal, full and h1/h2; identities asserted):
| | net25 | long leg | short leg | fee |
|---|---|---|---|---|
| PRIMARY | +0.426 (+0.694/+0.174) | +0.132 (+0.335/−0.060) | **+0.289 (+0.357/+0.224)** | +0.006 |
| SENS long-only | +0.133 (+0.335/−0.058) | +0.132 (+0.335/−0.060) | 0 by construction | +0.001 |

The improvement is mostly the SHORT side, and NOT by keeping meme shorts — by replacing
them. With only 4 short slots, memes crowd out better shorts: the replacement bottom-4
names (ACE +3 legs, BCH/WLD/ONDO +2, INJ/LINK/AAVE/UNI...) out-earned the meme shorts they
displaced in BOTH halves (+0.357/+0.224). The long-side benefit (avoided PUMP/FARTCOIN
bleed) is real but h1-concentrated (+0.335/−0.060). This is exactly why the asymmetric
variant fails: it keeps the short-slot crowding and harvests only the h1-heavy long-side
effect. Paired t on the PRIMARY delta: +1.36 full (h1 +1.11, h2 +1.06) — underpowered as a
t-test; the pre-registered gate is dominance, which it passes 4/4, but the effect size is
modest (+0.43%/rebal, ~13% relative lift, +$1.83/wk at current sizing).

## VERDICT: **DOMINANT (4/4 pre-registered checks) — wire-worthy as a reversible config-level
## exclusion, with the honesty caveat that the paired t is only ~1.4 (n=33).**
Deciding numbers: net25 **+3.68% vs +3.25%**, Sharpe **+0.636 vs +0.589**, both halves, both
metrics, survives 50bps, slightly LOWER turnover, zero new risk surface (same recipe, same
sizing, 42-name instead of 50-name eligible set). The long-only variant is REFUTED as a
half-measure (fails 2/4 checks). The forfeited meme-short EV (+0.157%/rebal) is more than
paid back by better replacement shorts (+0.289%/rebal net short-side delta).

## SPEC — implementable block (one config-driven line in the live engine; PRIMARY only)
```
change          hermes_trader/agents/xs_momentum_live.py::_eligible (line 134): after the
                tradeable-perp filter, drop coins in xs_momentum.exclude_coins BEFORE the
                volume ranking — i.e. the eligible set never contains them, BOTH legs
                (the asymmetric long-only variant FAILED its gate; do not wire that).
config          xs_momentum.exclude_coins = the 20 declared SECTOR_MAP MEME names
                (research/alpha_swarm/hypotheses/W-X2_xs_widening.py): DOGE, kPEPE, kBONK,
                WIF, FARTCOIN, TRUMP, SPX, PENGU, POPCAT, MEW, MOODENG, PUMP, CASHCAT,
                PURR, VINE, TURBO, HMSTR, BABY, kSHIB, kFLOKI. Hot-readable list; revert =
                set []. Everything else in the recipe unchanged (pct_k14, k4, H10, top-50,
                vol gate, exits per e248c13).
maintenance     the list is STATIC: a new meme entering the top-50 is not auto-excluded.
                Review exclude_coins whenever a new name enters the top-50 universe.
expected        +0.43%/rebal over baseline (+$1.8/wk at $76.8/leg sizing; scales with
                equity). Modest — the case is dominance + less tail exposure, not size.
KILL (pre-committed)
                1. counterfactual A/B: at each forward rebalance compute the unfiltered
                   book offline (deterministic from candles); if cumulative forward net25
                   delta (excluded − unfiltered) < 0 after 6 rebalances (~60d) → revert
                   exclude_coins to [] same day.
                2. any single rebalance where the exclusion costs > 2% book EV vs the
                   counterfactual → review immediately, revert unless explained.
                3. re-grade at 12 rebalances against +0.43%/rebal; < half of expectation →
                   revert (the paired t was 1.4; forward data must earn the change).
```

Caveats: survivor-biased top-50 cache (upper bounds); n=33 rebalances, 30 meme legs total —
small-sample; paired t ~1.4 (dominance gate passed, significance not established); funding
not modeled; sim ungated (live vol gate zeroes both variants identically); the meme list is
hand-declared (though declared BEFORE cell B ran, and W-X3 showed the mechanical alternative
fails).

## Scoreboard line
W-X4 meme_exclusion_overlay: DOMINANT 4/4 — live recipe minus declared MEME names: net25
+3.68% vs +3.25%, Sh 0.636 vs 0.589, both halves both metrics, survives 50bps, lower
turnover. Decomposition: forfeited meme-short EV +0.157%/rebal is out-earned by replacement
shorts (+0.289 net short-side delta, both halves); avoided meme-long bleed (PUMP −7.6pp,
FARTCOIN −4.5pp) is h1-heavy. Long-only exclusion FAILS 2/4 (keeps short-slot crowding).
Paired t only 1.4 (n=33) — wire via xs_momentum.exclude_coins with pre-committed
counterfactual kill; spec in findings.
