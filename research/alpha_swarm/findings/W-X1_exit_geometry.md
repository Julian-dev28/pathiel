# W-X1 — exit geometry / win-rate engineering on validated entry families

## Hypothesis
Win rate is a design parameter of exit geometry: for each VALIDATED entry family there is an
exit that maximizes win rate subject to EV@25bps > 0 (funding included on holds >= 8h) — and the
(win%, EV25) frontier quantifies exactly what each extra point of win rate costs in EV.

## Method (pre-registered in `hypotheses/W-X1_exit_geometry.py` docstring before the first run)
- Data: W-H0 extended 1h cache (40 coins, 2025-12-13..2026-07-09, ~208d), daily bars aggregated
  from completed 1h bars (UTC day, 23:00 bar required). Fills at the NEXT 1h open (must be exactly
  close_t+1h). funding.json hourly rows (coverage 2026-03-30..2026-06-28; episodes outside coverage
  use a conservative fallback: shorts collect 0 on missing hours, longs pay >= 1.25e-5/h).
- Entries FROZEN to the validated specs (not re-derived): F1 extreme_fade ARMED long (1d ret <= -12%,
  mkt 20d skew < 0 per findings/W-B2.md; stop 20%/3d), F1d deep tier (<= -20%, subset),
  F2 funding_spike_short (F24 z >= 2 vs own 30d per findings/W-F2.md; stop 15%/5d; n=25 episodes —
  exact match to W-F2's dedup count), F3 engulf_short (daily bearish engulf, stop 20%/1d),
  F4 crash_continue_short (BTC 2d ret > 0 & coin 2d ret <= -8%, stop 20%/10d, stop per
  engulf_crash_sweep.md). Per-coin dedup: no re-entry until entry + horizon (geometry-independent,
  every cell scores the SAME episode set).
- 29 exit cells/family: baseline; partial50@B (bank 50% at +B, B in 1.5/2/3/4/6%); fulltp@T
  (T in 2/3/4/6/8%); belock@B (stop->entry after +B, B in 2/3/4%); trail@P/rR (arm at peak >= P in
  1.5/2/3%, exit at peak*(1-R), R in 10/15/20/25/35%). Bonferroni m=29.
- PESSIMISTIC intra-bar ordering (audit 2026-07-09): open -> adverse extreme -> favorable extreme ->
  close (low-before-high for longs, mirrored for shorts). Stop+TP same bar => stop. Favorable fills AT
  the trigger level, never better; adverse gaps fill at open (worse). Engine self-test: 9 hand-computed
  cases pass (`--selftest`).
- Win = net > 0 at 25bps + funding. p_pos = bootstrap P(EV25<=0), 3000 iter. p_vs_base = paired
  sign-flip permutation vs baseline (3000 iter). OOS = first/second time half EV25.

## Headline
**The operator's 65-70% win-rate target is ALREADY met at the validated baseline exits for the three
strong families** — F1 77.5%, F2 76.0%, F4 71.7% win at 25bps — at FULL EV. Every overlay that pushes
win% higher (tight TP, tight trail) buys win rate by paying EV: the frontier is monotone and steep.
Breakeven locks are the anti-goal: they CRATER win rate (scratched exits = -25bps losses) while keeping
mid EV. Only engulf_short (F3) needs geometry help to clear 65%: trail@3/r10 lifts 56.4% -> 65.1% win
and actually RAISES EV25 (+0.12 -> +0.44%/ep) — the sole family where a tighter exit is EV-positive,
consistent with a 1-day horizon edge that decays intraday.

## F1 extreme_fade ARMED (long, stop 20%, hold 72h) — n=71, span 141d
| cell | n | win25 | EV12% | EV25% | avgW% | avgL% | mDD% | OOS25 h1/h2 | p_pos | p_vs_base |
|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 71 | 77.5 | +5.80 | +5.67 | +9.71 | -8.20 | 10.3 | +8.45/+2.81 | 0.0003 | — |
| partial50@1.5 | 71 | 77.5 | +3.17 | +3.04 | +5.48 | -5.32 | 5.8 | +4.85/+1.19 | 0.0003 | 0.0003 |
| partial50@2 | 71 | 78.9 | +3.41 | +3.28 | +5.63 | -5.48 | 6.0 | +5.10/+1.41 | 0.0003 | 0.0003 |
| partial50@3 | 71 | 83.1 | +3.74 | +3.61 | +5.82 | -7.26 | 6.4 | +5.60/+1.56 | 0.0003 | 0.0003 |
| partial50@4 | 71 | 83.1 | +4.06 | +3.93 | +6.24 | -7.46 | 6.8 | +6.10/+1.69 | 0.0003 | 0.0010 |
| partial50@6 | 71 | 83.1 | +4.62 | +4.49 | +7.09 | -8.26 | 7.3 | +6.98/+1.94 | 0.0003 | 0.0110 |
| fulltp@2 | 71 | 94.4 | +1.02 | +0.89 | +1.75 | -13.50 | 2.7 | +1.75/+0.00 | 0.0417 | 0.0003 |
| fulltp@3 | 71 | 91.5 | +1.68 | +1.55 | +2.74 | -11.44 | 3.6 | +2.75/+0.31 | 0.0060 | 0.0003 |
| fulltp@4 | 71 | 90.1 | +2.31 | +2.18 | +3.60 | -10.81 | 4.8 | +3.75/+0.57 | 0.0007 | 0.0010 |
| fulltp@6 | 71 | 85.9 | +3.45 | +3.32 | +5.34 | -9.01 | 5.9 | +5.51/+1.06 | 0.0003 | 0.0110 |
| fulltp@8 | 71 | 81.7 | +4.49 | +4.36 | +7.14 | -8.03 | 6.4 | +7.45/+1.19 | 0.0003 | 0.1283 |
| belock@2 | 71 | 23.9 | +1.94 | +1.81 | +11.50 | -1.23 | 5.3 | +3.32/+0.27 | 0.0133 | 0.0007 |
| belock@3 | 71 | 36.6 | +2.89 | +2.76 | +10.58 | -1.75 | 6.6 | +4.78/+0.69 | 0.0013 | 0.0010 |
| belock@4 | 71 | 59.2 | +4.48 | +4.35 | +9.30 | -2.81 | 7.9 | +7.23/+1.39 | 0.0003 | 0.1123 |
| trail@1.5/r10 | 71 | 94.4 | +1.48 | +1.35 | +2.23 | -13.50 | 2.2 | +2.34/+0.33 | 0.0073 | 0.0003 |
| trail@1.5/r15 | 71 | 94.4 | +1.45 | +1.32 | +2.21 | -13.50 | 2.3 | +2.19/+0.43 | 0.0103 | 0.0003 |
| trail@1.5/r20 | 71 | 94.4 | +1.32 | +1.19 | +2.06 | -13.50 | 2.3 | +2.05/+0.30 | 0.0140 | 0.0003 |
| trail@1.5/r25 | 71 | 94.4 | +1.18 | +1.05 | +1.92 | -13.50 | 2.3 | +1.91/+0.17 | 0.0227 | 0.0003 |
| trail@1.5/r35 | 71 | 94.4 | +0.93 | +0.80 | +1.65 | -13.50 | 2.4 | +1.67/-0.09 | 0.0613 | 0.0003 |
| trail@2/r10 | 71 | 94.4 | +1.91 | +1.78 | +2.69 | -13.50 | 2.7 | +2.72/+0.81 | 0.0023 | 0.0003 |
| trail@2/r15 | 71 | 94.4 | +1.86 | +1.73 | +2.64 | -13.50 | 2.8 | +2.56/+0.88 | 0.0030 | 0.0003 |
| trail@2/r20 | 71 | 94.4 | +1.70 | +1.57 | +2.47 | -13.50 | 2.8 | +2.39/+0.72 | 0.0040 | 0.0003 |
| trail@2/r25 | 71 | 94.4 | +1.54 | +1.41 | +2.30 | -13.50 | 2.9 | +2.23/+0.57 | 0.0073 | 0.0003 |
| trail@2/r35 | 71 | 94.4 | +1.24 | +1.11 | +1.99 | -13.50 | 3.0 | +1.94/+0.26 | 0.0170 | 0.0003 |
| trail@3/r10 | 71 | 91.5 | +2.25 | +2.12 | +3.37 | -11.44 | 3.6 | +3.32/+0.90 | 0.0010 | 0.0007 |
| trail@3/r15 | 71 | 91.5 | +2.17 | +2.04 | +3.29 | -11.44 | 3.7 | +3.12/+0.94 | 0.0017 | 0.0007 |
| trail@3/r20 | 71 | 91.5 | +2.00 | +1.87 | +3.09 | -11.44 | 3.7 | +2.92/+0.78 | 0.0023 | 0.0007 |
| trail@3/r25 | 71 | 91.5 | +1.83 | +1.70 | +2.91 | -11.44 | 3.8 | +2.74/+0.62 | 0.0040 | 0.0003 |
| trail@3/r35 | 71 | 91.5 | +1.46 | +1.33 | +2.51 | -11.44 | 3.9 | +2.39/+0.25 | 0.0137 | 0.0003 |

Pareto: trail@2/r10 (94.4%, +1.78) / trail@3/r10 (91.5%, +2.12) / fulltp@4 (90.1%, +2.18) /
fulltp@6 (85.9%, +3.32) / partial50@6 (83.1%, +4.49) / baseline (77.5%, +5.67).
**RECOMMENDED: baseline** (win 77.5% >= 65%, EV25 +5.67%, OOS +8.45/+2.81, p_pos 0.0003,
Bonferroni-safe p*29=0.009). 90%+ win is purchasable (trail@2/r10) at 69% EV give-up.

## F1d deep tier (<= -20%) — n=6: NOT-RIPE
All cells n=6, p_pos >= 0.38, OOS h1 negative everywhere. No conclusion; do not size this tier
separately on this sample.

## F2 funding_spike_short (short, stop 15%, hold 120h) — n=25, span 77d
| cell | n | win25 | EV12% | EV25% | avgW% | avgL% | mDD% | OOS25 h1/h2 | p_pos | p_vs_base |
|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 25 | 76.0 | +5.49 | +5.36 | +10.33 | -10.40 | 9.7 | +3.50/+7.72 | 0.0067 | — |
| partial50@1.5 | 25 | 76.0 | +2.91 | +2.78 | +5.80 | -6.79 | 5.6 | +1.43/+4.49 | 0.0203 | 0.0123 |
| partial50@2 | 25 | 76.0 | +3.01 | +2.88 | +6.06 | -7.19 | 6.0 | +1.41/+4.74 | 0.0183 | 0.0120 |
| partial50@3 | 25 | 76.0 | +3.34 | +3.21 | +6.57 | -7.42 | 6.6 | +1.82/+4.99 | 0.0130 | 0.0230 |
| partial50@4 | 25 | 76.0 | +3.77 | +3.64 | +7.08 | -7.24 | 6.9 | +2.22/+5.45 | 0.0087 | 0.0663 |
| partial50@6 | 25 | 76.0 | +3.99 | +3.86 | +7.81 | -8.65 | 7.6 | +1.97/+6.26 | 0.0100 | 0.0390 |
| fulltp@2 | 25 | 88.0 | +0.53 | +0.40 | +1.78 | -9.71 | 3.4 | -0.68/+1.76 | 0.2932 | 0.0120 |
| fulltp@3 | 25 | 84.0 | +1.20 | +1.07 | +2.81 | -8.03 | 4.8 | +0.14/+2.26 | 0.1116 | 0.0230 |
| fulltp@4 | 25 | 84.0 | +2.05 | +1.92 | +3.82 | -8.03 | 5.5 | +0.94/+3.17 | 0.0307 | 0.0663 |
| fulltp@6 | 25 | 80.0 | +2.49 | +2.36 | +5.31 | -9.44 | 6.5 | +0.44/+4.80 | 0.0410 | 0.0390 |
| fulltp@8 | 25 | 80.0 | +3.86 | +3.73 | +7.02 | -9.44 | 7.2 | +1.59/+6.46 | 0.0093 | 0.2552 |
| belock@2 | 25 | 16.0 | +0.33 | +0.20 | +9.57 | -1.58 | 4.9 | +0.27/+0.13 | 0.4435 | 0.0067 |
| belock@3 | 25 | 40.0 | +2.71 | +2.58 | +9.90 | -2.30 | 6.8 | +2.81/+2.29 | 0.0403 | 0.1056 |
| belock@4 | 25 | 60.0 | +4.73 | +4.60 | +9.89 | -3.34 | 7.7 | +4.31/+4.97 | 0.0017 | 0.6465 |
| trail@1.5/r10 | 25 | 92.0 | +0.60 | +0.47 | +1.56 | -12.10 | 2.6 | -0.30/+1.44 | 0.2439 | 0.0173 |
| trail@1.5/r15 | 25 | 92.0 | +0.51 | +0.38 | +1.46 | -12.10 | 2.6 | -0.39/+1.35 | 0.2739 | 0.0153 |
| trail@1.5/r20 | 25 | 92.0 | +0.41 | +0.28 | +1.36 | -12.10 | 2.6 | -0.48/+1.26 | 0.3126 | 0.0130 |
| trail@1.5/r25 | 25 | 92.0 | +0.35 | +0.22 | +1.29 | -12.10 | 2.6 | -0.53/+1.16 | 0.3532 | 0.0120 |
| trail@1.5/r35 | 25 | 92.0 | +0.16 | +0.03 | +1.09 | -12.10 | 2.7 | -0.71/+0.97 | 0.4342 | 0.0093 |
| trail@2/r10 | 25 | 88.0 | +0.75 | +0.62 | +2.02 | -9.71 | 3.4 | -0.36/+1.86 | 0.2119 | 0.0157 |
| trail@2/r15 | 25 | 88.0 | +0.64 | +0.51 | +1.90 | -9.71 | 3.4 | -0.46/+1.74 | 0.2449 | 0.0127 |
| trail@2/r20 | 25 | 88.0 | +0.53 | +0.40 | +1.78 | -9.71 | 3.4 | -0.57/+1.62 | 0.2796 | 0.0120 |
| trail@2/r25 | 25 | 88.0 | +0.42 | +0.29 | +1.65 | -9.71 | 3.4 | -0.67/+1.52 | 0.3236 | 0.0107 |
| trail@2/r35 | 25 | 88.0 | +0.20 | +0.07 | +1.41 | -9.71 | 3.5 | -0.88/+1.28 | 0.4172 | 0.0083 |
| trail@3/r10 | 25 | 84.0 | +1.64 | +1.51 | +3.33 | -8.03 | 4.8 | +0.41/+2.91 | 0.0640 | 0.0333 |
| trail@3/r15 | 25 | 84.0 | +1.49 | +1.36 | +3.15 | -8.03 | 4.8 | +0.29/+2.72 | 0.0753 | 0.0290 |
| trail@3/r20 | 25 | 84.0 | +1.33 | +1.20 | +2.95 | -8.03 | 4.8 | +0.15/+2.53 | 0.0933 | 0.0207 |
| trail@3/r25 | 25 | 84.0 | +1.20 | +1.07 | +2.80 | -8.03 | 4.8 | -0.00/+2.42 | 0.1153 | 0.0180 |
| trail@3/r35 | 25 | 84.0 | +1.06 | +0.93 | +2.64 | -8.03 | 4.8 | -0.19/+2.36 | 0.1403 | 0.0160 |

Pareto: trail@1.5/r10 (92.0%, +0.47) / trail@2/r10 (88.0%, +0.62) / fulltp@4 (84.0%, +1.92) /
fulltp@8 (80.0%, +3.73) / baseline (76.0%, +5.36).
**RECOMMENDED: baseline** (win 76.0%, EV25 +5.36%, OOS +3.50/+7.72, p_pos 0.0067; p*29=0.19 fails
Bonferroni here, but the FAMILY was validated independently in W-F2 with its own MC p=0.0027 — this
sweep only ranks exits within it). Note the pessimistic 1h engine reproduces W-F2's daily result
(+6.0% -> +5.36%): the edge is not an intra-bar-ordering artifact.

## F3 engulf_short (short, stop 20%, hold 24h) — n=553, span 207d
| cell | n | win25 | EV12% | EV25% | avgW% | avgL% | mDD% | OOS25 h1/h2 | p_pos | p_vs_base |
|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 553 | 56.4 | +0.25 | +0.12 | +3.41 | -4.14 | 4.3 | +0.26/-0.03 | 0.2926 | — |
| partial50@1.5 | 553 | 61.1 | +0.04 | -0.09 | +2.17 | -3.64 | 3.2 | +0.02/-0.21 | 0.7144 | 0.0060 |
| partial50@2 | 553 | 61.1 | +0.08 | -0.05 | +2.38 | -3.86 | 3.3 | +0.04/-0.14 | 0.6095 | 0.0207 |
| partial50@3 | 553 | 60.9 | +0.24 | +0.11 | +2.72 | -3.96 | 3.5 | +0.27/-0.06 | 0.2679 | 0.8810 |
| partial50@4 | 553 | 59.1 | +0.30 | +0.17 | +3.06 | -4.01 | 3.7 | +0.34/-0.01 | 0.1866 | 0.4385 |
| partial50@6 | 553 | 57.0 | +0.26 | +0.13 | +3.30 | -4.07 | 4.0 | +0.30/-0.05 | 0.2622 | 0.8147 |
| fulltp@2 | 553 | 68.7 | -0.08 | -0.21 | +1.68 | -4.37 | 2.6 | -0.18/-0.25 | 0.9304 | 0.0207 |
| fulltp@3 | 553 | 65.1 | +0.23 | +0.10 | +2.40 | -4.19 | 2.9 | +0.28/-0.09 | 0.2796 | 0.8810 |
| fulltp@4 | 553 | 60.9 | +0.35 | +0.22 | +2.97 | -4.07 | 3.3 | +0.42/+0.00 | 0.1100 | 0.4385 |
| fulltp@6 | 553 | 57.5 | +0.27 | +0.14 | +3.27 | -4.09 | 3.9 | +0.34/-0.07 | 0.2379 | 0.8147 |
| fulltp@8 | 553 | 57.0 | +0.32 | +0.19 | +3.41 | -4.07 | 4.0 | +0.44/-0.06 | 0.1726 | 0.3482 |
| belock@2 | 553 | 41.8 | +0.09 | -0.04 | +3.35 | -2.46 | 3.6 | +0.18/-0.25 | 0.5791 | 0.1430 |
| belock@3 | 553 | 48.6 | +0.26 | +0.13 | +3.35 | -2.93 | 3.8 | +0.37/-0.13 | 0.2599 | 0.9513 |
| belock@4 | 553 | 50.5 | +0.24 | +0.11 | +3.42 | -3.26 | 4.0 | +0.30/-0.09 | 0.2932 | 0.8820 |
| trail@1.5/r10 | 553 | 73.2 | +0.32 | +0.19 | +1.90 | -4.51 | 2.3 | +0.30/+0.08 | 0.1090 | 0.6734 |
| trail@1.5/r15 | 553 | 73.2 | +0.26 | +0.13 | +1.83 | -4.51 | 2.3 | +0.23/+0.03 | 0.1849 | 0.9390 |
| trail@1.5/r20 | 553 | 73.2 | +0.21 | +0.08 | +1.75 | -4.51 | 2.3 | +0.18/-0.03 | 0.3032 | 0.7871 |
| trail@1.5/r25 | 553 | 73.2 | +0.16 | +0.03 | +1.69 | -4.51 | 2.3 | +0.11/-0.06 | 0.4309 | 0.5545 |
| trail@1.5/r35 | 553 | 73.2 | +0.11 | -0.02 | +1.62 | -4.51 | 2.4 | +0.08/-0.13 | 0.5611 | 0.3472 |
| trail@2/r10 | 553 | 68.7 | +0.34 | +0.21 | +2.30 | -4.37 | 2.6 | +0.33/+0.09 | 0.0876 | 0.5308 |
| trail@2/r15 | 553 | 68.7 | +0.29 | +0.16 | +2.22 | -4.37 | 2.6 | +0.26/+0.05 | 0.1639 | 0.8161 |
| trail@2/r20 | 553 | 68.7 | +0.23 | +0.10 | +2.14 | -4.37 | 2.6 | +0.20/+0.00 | 0.2616 | 0.9037 |
| trail@2/r25 | 553 | 68.7 | +0.18 | +0.05 | +2.05 | -4.37 | 2.6 | +0.13/-0.04 | 0.3842 | 0.6175 |
| trail@2/r35 | 553 | 68.7 | +0.12 | -0.01 | +1.97 | -4.37 | 2.7 | +0.11/-0.13 | 0.5258 | 0.3699 |
| trail@3/r10 | 553 | 65.1 | +0.57 | +0.44 | +2.92 | -4.19 | 2.9 | +0.68/+0.19 | 0.0047 | 0.0147 |
| trail@3/r15 | 553 | 65.1 | +0.49 | +0.36 | +2.80 | -4.19 | 2.9 | +0.59/+0.12 | 0.0173 | 0.0770 |
| trail@3/r20 | 553 | 65.1 | +0.43 | +0.30 | +2.71 | -4.19 | 3.0 | +0.53/+0.07 | 0.0397 | 0.1786 |
| trail@3/r25 | 553 | 65.1 | +0.39 | +0.26 | +2.64 | -4.19 | 3.0 | +0.45/+0.06 | 0.0670 | 0.3206 |
| trail@3/r35 | 553 | 65.1 | +0.32 | +0.19 | +2.53 | -4.19 | 3.1 | +0.42/-0.05 | 0.1396 | 0.6245 |

Pareto: trail@1.5/r10 (73.2%, +0.19) / trail@2/r10 (68.7%, +0.21) / trail@3/r10 (65.1%, +0.44).
**RECOMMENDED: trail@3/r10** — win 65.1%, EV25 +0.44%/ep, OOS +0.68/+0.19, the ONLY family where the
tight-floor trail beats baseline EV (baseline +0.12%, pΔ=0.0147). MARGINAL under Bonferroni
(p_pos*29=0.135) and h2 is thin (+0.19%); treat as shadow-improvement, not new-money license.
Baseline engulf at 25bps is nearly free of edge (+0.12%/ep, p_pos 0.29) — weaker than the live
grader's replay (+1.15 m25, engulf_crash_sweep.md); the synthetic daily-engulf entry underestimates
the live book's recorded entries, or the live number carries optimistic intra-bar grading.

## F4 crash_continue_short (short, stop 20%, hold 240h) — n=53 scored (57 collected), span 205d
| cell | n | win25 | EV12% | EV25% | avgW% | avgL% | mDD% | OOS25 h1/h2 | p_pos | p_vs_base |
|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 53 | 71.7 | +5.36 | +5.23 | +14.11 | -17.26 | 14.2 | +7.99/+2.37 | 0.0090 | — |
| partial50@1.5 | 53 | 71.7 | +3.17 | +3.04 | +7.68 | -8.72 | 7.6 | +4.22/+1.81 | 0.0053 | 0.0533 |
| partial50@2 | 53 | 71.7 | +3.42 | +3.29 | +7.94 | -8.49 | 7.9 | +4.46/+2.07 | 0.0030 | 0.0926 |
| partial50@3 | 53 | 73.6 | +3.25 | +3.12 | +8.22 | -11.08 | 8.8 | +4.52/+1.67 | 0.0093 | 0.0427 |
| partial50@4 | 53 | 73.6 | +3.03 | +2.90 | +8.72 | -13.29 | 9.5 | +4.54/+1.21 | 0.0330 | 0.0120 |
| partial50@6 | 53 | 73.6 | +3.06 | +2.93 | +9.71 | -15.97 | 10.6 | +4.46/+1.34 | 0.0473 | 0.0020 |
| fulltp@2 | 53 | 98.1 | +1.47 | +1.34 | +1.75 | -20.25 | 3.5 | +0.94/+1.76 | 0.0043 | 0.0926 |
| fulltp@3 | 53 | 92.5 | +1.14 | +1.01 | +2.75 | -20.32 | 5.5 | +1.05/+0.97 | 0.0966 | 0.0427 |
| fulltp@4 | 53 | 86.8 | +0.70 | +0.57 | +3.74 | -20.26 | 6.7 | +1.08/+0.04 | 0.2682 | 0.0120 |
| fulltp@6 | 53 | 79.2 | +0.75 | +0.62 | +5.73 | -18.87 | 9.1 | +0.94/+0.30 | 0.3279 | 0.0020 |
| fulltp@8 | 53 | 77.4 | +2.00 | +1.87 | +7.69 | -18.00 | 9.6 | +2.56/+1.15 | 0.1163 | 0.0160 |
| belock@2 | 53 | 18.9 | +1.78 | +1.65 | +11.93 | -0.74 | 6.3 | +1.47/+1.83 | 0.0267 | 0.0990 |
| belock@3 | 53 | 26.4 | +2.16 | +2.03 | +14.31 | -2.38 | 8.4 | +3.18/+0.83 | 0.0633 | 0.0933 |
| belock@4 | 53 | 35.8 | +2.30 | +2.17 | +14.05 | -4.47 | 10.0 | +4.53/-0.28 | 0.1023 | 0.0570 |
| trail@1.5/r10 | 53 | 98.1 | +1.72 | +1.59 | +2.00 | -20.25 | 2.2 | +1.13/+2.06 | 0.0033 | 0.1083 |
| trail@1.5/r15 | 53 | 98.1 | +1.59 | +1.46 | +1.88 | -20.25 | 2.2 | +1.01/+1.93 | 0.0043 | 0.0960 |
| trail@1.5/r20 | 53 | 98.1 | +1.50 | +1.37 | +1.78 | -20.25 | 2.3 | +0.92/+1.83 | 0.0063 | 0.0890 |
| trail@1.5/r25 | 53 | 98.1 | +1.53 | +1.40 | +1.81 | -20.25 | 2.3 | +1.10/+1.70 | 0.0053 | 0.0903 |
| trail@1.5/r35 | 53 | 98.1 | +1.55 | +1.42 | +1.84 | -20.25 | 2.3 | +0.89/+1.96 | 0.0100 | 0.0840 |
| trail@2/r10 | 53 | 98.1 | +2.34 | +2.21 | +2.64 | -20.25 | 3.5 | +1.97/+2.46 | 0.0010 | 0.1823 |
| trail@2/r15 | 53 | 98.1 | +2.20 | +2.07 | +2.50 | -20.25 | 3.5 | +1.81/+2.34 | 0.0010 | 0.1649 |
| trail@2/r20 | 53 | 98.1 | +2.10 | +1.97 | +2.40 | -20.25 | 3.5 | +1.67/+2.28 | 0.0010 | 0.1543 |
| trail@2/r25 | 53 | 98.1 | +1.94 | +1.81 | +2.24 | -20.25 | 3.5 | +1.51/+2.13 | 0.0010 | 0.1360 |
| trail@2/r35 | 53 | 98.1 | +1.97 | +1.84 | +2.26 | -20.25 | 3.5 | +1.32/+2.38 | 0.0023 | 0.1313 |
| trail@3/r10 | 53 | 92.5 | +2.00 | +1.87 | +3.68 | -20.32 | 5.5 | +2.31/+1.41 | 0.0247 | 0.0996 |
| trail@3/r15 | 53 | 92.5 | +1.81 | +1.68 | +3.48 | -20.32 | 5.5 | +2.09/+1.26 | 0.0350 | 0.0856 |
| trail@3/r20 | 53 | 92.5 | +1.65 | +1.52 | +3.30 | -20.32 | 5.5 | +1.89/+1.13 | 0.0463 | 0.0696 |
| trail@3/r25 | 53 | 92.5 | +1.49 | +1.36 | +3.13 | -20.32 | 5.5 | +1.67/+1.05 | 0.0676 | 0.0606 |
| trail@3/r35 | 53 | 92.5 | +1.52 | +1.39 | +3.16 | -20.32 | 5.6 | +1.37/+1.41 | 0.0700 | 0.0610 |

Pareto: trail@2/r10 (98.1%, +2.21) / partial50@3 (73.6%, +3.12) / baseline (71.7%, +5.23).
**RECOMMENDED: baseline** (win 71.7%, EV25 +5.23%, OOS +7.99/+2.37, p_pos 0.009; p*29=0.26 —
family carries external validation, engulf_crash_sweep.md). trail@2/r10 is striking (98.1% win,
52/53) but its one loss is a full -20% gap-through: the geometry converts a 72%-win/-17% avg-loss book
into a 98%-win book with rare tail bombs. EV cost: -58%.

## Portfolio view (historical frequencies, $20-60/position, EV25)
| portfolio | families | blended win | eps/mo | $20/pos | $60/pos |
|---|---|---|---|---|---|
| A: max-EV s.t. win>=65% | F1+F2 base, F3 trail@3/r10, F4 base | **68.2%** | 114.4 | **+$43/mo** | **+$130/mo** |
| A ex-F3 (capital-real) | F1+F2+F4 baselines | **75.7%** | 33.1 | +$36/mo | +$109/mo |
| B: high-win variant | F1 trail@2/r10, F2 fulltp@4, F3 trail@1.5/r10, F4 trail@2/r10 | 78.7% | 114.4 | +$16/mo | +$47/mo |
| B ex-F3 | — | **92.2%** | 33.1 | +$13/mo | +$38/mo |

The win-rate/EV exchange rate: moving the 3 strong families from baselines (75.7% blended) to the
high-win geometries (92.2%) costs 65% of monthly EV ($109 -> $38 at $60/pos). 70% blended win is
achievable at essentially ZERO EV cost because the baselines already sit there.
F3's 81 eps/mo dominates any episode-weighted blend but is unrealizable under live notional caps —
the ex-F3 rows are the honest live projection.

## VERDICT: ROBUST for the frontier shape; baselines already clear the win target
Deciding numbers: F1 baseline 77.5% win / +5.67% EV25 (p*29=0.009, OOS +8.45/+2.81); F2 baseline
76.0% / +5.36% (externally validated family); F4 baseline 71.7% / +5.23%; blended 75.7% win at full
EV with zero geometry changes. The requested 65-70% portfolio win rate does NOT require buying win
rate with EV. The only geometry CHANGE with positive expected impact is engulf_short -> tight-floor
trail (arm +3%, retrace 10%), MARGINAL significance.

## Caveats
- Survivorship: today's-liquid-40 universe; all positive EV is an upper bound (shorts less affected).
- n=25 (F2) and n=53 (F4) are small; per-cell Bonferroni fails for their baselines — both families
  lean on their original validation for existence, this study only orders their exits.
- funding.json covers 2026-03-30..06-28; F1/F3/F4 episodes outside it use the conservative fallback
  (longs pay estimated funding, shorts collect none on uncovered hours).
- One regime cycle (~7 months). F1/F4 OOS h2 decays (+8.45->+2.81, +7.99->+2.37): expect the weak end
  in flat tape.
- 1h bars still hide sub-hour paths; pessimistic ordering bounds but does not eliminate path risk for
  stops and trails tighter than ~1.5%.
- Live DSL must fill trail exits with market orders on 1h marks to match this sim; slower polling
  loosens realized retraces.
