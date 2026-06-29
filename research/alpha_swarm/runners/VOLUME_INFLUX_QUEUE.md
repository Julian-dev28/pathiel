# Volume-Influx Runner Swarm — crack the MANTA/MEME/CELO play

Operator's hypothesis (2026-06-29, from MANTA/MEME/CELO 5m charts):
- ENTRY: 5m candle with volume >= 1.5x prior candles (green volume influx) -> long.
- EXIT: sell when a red candle's volume >= 80% of the last green candle's volume.
- "There are plays every day."

## What's already TESTED (operator_vol_rule.py, 180-coin 5m movers, net 12bps, OOS halves):
- The raw rule (1.5x entry / 0.8x red-green exit): **EV -0.11%, win 33%, -EV both halves.** The
  volume-reversal EXIT is the weak part (fires on any noisy red pullback -> exits at small losses).
- Same 1.5x entry with a TIGHT FLOOR exit instead: +0.01% EV, 66% win, but OOS MIXED (h1 +0.11 / h2 -0.08).
- +follow-through (2nd candle holds vol) + tight floor: +0.03% EV, 65% win, OOS still mixed (h2 -0.05).
- run>=20% is ~0% across ALL variants: the MANTA/MEME/CELO runners are rare SURVIVORS; the same signal
  fires 15,000x and almost none reach +20% on the 5m horizon. The charts are survivorship.
=> The raw rule is BREAKEVEN, not the alpha. Don't re-grind it. The edge (if any) is in SELECTION.

## The real frontier — 3 lanes (all READ-ONLY backtests, lookahead-safe, OOS halves, net-of-fee,
## SURVIVOR universe = upper bound; score EXCESS over a matched random-entry null):

### Lane 1 — SELECTION: which influx runs?
Among the ~15k volume-influx events, build a classifier for P(MFE>=10% / >=20%). Features to test:
influx magnitude (vol/trailing), follow-through (2nd/3rd candle vol+color), prior range-compression/coil,
consecutive-green count, body/wick ratio of the influx candle, prior 1h trend, BTC regime, time-of-day,
coin 24h vol. Report OOS AUC + top-quartile EV lift vs base. Extends the momentum equation (prior OOS AUC
0.667, +2x EV top-quartile). GOAL: a selection score that turns the breakeven entry +EV.

### Lane 2 — MAGNITUDE REGIME: does EXTREME volume change the game?
Operator's MEME was ~100x+ (80k->13M->50M). Test runner-rate + EV as a function of influx magnitude
buckets {1.5-3x, 3-10x, 10-50x, 50x+}. Is there a magnitude threshold where runner-rate jumps materially?
Does a 50x+ influx on a quiet coin behave differently from a 2x on an already-active one?

### Lane 3 — EXECUTION on confirmed runners (the MANTA ride):
GIVEN an entry that reaches +10% (a real runner started), what exit MAXIMIZES capture? The live MANTA trade
scaled out partials at ~+145% ROE then trailed to a +94% floor-breach close. Compare tight-floor vs
scale-out-and-trail vs ATR-trail on the subset that reaches +10%. Quantify the give-back of each.

## Data: /private/tmp/.../scratchpad/movers_5m.json (180 coins, 5m, ~5000 bars). Rebuild via build_5m_movers.py.
## Methodology: research/alpha_swarm/SWARM-RULES.md. Write findings to research/alpha_swarm/runners/<lane>.md.
