# MANTA runner research — synthesis (2026-06-28)

Q: can we systematically catch more MANTA-type +50/100% runners? 4-agent swarm + logistic eq, on a
broad movers dataset (v1 160 coins / v2 421 coins incl HIP-3, 110 coins with a >=50% run).

ANSWER: there IS a real signal, but it's a ~2x SELECTION lift, not a way to ride the tail or predict the +100%.

1. RUNS ARE NOT RANDOM. Candle fingerprint (staircase/expansion/gap-and-go) ~2x runner odds p<0.001
   (candlestick_variations) but ~99% FP, OOS-flip => not standalone tradeable.
2. CONTINUATION SCORE works as SELECTION: rising-vol+momentum+accel+low-wick top-decile = 3x runner
   density; high-score breakouts + TIGHT floor = +1.36%/trade vs +0.82% all (OOS-robust both halves)
   (continuation_features + score_conditioned_exit). It's a RANKING overlay, not a green light.
3. THE EQUATION (logit_runner, v2, time-OOS): P(runner)=sigmoid(-5.8 + 0.42 z(momentum) + 0.27 z(ext)
   - 0.27 z(low_wick) - 0.15 z(accel) ...). MOMENTUM dominates; volume-surge ~0 (coincident, not predictive).
   OOS AUC 0.667. Top-quartile-by-model EV +0.96% vs rest +0.48% (~2x). Real, modest, generalizes.
4. EXIT: wide trail / ride-the-tail is -EV even on the high-score subset (tail too thin: 0.4-0.8% hit
   +50/100%). TIGHT floor (live policy) optimal everywhere (asymmetric_exit). The exit is NOT the lever.
5. GATES: do NOT relax. Extension cap blocks ZERO tradeable runners (the "missed" ones are <$0.2M micro-cap
   pumps, unfillable); relaxing admits dumps (gate_bypass). Bot already enters early (96% <20% ext).

USABLE: a momentum-led runner-quality SCORE to RANK/SIZE breakout entries (~2x EV select), banked with the
tight floor. NOT a MANTA-predictor. Survivorship makes runner rates an upper bound. See [[project_early_runner_no_tell]].
