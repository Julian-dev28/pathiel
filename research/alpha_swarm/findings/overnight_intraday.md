# overnight_intraday — crypto intraday-segment drift anomaly

## Hypothesis
Crypto daily returns split into a UTC "overnight" block (00:00–12:00) and an
"active" block (12:00–24:00); one segment systematically carries the drift and a
rule long-only during the good segment beats always-long buy-and-hold (the crypto
analog of the equities overnight-drift anomaly).

## Data / mechanics
- 40 liquid perps, 1h candles, ~83 days, ~3,320 coin-days per segment.
- Segment return = **open-to-open** across the segment boundary (open@start →
  open@end). Naturally fillable, lookahead-safe; each segment = 1 round-trip trade.
- BTC regime = BTC daily close vs its 20d SMA at the most recent daily close ≤
  segment start (lookahead-safe).
- Costs/OOS via `alpha_lib.summarize` (slip 0/6/12/25/50 bps + first/second TIME halves).

## Result 1 — the gross drift split is REAL, the timing rule is NOT
Per-segment mean return (net of slip):

| Segment (long)          | slip0    | net12bps | net25bps | win  | OOS h1 / h2     | verdict     |
|-------------------------|----------|----------|----------|------|-----------------|-------------|
| overnight 00–12         | -0.046%  | -0.166%  | -0.296%  | .445 | -0.120 / -0.213 | bleeds      |
| active 12–24            | +0.168%  | +0.048%  | -0.082%  | .462 | +0.220 / -0.127 | SIGN-FLIP   |
| buy-hold full day       | +0.121%  | +0.001%  | -0.129%  | .464 | +0.222 / -0.225 | flat        |

Direction confirms the folklore: the **active/US block carries the daily drift,
the overnight block bleeds.** But the active-only rule is a SIGN-FLIP across halves
(+0.22 → -0.13) and its net-12bps edge (+0.048%/seg ≈ 4.8bps) is below the ~12bps
survival bar. 6h quarters: only **12–18 UTC** is robust both halves (+0.062/+0.030)
but at +4.6bps net it's economically dead. US split (13–21 vs rest): both sign-flip.

## Result 2 — it's entirely regime-gated, and timing still loses to buy-hold
| Rule                          | net12bps | net25bps | win  | OOS h1 / h2     | verdict          |
|-------------------------------|----------|----------|------|-----------------|------------------|
| LONG active \| BTC up         | +0.228%  | +0.098%  | .481 | +0.336 / +0.109 | ROBUST           |
| LONG overnight \| BTC up      | +0.080%  | -0.050%  | .476 | -0.070 / +0.246 | sign-flip        |
| LONG active \| BTC down       | -0.135%  | -0.265%  | .442 | -0.179 / -0.089 | bleeds           |
| **LONG buy-hold full day \| BTC up (benchmark)** | **+0.427%** | **+0.297%** | .512 | +0.381 / +0.477 | ROBUST |
| LONG both segs \| BTC up      | +0.154%  | +0.024%  | .479 | +0.132 / +0.177 | robust but weak  |

The killer: in an up regime **buy-and-hold (+0.427%/seg) beats long-active-only
(+0.228%)**. Timing into the active block throws away the (also-positive) overnight
drift and pays a second round-trip — it strictly *underperforms* just holding.
The "active carries drift" effect is true but does not produce a strategy that beats
holding, because buy-hold banks both segments on one cost.

## Result 3 — one genuine residual: SHORT overnight in BTC downtrends
| Rule                          | net12bps | net25bps | win  | OOS h1 / h2     | verdict   |
|-------------------------------|----------|----------|------|-----------------|-----------|
| **SHORT overnight \| BTC down** | **+0.178%** | +0.048% | .538 | +0.299 / +0.051 | ROBUST@12 |
| SHORT active \| BTC down       | -0.105%  | -0.235%  | .519 | -0.061 / -0.151 | sign-flip |

This is the only piece that is *additive* rather than "just be long crypto in an
uptrend." The down-regime damage concentrates specifically in the 00–12 overnight
block: shorting it is robust both halves @12bps, while shorting the active block in
the same downtrend is REFUTED. So the segment asymmetry is real and directional.
BUT it's marginal: dies toward 25bps (+0.048%) and h2 is thin (+0.051%), and it
overlaps with plain "short crypto while it's falling."

## Result 4 — continuation/reversal link is noise
corr(active_ret, next-day overnight_ret) = **+0.038** (n=3,280). Both the
continuation rule (long next overnight after strong active, >0.5%) and the reversal
rule are SIGN-FLIP across halves. No predictive link between segments.

## VERDICT: REFUTED (as a buy-hold-beating rule)
The intraday-segment drift exists *gross* — the active/US 12–24 UTC block carries
the positive daily drift, the 00–12 overnight block bleeds, and the bleed concentrates
in BTC downtrends. But **no long-only segment-timing rule beats buy-and-hold**: in
up regimes holding the whole day (+0.427%/seg net12) dominates active-only (+0.228%),
and unconditionally every segment-timing variant is a sign-flip or sub-12bps. The
deciding number: long-active-only loses to buy-hold by ~20bps/seg in the only regime
where either is positive.

One **MARGINAL** survivor worth a shadow line, not a live flip:
**SHORT 00–12 UTC overnight when BTC < 20d SMA** — net12 +0.178%/seg, ROBUST both
halves, win .538. Risks: (1) dies near 25bps so live slippage erases it; (2) largely
redundant with a generic "short in downtrend" filter; (3) survivor-biased universe =
upper bound; (4) only ~1,640 segment-days / ~41 down-days of breadth.
