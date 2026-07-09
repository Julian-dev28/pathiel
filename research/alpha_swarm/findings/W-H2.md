# W-H2 — hourly dispersion spikes as an xs-momentum overlay — REFUTED (both directions)

## Hypothesis
When cross-sectional 1h return dispersion spikes (>= expanding P90), the next
6-24h resolve either as momentum CONTINUATION (spread widens -> upsize the
momentum book on spikes) or CONVERGENCE (spread compresses -> downsize/flip).
Either direction would be a conditional overlay on the live xs-momentum book.

## Distinct from prior art
A8 `dispersion_mean_reversion.md` refuted DAILY convergence as a standalone
book. This is hourly dispersion, scored as an overlay (spike-cell vs calm-cell
vs an always-on pool), with a pre-declared decision rule (see
`hypotheses/W-H2.py` docstring): overlay only if spike-cell net(2x12bps) > 0,
OOS both halves positive, mc_p < 0.05 (mirrored for the convergence claim).

## Rule
disp[i] = xs pstdev of 1h rets (>=20 names); spike = disp >= strictly-past
expanding P90 (burn-in 336 bars). Book: rank trailing-24h return, long top-8 /
short bottom-8, fill open[i+1], hold {6,24}h, per-event spread, cost 2x tier,
funding netted on 24h holds where funding.json covers. Episodes deduped to
hold-length spacing. MC pool = same book at every 3rd eligible bar (n=993+).

## Results (per-event book spread)
| cell | n | gross | net12 | net25 | OOS12 h1/h2 | mc_p | excess |
|--|--|--|--|--|--|--|--|
| SPIKE 6h | 251 | +0.074% | -0.166% | -0.426% | -0.334/+0.002 | 0.442 | +0.013% |
| CALM 6h | 747 | +0.032% | -0.208% | -0.468% | -0.325/-0.091 | 0.711 | -0.028% |
| SPIKE 24h | 110 | +0.280% | **+0.022%** | -0.238% | **-0.409/+0.505** | 0.466 | +0.017% |
| CALM 24h | 192 | +0.138% | -0.115% | -0.375% | -0.202/+0.000 | 0.736 | -0.127% |

spike-minus-calm gross diff: +0.042% (6h), +0.142% (24h). Funding adjustment
where covered: -0.013% to -0.017% per book (negligible).

## VERDICT: REFUTED (as an overlay, both directions)
Deciding numbers: the only net-positive cell (SPIKE 24h, +0.022% @2x12bps)
**sign-flips OOS (-0.409/+0.505) and its mc_p vs the always-on pool is 0.466**
— the spike gate adds nothing beyond unconditional momentum timing (excess
+0.017%). The convergence direction fails symmetrically (spike-cell EV is
positive-gross, not negative, so there is nothing to fade). Hourly dispersion
spikes carry no conditioning information for the momentum book at 6-24h. The
declared decision rule is not met on any cell; no overlay. Survivorship caveat
applies.
