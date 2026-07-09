# W-F3 funding_settlement_micro

**Hypothesis.** HL settles funding HOURLY; around a settlement whose payment is extreme,
there is systematic short-horizon price drift (positioning unwinds around the payment).
This is CONDITIONAL on the funding state — not the refuted unconditional 22:00
time-of-day effect.

**Rule** (`hypotheses/W-F3.py`). Conditioning signal = the rate settled at hour t−1
(published then; hourly funding is persistent so it proxies the payment at t). Event =
per-coin z of that rate vs own trailing-30d hourly distribution, |z| ≥ {2.5, 3.0} AND
|rate| ≥ 5e-5/h; consecutive extreme hours = ONE episode (first hour only). Windows on
1h candles: INTO = bar [t−1,t) o→c, OUT = bar [t,t+1) o→c, OUT3 = o(t)→c(t+2). Null =
`mc_null` vs the all-eligible-hours pool per window (3000 iters), both directions.
34 coins × ~83d of 1h bars.

## Per-event drift (bps, gross)

| side | z | n | into (p) | out (p) | out3 (p) |
|---|---|---|---|---|---|
| positive funding | 2.5 | 145 | −9.2 (.15 dn) | −9.3 (.15 dn) | +17.0 (.15 up) |
| positive funding | 3.0 | 122 | −2.7 (—) | +12.6 (.11) | +7.3 (—) |
| **negative funding** | 2.5 | 251 | **+25.1 (.0003)** | +7.5 (.15) | +6.3 (—) |
| **negative funding** | 3.0 | 193 | **+21.3 (.006)** | **+20.8 (.009)** | +1.9 (—) |

POSITIVE side: no coherent drift (REFUTED). NEGATIVE side: price drifts UP through the
extreme-negative settlement and the hour after; dead by hour 3 (out3 flat) — the move is
concentrated in a 2h window around settlement.

## Cluster honesty checks (the part that usually kills these)

2h combined trade = long at t−1 open (right after the extreme settlement), exit close of
bar t, + the funding a long COLLECTS at t (negative funding pays longs):

| z | aggregation | n | mean | net@12 | net@25 | p |
|---|---|---|---|---|---|---|
| 2.5 | per event | 251 | +33.8 | +21.8 | +8.8 | 0.0003 |
| 2.5 | per HOUR cluster | 222 | +26.8 | +14.8 | +1.8 | 0.006 |
| 2.5 | per DAY cluster | 56 | +34.5 | +22.5 | +9.5 | 0.0500 |
| 3.0 | per event | 193 | +43.8 | +31.8 | +18.8 | 0.0003 |
| 3.0 | per HOUR cluster | 174 | +36.9 | +24.9 | +11.9 | 0.0017 |
| **3.0** | **per DAY cluster** | **53** | **+47.5** | **+35.5** | **+22.5** | **0.017** |

- Day-cluster OOS halves: z=3.0 **+63.2 / +32.4 bps** (both +); z=2.5 +38.9 / +30.2.
- Not a time-of-day artifact: events spread across all 24 UTC hours (max share 7.3%).
- Not one coin: 27 coins fire; max single-coin share 11% (JUP).
- Max 4 coins fire in the same hour; 53 distinct UTC days at z=3.0.

## VERDICT: MARGINAL (+EV, real but execution-bounded) — negative side only

Deciding numbers: z≥3.0 negative-funding settlement long, 2h hold: +43.8 bps gross,
+18.8 bps net@25 per event, day-cluster p=0.017, both OOS halves positive at every
aggregation. It dies by ~50 bps slippage — this is an execution-sensitive micro edge on
often-thin alts during volatile hours, ~2.3 events/day. NOT wired live on that ground.

**Live-book relevance (flag for the operator):** this is direct evidence AGAINST the
`neg_funding_fade` SHORT book on short horizons — right after extreme-negative funding
settlements, price systematically drifts UP for ~2h and the short also PAYS the funding.
Consistent with that book currently regrading negative once funding is included.

**Shadow spec if wanted:** long at settlement of an hourly rate with z≥3 vs own 30d and
rate ≤ −5e-5; exit 2h later; maker entries only (the edge ≈ one taker round-trip);
skip if spread > 10 bps. Survivor-universe upper bound applies.
