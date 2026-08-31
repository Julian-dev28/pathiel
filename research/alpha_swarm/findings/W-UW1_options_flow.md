# W-UW1 / W-UW2 — Unusual Whales options flow predicts xyz-equity moves (VALIDATED)

**Question (operator, 2026-07-23):** the data moat. Does smart-money options flow (UW) lead
spot on the SAME names we trade as xyz tokens — a real, non-candle edge on our best vertical?

**Method (`hypotheses/W-UW1_flow_backtest.py`, `W-UW2_signal_battery.py`):** 15 liquid US
equities that trade as xyz tokens on HL. For each US-trading day (last 45 weekdays, UW's 90d
lookback), pull UW `net-prem-ticks` summed to a daily directional signal; join to the
`xyz:TICKER` forward return from HL. Each day: rank names by the signal, LONG top-3 / SHORT
bottom-3 (cross-sectional), hold to +1d and +5d. Per-leg signed EV net 25bps, matched
same-day random-book null (2000), OOS halves. 580 ticker-day rows, 42 dates, 14 names.

## Results — ROBUST, multi-signal

| signal | style | H | n | EV/leg net25 | halves | null p | verdict |
|---|---|---|---|---|---|---|---|
| net_volume (call−put) | xs | 1d | 41 | **+1.93%** | +2.22/+1.64 | 0.0005 | ROBUST |
| net_volume | xs | 5d | 39 | **+2.02%** | +1.27/+2.74 | 0.0005 | ROBUST |
| net_premium (call$−put$) | xs | 1d | 41 | +1.73% | +1.97/+1.50 | 0.0005 | ROBUST |
| net_premium | xs | 5d | 39 | +1.73% | +1.47/+1.97 | 0.0005 | ROBUST |
| neg put/call ratio | xs | 1d | 41 | +0.60% | +0.12/+1.06 | 0.001 | weak |

Directional (own-ticker sign) confirms: net_premium +1.65% (1d) / +1.83% (5d), net_volume
+1.51% / +1.42% — all positive. (Note: "aggression" = ask−bid scored identical to net_volume
because UW's net-volume IS ask-minus-bid — one signal, not two.)

## Reading

1. **Options flow leads xyz-equity spot — validated, not noise.** Two independent flow
   measures (premium $ and contract volume) both rank the cross-section with +1.7–2.0%/leg
   net of fees, BOTH OOS halves positive, beating the same-day random-book null at p=0.0005.
   Multi-signal agreement is the tell: it's one real edge confirmed several ways, the opposite
   of the numerology/φ multiple-comparisons noise this session also produced.
2. **Strongest: net call-minus-put VOLUME, +2.0%/leg (5d).** That is ~2.6× the per-leg edge of
   `xs_xyz_equities` (+0.65%), on the same vertical — the payoff of the new alt-data.
3. **Mechanism:** dealers/institutions position in options before spot moves; the net flow is
   the leading tell. It is a real, documented equity effect, now measured on the xyz tape.

## VERDICT: **VALIDATED — wired LIVE, bounded.** `uw_flow_xs` book (agents/uw_flow_xs_live.py):
LONG top-k bullish-flow / SHORT bottom-k bearish-flow, $20/leg, 3x, k=2, 5d hold, 20% stop,
once/UTC-day, UW data via `pathia/client/uw_client`. KILL: cumulative forward EV25 < 0
over 12 rebalances → shadow_only. Caveats: 42 days = ONE ~2-month tape (UW's 90d lookback cap),
14 names, survivor-biased liquid set; both-halves + p=0.0005 + multi-signal make it the
strongest signal of the session, but scale only after it holds forward. Artifacts:
`W-UW1_flow_backtest.py`, `W-UW2_signal_battery.py`.
