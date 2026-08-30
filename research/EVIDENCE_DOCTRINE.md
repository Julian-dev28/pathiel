# Evidence doctrine

**No shadows. No recorders. Proven backtests.**

Operator directive, 2026-08-30. This file is the rule; the tests named at the
bottom are the enforcement.

## The three states a book may be in

There are two. A book **trades**, or it **does not exist**.

There is no shadow tier, no recorder tier, and no "validated but has no capital
path" tier. Those produced the exact state this doctrine exists to end: on
2026-08-29 the grader printed `unlock_short — VALIDATED: validated but has no
bounded capital path (recorder//counterfactual)`. A book that can prove itself
and still never trade costs API budget, log volume and attention to maintain
evidence nothing is allowed to act on.

## How a new book earns capital

A backtest that clears all four:

1. **Positive net of real costs.** 25bps round trip, not the measured 6.1bps.
   A stop checked on the intrabar extreme, not the close.
2. **Both OOS halves positive.** A decaying edge is a fit. W-SESS1 died here:
   +0.504R first half, +0.060R second.
3. **Beats a matched null.** Same coins, same holding rule, random entry times,
   >=2000 draws. Never "beats zero" — a drifting asset beats zero. W-ME1 died
   here: +2.15% against a +1.61% random-entry null.
4. **Survives multiple-comparison correction.** Bonferroni over every cell
   examined, not just the one being reported. W-SESS1 also died here: p=0.036
   against a 0.00625 threshold for 8 cells.

Clear all four and the book goes **live**, bounded. Fail any and it is deleted
and the refutation written to `research/alpha_swarm/findings/` so nobody
rediscovers it.

## The honest limit of this doctrine

**Some signals have no retrievable history and therefore cannot be backtested.**
Of the four live books, three are in this position:

| book | why not backtestable |
|---|---|
| `news_surge_short` | Google News has no point-in-time history API |
| `news_surge_multi` | same, across 15 RSS firehoses |
| `social_trending` | every free social-history source is dead — CoinGecko `/coins/{id}/history` returns 0.0 for reddit fields, LunarCrush v4 is paid |

These three are live on **forward** evidence (n=255 / 230 / 185, both OOS halves
positive, mc_p=0.0005 each), which the grader's own docstring argues is
*stronger* than a backtest: it is point-in-time by construction with no
survivorship bias.

That evidence already exists and was accrued before this doctrine. Going
forward the consequence is strict and worth stating plainly:

> A signal with no retrievable history and no existing forward record cannot be
> validated under this doctrine, and therefore must not be built.

That is a real cost. It rules out a class of idea. It is preferred to the
alternative, which is a permanent shelf of recorders accruing evidence nobody
may act on.

## Where the evidence lives

- backtests: `research/alpha_swarm/hypotheses/W-*.py`
- verdicts: `research/alpha_swarm/findings/W-*.md`
- forward grades: `.state/shadow_ledger/<book>.jsonl`, graded by
  `scripts/autonomous_cycle.py`, which demotes any live book whose forward
  record turns negative at the verdict fee tier

## Refuted, do not rebuild

| finding | what died |
|---|---|
| W-ME1 | main_engine — no excess over null (p=0.117), 0 signals in 17 days at the live gate |
| W-ME2 | the main-engine trigger stack, every component, 208 days, n up to 1140 — zero of 12 pre-registered cells clear Bonferroni. trendStrength's claimed +2.08% lift does NOT survive out of sample |
| W-SESS1 | Asia/London session-sweep reversal — fails Bonferroni, decays 8x across halves |
| W-U2 | (validated — unlock run-in, n=408, p=0.0040) |
| W-FND1 | funding z-score battery — 8 cells, majors, 208d. Zero survivors. Funding on majors is asymmetric: +2σ fires 20x, −2σ fires 219x |
| W-XS2 | cross-sectional momentum/reversal — 6 cells, 833d. Zero survivors. Momentum-120 shows the edge EXPIRING: h1 +1.37%, h2 −0.90% |
| W-SEARCH | the 2026-08-30 search in full: 39 pre-registered cells, one survivor, and that one was already live |
