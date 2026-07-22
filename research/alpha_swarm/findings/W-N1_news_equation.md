# W-N1 — News-Score Equation & the Overnight→Open News Gate

**Date:** 2026-07-23
**Scope:** tokenized-equity perps (`xyz:`) + crypto, news-coverage-surge episodes
**Data:** `.state/shadow_ledger/{news_catalyst, news_surge_short, news_surge_multi}.jsonl`
(07-12 → 07-22) joined to HL 1h candles via `fetch_hl_candles`.
**Classifier:** `mover_recorders.classify_news_polarity` ONLY, `news_risk=None` →
pure deterministic keyword on the recorded `top3_titles`. No LLM re-read (no look-ahead).
**Method:** PIT. Dedup to (coin, UTC-day) episodes. Matched null = per-coin random
entry bar, B=2000. EV net 25bps roundtrip.

## VERDICT

- **REFUTE** the news gate for `mover_pass_short` / `young_mover_short`.
- **REFUTE** the NEWS_SCORE equation as a forward-return predictor (R²≈0.004, hit rate ≈ coin flip).
- **REFUTE** the inverse long-the-positive-news edge (−2.2% net, worse than random, mc_p 0.80).
- The operator's first-pass (positive-news +5.47%, 78% up on n=4–9) **inverts on the 3× larger sample**: positive-news equities went DOWN (mean −2.05%, 68% down). Small-n luck.

## THE EQUATION

```
NEWS_SCORE = pol_sign · log(1 + surge_max) · exp(−age_h / 12)
  pol_sign  ∈ {−1, 0, +1}   classify_news_polarity(None, " ".join(top3_titles))
  surge_max = peak coverage-surge (surge_x) that coin/day
  age_h     = age of the freshest of the top-3 headlines (hours)
```

Regressing forward LONG return on NEWS_SCORE (OLS):

| set | horizon | n | slope | R² | corr | dir. hit rate |
|---|---|---|---|---|---|---|
| all | ret_1d | 104 | −0.0025 | 0.001 | −0.03 | 43% (n=53 polar) |
| equity | ret_1d | 72 | +0.0038 | **0.004** | +0.06 | **49%** (n=37) |
| equity | ret_to_open | 79 | +0.0009 | 0.000 | +0.02 | 51% (n=39) |

Component decomposition (equity, ret_1d): `pol_sign`, `signed_log_surge`,
`log_surge`, and the full `news_score` all land R²≈0.003–0.004, corr≈0.05–0.06.
**No component carries signal.** The equation is noise on forward return. Directional
hit rate is a coin flip. There are no usable coefficients — the fit is flat.

## OPERATOR TABLE — forward return by polarity (EQUITY, deterministic keyword)

| polarity | n | mean ret_1d | %up | mean ret_to_open | %up |
|---|---|---|---|---|---|
| positive | 22 | **−2.05%** | **32%** | −1.27% | 33% |
| negative | 15 | −3.68% | 27% | −2.33% | 20% |
| neutral  | 35 | +0.01% | 54% | +0.39% | 57% |

The operator's n=4–9 pass ("POSITIVE +5.47%, 78% up") **does not replicate**. On n=22
positive-news equity episodes the names went DOWN 68% of the time (good for a short,
not bad). Everything freshly-surged drifts down (pos −2.0%, neg −3.7%, neutral ~0):
the real pattern is polarity-AGNOSTIC mean-reversion of names that just moved — the
existing extreme_fade / mover-fade edge, not a news signal. Matched null: positive-news
%up=32% vs random-day, **mc_p=0.94** (positive news, if anything, predicts DOWN — and
even that is not significant beyond the general down-drift).

## THE GATE — block a SHORT when NEWS_SCORE is strongly bullish

Joined each short-book trade to its nearest titled news read (≤8h). 11 equity shorts
matched (small n — flagged honestly).

| | n | short EV_net (1d) |
|---|---|---|
| UNGATED equity shorts | 11 | −6.07% |
| gate `news_score≥1.0` → KEPT | 6 | −6.15% |
| … BLOCKED (would-forfeit) | 5 | −5.98% |

**Delta from gating = −0.08%.** The gate removes shorts that were about as bad as the
ones it keeps — it does not separate winners from losers. Every threshold {0.5, 1.0,
1.5, 2.0} is the same story. Matched null on the gate delta: ~0.

### Why the gate is structurally broken — the operator-flagged losses

