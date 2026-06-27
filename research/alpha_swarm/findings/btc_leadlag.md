# btc_leadlag — does a strong BTC bar predict alt returns in the NEXT bar?

## Hypothesis
BTC (the major) leads the alts: a strong BTC move in bar i predicts alt returns in
bar i+1, so a fast follower that buys high-beta alts after a BTC up-bar (shorts after a
down-bar) is +EV before it's arbed away — IF it survives round-trip costs.

## Data / method
- Dataset: 40 liquid perps, 5m (~5000 bars, ~17 days) and 1h (~2000 bars, ~83 days).
- Lookahead-safe: BTC signal = bar-i close-to-close return (known at close[i]).
  Alts FILLED at i+1 OPEN, exited at i+1 close (hold=1) or i+2 close (hold=2).
- Alt next-bar return measured open->close (tradeable). Symmetric short on BTC down-bars.
- Three rules: (A) directional all-alts above threshold, (B) directional top-10 lead-beta
  subset, (C) cross-sectional catch-up (long the 8 alts that lagged BTC's bar-i move most).
- EV per slippage tier 0/6/12/25/50bps + OOS first/second-half via `alpha_lib.summarize`.

## The lead-lag is essentially zero
Pooled predictive correlation of alt[i+1] (open->close) on BTC[i] (close-to-close):

| interval | LEAD corr (i -> i+1) | LEAD beta | contemporaneous same-bar corr |
|---|---|---|---|
| 5m | **+0.0144** | +0.036 | +0.534 |
| 1h | **-0.0365** | -0.089 | +0.559 |

The alts move WITH BTC in the SAME bar (corr ~0.53-0.56) — that co-move is already
complete by the time bar i closes, so it is NOT capturable at i+1 open. The residual
predictive (lead) signal is ~0.014 at 5m (noise) and actually NEGATIVE at 1h (alts
mean-revert the hour after a BTC move, not follow through). Best single alt lead-corr at
5m is PAXG +0.063 (gold proxy, near-flat); top alts at 1h are all negative.

## Tradeable rules — every broad version dies before 12-25bps

5m directional, all alts (mean ret % NET per trade):

| rule | 0bps | 6bps | 12bps | 25bps | 50bps | OOS h1/h2 @12 | verdict |
|---|---|---|---|---|---|---|---|
| thr 0.3% h1 | +0.011 | -0.049 | -0.109 | -0.239 | -0.489 | -0.137 / -0.080 | dead, both halves neg |
| thr 0.5% h1 | +0.114 | +0.054 | -0.006 | -0.136 | -0.386 | -0.087 / +0.078 | dead @12, sign-flip |
| thr 1.0% h1 | +0.414 | +0.354 | +0.294 | +0.164 | -0.086 | +0.44 / +0.096 | "ROBUST" — see below |
| top-10 beta 0.3% | +0.037 | -0.023 | -0.083 | -0.213 | -0.463 | -0.105 / -0.061 | dead |
| cross-sec 0.5% | +0.148 | +0.088 | +0.028 | -0.102 | -0.352 | -0.040 / +0.099 | dead @12, sign-flip |

1h directional (all thresholds) and 1h cross-sec: **negative at every tier**, both OOS
halves negative. e.g. 1h thr 1% h1 = -0.35% @12bps (-1032% total). No lag to trade at 1h.

## The one "ROBUST" cell is a 7-event mirage
Only `5m thr=1.0% hold=1` passes the summarize gate (h1 +0.44%, h2 +0.096% @12bps,
n=266). It does NOT survive scrutiny:
- The 266 "trades" come from only **3 distinct BTC up-bars** (longs) and **4 distinct
  down-bars** (shorts) — the same ~38 alts fanned across 7 macro candles. Effective
  independent sample is **n≈7 events**, not 266. Correlated cross-section, not a real n.
- Split by side: LONGs (n=114, 3 events) OOS **sign-flip hard**: h1 +0.54% -> h2 **-0.67%**.
  The "edge" is entirely 4 SHORT events (BTC dumps >1% in 5min, alts keep falling) — i.e.
  panic-cascade continuation, not a structural follower lag, and 4 events can't be trusted.
- Decays fast with cost (alive +0.16% @25bps, dead -0.09% @50bps) and with horizon-cost.
- This tail overlaps the already-known momentum/extension edges (big-thrust continuation),
  adds nothing new, and the trigger is so rare (7 bars in 17 days) it's untradeable on its own.

## Decay summary
- As COST rises: every rule's EV falls ~0.6%/trade per +50bps (the 1-bar round-trip tax
  swamps the signal). Anything needing thr<1% is already dead by 12bps.
- As HORIZON rises (hold 1->2 bars): no improvement; the (near-zero) signal doesn't compound.
- The predictive corr itself is ~0.014 at 5m and goes NEGATIVE at 1h — the lag decays to
  nothing within one bar and inverts by an hour.

## VERDICT: REFUTED
The deciding number: pooled lead-correlation of alt[i+1] on BTC[i] = **+0.0144 at 5m**
(noise) and **-0.0365 at 1h** (inverts). The contemporaneous same-bar corr is ~0.53, so
the BTC->alt co-move is real but already consummated within the bar — there is no
exploitable cross-bar follower lag. Every broad tradeable rule is negative by 12bps. The
lone gate-passing cell (5m thr1%) is 7 macro candles in disguise, sign-flips OOS on the
long side, and dies by 50bps. No tradeable BTC->alt lag survives 12-25bps.

Survivorship note: 40 TODAY-liquid coins; dead/delisted alts absent. That bias would only
INFLATE a follower edge, and we still find none — strengthens the refutation.
Single biggest caveat: 5m sample is only ~17 days, so the extreme-thrust tail (n≈7 events)
is genuinely under-sampled — but under-sampled is exactly why it can't be trusted, not a
reason to chase it.
