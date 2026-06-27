# A12 beta_rotation

**Hypothesis:** Rotate to a high-beta basket in BTC-up regime and a low-beta basket in
BTC-down regime; beats a static beta tilt.

**Exact rule:** day i, beta_c to BTC over W. regime = BTC close vs SMA(20). rot_long: up->
long top-m=8 beta, down->long bottom-m. Compared to static_high, static_low, and rot_ls
(up: long high/short low; down: flip). Fill open[i+1], hold H, non-overlapping. vs random
baseline at matched longfrac. W{40,60} x H{5,7}.

## Results (per-leg signed gross %, 12bps)
| W | H | rot_long | static_low | static_high | rot_ls (h1/h2) |
|---|---|---|---|---|---|
| 40 | 5 | -0.49 | **-0.44** | -1.06 | +0.14 (+0.32/-0.05) |
| 40 | 7 | -0.75 | **-0.51** | -1.57 | +0.17 (+0.18/+0.16) |
| 60 | 5 | -0.72 | **-0.54** | -1.39 | +0.13 (+0.85/-0.62) |
| 60 | 7 | -1.71 | **-1.05** | -1.76 | -0.43 |

## Verdict: **REFUTED**
Deciding number: rot_long is **worse than static_low in all 4 configs** (e.g. W40/H7 rot_long
-0.75 vs static_low -0.51) — regime-rotation adds nothing over simply holding the low-beta
(defensive) basket; the "go high-beta when BTC > SMA" leg still bled because the up-windows
weren't strong enough. The only positive variant (rot_ls, long/short) peaks at EV12 +0.17 and
**dies by 25 bps** (+0.04), and it's just the short-high-beta directional tilt already covered
and dismissed in A4. No rotation edge.
