# worldmonitor relevance — thesis test (2026-07-21)

Operator ask: can we use github.com/koala73/worldmonitor (500+ news feeds
AI-synthesized; cross-stream military/economic/disaster/escalation
convergence; finance radar 7-signal composite) for a trading edge? **Test the
thesis first; build only if EV+.**

## Method

Did NOT clone the dashboard (Ollama + 500 feeds + 3D globe — huge, and most of
it is irrelevant to what we trade: crypto perps + xyz US-equity tokens + a few
commodities). Instead tested the THESIS behind each category against data we
already have, and pulled the one extractable asset (the curated feed list).

## Category-by-category verdict

### 1. News feeds / AI-synthesized attention — RELEVANT (already trading it)

We have a 3,858-row clean-epoch news-attention dataset (the `news_catalyst`
ledger: `surge_x` coverage-surge + `n_recent` article count per coin per read).
Tested the core worldmonitor premise — *does news-attention MAGNITUDE predict
the move?* — with the lookahead-safe production grader (6% stop, the real live
geometry), SHORT side (the validated attention-fade direction):

| news attention | n | SHORT EV25 | win |
|---|---:|---:|---:|
| non-breaking (surge<3) | 200 | +1.94% | 55% |
| **breaking (surge≥3)** | 10 | **+9.71%** | 80% |

**~5x dose-response.** More news attention → bigger next-day move. The thesis
is real — but we ALREADY exploit it: `news_surge_short` is live and shorts the
breaking end. worldmonitor's incremental value is source BREADTH (catch surges
our single Google-News query misses), not a new edge.

### 2. Cross-stream escalation (military/disaster/econ convergence) — LOW

Our instruments are crypto + US AI/tech-equity tokens. These are not
geopolitically driven at the daily scale. W-P2 already tested scheduled macro/
policy catalysts and REFUTED them: crypto policy resolves into majors
continuously, not at the gavel (no abnormal move vs a 2000x random-time null,
p 0.21-0.69). Geopolitical→commodity links (oil/gold) are real, but we barely
trade commodities. A conflict/escalation signal would need its own forward test
and has a low prior for our book. NOT pursued now.

### 3. Finance radar / 7-signal market composite — LOW (near-refuted class)

A cross-asset market-state/breadth composite is a regime signal, and we have
largely refuted that class: `beta_rotation` REFUTED, `correlation_regime_gate`
MARGINAL, W-X6 momentum-theory layers 0/13 beat the live recipe. Low prior.

## The extractable asset

`research/worldmonitor/rss-feeds-report.csv` (420 feeds, copied from the repo).
The working DIRECT (non-Google-proxied) finance/tech feeds — genuinely new
coverage we don't already read:

CNBC, CNBC Tech, Financial Times, Yahoo Finance (x2), Seeking Alpha, TechCrunch,
Ars Technica, The Verge, MIT Tech Review, ZDNet, TechMeme, Engadget, Fast
Company, Hacker News.

**Mechanism mismatch to flag:** these are GENERAL firehoses (CNBC home, FT home),
while `news_surge_short` uses a PER-COIN Google-News query. Using the firehoses
for per-coin surge needs headline→coin entity matching — a real build, not a
config swap.

## Recommendation (bounded, follows the operator's test→build→ship flow)

1. **Do not clone the dashboard.** Wrong tool for our instrument set.
2. **The one EV+ path:** widen `news_surge_short`'s coverage. Build a ZERO-CAPITAL
   shadow recorder that reads the ~15 direct finance/tech firehoses, entity-
   matches headlines to our coin set, computes a multi-source coverage surge,
   and records to a NEW ledger. The autonomous cycle grades "multi-source surge"
   vs the live single-source read. Ship live ($20/10x, the same bounded
   geometry) ONLY if it grades EV+ AND beats the current source. Same
   THESIS→TEST→EVOLUTION loop as everything else — no capital until validated.
3. Escalation + market-composite: parked, low prior, not built.

## Status

Thesis TESTED (news attention EV+, dose-response confirmed; other two low).
Asset SAVED. Build = the multi-source surge recorder above — a bounded next
step, spec'd here. No live change made from this analysis.
