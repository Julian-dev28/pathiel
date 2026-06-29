# FADE (SHORT) the negative-funding 5m volume-influx pop

**Verdict (one line):** YES. Shorting the negative-funding 5m volume-influx pop is a real,
OOS-robust, stop-robust +EV fade. The prior study's long bleed flips clean to a short edge.
Tradeable cell: **funding <= -0.10%/8h, short on the influx (immediate next-bar open) or on
the first red candle, tight-floor-short exit, stop 20-25% -> +0.46% net-of-funding EV per
attempt, +EV in BOTH OOS halves, +EV at every stop width 8-40%, generalizes across 69 coins.**
Deeper funding (<= -0.30, <= -1.0) is stronger (+2.5%, +4.0% net) but concentrates into a
handful of coins, so size it as a gradient: looser = broad-and-modest, deeper = thin-and-big.

## Why this is the right test
Prior study (`influx_funding_oi.py`) found the green 5m vol-influx (vol >= 1.5x trailing-6
mean) on deep-negative-funding coins clusters runners (rr10 up to 0.20) but BLEEDS as a LONG
(-3.6% to -5.5% EV, win 15-23%): crowded-short coins that pop then fail and resume falling.
The fade thesis: deep-negative funding = shorts are paying = persistent downtrend; the green
vol pop is a failed short-squeeze; short the failed pop and ride the continuation down. The
exact coins that bled the long (TNSR, LAYER, SOPH) pay the short.

## Setup (lookahead-safe)
- Event = green 5m candle, vol >= 1.5x trailing-6 mean, on a coin with funding <= F per-8h.
- Fill = next-bar OPEN after the (signal / confirmation / stall) bar. SHORT.
- Forward = 96 bars (8h). Net 12bps round-trip (FEE). Short PnL = (entry-exit)/entry - FEE.
- Intrabar ordering is conservative: the adverse leg (stop, the high) is checked BEFORE the
  favorable leg (trail/TP, the low). No same-bar optimism. Results are if anything understated.
- "Runner (adverse)" for a short = price runs UP (squeeze): adv10 = maxhigh/entry-1 >= 0.10.
- OOS = time-sorted halves (h1EV/h2EV) of the negative-funding event set.
- Null = matched random-SHORT: same coin, random time, SAME exit, 10 draws/event. `exc` =
  cell EV - null EV. This isolates the influx-TIMING alpha from the coins' general downdrift.
- Universe = top-180 by current dayNtlVlm (SURVIVOR set). For a SHORT, survivor bias is
  ambiguous-to-conservative: coins that collapsed/delisted (big short wins) are EXCLUDED.
- 91,693 influx events, 100% funding-attached. 4,984 negative-funding (<= -0.05%/8h) events.
- Script: `scratchpad/influx_funding_fade.py` (reuses `movers_5m.json`, `funding_180.json`).

## Baseline
```
ALL-influx SHORT (immediate, tightfloor 25% stop):   n=91693  EV -0.082%  win 0.514
matched random-SHORT null (tightfloor 25%):                   EV -0.103%  win 0.518
```
Shorting EVERY influx is ~breakeven. The edge is conditional on negative funding.

## 1. Stop-width x entry-timing x funding (tight-floor-short exit) — raw EV
`adv10`/`adv20` = squeeze (up) rate. `exc` = vs matched random-short null. h1/h2 = OOS halves.

### funding <= -0.10%/8h  (n~1486, 69 coins — the BROAD, trustworthy cell)
```
cell                     n     EV%    win   adv10  adv20   exc    h1EV   h2EV
immediate   stop  8%   1486  +0.525  0.551  0.069  0.009  +0.62  +0.24  +0.75
immediate   stop 20%   1486  +0.697  0.559  0.069  0.009  +0.86  +0.44  +0.91
immediate   stop 40%   1486  +0.731  0.559  0.069  0.009  +0.84  +0.47  +0.94
confirm-red stop 20%   1477  +0.686  0.559  0.067  0.009  +0.76  +0.27  +1.02
stall-3     stop 25%   1420  +0.728  0.580  0.061  0.008  +0.91  +0.28  +1.08
```

### funding <= -0.30%/8h  (n=246, 19 coins)
```
immediate   stop 20%    246  +3.208  0.675  0.175  0.016  +3.54  +2.67  +3.59
confirm-red stop 20%    246  +3.033  0.659  0.175  0.020  +3.32  +2.35  +3.52
stall-3     stop 25%    238  +3.248  0.702  0.176  0.013  +3.57  +3.00  +3.42
```

