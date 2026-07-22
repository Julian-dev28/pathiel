# W-MATH3 — Sector momentum on the xyz tokenized-equity complex

Lane MATH, 2026-07-23. READ-ONLY. Scripts + caches live in the session
scratchpad (`fetch_sector.py`, `mathlib.py`, `math3_run.py`, `math3_supp.py`,
`W-MATH3_cache_daily.json`). Data = fresh HL daily candles through 2026-07-22
for the full live `xyz` HIP-3 universe (104 markets) + BTC / xyz:XYZ100 /
xyz:SP500 benchmarks, fetched once, paced 0.35s. All returns are perp returns
(what we would actually trade), PIT, no lookahead. Costs 25 bps/side (50 bps
round-trip per leg, 100 bps for a long/short pair). Seed 20260723.

## Motivation

Our xyz-equity SHORTS all stopped out together on 2026-07-21 when the AI/semis
bloc ripped. This lane asks whether there is a tradeable SECTOR-momentum edge
distinct from (a) the refuted broad up/down regime gate and (b) the existing
coin-level `xs_momentum` book.

## Sectors (equal-weight daily-return indices, only names with data)

Built from calendar-consecutive perp daily returns; a date enters a sector
index only when >=2 members have a valid return that day (handles staggered
listing dates without survivorship bias). 10 sectors cleared >=20 days of
history:

| sector | members with data | index history | median $vol |
|---|---|---|---:|
| ai_neocloud | NBIS, CRWV, CBRS (3) | 2026-05-02.. (82d) | $21.5M |
| memory_storage | MU, SNDK, SKHX, SKHY, DRAM, KIOXIA, WDC (7) | 2026-01-13.. (191d) | $260M |
| semis_chips | NVDA, AMD, AVGO, TSM, ASML, ARM, AMAT, QCOM, INTC, MRVL, SMH (11) | 2025-12-04.. (231d) | $1.9M |
| megacap_tech | GOOGL, TSLA, AAPL, MSFT, META, AMZN, NFLX (7) | 2025-11-19.. (246d) | $19.5M |
| crypto_equity | COIN, MSTR, HOOD, CRCL (4) | 2025-11-27.. (238d) | $9.3M |
| china_tech | BABA, ZHIPU, MINIMAX (3) | 2026-06-19.. (34d) | $3.9M |
| space | RKLB, SPCX (2) | 2026-05-18.. (66d) | $247M |
| enterprise_sw | ORCL, IBM, NOW, DELL (4) | 2026-06-02.. (51d) | $6.9M |
| metals | GOLD, SILVER, COPPER, PLATINUM, PALLADIUM (5) | 2025-12-27.. (208d) | $1.7M |
| energy | CL, BRENTOIL, NATGAS, XLE (4) | 2026-01-22.. (182d) | $163M |

**HARD small-n / young-history flag up front.** The AI complex only co-exists
from ~2026-05; the cross-section of sectors with real history is ~5-8 for most
of the sample, and the clean non-overlapping headline tests have **n=12-26
rebalances**. Every number below is one xyz listing wave (semis/AI-hardware
tape). Treat as a coefficient estimate, not a validated book.

## 1. Time-series sector momentum (own trailing return -> own forward return)

Pooled OLS `fwd_h ~ a + b·trail_lb`, non-overlapping decision dates (step=h):

| lb | h | n | slope b | t(b) | r | fwd\|strong tercile | fwd\|weak | L-S net |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 7 | 5 | 287 | +0.063 | +1.21 | +0.07 | +1.20% | -0.39% | +0.59% |
| 7 | 14 | 101 | **-0.149** | -0.97 | -0.10 | -0.12% | +1.67% | -2.80% |
| 14 | 7 | 195 | -0.009 | -0.17 | -0.01 | +0.95% | +0.02% | -0.07% |
| **14** | **14** | 94 | **+0.208** | **+2.10** | +0.21 | +4.92% | +0.02% | **+3.89%** |
| 30 | 10 | 119 | +0.075 | +1.64 | +0.15 | +3.35% | +1.34% | +1.00% |
| 30 | 14 | 82 | +0.117 | +1.94 | +0.21 | +4.26% | -0.34% | +3.60% |

