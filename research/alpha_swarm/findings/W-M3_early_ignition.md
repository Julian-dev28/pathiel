# W-M3 — early ignition (volume spike before the coin is a top mover)

**Hypothesis (the "as early as possible" ask):** a 1h bar with dollar volume >= 4x its trailing-24h mean AND +3% 1h return, while 24h extension is still < +8% (i.e., BEFORE it shows up as a top mover), marks the start of a run.

**Rule tested (pre-registered):** signal at bar close (vol>=4x trailing mean, ret1h>=+3%, r24<+8%, dv24 >= floor {$5M,$20M}), 24h per-coin dedup, fill next 1h open. Full 13-policy exit grid, all/up/dn regime views, same-coin random-time MC null. Family 78 cells, Bonferroni alpha 6.4e-04. 179 signals at $5M, 75 at $20M.

## Result: 0 / 78 cells wire-eligible — negative essentially everywhere

EV25 (%/trade), view=all:

| exit | $5M (n=178) | $20M (n=75) |
|---|---|---|
| h6_s5 | -0.69 | -0.86 |
| h6_s15 | -0.45 | -0.45 |
| h12_s5 | -0.44 | -0.69 |
| h12_s15 | -0.56 | -0.77 |
| h24_s8 | -0.97 | -1.08 |
| h24_s15 | -0.83 | -0.69 |
| h48_s5 | -0.36 | -0.69 |
| h48_s15 | -1.42 | -1.41 |
| trail(.02/.10) | -1.06 | -0.86 |

- All 26 all-view cells negative at EV25 (and all but two already negative at 12bps).
- Regime split does not rescue it: best cell in the whole family is `f5M|h48_s5|up` at EV25 +0.32 with OOS halves (-0.95, +1.75) — a sign flip — and p=0.126.
- dn-regime: uniformly negative.

## VERDICT: REFUTED

Deciding number: 0/78 cells pass; all-view EV25 < 0 in every cell. Volume ignition + small pop + low prior extension is an early entry into CHOP, not into runs — consistent with the earlier early-runner precursor refute (FP ~99.5%), now confirmed with a volume-conditioned signature on 208 days. The "earlier is better" direction is not where the edge lives on this universe.

Survivorship: today's-liquid-set upper bound applies.
