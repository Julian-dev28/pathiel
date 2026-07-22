# W-MATH2 — Portfolio math: correlation, effective bets, Kelly/MV allocation, sector cap, deployment target

Date: 2026-07-23 · Equity ~$113 ($60 crypto dex / ~$53 xyz dex) · Net of fees (12bps round-trip) · PIT forward grades

**Bottom line:** we are running **~3 effective bets, not ~15**. 57% of short risk sits in one correlated
cluster (the xyz-equity "short AI-momentum" complex), which is **1.28 effective bets held four ways** and which
stopped out together overnight (-$8.59). Worse than concentration: **no book has a statistically real positive
edge net of fees**, and **every live xyz-short book has ZERO resolved episodes** — we are deploying real money into
unmeasured, mutually-identical bets. The path to "more deployed" is diversification: cap the xyz-short cluster to
~1 name's worth of risk and redeploy the freed budget into the one genuinely uncorrelated book (xs_momentum crypto).

---

## 0. Method & honesty flags

- **Grading**: replicated `hermes_trader.agents.shadow_ledger.grade_records` exactly (dedup episodes → simulate
  side/stop/horizon exit → net of funding), per-coin candle cache to stay gentle on HL (186 fetches total).
- **The data is thin.** After de-duplicating re-signalled clusters into independent episodes, only **6 of ~20 books
  have ≥8 resolved episodes**, and the newest live short books have **0** (1-day horizon needs 3 days to resolve;
  the books are 2-3 days old). Per-book means are therefore mostly noise — flagged n<8 / n<30 throughout.
- **Correlation cannot be estimated from realized daily book PnL** — the live short books have 1-2 active days each
  and don't overlap in time. So correlations use a **basket-implied proxy**: build the 119-coin daily-return
  covariance Σ (60 days, winsorized ±40%), define each book's signed-frequency exposure vector w over coins, and
  compute implied book covariance wᵢᵀΣwⱼ. This is dense, uses all 60 days, and directly measures "do these baskets
  move together" regardless of when each book fired. Validated where a time-series proxy also exists (extreme_fade
  ↔ long-crypto books both methods agree positive).
- **Assumption**: absolute gross dollar exposures are estimated from config sizing × plausible concurrency, then
  **calibrated to the observed -$8.59 overnight loss** (≈1.3σ down-day ⇒ real concurrent gross ≈ 4.5x equity).
  Treat gross as a multiple of equity (robust); the absolute $ is ±40%.

---

## 1. Correlation matrix & effective number of independent bets

Basket-implied return correlation (via coin covariance, all 60 days). Longs and shorts on opposite baskets are
negatively correlated by construction; the story is the **positive block among the shorts**.

```
                  xs_mom xs_xyz extrm mp_sh ym_sh ns_sh ns_ml engf crash unlk whale ylist mvpas mb15
xs_momentum(cr)    1.00
xs_xyz_equities    0.12  1.00
extreme_fade      -0.18  0.07  1.00
mover_pass_short   0.03  0.60 -0.35  1.00
young_mover_short  0.03  0.60  0.01  0.69  1.00
news_surge_short  -0.04  0.44 -0.54  0.91  0.68  1.00
news_surge_multi  -0.01  0.60 -0.33  0.94  0.84  0.94  1.00
engulf_short       0.09  0.04 -0.63  0.48  0.17  0.53  0.45  1.00
crash_cont_short   0.30  0.09 -0.57  0.43  0.21  0.43  0.44  0.62 1.00
unlock_short      -0.25  0.00 -0.59  0.50  0.20  0.65  0.49  0.66 0.37 1.00
whale_flow         0.25  0.10  0.79 -0.46 -0.03 -0.67 -0.41 -0.64 -.36 -.83 1.00
```

**Effective number of independent bets** (participation ratio (Σλ)²/Σλ² of the correlation eigenvalues):

| Scope | Books | Effective bets |
|---|---:|---:|
| Full universe (basket-implied) | 15 | **2.87** |
| Live subset | 10 | 3.21 |
| Live, allocation-weighted at CURRENT sizing | 10 | 4.54 *(inflated — xs_momentum alone holds 40% at ~0 corr)* |
| **The 4 xyz-equity SHORT books** | 4 | **1.28** |
| All 7 short books | 7 | 2.23 |

**Most-correlated cluster:** `mover_pass_short`, `young_mover_short`, `news_surge_short`, `news_surge_multi` —
mean pairwise corr **0.83** (news_surge_short↔news_surge_multi = **0.94**, mover_pass_short↔news_surge_multi = 0.94).
These four are one bet. `xs_momentum` (crypto XS) is the only book **orthogonal to everything** (|corr| ≤ 0.30) — the
single best diversifier we own.

