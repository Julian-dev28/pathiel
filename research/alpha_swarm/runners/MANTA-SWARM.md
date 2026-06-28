# MANTA runner swarm — can we catch the +50/100% runs better?

THE REFRAME (why the first crude test was insufficient): predicting a runner BEFORE it starts has
no tell (prior research + the MANTA trace: vol expands into it, no coil). But the real question is
CONTINUATION: the bot enters at the EARLY breakout (MANTA at +15-20%, entry 0.10, before the big
candle). Given a move has STARTED, what distinguishes the ones that CONTINUE to +50-100% from the
ones that fizzle? That is tractable and is what the bot does (enter early + trail the rare runner).

## Data
`movers_dataset.json` (in the scratchpad): ~160 native perps, 1h candles ~2000 bars each, broad
(small-caps where runners happen). Load: it's {meta, universe, candles[coin]['1h'] = [[t,o,h,l,c,v]]}.
A "RUNNER" = a coin that went >=50% (and separately >=100%) low->high within ~48h. A "FIZZLE" = an
early breakout that did NOT continue. The dataset has both — that's the labeled set to learn from.
Survivorship: live coins only (dead pumps absent) — so a real signal is if anything understated, but
positive forward EV is still an upper bound. Use `alpha_lib` for OOS/slippage helpers if useful.

## MANTA fingerprint (from the trace, for reference)
48h base range 23.8%, ATR% 2.2%, vol EXPANDING (compression 2.06), ignition = 65x-volume +50% bar,
new 48h high. The ignition is coincident (not predictive); the EARLY breakout (the +10-20% stage) is
the entry point. The biggest-volume spikes are EXHAUSTION on average (my test: 30x surge -> -6.36% fwd).

## Rules (every agent)
READ-ONLY, cache-only (movers_dataset.json), NO live code / orders / pytest. Lookahead-safe: decide on
bars up to i, fill i+1 open. The decisive metric is NOT raw mean EV (most breakouts fizzle) — it's:
  (a) does the feature LIFT the runner-capture rate vs the base rate (precision/recall on continuation), AND
  (b) does an ENTER-EARLY + ASYMMETRIC-EXIT policy turn the high-FP breakout into +EV (the rare +100%
      runner pays for the many small losses — asymmetric payoff is the whole game here).
Report FP rate honestly. A clean "no feature separates runners from fizzles" is a real result; but EXIT
asymmetry might still make it +EV even with no entry edge. Score EXCESS over a matched random-breakout null.
Write findings to `scratchpad/findings/<id>.md`.
