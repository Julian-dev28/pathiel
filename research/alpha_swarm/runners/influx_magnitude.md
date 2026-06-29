# Lane 2 — Volume-Influx MAGNITUDE Regime

Does EXTREME influx magnitude change the runner odds? The operator's MEME example
was ~80k -> 13M -> 50M on consecutive 5m candles (100x+). The raw 1.5x rule is
breakeven across the full event set. This tests whether magnitude, dollar-size,
price-move, or escalating shape opens a +EV threshold.

Script: `research/alpha_swarm/runners/influx_magnitude.py`
Data: `movers_5m.json` — 180 perps, 5001 5m bars each (~17 days).

## Method (lookahead-safe)
- Event = GREEN 5m bar `i` with `vol[i] / mean(vol[i-48..i-1]) >= 1.5`.
- Decide on bar `i`, ENTER at bar `i+1` open. Forward window 96 bars (8h).
- runner-rate = fwd-96 MFE on entry-open hits +10% / +20%.
- Net EV @ 12 bps round-trip, tight-floor exit = 5% trailing stop off running peak
  (the live bank-quick breakout exit). 67,743 events total.
- Matched random null = random green bar, same coin weighting, same exit.

## Bucket table (magnitude = vol / trailing-mean)

| bucket | n | run>=10% | run>=20% | netEV/12bps | win | medMFE | OOS-A | OOS-B | EV excess vs null |
|---|---|---|---|---|---|---|---|---|---|
| 1.5-3x | 35726 | 2.2% | 0.4% | -0.22% | 42.9% | +1.52% | -0.05% | -0.38% | -0.04% |
| 3-10x | 25015 | 2.6% | 0.5% | -0.28% | 41.9% | +1.51% | -0.07% | -0.49% | -0.10% |
| 10-50x | 6321 | 2.4% | 0.2% | -0.41% | 39.2% | +1.39% | -0.06% | -0.75% | -0.23% |
| 50x+ | 681 | 2.8% | 0.6% | -0.47% | 38.2% | +1.33% | -0.23% | -0.71% | -0.30% |

Null: runner>=10% = 1.96%, runner>=20% = 0.30%, net EV = -0.18%.

**Magnitude does NOT help — it hurts.** Runner-rate is flat across all four buckets
(2.2% -> 2.8% at +10%, statistically noise vs the 1.96% null). Net EV gets MORE
negative as magnitude rises: -0.22% at 1.5-3x down to -0.47% at 50x+. Win rate
falls monotonically (42.9% -> 38.2%). OOS both halves are negative in every bucket
and worsen with magnitude (50x+: -0.23% / -0.71%). EV-excess over null is negative
everywhere and most negative for the biggest spikes (-0.30% at 50x+). The extreme
influx is a worse entry, not a better one.

## Interactions (all within high-mag m>=10, n=7002)

**(a) Dollar-volume of the influx candle.** Split high-mag at the median $-notional.
High-$vol raises raw MFE runner-rate (run10 3.4% vs 1.4% low-$vol) — a real liquidity
signal that more dollars showed up — but net EV is still negative on both sides
(-0.44% high vs -0.39% low). A 50x spike on an active coin reaches +10% MFE more
often but you still lose money entering it. No threshold.

**(b) Big price-move vs pure-volume spike.** THE one place runner-rate jumps:
body>=3% within extreme volume hits +10% MFE 17.7% of the time (vs 2.1% for
body<3%, vs 1.96% null) and +20% at 3.5%. n=141. But net EV is -0.74%, WORSE than
the body<3% group (-0.41%). The candle already moved 3%+ on the spike; entering at
the next open buys the top and reverts. The MFE is a head-fake — measured from entry
open, a handful run while the majority reverse immediately.

**(c) Escalating shape (rising vol 3 bars, the MEME 13M->50M shape) vs single spike.**
Escalating: run10 2.5%, EV -0.37%, OOS (-0.11%, -0.62%). Single-spike: run10 2.3%,
EV -0.46%. Escalation is marginally less bad but still firmly -EV both OOS halves.
The MEME *shape* alone carries no edge.

**Combo (m>=10 & escalating & body>=3%, the full MEME signature):** n=81,
run10 17.3%, run20 6.2% — runner-rate genuinely elevated — but net EV -0.83%,
win 33.3%, OOS (-0.89%, -0.76%), excess -0.65%. The cleanest visual match to the
operator's example is the worst entry in the set.

## Can a different exit rescue the body>=3% MFE jump?

The 17.7% +10%-MFE rate is the only thing that looks like a runner signal, so I
swept exits on that subset (n=141). None capture it:

| exit | netEV | win | median |
|---|---|---|---|
| tight-trail 5% | -0.74% | 35.5% | -1.45% |
| trail 10% | -1.26% | 32.6% | -2.15% |
| trail 15% | -1.34% | 33.3% | -2.02% |
| hold 8h | -1.25% | 32.6% | -2.02% |
| tp20/sl10 | -1.21% | 32.6% | -2.02% |
| tp30/sl15 | -1.41% | 31.9% | -2.02% |

Every exit loses, and wider exits lose MORE (the reversion is deeper than the run).
Median return -1.45% to -2.02%. The MFE exists but is unbankable: the move happens
on the influx candle itself, before you can enter. This is textbook late-chase —
the same pattern the live book already blocks (TNSR +44%-ext entry, GRASS chase).
For comparison the full 1.5x baseline under the same exits is only mildly negative
(-0.04% to -0.18%), so the extreme+body subset is actively worse than the average
influx, not better.

## Caveats
- **Survivor universe = upper bound.** These 180 perps are coins that exist and
  trade today; delisted/dead spikes are absent. True EV is below these numbers, so
  a negative result here is robustly negative.
- Exit fills at the stop/target assume no extra slippage beyond the 12 bps; on the
  illiquid coins where extreme multiples occur, real slippage is worse. Again this
  only makes the verdict more negative.
- MFE is measured from entry open (lookahead-safe); it counts the favorable
  excursion you could theoretically catch, which is why high MFE with negative EV
  is the diagnostic, not a contradiction.

## VERDICT
**No.** There is no magnitude or shape threshold where the influx becomes +EV.
Bigger multiples lower win rate and EV (50x+ is the worst bucket); the only subset
with elevated runner-rate (extreme volume + body>=3%, the MEME signature) is a
late-chase trap that loses -0.74% to -1.4% under every exit because the move is
already over by the next-bar entry. Volume magnitude is not a tradeable entry edge.
