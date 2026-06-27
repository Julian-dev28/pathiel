# A5 idiosyncratic_vol_anomaly

**Hypothesis:** Low idiosyncratic vol (residual after stripping BTC beta) outperforms;
long low-idio-vol / short high-idio-vol is a +EV market-neutral factor (IVOL anomaly).

**Exact rule:** day i, trailing W daily rets, beta to BTC, residual e=r_c-beta*r_btc,
idio_vol=std(e). Long bottom-m=6, short top-m=6. Fill open[i+1], hold H, non-overlapping.
Also a total-vol (no-strip) variant. vs random 50/50 baseline. W{20,40,60} x H{5,7,14}.

## Results (per-leg signed gross %, 12bps)
| variant | W | H | EV0 | EV12 | OOS h1 / h2 | excess |
|---|---|---|---|---|---|---|
| idio | 20 | 5 | -0.35 | -0.47 | +0.71 / **-1.69** | -0.43 |
| idio | 40 | 14 | -1.98 | -2.10 | -1.52 / -2.82 | -1.36 |
| idio | 60 | 14 | -1.23 | -1.35 | +0.51 / **-3.74** | -0.61 |
| total | 60 | 14 | +0.17 | +0.05 | +1.43 / **-1.72** | +0.79 |

## Verdict: **REFUTED**
Deciding number: the idio variant is **negative at 0 bps in all 9 configs** (best -0.35%/leg)
and every config shows a violent sign-flip across halves — h1 > 0 then h2 ≈ -1.7 to -3.7.
Low-idio-vol led in the first (down) half; high-idio-vol/junk *bounced hard* in the second
(recovery) half. The one positive total-vol config (W60 H14, EV0 +0.17) dies by 25 bps and
sign-flips +1.43/-1.72. No stable idiosyncratic-vol premium; it's a disguised regime bet that
reverses with the tape.
