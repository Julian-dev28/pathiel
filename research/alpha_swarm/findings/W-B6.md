# W-B6 cross_asset_vol_spillover

## Hypothesis
BTC realized vol LEADS alt realized vol (vol clustering across the cross-section); a BTC-vol sizing
signal can pre-position the XS book. Sizing edge, not direction.

## Rule
Realized vol = 5d stdev of daily returns. alt_vol[t] = mean RV across the 39 non-BTC coins. (1) lead-lag
corr(BTC_vol[t], alt_vol[t+k]) k=0..3, and BTC's lead vs alt-vol's own AR(1) persistence at predicting
alt_vol[t+1]. (2) BTC-vol median-split sizing overlay on the XS book (k=14,H=7,m=6): up-size (1.5x hi /
0.67x lo) vs down-size vs flat; annSharpe lift + maxDD, OOS both halves.

## Results
Lead-lag corr(BTC_vol[t], alt_vol[t+k]): k=0 +0.661, k=1 **+0.542**, k=2 +0.389, k=3 +0.241.
Predict alt_vol[t+1]: BTC_vol[t] corr +0.542 vs alt_vol[t] own persistence corr **+0.850**.

| sizing | annSh | maxDD | OOS h1/h2 sh | lift vs flat |
|--|--|--|--|--|
| flat | +3.279 | -9.9% | 0.67/0.27 | — |
| up-size hi-BTCvol | +2.652 | -14.8% | 0.66/0.16 | -0.627 |
| down-size hi-BTCvol | +3.421 | -10.6% | 0.56/0.38 | +0.142 |

## VERDICT: REFUTED
Deciding number: BTC vol does spill over (corr to alt_vol[t+1] = +0.542, decaying with lag), but it is
**redundant — alt vol's own AR(1) persistence (+0.850) dominates BTC's lead (+0.542)**, so BTC adds no
marginal predictive info; the vol complex just clusters jointly. As a sizing signal it is inert: the best
overlay (down-size in high BTC vol) lifts annSharpe only **+0.142** (inside noise on n=40 rebals), while
the intuitive up-size (B15 "book loves turbulence") actually HURTS -0.627 and worsens drawdown — the same
"high-vol up-size adds variance faster than mean" failure seen in W-B1/W-B3/B15. No tradeable
pre-positioning edge. The faint down-size-in-high-vol tilt agrees in sign with B5 (book likes calmer
tape on a Sharpe basis) but is too small to wire. RISK: n=40 thin; 5d RV is one window choice.
