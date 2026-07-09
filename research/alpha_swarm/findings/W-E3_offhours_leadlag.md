# W-E3 — closed-hours BTC -> xyz equity lead-lag

**Hypothesis:** while the underlying is SHUT (nights/weekends), crypto is the only live macro venue, so BTC's last bar should lead the xyz equity perp's next bar (the complement of findings/stock_crypto_leadlag.md, which refuted lead-lag on equity-ACTIVE bars only).

**Rule tested:** closed bar = 1h (and 15m) bar fully outside RTH incl. weekends/holidays. Cross-correlations pooled over 36 names; tradeable probe: |BTC 1h ret| >= {0.5%,1%} during closed hours -> same-direction xyz at next bar open, hold {1,3}h, basket per event hour (dedup), full cost tiers. Script: hypotheses/W-E3_offhours_leadlag.py.

## Results

Correlations (closed hours only):

| window | contemp | BTC leads 1 | BTC leads 2 | xyz leads 1 |
|---|---|---|---|---|
| 1h (n~115k) | +0.301 | **+0.041** | +0.007 | -0.005 |
| 15m (n~28k) | +0.384 | +0.023 | +0.005 | +0.001 |
| 1h weekend only | +0.199 | +0.018 | — | — |
| 1h weekday-night | +0.327 | +0.047 | — | — |

Tradeable probe (episodes = distinct event hours):

| rule | n | 0bps | 6bps | 12bps | 25bps | p_sign | OOS @12bps |
|---|---|---|---|---|---|---|---|
| \|btc\|>=0.5%, 1h | 737 | **+0.057%** | -0.003% | -0.063% | -0.193% | 0.0000 | -0.04 / -0.08 |
| \|btc\|>=1.0%, 1h | 183 | +0.078% | +0.018% | -0.042% | -0.172% | 0.013 | +0.00 / -0.09 |
| 3h holds | — | +0.044% | negative | negative | negative | 0.07-0.25 | both neg |

**VERDICT: REFUTED (by costs).** The deciding number: a statistically real off-hours lead EXISTS — +0.057%/next-hour at p_sign=0.0000, lead-corr +0.04 vs the active-bars ~symmetric +0.02-0.06 of the prior study — but it is worth ~6bps gross and is **negative at 6bps slippage, -0.19% at 25bps**, at every threshold and hold. The co-move is 87% contemporaneous (0.30 same-bar vs 0.04 next-bar): the xyz perp reprices crypto-style macro within the same hour even at 3am. Textbook statistically-significant-economically-dead. Do not build. (Also closes the loop on the prior refute: neither active NOR closed hours are laggable.)