**Why the cluster is one factor — coin-level evidence (deterministic, no estimation):**
- **57%** of all live short signals (889 / 1548) are xyz-equity names.
- **22 distinct xyz names are shorted by 3 different books at once** (e.g. NBIS, CBRS, SNDK, MU, DRAM, SKHX, RKLB,
  ZHIPU, MINIMAX, DELL, KIOXIA, WDC, TSLA...). Same names, same side, held 3-4 ways.
- Among 43 xyz names the top principal component explains **42%** of daily-return variance (≈5 effective independent
  names across the whole sector) vs 37% common for crypto — so shorting the sector broadly ≈ one macro short.
- The -$8.59 overnight ≈ 7 xyz shorts × $20 notional × 6% stop, all tripping together. The math predicts exactly this.

---

## 2. Per-book edge / variance (deduped independent episodes, net of 12bps)

| Book | n | mean%/ep | std% | t | p | Sharpe/ep | win | verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| whale_flow | 77 | -0.05 | 9.64 | -0.05 | .96 | -0.01 | .42 | **noise** (~0; +67% listing outlier masks a slightly-neg median) |
| mover_pass (long) | 20 | **-5.35** | 6.48 | **-3.69** | .002 | -0.83 | .25 | **REAL NEGATIVE** |
| young_listings | 18 | -2.50 | 9.19 | -1.15 | .26 | -0.27 | .28 | noise (neg lean) |
| mover_b15_up | 15 | -2.44 | 13.11 | -0.72 | .48 | -0.19 | .40 | noise |
| **news_ta_quadrant** | 13 | **+4.39** | 8.10 | 1.95 | .075 | +0.54 | .77 | **best +EV, near-significant** (thin) |
| premium_fade_short | 9 | -6.86 | 7.64 | -2.70 | .027 | -0.90 | .11 | REAL NEGATIVE (already refuted) |
| extreme_fade | 6 | **-10.16** | 9.85 | -2.53 | .05 | -1.03 | .17 | **negative, UNPROVEN n<8** — 3 big stops (-22/-21/-11) |
| unlock_short | 6 | +2.30 | 4.90 | 1.15 | .30 | +0.47 | .83 | positive, unproven |
| unlock_short_runin | 6 | +1.14 | 7.21 | 0.39 | .72 | +0.16 | .67 | noise |
| engulf_short | 4 | -2.98 | 8.04 | -0.74 | .51 | -0.37 | .50 | unproven |
| **xs_xyz_equities** | **0** | — | — | — | — | — | — | **live, NO resolved data** |
| **mover_pass_short** | **0** | — | — | — | — | — | — | **live, NO resolved data** |
| **young_mover_short** | **0** | — | — | — | — | — | — | **live, NO resolved data** |
| **news_surge_short / _multi** | **0** | — | — | — | — | — | — | **live, NO resolved data** |
| xs_momentum (crypto) | 0 | — | — | — | — | — | — | live (main-engine recorder); prior ~+1.4%/10d from memory |

**Reading:** exactly **one** book is positive with |t| approaching 2 (news_ta_quadrant, +4.39%, t=1.95, n=13).
Two are significantly **negative** (mover_pass, premium_fade). The designated crypto-long diversifier
`extreme_fade` is **-10%/ep** on its last 6 (down-tape crushing the long-fade; note config has it `shadow_only:true`
and the *live* policy is the `armed=true` subset only, which I did not isolate — re-grade that subset before leaning
on it as the hedge). **Every live xyz-short book is unmeasured.** With means this noisy, a mean-variance optimizer
would fit garbage — so the allocation below is driven by **variance (robust)**, not by these means.

---

## 3. Optimal allocation (robust: min-variance & risk-parity, since means are unreliable)

At the **same total gross**, re-mixing from current sizing to long-only **min-variance** cuts portfolio daily vol
**35%** (15.0% → 9.7% of equity) and raises effective bets (4.54 → 4.93):

| Book | CURRENT gross % | Min-variance % | Note |
|---|---:|---:|---|
| xs_momentum (crypto) | 43.9% | 29.7% | keep — the diversifier |
| unlock_short | 3.0% | 25.9% | ⚠ MV chases lowest-vol/low-corr book; **unproven n=6** — cap, don't chase |
| xs_xyz_equities (long) | 4.9% | 19.5% | long xyz hedges the xyz shorts |
| extreme_fade | 14.6% | 16.7% | hedge, but currently bleeding — re-validate |
| news_surge_short | 6.1% | 5.6% | **keep ONE** xyz-short as the sector representative |
| mover_pass_short | 6.1% | **0.0%** | redundant with news_surge — **drop** |
| young_mover_short | 6.1% | **0.0%** | redundant — **drop** |
| news_surge_multi | 6.1% | **0.0%** | redundant (0.94 corr) — **drop** |
| engulf_short | 4.6% | 1.6% | |
| crash_continue_div_short | 4.6% | 1.0% | |

