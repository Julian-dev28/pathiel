# LANE C scoreboard (microstructure / behavioral / event / exotic)

C1 oi_divergence: BLOCKED-DATA — only a single OI snapshot in cache, no time series; logic stubbed + unit-tested (5 cases green), ready for data_logger.
C16 montecarlo_null_harness: TOOL-READY — mc_null.py (shuffle-label + block-bootstrap p-values), self-test green (random p=0.40 vs edge p=0.0003). Import it from later tests.
C2 liquidation_cascade_fade: REFUTED — fee-dominated 5m mirage; EV 0.13%@12bps collapses to 0.005%@25bps, neg@50bps, sign-flips OOS (n=1450). No robust cell in 180 combos.
C3 volume_divergence: REFUTED — raw short EV is the -44% tape; excess over matched random-short only +0.66%, z=1.16, p=0.12. High-EV cells sign-flip OOS.
C4 wick_rejection: REFUTED — no robust cell on 1h or 4h; best EV@25bps ~0.02% (zero), neg@50bps, OOS sign-flips, win<50%. Fee-dominated noise.
C5 nday_high_breakout: MARGINAL/SHADOW — 50d-high long + BTC-up gate + 25% stop + 20d hold: excess +5.84% vs random-long-in-up (p=0.02 shuffle, 0.025 block), survives 50bps, both halves +, 15/15 family cells +EV. Risk: survivorship (upper bound).
C6 round_number_magnet: REFUTED — no robust cell; 1d short cells sign-flip (h1 +2.0/h2 -0.4, = short-beta tape) and 1h fee-dominated negative. No magnet/rejection edge.
C7 opening_range_breakout: REFUTED — no robust cell; best EV@25bps -0.125% w/ OOS sign-flip, ungated -0.34%/trade (win 44%). Fee-dominated coin-flip.
C8 vwap_reversion: REFUTED — cost-brutal; faint reversion (EV@0 +0.14%, win 53%) but negative by 25bps. No cell survives slippage.
C9 engulfing_reversal_xs: ROBUST +EV (modest) — SURPRISE survivor. Daily bullish-engulf long/bearish-engulf short, 1d hold: excess +0.60-0.86% vs 3 nulls incl bigbar control (p<=0.0006), both halves +0.79/+0.77, survives 50bps, full hz=1 stop family +. Risk: modest per-trade edge + survivorship.
C10 nr7_range_compression: MARGINAL (short-only) — NR4-downbreak short in down-regime adds +1.88% excess over down-regime-matched random short (p=0.00025) but OOS h2 only +0.43% (front-loaded). Long side REFUTED (sign-flip). Overlaps live down-regime short.
C11 gap_fill: INCONCLUSIVE (data-structural) — only 31 gap events >=0.5% across 40 coins/~83d; 24/7 perps don't gap. No tradeable sample. Effectively refuted by absence.
C12 entropy_predictability_filter: INCONCLUSIVE — low-PE crash-fades better & OOS-robust (high-PE h2 -2.6), monotone terciles, but low-vs-high EV gap +2.18%/trade p=0.22 (not significant at n=177). Right direction, undersized. Re-test on higher-n base.
C13 obv_vpt_slope: REFUTED (not distinct) — OBV-flow book is +EV but it IS momentum; OBV-minus-MOM incremental negative in 6/8 cells, sign-flips in rest. Subsumed by live XS-momentum book.
C14 sector_rotation: REFUTED — sector-momentum sign-flips (~0 EV); intra-sector momentum +EV but weaker than all-universe book in 4/4 cells; intra-sector RV negative. Sector structure discards signal.
C15 sympathy_followthrough: REFUTED — paired (laggard - same-side same-time out-of-sector) EV -0.12 to -0.20% in all 9 cells, both halves neg. Co-movement is just market beta; chasing laggards is a net loser.