- **The signal is a 14-day phenomenon.** At h=14 both the 14d and 30d
  lookbacks give a positive momentum coefficient (b≈0.12-0.21, t≈1.9-2.1,
  r≈0.21). At h=5-7 there is nothing (|t|<1.3). At lb=7/h=14 the sign FLIPS
  negative (short-horizon extension mean-reverts). So momentum only exists
  when both the lookback and the hold are ~2 weeks; it is not robust across
  the (lb,h) grid.
- **Momentum half-life ≈ 6 days.** AR of the trailing-14d return on the next
  non-overlapping trailing-14d return: rho=+0.208 (t=+2.10, n=94) →
  half-life = ln0.5/ln(rho)·14 ≈ **6.2 days**. Strong sectors do keep winning,
  but the persistence decays inside a week.
- **Verdict (TS):** a real, marginally-significant +momentum coefficient at
  the 14d horizon, half-life ~6d. t~2 on overlapping n; the honest effective
  sample is a handful of independent 2-week blocks.

## 2. Cross-sectional sector momentum (long strongest / short weakest sector)

Rank sectors by trailing-lb, long top-k / short bottom-k, hold h, net 100 bps
pair fee, non-overlapping. Matched null = same dates, RANDOM sector picks (2000
iters). "Sharpe" column below is the aggregate t (= per-rebal Sharpe·√n).

| lb | h | k | n | EV net | t | win | null EV | mc_p |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 7 | 7 | 1 | 26 | +0.14% | +0.05 | 46% | -1.06% | 0.29 |
| 14 | 7 | 1 | 25 | +1.65% | +0.75 | 56% | -1.00% | 0.10 |
| **14** | **14** | 1 | 12 | **+7.88%** | +1.51 | 58% | -0.96% | **0.018** |
| 14 | 14 | 2 | 12 | +4.65% | +1.39 | 58% | -0.91% | 0.024 |
| 30 | 14 | 1 | 11 | +2.29% | +0.44 | 36% | -0.98% | 0.23 |

The only cell that beats the random-sector null is L14/H14 (mc_p 0.018), and it
**does not survive robustness**:
- **n=12, driven by 3 fat rebalances** (+24.3, +34.5, +41.7%) out of 12;
  the LONG leg is memory_storage 7 of 12 times — a one-sector artifact.
- **Sign-flip permutation p = 0.080** (n=12) — the honest test of "does the
  momentum-selected spread beat zero" fails at 0.05. The random-sector mc_p is
  optimistic because it dilutes with bad pairs; the sign-flip p is the real
  one.
- **Overlapping-sample (step=1, n=164): EV +2.03%, naive t=1.47** (t inflated
  by overlap) — i.e. once you use all the data the effect shrinks to marginal.

**Verdict (XS): REFUTE.** No cross-sectional sector-momentum config clears a
matched null honestly. What edge appears is one memory-sector wave inside a
12-point sample.

## 3. Ride vs fade — the AI-sector event and the general asymmetry

**(a) The 2026-07-21 event was a REVERSAL, not momentum.** Into the pop the
AI-bloc trailing return was NEGATIVE (7d -7.5%, 14d -12.0%); it then printed
**+10.6% in one day, 18 of 24 names up >5% simultaneously**. A sector-MOMENTUM
book would have been SHORT the bloc into 07-21 (weak trailing return) — the
SAME side as our book — and taken the same hit. Sector momentum would NOT have
saved us here. The failure mode was a correlated short-squeeze of a
beaten-down bloc (co-movement risk), not being on the wrong side of a trend.

**(b) The ride/fade asymmetry is real and one-sided (pooled sectors):**