| coin | book | short_ret_1d | keyword polarity |
|---|---|---|---|
| xyz:CBRS | mover_pass_short | **−15.14%** | **neutral** |
| xyz:SNDK | mover_pass_short | **−9.11%** | **neutral** |
| xyz:DELL | young_mover_short | −10.76% | **(no news read)** |
| xyz:NBIS | young_mover_short | −10.61% | positive ✓ catchable |
| xyz:DELL | young_mover_short | −8.83% | positive ✓ catchable |
| xyz:NBIS | young_mover_short | −7.77% | neutral |

The two worst blow-ups (CBRS −15%, SNDK −9%) classify **NEUTRAL** — the keyword list
("Memory Slump", "Kaplan Fox Alerts…") never fires bullish. One DELL loss had no news
read at all. A "block positive-news short" gate catches only 2 of 6 flagged losses and
pays for them by also forfeiting profitable shorts. It cannot see the tail it was built
to stop.

## THE OPEN-GAP MECHANISM — real, but polarity-blind

The 13:00 UTC (9:30 ET) open bar and the overnight drift into it (equity):

| polarity | n | open-bar move | %up | overnight drift (entry→open) |
|---|---|---|---|---|
| positive | 24 | +0.58% | 58% | −1.81% |
| negative | 15 | +0.04% | 67% | −2.36% |
| neutral  | 40 | **+1.52%** | 65% | −1.11% |

The operator's observation is REAL: names drift down overnight (short goes green) then
the open bar gaps UP. But it happens for **every** polarity and is **strongest for
NEUTRAL** names. News polarity cannot select which names gap up. Overnight-entry shorts
covered at the open were net **+0.36%** (n=41, 49% win) — positive-news overnight shorts
to open were +1.36%, i.e. the thesis-predicted disaster did not occur in aggregate.

## INVERSE — LONG strong-positive-news equity names

| gate | n | LONG EV_net (1d) | to_open EV_net |
|---|---|---|---|
| news_score≥0.5 | 22 | −2.30% | −1.51% |
| news_score≥1.0 | 19 | −2.22% | −1.36% |
| news_score≥1.5 | 10 | +0.42% | −0.48% |

Matched null (news_score≥1.0 long, per-coin random day): obs −1.97%, **mc_p=0.80** —
positive-news longs did WORSE than random timing. Not an edge. REFUTE.

## MATCHED-NULL SUMMARY (B=2000, per-coin random entry bar)

| claim | obs | n | mc_p | read |
|---|---|---|---|---|
| A: equity-short gate thr=1.0 beats ungated | Δ −0.08% | 11 | ~1.0 | gate useless |
| B: LONG positive-news equity fwd_1d | −1.97% | 19 | 0.80 | worse than random |
| C: ungated equity SHORT fwd_1d | −5.82% | 11 | 0.998 | books shorted right before up-moves |
| D: positive-news equity %up | 32% | 22 | 0.94 | positive news ≠ up |

## WHERE NEWS DOES NOT PREDICT (the honest boundary)

- Keyword polarity has **no forward-return signal** on this universe/window (R²≈0, hit
  rate ≈ 50%). The classifier is neutral-heavy: 45/88 equity episodes read neutral,
  including the biggest short losses, so it is blind exactly where it would need to fire.
- The open-gap-up is not polarity-conditional, so no news feature selects it.
- The real, polarity-agnostic pattern (surged names revert down intraday) is already
  captured by extreme_fade / the mover-short books. The mover-short **equity** leg's
  problem is entry SELECTION (young_mover_short equity −8.1% net, 19% win — it shorts the
  names that gap up), not a missing news filter. Fixing that is an entry-quality problem,
  not a news-gate problem.

## CAVEATS

- Small n throughout (11–25 matched shorts; 22 positive-news equity episodes; 2.5–10 day
  window). Nothing here can be PROMOTED. But every cut — regression, null, the flagged-loss
  audit, the open-gap decomposition — points the same way, and the gate's failure to see
  4 of 6 biggest losses is structural, not a sample-size artifact.
- Deterministic keyword classifier only (as instructed). A better event-time NLP read
  MIGHT carry signal the keywords miss, but that is a new data question, not this gate.

## RECOMMENDATION

Do **not** wire a NEWS_SCORE gate onto `mover_pass_short` / `young_mover_short`. It does
not improve short EV and cannot see the tail losses that motivated it. Do **not** add a
long-positive-news book. If the mover-short equity bleed is the concern, the lever is
entry selection (why it shorts names about to gap up), which is orthogonal to news and
should be studied against the extreme_fade fade-quality work, not a polarity filter.
