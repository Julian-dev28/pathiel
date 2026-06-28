# engulf_opposite — SHORT (live book) vs LONG (opposite call) on the same engulf signal

Robustness check on the live engulf_short edge: a real directional signal should have its
OPPOSITE-direction call be -EV. Same bearish full-body engulf, same exit (20% stop), both sides.

n=780 bearish-engulf signals (40-coin dataset):
  hold 1d:  SHORT EV12 +1.29% (win .59, OOS +2.12/+0.44, ROBUST)  |  LONG EV12 -1.06% (win .39, OOS -1.62/-0.49)
  hold 3d:  SHORT EV12 +2.82% (win .59, OOS +4.30/+1.32, ROBUST)  |  LONG EV12 -2.42% (win .40, OOS -3.43/-1.39)

VERDICT: going LONG on the engulf signal is cleanly -EV, the near-mirror of the short (not symmetric
to the penny due to stop/cost asymmetry). This is the signature of a REAL directional edge (noise would
leave both sides ~breakeven). Confirms engulf_short's directionality + the Lane C2 'short-only' finding.
Survivor caveat: absolute magnitudes are an upper bound, but the SHORT-vs-LONG contrast is robust (same coins).
See [[project_engulf_short]].
