# Influx x Funding / OI / Volume regime — does it flip breakeven to +EV?

**Verdict (one line):** NO. No funding, OI, or volume regime turns the 5m volume-influx
entry +EV. Negative funding RAISES the runner-rate (operator's pattern is a real selection
signal) but the realized EV gets MUCH worse (tight-floor -3.6% to -5.5% at the negative tail
vs -0.21% baseline), because the crowded-short coins chop you out before the rare squeeze —
win rate craters from 0.43 to 0.15-0.23. The 5 movers the operator spotted are survivors.

## Setup
- Event = green 5m candle, vol >= 1.5x trailing-6 mean. Enter i+1 open. Fwd 96 bars (8h).
- Runner = MFE >= 10% (also track 20%). Exit = tight-floor (0.10 give-back trail, 0.65 hard
  floor), net 12bps. OOS = time-sorted halves (h1EV / h2EV). Null = matched random-entry
  (same coins, random times) = the universe baseline; `exc` = bucket EV - matched null EV.
- Universe = top-180 by current dayNtlVlm (SURVIVOR set => positives are UPPER BOUNDS).
- Funding = HL hourly rate at-or-before the event, expressed PER-8H in percent (rate x 8 x 100).
- 91,693 influx events, 100% funding-attached (fetched fresh for all 180 coins, ~06-09..06-28).
- Script: `scratchpad/influx_funding_oi.py` (+ `fetch_funding_180.py`). Read-only; no live code touched.

## Baseline
```
all influx:        n=91693  rr10 0.021  rr20 0.003  EV -0.209%  win 0.431
matched-random null:                              EV -0.171%  win 0.444
EXCESS all-influx vs null:                        -0.038%   (breakeven-to-slightly-negative, as known)
```

## 1. FUNDING (per-8h) x influx
```
bucket                n     rr10   rr20    EV%     win    exc     h1EV    h2EV
fund <= -0.30%        246   0.175  0.016  -3.648  0.232  -3.55   -2.88   -3.83
-0.30..-0.05%        4738   0.027  0.005  -0.311  0.441  -0.11   -0.17   -0.39
-0.05..+0.05%       86070   0.020  0.003  -0.191  0.432  -0.02   -0.01   -0.37
fund > +0.05%         639   0.047  0.000  -0.630  0.421  -0.56   -1.13   +0.35
```
Finer negative tail:
```
fund <= -1.0%          79   0.203  0.038  -5.478  0.152  -5.91   -5.93   -5.35
-1.0..-0.5%            83   0.217  0.012  -2.795  0.265  -3.33   -2.12   -2.86
-0.5..-0.2%           243   0.103  0.008  -1.575  0.342  -1.79   -0.31   -1.98
-0.2..0%            24656   0.017  0.004  -0.193  0.443  +0.01   +0.05   -0.37
fund > 0%           66632   0.022  0.003  -0.201  0.428  -0.07   -0.05   -0.37
```
**Read:** runner-rate (rr10) climbs monotonically as funding goes negative (0.02 -> 0.20+),
so the operator's intuition that runners cluster in negative funding is correct as a
*selection* statistic. But it is NOT tradeable: the deeper the negative funding, the worse the
realized EV and the lower the win rate. Negative funding = crowded shorts = already-violent
two-way coins; the entry buys into chop and the 0.65 floor stops you out (-0.35) far more often
than the occasional squeeze pays. Excess vs null is negative in every meaningful bucket and
fails OOS (h2EV negative across the board).

## 2. Open interest (forward slice only — caveat heavily)
`.oi-timeseries.jsonl`, ~06-20..06-28, ~10.9min cadence, ~49 main-perp coins. 9,871 influx
events have OI coverage (both at-event and 1h-prior).
```
OI 1h change       n      rr10   rr20   EV%     win
rising >+0.5%      1216   0.020  0.000  -0.374  0.415
flat               7618   0.012  0.000  -0.335  0.418
falling <-0.5%     1037   0.019  0.000  -0.360  0.406
```
No separation. Rising vs falling OI at the influx gives identical (negative) EV. Small slice,
8 days, directional only — but there is zero signal here to build on.

