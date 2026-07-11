# W-M1 — extension-band momentum long, big-data re-run

**Hypothesis (operator instinct):** buying the first 1h close where a liquid coin's rolling-24h return crosses +B% is EV+, at least in some band/regime.

**Rule tested (pre-registered in `hypotheses/W-M0_engine.py`):** signal = r24 crosses B from below at bar close, trailing-24h dollar volume >= floor, 24h per-coin dedup, fill at next 1h open. Bands {6,8,10,12,15,20,25,30}%, floors {$5M,$20M}. Exits: hold {6,12,24,48}h x stop {5,8,15}% + KAITO trail (arm +2%, retrace 0.10, 15% hard stop, 48h max). LOW-before-HIGH intra-bar, gap-through-stop at open. Same-coin random-time MC null (2000 iters, 100k escalation). Views: all / BTC-20d-up / BTC-20d-dn. Data: 208d x 40 liquid coins x 1h (2025-12-13..2026-07-09, Lane-H cache).

**Family:** 8 x 2 x 13 x 3 = 624 cells, Bonferroni alpha = 8.0e-05.

## Result: 0 / 624 cells wire-eligible

EV25 (%/trade), view=all, floor $5M (floor $20M is the same or worse everywhere; full grids in `scratchpad/W-M1_results.json`):

| band | h6_s5 | h6_s15 | h12_s8 | h24_s8 | h48_s8 | trail |
|---|---|---|---|---|---|---|
| +6% | -0.34 | -0.26 | -0.34 | -0.39 | -0.21 | -0.42 |
| +8% | -0.11 | -0.11 | -0.17 | -0.17 | -0.09 | -0.19 |
| +10% | -0.14 | -0.06 | -0.23 | -0.20 | -0.39 | -0.10 |
| +12% | -0.01 | -0.03 | -0.17 | -0.28 | -0.50 | -0.50 |
| +15% | **+0.07** | **+0.20** | -0.61 | -0.29 | -0.65 | **+0.14** |
| +20% | -1.25 | -1.23 | -1.68 | -2.05 | -1.68 | -0.40 |
| +25% | -1.98 | -2.25 | -2.41 | -2.24 | -1.17 | -2.11 |
| +30% | -2.30 | -3.57 | -5.08 | -3.95 | -3.41 | -1.56 |

- view=all: 8/182 cells (n>=30) have EV25>0, all clustered at B15, best p=0.064. Chance expects ~9 at p<0.05.
- view=dn (BTC 20d negative): 9/169 positive, best p=0.077. Nothing close.
- view=up (BTC 20d positive): 43/143 positive — longs ride the up-tape, as expected. Best cell:

**The single near-miss:** `B15 | $5M | trail(.02/.10) | BTC-up`: n=66, EV0 +1.10%, EV12 +0.98%, EV25 +0.85%, EV50 +0.60%, win25 76%, OOS halves (+0.58, +1.14), MC p = 0.022. Passes every gate EXCEPT Bonferroni (needs 8.0e-05, got 2.2e-02). With 624 cells, ~31 pass p<0.05 by luck; this is not distinguishable from the luckiest cell of a big grid. Same cell at $20M floor: n=33, EV25 +0.91, p=0.054 — weaker, not confirmation.

**June study re-litigated:** the June finding (+EV only in the 20-30% band, crash tape) does NOT replicate on 208 days: B20-B30 are the WORST cells everywhere (-1.2 to -6.8% per trade), in both regimes. The June result was tape-specific. The gradient on the big sample runs the other way: less-extended entries (B8-B15) lose least, deep extension chasing loses most.

## VERDICT: REFUTED

Deciding number: 0/624 cells pass the pre-registered gates; best cell p=0.022 vs required 8.0e-05. Entering on 24h-extension crossings is not EV+ at 25bps anywhere in this grid, including regime-conditioned. The one suggestive cell (B15 + tight trail in BTC-up tape) is worth at most a zero-capital shadow recorder, not a wire.

Survivorship: universe is today's liquid set — these (negative) numbers are UPPER bounds.