**Headline:** the math **zeroes out 3 of the 4 xyz-short books** as pure redundancy and keeps one representative.
Min-variance's 26% tilt into `unlock_short` is a robustness artifact (it rewards low measured vol on n=6) — use
**risk-parity** as the deployable template instead (xs_momentum 28%, xs_xyz 22%, unlock 13%, extreme_fade 0%,
each short book 4.5-7.5%), which spreads risk without over-betting one unproven low-vol book. Full-Kelly on the
prior means ≈ 2.9x equity gross; **half-Kelly ≈ 1.4x** — but Kelly on these means is not trustworthy; treat it as an
upper sanity bound, not a target.

**Contrast with current:** current sizing is **over-concentrated + under-diversified** — 44% of gross and 40% of
*risk* in xs_momentum, another 43% of risk in the xyz cluster, and it runs portfolio daily vol at **exactly 15.0%
of equity = the kill-switch's 1σ**. One ordinary bad day trips the daily kill.

---

## 4. The sector cap (the number)

Risk contribution today: **xyz-equity cluster = 43% of portfolio risk, all-shorts = 57%.** The 4 xyz-short books
are 1.28 effective bets, so funding four of them buys ≈ zero diversification over funding one.

**Cap (two equivalent forms, both enforced):**
1. **≤ 2-3 concurrent DISTINCT xyz-equity short names**, deduped **at the sector level across all books** (today the
   four books re-short the same ~22 names → ~7 concurrent; cut to ≤3).
2. **xyz-equity short notional ≤ 20-25% of equity (~$25-28 total)** — down from the ~$140 (124% of equity) that was
   on overnight. This is min-variance's ~6% gross share scaled to a 3.5x deployment; the round number is **25% of equity**.

Redeploy the freed budget into the **uncorrelated** book (`xs_momentum` crypto, |corr|≤0.3) and the long xyz hedge
(`xs_xyz_equities`), not into more shorts. Because the freed capital moves from a 0.83-correlated cluster into a
~0-correlated book, portfolio variance falls for the **same** gross and Sharpe rises for **any** positive mean —
independent of the (unreliable) edge estimates.

---

## 5. Full-deployment target

Daily return vol per unit of deployed gross: **concentrated (current) mix = 1.29%/gross**; **diversified (min-var)
mix = 0.83%/gross**. With the 15%-of-equity daily kill switch, the max gross before a bad day auto-kills:

| Bad-day size | Concentrated mix | **Diversified mix** |
|---|---:|---:|
| 2.0σ = kill | 4.6x equity | **9.0x** |
| 2.5σ = kill | 3.6x | **7.2x** |
| 3.0σ = kill | 3.0x | **6.0x** |

We are **already at the concentrated ceiling** (~4.5x gross, calibrated to -$8.59). **Diversification is what lets us
deploy more:** the same ruin budget supports ~7x gross diversified vs ~4.5x concentrated — **≈60% more deployable
capital for the same risk.**

**Recommendation — deploy ~3.5-4x gross on the diversified/capped mix** (≈ half-Kelly and ≈55% of the 2.5σ ceiling,
leaving headroom for fat tails and for the fact that the edges are unproven). In margin terms at ~8x avg leverage
that is ≈ 45-50% of equity as margin. Concretely: this is **roughly today's capital-at-risk, re-mixed** — same
gross, but ~35% lower daily vol, the daily-kill 1σ→~2.5σ away instead of 1σ, and room to scale toward ~6x as the
xyz-short and xs_momentum books actually accumulate resolved episodes. **Do not push toward "fully deployed" on the
current concentrated book — that ceiling is 4.5x and we're on it.**

---

## Caveats
- Means are too thin to trust; allocation is variance-driven. Re-run when the live xyz-short + xs_momentum books
  have ≥8 resolved episodes each (≈1-2 weeks).
- Basket correlations are 60-day estimates; the long/short hedge relationships min-variance exploits could break in a
  correlated crash (everything → 1). The deployment target already discounts for this (half-Kelly, k=2.5σ).
- `extreme_fade` graded unconditionally here; its live policy is the `armed=true` subset — re-grade that subset.
- `funding_spike_short` is live in config but has **no ledger file** → 0 records, un-analyzable; verify it is
  recording.
```
scripts used: scratchpad/{grade,extend_panel,basket_corr,edge,alloc2}.py — read-only on live code/state.
```
