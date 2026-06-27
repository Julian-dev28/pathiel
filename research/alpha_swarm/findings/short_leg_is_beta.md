# W-A3 short_leg_is_beta — is the short-deep leg alpha or down-beta?

**Hypothesis:** shorting the deepest-50d-drawdown basket is +EV on its own. Test whether it
survives BTC-beta residualization or is just down-beta in the −44% tape.

**Rule:** rank by close/max(50d high)−1; short bottom-6 DEEP. beta_i = OLS to BTC on trailing
30 daily rets (bars≤i). residual fwd = coin_fwd − beta_i·BTC_fwd. Score RAW short vs
BETA-RESIDUAL short, each as excess over its matched random-short pool. Fill open[i+1]→[i+1+H].

## Results
| | n | EV0 | EV12 | EV25 | EV50 | OOS h1 / h2 | excess | z | p |
|---|---|---|---|---|---|---|---|---|---|
| DEEP-short **RAW** (H7,k6) | 762 | +1.09 | +0.97 | +0.84 | +0.59 | +0.24 / +1.71 | +1.60% | +3.54 | 0.00025 |
| DEEP-short **BETA-RESIDUAL** (H7,k6) | 762 | +0.21 | **+0.09** | −0.04 | −0.29 | **+1.95 / −1.80** | +1.30 | +3.06 | 0.0018 |
| RAW (H7,k8) | 1016 | +0.66 | +0.54 | +0.41 | +0.16 | +0.09 / +1.00 | +1.17 | +2.95 | 0.0015 |
| RESIDUAL (H7,k8) | 1016 | −0.13 | **−0.25** | −0.38 | −0.63 | **+1.79 / −2.33** | +0.96 | +2.59 | 0.0018 |
| RAW (H5,k6) | 774 | +0.72 | +0.60 | +0.47 | +0.21 | +0.15 / +1.05 | +1.06 | +2.73 | 0.0033 |
| RESIDUAL (H5,k6) | 774 | +0.02 | **−0.10** | −0.23 | −0.48 | **+1.32 / −1.55** | +0.83 | +2.28 | 0.0098 |

**Deep-basket avg beta = +1.24 vs universe avg beta = +1.15** (deep names are higher-beta).

## VERDICT: **REFUTED — the short-deep leg is ~down-beta, not standalone alpha.**
Deciding number: **stripping BTC beta collapses EV12 from +0.97 to +0.09 (→ −0.25 at k8) and
the OOS halves SIGN-FLIP (+1.95 / −1.80).** The raw short edge was harvested from the −44%
tape: deepest-drawdown coins carry beta +1.24 (above the +1.15 universe), so shorting them is
overweight down-beta. The residual (true cross-sectional) component is a sign-flip = noise,
positive in the falling first half and negative in the recovering second half — exactly a
regime bet, not alpha.

**Why the matched-random-short control (W-A2, excess +1.60 z=3.54) was misleading:** that pool
carries only the AVERAGE beta (1.15); the deep basket's beta TILT (1.24) shows up as "excess"
that is really beta, not skill. Explicit per-coin beta-residualization is the correct control
and it kills the leg.

**Amends W-A2:** I called short-deep the "stronger standalone keeper." Corrected — after
beta-stripping NEITHER leg is a clean standalone. A13's both-halves L/S robustness leaned on
the short leg harvesting down-beta in this specific falling tape; it will NOT generalize to a
flat/up regime. Mirrors the A2/A4/A12 short-tilt caveat. **Do not size the short-deep leg as
alpha.** If A13 ships at all, it must be as the LONG-near leg (front-loaded, regime-gated) or a
beta-NEUTRALIZED L/S, not the raw L/S whose short side is a regime bet.