| horizon | bucket | n | trail | fwd | RIDE(long) net | FADE(short) net | t(fwd) |
|---|---|---:|---:|---:|---:|---:|---:|
| h=7 | Q5 strongest | 41 | +12.7% | +2.98% | **+2.48%** | **-3.48%** | +1.57 |
| h=7 | Q1 weakest | 41 | -10.5% | +1.50% | +1.00% | -2.00% | +1.05 |
| h=14 | Q5 strongest | 18 | +19.1% | +6.23% | **+5.73%** | **-6.73%** | +1.87 |
| h=14 | Q1 weakest | 18 | -13.1% | +1.28% | +0.78% | -1.78% | +0.49 |

- **Riding strong sectors long is +EV; fading them (shorting winners) is
  sharply -EV** — the asymmetry the operator asked about (+2.5 vs -3.5 at h7,
  +5.7 vs -6.7 at h14, widening with horizon, consistent with the 14d
  momentum). t=1.6-1.9: real-signed, marginal-n.
- **Shorting recent LOSERS is also -EV** (Q1 weakest bounces forward +1.3 to
  +1.5% → short-weak -1.8 to -2.0%). So EVERY short posture on this complex
  loses: fade winners -3.5/-6.7%, short losers -2.0/-1.8%. The short leg is the
  problem, whichever way the sector was trending. Our 07-21 book shorted a weak
  bloc → the -2% (reversion) bucket, then caught the squeeze tail.

## 4. Coin trend vs sector trend — sector adds nothing beyond the coin's own trend

Pooled 2-factor test on all xyz coins, lb=14/h=14, n=829 coin-obs:

| predictor of a coin's fwd-7d | slope | t | r |
|---|---:|---:|---:|
| coin's OWN trailing-14d | +0.047 | **+2.00** | +0.07 |
| its SECTOR's trailing-14d | +0.039 | +1.33 | +0.05 |
| corr(coin trail, sector trail) | | +0.81 | |
| sector INCREMENTAL (resid on coin) -> fwd | | **-0.50** | |
| coin INCREMENTAL (resid on sector) -> fwd | | +1.57 | |

Double-sort mean fwd-7d by sign of (coin, sector) trailing-14d:

| | sector+ | sector- |
|---|---:|---:|
| coin+ | +0.82% (n=314) | **+1.23%** (n=74) |
| coin- | -0.13% (n=88) | -0.58% (n=353) |

- **The coin's own trend is the predictor (t=2.0); the sector's trend is not
  additive.** Once you condition on the coin's own trailing return, the sector
  trend's incremental t is **-0.50** (nothing). corr(coin,sector) = 0.81 — a
  coin's "sector momentum" is 81% just its own momentum. In the double-sort the
  coin sign dominates; coin+/sector- actually beats coin+/sector+.
- **Combining coin AND sector does NOT beat coin-alone.** A sector overlay is
  redundant with a coin-level momentum book.

## 5. This is NOT the broad-regime gate, and NOT redundant with xs_momentum

- **Not the broad-regime gate.** On 07-21 the broad index barely moved —
  XYZ100 +1.89%, SP500 +0.79% — while the AI-hardware bloc averaged +10.6%.
  A broad up/down gate reads "quiet +2% day" and flags nothing; the entire
  event lived inside one sector. Cross-sector dispersion of trailing-7d returns
  averages **6.4% std** across sectors — sectors do NOT move as one bloc, so a
  sector signal carries information the market-direction gate cannot. Sector /
  XYZ100 trailing-7d correlations: AI 0.81, semis 0.76, memory 0.74, megacap
  0.63, but energy -0.23, enterprise 0.34, metals 0.45 — the low-corr sectors
  are where a sector L/S would be genuinely market-neutral. This is a distinct
  object from the refuted blanket regime gate (`regime_gate_backtest.py`) and
  from the W-Y4 eq7 gate (which is the broad equity index by construction).
