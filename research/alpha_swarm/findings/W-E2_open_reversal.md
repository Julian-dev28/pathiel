# W-E2 — RTH-open volatility transfer: overnight xyz drift into the US open

**Hypothesis:** the weekday overnight xyz move (underlying shut, perp trading) either reverts when real flow arrives at the US open (fade) or continues (price discovery / after-hours-news drift).

**Rule tested:** episode = plain weekday overnight (prev trading day p -> d, 1 calendar day; weekends are W-E1). ref = close at p's RTH close; m = pre-open close on d (13:00 UTC EDT) vs ref; fill at next 1h bar open; exit at d's RTH close (~7h). Variants: first-3h exit, post-open (open+30m) fill, and **BTC-residualized m_res = m − beta_PIT·BTC_overnight** (expanding per-name beta, min 20 obs — splits crypto-tracked drift from idiosyncratic drift). Basket per day = ONE episode. Script: hypotheses/W-E2_open_reversal.py; data W-E_dataset.json (36 names, 107 overnights).

**Structure result:** per-day basket corr(m, session ret) = **-0.01** — no aggregate open-reversal on weekdays (opposite of the weekend's -0.34). Name-level: full-session +0.05, post-open->close +0.12 (mild continuation), indices first-3h -0.13 (small index open-fade, not tradeable size).

## Results (per-day basket episodes)

| rule | n | 12bps | 25bps | 50bps | OOS H1/H2 @12bps | p_sign | p_shortpool |
|---|---|---|---|---|---|---|---|
| FADE raw \|m\|>=1% | 106 | -0.49% | -0.62% | -0.87% | -0.81 / -0.16 | 0.96 | 0.99 |
| MOMO raw \|m\|>=1% | 106 | +0.25% | +0.12% | -0.13% | +0.57 / **-0.08** | 0.039 | 0.167 |
| MOMO raw \|m\|>=2% | 101 | +0.32% | +0.19% | -0.06% | +0.85 / **-0.22** | 0.049 | 0.376 |
| **MOMO-RESID \|m_res\|>=0.5%** | 87 | +0.23% | +0.10% | -0.15% | **+0.21 / +0.24** | **0.017** | **0.020** |
| **MOMO-RESID \|m_res\|>=1.0%** | 87 | +0.29% | +0.16% | -0.09% | **+0.24 / +0.33** | **0.031** | **0.035** |
| MOMO-RESID \|m_res\|>=2.0% | 80 | +0.47% | +0.34% | +0.09% | +0.38 / +0.56 | 0.035 | 0.726 (short leg = beta) |
| FADE post-open fill \|m\|>=1% | 106 | -0.43% | -0.56% | — | both neg | — | — |

Stops on the fade: inert (8-40% all ~-0.22..-0.25% name-level) — the fade is just wrong, not stopped out.

**Reading:** overnight FADE is cleanly refuted on weekdays. Raw momentum sign-flips OOS, but **residualizing out the coin's crypto beta fixes it at every threshold**: the crypto-correlated share of overnight drift is noise; the IDIOSYNCRATIC share (after-hours earnings/news priced by the 24/7 perp while cash is shut) CONTINUES through the session — classic overnight-news drift, and it is the coherent complement of W-E1 (news-less weekend drift fades; news-driven weekday drift persists).

**VERDICT: MARGINAL (shadow-wire candidate).** Deciding numbers: MOMO-RESID @1% — n=87 independent day-episodes, +EV both OOS halves (+0.24/+0.33 @12bps), survives 25bps (+0.16%), both MC nulls < 0.05 (p_sign 0.031, p_pool 0.035), consistent across all three thresholds. Kept MARGINAL not ROBUST because: EV@25bps is thin (sharpe-like 0.08), it is the best cell of a 12-cell sweep, and the 2% threshold's short leg fails the same-side pool (down-beta, mirror of the W-A3 caveat). Survivorship upper bound applies.

**Shadow-wire spec (the Lane E best):** daily at 13:00 UTC (EDT; 14:00 EST): for each US-RTH xyz name >= $700k vol with >=20 prior overnights, m_res = overnight move minus PIT-beta × BTC overnight move; if |m_res| >= 1%, enter sign(m_res) at next 1h open, equal-weight basket, exit at RTH close; wide stop 15% (inert); log to shadow ledger and forward-grade ~87 episodes/quarter.
