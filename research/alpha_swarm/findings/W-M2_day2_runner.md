# W-M2 — day-2 runner persistence

**Hypothesis:** a coin that closes a UTC day +8..20% on >= $5M day volume keeps running the next day (SYRUP did +13% then +15% — real pattern or selection memory?).

**Rule tested (pre-registered):** complete UTC day return in [+8%,+20%], day dollar volume >= $5M -> enter LONG at next day's first 1h open. Exits: hold {24,48}h x stop {5,8,15}% + KAITO trail. Splits all / BTC-20d-up / BTC-20d-dn. MC null = same-coin random-day entry (every liquid day, no move condition). Family 21 cells, Bonferroni alpha 2.38e-03. 296 signals over 208d x 40 coins.

## Result: 0 / 21 cells wire-eligible

| cell | n | EV12 | EV25 | OOS h1 | OOS h2 | MC p |
|---|---|---|---|---|---|---|
| h24_s5 all | 296 | +0.02 | -0.11 | -0.33 | +0.11 | 0.17 |
| h24_s8 all | 296 | +0.01 | -0.12 | -0.48 | +0.25 | 0.17 |
| h24_s15 all | 296 | -0.20 | -0.33 | -0.64 | -0.01 | 0.42 |
| h48_s5 all | 296 | +0.07 | -0.06 | -0.35 | +0.22 | 0.18 |
| h48_s8 all | 296 | +0.01 | -0.12 | -0.89 | +0.66 | 0.18 |
| h48_s15 all | 296 | -0.34 | -0.47 | -1.50 | +0.56 | 0.46 |
| trail all | 296 | -0.48 | -0.61 | -1.54 | +0.33 | 0.51 |
| best regime cell: h48_s8 dn | 156 | +0.34 | +0.21 | **+1.13** | **-0.76** | 0.17 |
| worst instability: h48_s15 up | 119 | -0.79 | -0.92 | -4.26 | +3.09 | 0.54 |

Every all-view cell is EV25-negative. The only EV25-positive cells (dn-regime h24_s8/h48_s5/h48_s8) all sign-flip across OOS halves and have p >= 0.15. The up-regime cells swing -4%/+3% between halves — pure noise.

## VERDICT: REFUTED

Deciding number: best cell EV25 +0.21% with OOS halves (+1.13, -0.76) and p=0.17 vs required 2.4e-03. Day-2 persistence after a +8..20% day is not a tradeable pattern on this universe; SYRUP was selection memory. Survivorship makes even these numbers upper bounds.
