# W-E1 — weekend gap-fade on xyz tokenized equities

**Hypothesis:** xyz equity perps drift while the underlying is closed over the weekend, and that drift is thin-liquidity overshoot that real flow REVERTS at the Monday reopen (gap-fade), rather than price discovery (momentum).

**Rule tested:** episode = closure span >=2 calendar days (weekend / long weekend). ref = xyz close at Friday's RTH close (20:00 UTC EDT / 21:00 EST). m = pre-open close on reopen day (13:00 UTC EDT) vs ref. If |m| >= thr: fade = enter -sign(m) at the NEXT 1h bar open (lookahead-safe i+1 fill), exit at reopen day's RTH close (~7h hold). Equal-weight basket across triggered names per weekend = ONE episode (dedup). Data: 36 US-RTH xyz names (vol >= $700k), 1h candles 2025-12-15..2026-07-09, W-E_dataset.json via W-E0_fetch.py. Script: hypotheses/W-E1_weekend_gap_fade.py.

**Structure result (model-free):** corr(weekend move, reopen-session return) = **-0.337 per-weekend basket** (n=31 weekends); -0.077 name-level (n=761); indices -0.197, singles -0.073. The sign is FADE, not discovery. Weekend hourly vol is structurally LOW (SP500: 0.13% weekend vs 0.30% RTH; MSTR 0.49% vs 1.75%) — weekend moves are thin, then real flow re-prices.

## Results (per-weekend basket, n=31 episodes)

| rule | 0bps | 12bps | 25bps | 50bps | OOS H1/H2 @12bps | p_sign | p_shortpool |
|---|---|---|---|---|---|---|---|
| FADE \|m\|>=0.5% | +0.38% | +0.26% | +0.13% | -0.12% | +0.45 / +0.07 | 0.082 | 0.101 |
| FADE \|m\|>=1.0% | +0.48% | +0.36% | +0.23% | -0.02% | +0.40 / +0.32 | 0.073 | 0.194 |
| FADE \|m\|>=2.0% | +0.59% | +0.47% | +0.34% | +0.09% | +0.64 / +0.29 | 0.112 | 0.247 |
| MOMO (all thr) | -0.38..-0.59% | negative | negative | negative | both neg | 0.89-0.93 | — |

Stop sweep (fade @1%, name-level): 8% stop +0.33%, 15-40% +0.24-0.25% — stops are near-inert at equity vol, no squeeze-inversion.

Leg decomposition @1% thr: indices n=20 episodes +0.39%@25bps win 0.70 p_sign 0.046 but H2 flips (-0.04, index perps young — SP500 listed 2026-03-18); singles n=31 +0.24%@25bps, H1/H2 +0.40/+0.34 both positive, p_sign 0.075. The all-names basket is the honest cell.

**VERDICT: MARGINAL.** The deciding numbers: EV survives 25bps at every threshold with BOTH OOS halves positive (@1%: +0.40/+0.32 @12bps), and momentum is its clean mirror-negative — but the random-sign MC p is 0.073-0.112 at n=31 weekends, short of 0.05, and the same-side-pool p (0.10-0.25) says part of the fade's short leg is tape. NOT wire-ready; it accrues only ~1 episode/week, so ~15 more weekends of shadow-grading would decide it. Survivorship: today's xyz roster = upper bound. Costs note: xyz funding over a 7h hold < 2bp (inside the 25bps tier).

**If promoted later:** fade |m|>=1%, fill next 1h bar open after the Monday 13:00 UTC pre-open print, exit at RTH close, basket across triggered names, wide stop (>=15%, inert), names >= $700k vol.