### funding <= -1.0%/8h  (n=79, 7 coins — episode-driven, treat as gradient confirmation)
```
immediate   stop 20%     79  +5.366  0.759  0.203  0.038  +5.80  +3.21  +7.69
confirm-red stop 40%     79  +6.183  0.810  0.241  0.051  +6.70  +4.07  +8.46
stall-3     stop 25%     78  +5.481  0.744  0.218  0.026  +5.91  +3.30  +7.78
```
**Read:** every cell is +EV, beats null by a wide margin, and is +EV in BOTH OOS halves.
The edge is monotone in funding-negativity (the same gradient that made the long worse makes
the short better). EV is ROBUST to stop width 8-40% — the over-refute trap does not bite here
because the squeeze (adv10) is only 7% at -0.10 and ~18-20% at the deep tail, not frequent
enough to invert the fade even with a tight 8% stop. Entry timing barely matters; immediate ~=
confirm ~= stall (stall slightly best on win-rate). Stop 20-25% is the sweet spot.

## 2. Fixed TP-down (with 25% stop) — banking the fade
### funding <= -0.30%/8h
```
cell                       n    EV%    win   exc    h1EV   h2EV
immediate TP 5%/stop25    246  +2.296 0.785 +2.49  +2.74  +1.98
immediate TP10%/stop25    246  +3.725 0.768 +3.83  +3.70  +3.75
immediate TP15%/stop25    246  +4.244 0.768 +4.38  +4.28  +4.22
```
A TP-down lifts win-rate to ~78% and is OOS-stable. TP10-15% banks more than TP5% (the fade
keeps running). All +EV both halves.

## 3. Time-exit 8h (hold to horizon with stop) — the move keeps going
### funding <= -0.30%/8h
```
immediate time/stop25     246  +4.439 0.768  +4.67  +4.37  +4.49
immediate time/stop40     246  +4.567 0.772  +4.66  +4.68  +4.49
```
Just holding the short 8h with a wide stop is the single best exit (+4.4-4.6%), confirming the
continuation thesis: after the failed pop the coin keeps grinding down for the whole window.

## Robustness — funding cost + coin concentration (immediate, tightfloor stop 25%)
A short on negative funding PAYS funding; netted below (|neg funding| per-8h, prorated by hold).
`dropTop` = remaining net-EV after removing the single biggest-PnL coin (generalization test).
```
fund<=    n    coins  EVraw%  EVnetfund%  top3share  dropTop_netEV%
-0.10   1486    69    +0.699    +0.461      0.25       +0.321  (dropped TNSR)
-0.30    246    19    +3.188    +2.471      0.62       +2.315  (dropped TNSR)
-1.00     79     7    +5.303    +4.039      0.81       +3.206  (dropped LAYER)
```
- Funding cost dents but does not kill it: +0.70 -> +0.46 net at -0.10, +5.30 -> +4.04 at -1.0.
- The **-0.10 cell is the keeper**: 69 distinct coins, top-3 only 25% of events, and it stays
  +0.32% net after dropping the biggest single contributor. That is a real population edge.
- The deeper buckets are stronger but thin: <= -1.0 is 7 coins (81% top-3, TNSR/LAYER/SOPH).
  Do NOT trust the deep tail as an independent edge; it is the same gradient seen on more
  extreme funding in fewer coin-episodes. Still net +3.2% after dropping the top coin.

## Honest caveats
- Survivor universe = the population is top-180-by-volume coins; a coin that fully collapsed
  (a giant short win) is excluded, so short EV here is if anything a LOWER bound, not inflated.
- ~17-day window (06-11 to 06-28), one regime. The OOS split is time-ordered, both halves +EV,
  but this is not multi-regime. Validate forward in SHADOW before any live size.
- adv10 (squeeze) is real: 7% of -0.10 events run up >=10%, ~18-20% in the deep tail. The
  stop-robustness (8-40% all +EV) says the squeezes don't dominate, but a hard stop is required
  — never run this fade naked.
- Funding cost grows with the deep tail; at <= -1.0%/8h you pay ~1%+ per 8h held, already netted.

## Bottom line
- The negative-funding influx FADE is a genuine +EV edge and the clean inverse of the refuted
  long. It beats a matched random-short null, holds in both OOS halves, and survives every
  stop width 8-40% and the drop-the-top-coin test.
- **Tradeable spec:** funding <= -0.10%/8h, green 5m vol-influx (>=1.5x trail-6), SHORT at the
  next-bar open (or first red candle), tight-floor-short trail, hard stop 20-25%, hold toward
  8h. ~+0.46% net-of-funding per attempt across 69 coins; scales to +2.5%/+4.0% as funding
  deepens (with fewer, more concentrated names). A fixed TP-down 10-15% or a pure time-exit
  both monetize it; the continuation (time-exit) is strongest.
- Next step: SHADOW-deploy the fade book (entry = neg-funding influx, side = SHORT) and
  forward-grade; size the looser -0.10 threshold for breadth, gate the deep tail by coin count.
