# W-GAP1 xyz 9:30-ET open gap — REFUTED (no open-excess; it's pure same-day tape/beta)

## Hypothesis
Tokenized US-equity perps ("xyz:") systematically GAP UP at the 9:30 ET open, so
"buy pre-open, sell into the open" is a tradeable edge. Prior W-N1 saw the open bar
**+1.52%** for *neutral* names — but that was a BIASED sample (news-surged names only)
in a BULL window with n=40. This tests it on the FULL universe.

## Data / method (PIT, no lookahead)
- **Full xyz universe from HL meta** (`metaAndAssetCtxs?dex=xyz`), NOT the ledgers →
  **103 markets**; **77 equity single-names** after excluding the 26 declared
  non-equity xyz (indices/commodities/fx/baskets, `NON_EQUITY_XYZ`).
- **1h candles**, up to 5000 bars/coin (median 1906, max 5001). Span **2025-12-29 →
  2026-07-22, 148 weekdays**. 15 names flagged with <30 open obs; IBOV had 0 bars.
- **Open bar** = the 1h bar whose **ET-local start hour == 9** (covers 09:00–10:00 ET,
  contains the 09:30 cash open). DST handled by converting each UTC bar ts → America/New_York.
  On a 24/7 perp adjacent 1h bars are contiguous (prev_close == next_open) → there is NO
  inter-bar gap; the "open move" is the o→c realized during that hour. **This is the exact
  measurement basis W-N1 used** (its "13:00 UTC open bar"), so the comparison is apples-to-apples.
- Bar return = c/o − 1. Costs: 12 & 25 bps round-trip. n and t-stats everywhere.
  Scripts: `research/alpha_swarm/hypotheses/W-GAP1_{fetch,analyze}.py`,
  cache `W-GAP1_cache_1h.json`.

## TEST 1 — the open-bar return (pooled, 77 equity names, n=6349)
| metric | value |
|---|---|
| mean | **−0.074%** |
| median | −0.008% |
| hit rate (>0) | **48.1%** |
| std | 2.173% |
| t | **−2.72** (p=0.0066) |
| net 12bps | −0.194% |
| net 25bps | −0.324% |

The open bar is **slightly NEGATIVE**, not +1.52%. The prior number was entirely the
news-surge selection × bull window. On the full universe the open bar loses money and
its up-rate is below 50%.

## TEST 2 — EXCESS at the open vs the same names' other bars  [THE VERDICT]
| quantity | value | n |
|---|---|---|
| open-bar mean | −0.0741% | 6349 |
| baseline A — all 24 weekday hrs | −0.0052% | 152,696 |
| baseline B — other equity hrs 10–16 ET | −0.0065% | 38,319 |
| **excess vs all-24h (paired /name,day)** | **−0.0717%**, t=**−2.62**, p=0.0088 | 6349 |
| excess vs equity-other (paired /name,day) | −0.0682%, t=−2.45, p=0.014 | 6349 |
| **MC-null (random-hour-as-open, 2000 draws)** | obs −0.0717% vs null +0.0001%±0.0120% → **p(one-sided for positive excess)=1.0000** | — |

There is **no positive open-excess** — the open bar is *worse* than a random hour, and
the observed excess sits at the extreme bottom of the random-hour null. The decisive test
fails hard: the open is not special, except very slightly to the downside.

## BETA CHECK — is any "gap" just market beta?
- **xyz:XYZ100 open-bar mean = +0.003% (t=0.06)** — the US index itself does NOT gap up.
- xyz:SP500 open-bar mean = +0.038% (t=1.10) — not significant.
- market-neutral (name-open − EW-of-names-open, same day) = +0.0000%, t=0.00 — zero
  cross-sectional selection at the open. Nothing to pick.

## TEST 3 — persistence + regime  (the "it's just drift" proof)
| slice | open mean | n | t |
|---|---|---|---|
| H1 (≤2026-04-10) | −0.074% | 2084 | −1.86 |
| H2 (>2026-04-10) | −0.074% | 4265 | −2.08 |
| H1 paired-excess | −0.079% | 2084 | −1.97 (p=0.049) |
| H2 paired-excess | −0.068% | 4265 | −1.91 (p=0.056) |
| **open on UP-tape days (EW-open≥0)** | **+0.651%** | 3054 | **+17.86** |
| **open on DOWN-tape days (EW-open<0)** | **−0.746%** | 3295 | **−20.42** |

The (slightly negative) open effect is **stable in both halves — it never flips positive**
in any sub-period, so it is NOT an AI-rally artifact that turned off. And the open bar
simply moves **with the day's tape**: +0.65% on up days, −0.75% on down days, symmetric.
That is textbook contemporaneous beta/drift — there is no standalone gap-up you could
capture without already knowing the day's direction.

## TEST 5 — direction / fade
- post-open hour (10:00–11:00 ET) o→c: −0.004%, t=−0.17, p=0.86 — **no fade**.
- next-hr after an UP open: −0.065% (t=−2.09); after a DOWN open: +0.053% (t=1.99) — a
  ~5–6bps mean-reversion, both **far below** any round-trip cost. Not tradeable.
- **Short-the-open** would capture the +7.4bps gross downdrift but pay ≥12–25bps → net
  **−EV** (gross edge < cost). Neither long nor short survives costs.

## TEST 4 — tradeable structure (best of the entry-lead × hold sweep, net 25bps)
| entry → exit | gross | net25 | t | Sharpe(net) |
|---|---|---|---|---|
| 09:00 → 10:00 | −0.074% | −0.324% | −2.72 | −0.149 |
| 08:00(−1h) → 10:00 | −0.105% | −0.355% | −3.62 | −0.154 |
| 07:00(−2h) → 10:00 | −0.065% | −0.315% | −2.13 | −0.129 |

**Every** entry/hold combination is negative gross and net. Entering pre-open (08:00 ET)
is strictly worse. There is no capture structure.

## BREADTH
Only **5/62** names (≥30 obs) have a net-25bps-positive open bar (BB, WDC, NBIS, DELL,
INTC — low-n memory/semis names that rode the H1-2026 semis run; survivorship, not an
open effect). 57/62 are negative; the worst (RKLB −1.10%, BIRD −1.12% net) are speculative
small caps.

## VERDICT — **REFUTED**
The "buy pre-open, sell into the 9:30 gap" edge does not exist on the full xyz universe.
The +1.52% was biased-sample bull drift. Deciding numbers: full-universe open-bar mean
**−0.074%** (t=−2.72), paired open-excess **−0.072%** (t=−2.62, MC-null p=1.00 for any
positive excess), the US index open bar **+0.003%** (t=0.06, no gap), and the open moving
+0.65%/−0.75% purely with same-day tape. It is drift/beta, not an open-specific edge, and
the residual ±7bps is smaller than the round-trip cost in either direction. **Do not build
an open-gap book.** Live xyz alpha stays with the validated cross-sectional residual-momentum
book (W-X2/W-X5), which this does not touch.

**Caveat (honest):** measured at 1h resolution (same basis as the W-N1 claim it refutes).
A sub-minute pop-and-fade right at 09:30:00 could in principle hide inside the 1h bar, but
(a) that is not what W-N1 claimed and (b) it would be un-capturable on these HIP-3 spreads
anyway. Not worth further API strain given how decisive the 1h verdict is.