- **Not redundant with xs_momentum — by universe.** `xs_momentum._eligible`
  drops any coin containing ':' → every xyz name is excluded; xs_momentum
  trades CRYPTO perps only. Universe overlap with the xyz sector study = ∅.
  BUT (part 4) the sector signal is subsumed by COIN-level own-trend momentum,
  which is exactly the factor xs_momentum already harvests. So sector momentum
  is the same factor xs_momentum trades, on a disjoint universe, in a form
  (sector basket) that a coin-level book would dominate.

## TS-vs-XS verdict

**Time-series sector momentum is the stronger of the two** (14d coefficient
b≈0.21, t≈2.1, half-life ~6d) but only at the 2-week horizon and only
marginally significant. **Cross-sectional sector momentum is refuted**
(sign-flip p=0.08, memory-wave artifact, shrinks to +2%/naive-t-1.5 on full
data). Both are 14d-horizon effects; shorter horizons are noise or mild
reversal. Neither survives as a standalone book.

## DECISION: REFUTE a standalone tradeable sector-momentum book

Three independent reasons, any one sufficient:
1. **Too weak.** Best XS config n=12, sign-flip p=0.080; TS t~2 on overlapping
   n only. No config clears a matched null at p<0.05 on the honest test.
2. **Redundant.** Sector momentum is subsumed by a coin's own-trend momentum
   (incremental sector t=-0.50, corr 0.81); coin-level momentum is the stronger
   predictor (t=2.0) and is already the `xs_momentum` mechanism.
3. **Not tradeable at scale.** Half the sectors' members are sub-$5M/day
   (semis median $1.9M, metals $1.7M); the liquid cross-section is ~5 sectors —
   too thin for a robust rank at the assumed 25bps.

## Actionable, non-book findings for the LIVE short books (this is the real payload)

The event that burned us was **correlated-basket risk**, and the momentum math
says the AI-hardware SHORT is -EV in every trend state. Two guarded overlays,
both distinct from the refuted broad gate, both testable on existing ledgers:

1. **Sector/correlation cap on same-bloc shorts.** 07-21 stopped out the whole
   book because it held many shorts in ONE correlated bloc (18/24 AI-hardware
   names popped together). Cap concurrent shorts per sector (e.g. <=2 of the
   AI/semis/memory/china bloc) so one squeeze is not one account-wide bet. This
   is what would actually have bounded the 07-21 loss — a broad regime gate
   would not (index was flat).
2. **"Don't short a strong sector" veto.** If a short candidate's SECTOR
   trailing-14d return is in the top quintile, skip it: fading strong sectors
   grades -3.5% (h7) / -6.7% (h14). This is the sector-level analog of the
   W-Y4 broad eq7 gate and is complementary to it (07-21's sector was weak, so
   this veto would not have caught it — reason #1 would).

Both are size/veto overlays on the existing short books, NOT a new momentum
book, and each ships with its own two-sided ledger split before enforcement.

## Caveats

- One xyz listing wave (~May-Jul 2026), semis/AI-hardware tape; effective
  independent sample is a few 2-week blocks. n<30 on every headline (TS 14d
  n=94 overlapping, XS n=12, ride/fade n=18-41).
- xyz perps track stocks that reprice only on weekdays; weekend perp candles
  add noise the 7-30d windows mostly wash out but do not eliminate.
- 25bps/side is optimistic for the illiquid sector members; a real book uses
  only the >=$5M names, further thinning the cross-section.
- Sector definitions are hand-drawn; a different grouping could shift the XS
  ranks. The TS and coin-vs-sector conclusions do not depend on the grouping
  boundary.

## Decision rule going forward

Do NOT build a sector-momentum book. If the two overlays above are pursued,
re-run this cache monthly (xyz history is growing ~1 wave/month); revisit the
TS 14d coefficient at 2026-08-23 when the AI complex has ~2x the history — only
re-open the XS book question if the sign-flip permutation p drops below 0.05 on
n>=25 non-overlapping rebalances.
