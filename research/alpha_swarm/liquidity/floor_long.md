# floor_long — can `min_market_volume_usd` ($700k long floor) drop?

**VERDICT: KEEP $700k.** If anything the long-breakout edge concentrates ABOVE the floor (≥$5M), not below it. Every band at and below the floor is −EV net of its own (higher) slippage, OOS-fragile, and these are survivor-biased upper bounds. Lowering the floor admits −EV longs. This confirms and sharpens the prior extension/latency finding.

**Deciding number:** the lowest band whose net-of-band-slippage edge is +EV in BOTH OOS halves is **$5M–$20M (25bps band)**. The edge dies stepping down to 2-5M and is catastrophic at 0.1-0.7M (the sub-floor band). The current $700k floor sits two bands *below* where the edge actually lives.

## Method
- NATIVE coins only (16/16/14/12/3/5 per band), daily bars from `marginal_dataset.json`. BTC up-regime (close>20d SMA) from main `dataset.json`, aligned by timestamp.
- Entry A (the bot's real long): **breakout** = new 20-bar-high close + ≥1% 1-bar pop + volume > 1.5×avg(20). Entry B: **trailmom** = close>20d SMA, fast(10)-bar return>0, this bar up.
- Lookahead-safe: signal on bars [..i], fill **i+1 open**. Exits: hold {1,3,5}d, plus profit-floor (TP +3% / hard −15% stop). Non-overlapping per coin.
- Net = gross − `band_slippage_bps(band_median_vol, mult)/1e4`. Slippage grows as volume falls (0.1-0.7M=120bps … 50M+=6bps). Slippage-mult sweep {0.5, 1.0, 1.5}.
- Survivorship: low-vol natives that died are absent → every positive below is an UPPER BOUND, doubly so for low bands.

## EV-by-band — bot's real entry (breakout, BTC-up gate, the favorable case)

Band slippage shown is the round-trip cost the band must clear. `net@m` = net EV per trade at slip-mult m.

**hold-1d**
| band | n | slip bps | gross% | net@0.5 | net@1.0 | net@1.5 | win | OOS h1 | OOS h2 | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| 0.1-0.7M (sub-floor) | 73 | 120 | −0.27 | −0.87 | −1.47 | −2.07 | .36 | −0.71 | −2.22 | **−EV** |
| 0.7-2M (just-above floor) | 83 | 70 | +0.47 | +0.12 | −0.23 | −0.58 | .37 | −2.39 | +1.87 | **−EV (OOS flip)** |
| 2-5M | 98 | 45 | −0.51 | −0.73 | −0.96 | −1.18 | .42 | −1.35 | −0.57 | −EV |
| 5-20M | 73 | 25 | +1.15 | +1.02 | +0.90 | +0.77 | .45 | +1.45 | +0.36 | **ROBUST +EV** |
| 20-50M | 15 | 12 | −3.42 | — | −3.54 | — | .27 | — | — | −EV (n=3 coins) |
| 50M+ | 31 | 6 | +1.14 | +1.11 | +1.08 | +1.05 | .42 | −2.95 | +4.86 | +net, not-OOS |

**hold-3d** (clearest signal)
| band | n | slip | gross% | net@0.5 | net@1.0 | net@1.5 | win | OOS h1 | OOS h2 | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| 0.1-0.7M | 53 | 120 | −0.03 | −0.63 | −1.23 | −1.83 | .32 | −1.76 | −0.72 | −EV |
| 0.7-2M | 57 | 70 | +0.11 | −0.24 | −0.59 | −0.94 | .35 | −4.58 | +3.26 | **−EV (OOS flip)** |
| 2-5M | 62 | 45 | −0.52 | −0.74 | −0.97 | −1.19 | .31 | −1.92 | −0.02 | −EV |
| 5-20M | 48 | 25 | +1.92 | +1.80 | +1.67 | +1.55 | .40 | +1.46 | +1.89 | **ROBUST +EV** |
| 50M+ | 24 | 6 | +0.10 | +0.07 | +0.04 | +0.01 | .38 | −3.53 | +3.60 | +net, not-OOS |

(20-50M has only 3 native coins AAVE/XPL/XRP → idiosyncratic, ignore. hold-5d and trailmom tables in `floor_long.py` output — same shape: no sub-$5M band is robust.)

## Slippage sensitivity (the crux)
The 5-20M edge survives all three mults (net@1.5 still +0.77/+1.55). The sub-floor bands are −EV even at mult=0.5 (0.1-0.7M: −0.87/−0.63) and collapse further at 1.0/1.5. The 0.7-2M band's only positives are gross/net@0.5, and they evaporate by mult=1.0 AND fail OOS (h1 deeply negative, h2 positive = a single-half artifact, classic noise). No assumption about slippage rescues anything below $5M.

## Where the edge dies vs volume
- **≥$5M (5-20M, 25bps):** breakout robust +EV both halves (+0.9% to +1.7%/trade net). This is the productive long zone — and it is ABOVE the floor.
- **$2-5M (45bps):** gross already negative (−0.5%); net deeply −EV. Edge gone.
- **$0.7-2M (just above the floor, 70bps):** gross hovers ~0; net@1.0 negative; OOS half-flip = noise. No edge.
- **$0.1-0.7M (below the floor, 120bps):** gross ≤0 and 120bps slippage swamps it → −1.5% to −4%/trade. Worst band.
- The profit-floor exit (+3% TP / −15% stop) is −EV in EVERY band (win .6-.7 but the −15% tail dominates) — consistent with the documented exit-asymmetry leak; clipping winners short doesn't save low-vol longs.

## Why this beats (not re-discovers) the prior finding
Prior work showed the thin +EV long band dies "by 25bps slippage." This pins down WHERE on the volume axis 25bps lives: it's the **5-20M band**, which sits at/above current liquidity, not below it. The floor question is therefore settled from the other direction: the long edge needs ≥$5M of volume to clear its spread; the $700k floor is already *more permissive* than the edge supports. Lowering it strictly admits −EV trades. Survivorship makes this conservative — the dead low-vol coins absent from the sample can only make sub-floor EV worse.

**Action: KEEP `min_market_volume_usd` = $700k.** Do not lower. (Optional, separate question: a long-breakout-only sub-strategy could justify a *higher* $5M gate, but that's an entry-quality tweak, not a floor change, and out of scope here.)