## 3. Volume regime (beyond the liquidity floor)
Influx-candle dollar volume quartiles:
```
dvol Q1 <1k     22924  rr10 0.012  EV -0.193%  exc +0.02  h2 -0.35
dvol Q2         22923  rr10 0.016  EV -0.117%  exc +0.08  h2 -0.28
dvol Q3         22923  rr10 0.023  EV -0.201%  exc -0.04  h2 -0.36
dvol Q4 >13k    22923  rr10 0.033  EV -0.327%  exc -0.22  h2 -0.56
```
Trailing dollar volume (liquidity) quartiles: same shape — Q4 (liquid) EV -0.284, exc -0.17.
**Read:** bigger $-volume = higher runner-rate but WORSE EV (you buy the top). Confirms the
prior "magnitude does not help / bigger = worse." No volume band crosses into +EV-excess with
OOS both halves positive.

## 4. Combined
```
combo                    n      rr10   rr20   EV%     win    exc    h1EV   h2EV
neg-fund & dvol>med     2421   0.057  0.009  -0.771  0.409  -0.66  -0.67  -0.84
neg-fund & dvol<=med    2563   0.013  0.002  -0.196  0.451  +0.13  +0.25  -0.41
pos-fund & dvol>med      435   0.053  0.000  -1.011  0.398  -0.87  -1.81  +0.29
extreme neg <=-0.5%      162   0.210  0.025  -4.103  0.210  -4.24  -4.82  -3.98
```
Best single cell on excess is `neg-fund & dvol<=med` (+0.13 excess) but it FAILS OOS (h2 -0.41)
and is just noise around null. Nothing survives.

## 5. Can a wider exit monetize the negative-funding squeezes?
Ride exit (25% give-back, 0.65 floor) by funding bucket — tests whether the clustered runners
in negative funding can be caught by holding looser:
```
fund <= -1.0%    n=79    rideEV -7.824%  win 0.089   h1 -6.09  h2 -8.30
-1.0..-0.5%      n=83    rideEV -3.778%  win 0.265   h1 -2.12  h2 -3.93
-0.5..-0.2%      n=243   rideEV -1.577%  win 0.346   h1 -0.24  h2 -2.01
-0.2..0%       n=24656   rideEV -0.179%  win 0.442   h1 +0.06  h2 -0.35
fund > 0%      n=66632   rideEV -0.149%  win 0.430   h1 -0.03  h2 -0.29
```
The wider exit makes the negative-funding buckets WORSE, not better — it holds through bigger
drawdowns. The squeezes exist (rr10 0.20) but are un-catchable with this entry: the move is a
violent round-trip and either exit gets run over. Negative funding is a TRAP for the influx
long, not an edge.

## Why the operator saw what they saw
CELO (-0.59%), GAS (-0.30%), MEME, TURBO, S today are the WINNERS of the negative-funding
influx population. The full population (246 events at <= -0.30%/8h, 79 at <= -1.0%) bleeds
-3.6% to -5.5% per attempt. You cannot tell the CELO squeeze from the 4-in-5 negative-funding
influxes that chop you out, at entry time. This is the exact survivorship trap: conditioning on
the outcome (it ran) looks like an edge; conditioning on the observable (funding at entry)
is sharply -EV.

## Bottom line
- Funding does separate runner-RATE (real, monotone) but inverts realized EV. No.
- OI: no separation (small slice). No.
- Volume: bigger = worse, confirms prior. No.
- No regime, single or combined, reaches +EV-excess-over-null with OOS both halves positive.
- Do NOT build a funding/OI/volume-conditioned influx long. The negative-funding tail is a
  short-squeeze trap that costs more than it pays. If anything, deep-negative funding + influx
  is a candidate to AVOID (or study as a fade), not to chase long.
