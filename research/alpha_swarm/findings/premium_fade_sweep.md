# premium_fade_sweep — config sweep (is the calm-regime weakness structural?)

Live-spec backtest swept over z{2.0,2.5,3.0} x hold{5,7} x stop{20} x regime{all,down,up},
each graded through the identical shadow_ledger.classify. Time-OOS via sorted records.

Two findings:
1. HOLD 7 > 5 everywhere. all-regime z2.0: hold5 MARGINAL (OOS -2.52/+5.99) -> hold7 VALIDATED
   (m25 +3.83, OOS +1.30/+6.62). Consistent across all cells = real horizon effect (reversion
   takes ~7d), not overfitting. => changed live shadow config hold_days 5->7 (hot-read, reversible).
2. The calm/up-regime weakness is STRUCTURAL. Every BTC-UP cell has a negative first OOS half
   (-7.57 at z2/h5; still -3.42 at z3/h7). The edge does NOT exist cleanly in up-regimes -- it's a
   DOWN-regime trade. down-only z2.0/h5: VALIDATED, m25 +5.39, win .78, OOS BALANCED +6.91/+4.12.

Tension: down-gating makes it cleanly robust but OVERLAPS the existing rally_exhaustion down-regime
short thesis (less orthogonal). all-regime+hold7 stays orthogonal (fires in up too) and is VALIDATED,
but leans on the down events. Kept ALL-regime + hold7 live (shadow) for orthogonality; the forward
shadow + an eventual up-regime sample decide whether to add a down-gate. Survivor universe = upper
bound; VALIDATED-on-backtest != live-ready. See [[project_premium_fade_short]].
