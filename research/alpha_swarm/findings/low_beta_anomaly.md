# A4 low_beta_anomaly (BAB)

**Hypothesis:** Betting-against-beta — long low-beta-to-BTC coins, short high-beta,
leverage-neutralized — is a +EV market-neutral factor.

**Exact rule:** day i, beta_c = cov(r_c, r_btc)/var(r_btc) over trailing W daily rets.
Long bottom-m=6 beta, short top-m=6. Fill open[i+1], hold H, non-overlapping.
RAW = equal-weight legs. BNEUT = the actual BAB: long leg scaled 1/avg|beta_long|,
short leg scaled 1/avg|beta_short| so the book is ~beta-neutral. vs random 50/50 baseline.

## Results (per-leg / portfolio signed gross %, 12bps)
| W | H | RAW EV12 | RAW h1/h2 | excess | **BNEUT EV12** | BNEUT h1/h2 |
|---|---|---|---|---|---|---|
| 40 | 5 | +0.32 | +0.37 / +0.27 | +0.36 | **-0.21** | -0.59 / +0.19 |
| 60 | 5 | +0.29 | +0.31 / +0.27 | +0.33 | **-0.08** | -0.51 / +0.36 |
| 60 | 7 | +0.47 | +0.72 / +0.19 | +0.58 | **-0.15** | -0.68 / +0.42 |
| 60 | 14 | +0.73 | +0.44 / +1.10 | +1.47 | **-0.75** | -1.82 / +0.62 |
| 20 | 14 | +0.97 | +3.47 / -1.81 | +1.71 | +0.42 | +0.92 / -0.13 |

## Verdict: **REFUTED** (as an anomaly)
Deciding number: the leverage-neutral BAB construction — the *only* version that isn't just
a market-direction bet — is **negative in 8 of 9 configs** (W60 H14 BNEUT = -0.75%/leg).
The RAW long-low/short-high book IS robustly positive both halves at W60 (EV12 +0.29 to +0.73,
excess up to +1.47, h2 holds), but that book is net-SHORT-beta (it shorts the high-exposure
leg) and only pays because the tape is -44%. Neutralize the beta and the edge evaporates →
there is no low-beta *anomaly*, only a directional short-beta tilt that already lives inside
tsmom (A2) and the regime layer. Do not size it as a separate market-neutral factor.
