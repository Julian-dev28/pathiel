# C8 vwap_reversion — REFUTED (real at 0bps, dead by 25bps — cost-brutal)

## Hypothesis
Extreme intraday deviation from session VWAP mean-reverts; fade above→short, below→long.

## Exact rule
5m candles, session VWAP resets each UTC day (cumulative typical-price×vol up to bar i,
lookahead-safe). dev=(close−vwap)/vwap; |dev|>thr → fade toward VWAP. Fill i+1 open. Exit:
horizon {6,12,24} 5m bars + stop sweep {8..40}%. thr∈{0.5%,1%,2%}. `alpha_lib.summarize`
with the full slippage decay 0→50bps reported.

## Results (slippage decay is the point)
| thr | hz | stop | n | EV@0 | EV@12 | EV@25 | EV@50 | win | h1 | h2 |
|--|--|--|--|--|--|--|--|--|--|--|
| 2% | 24 | .15 | 2778 | **+0.142%** | +0.022% | **−0.108%** | −0.358% | .534 | +0.026 | +0.019 |
| 1% | 24 | .15 | 5373 | +0.110% | −0.011% | −0.141% | −0.391% | .505 | +0.016 | −0.037 |

- A faint reversion IS there at zero cost (EV@0 +0.14%, win 53.4%, both halves +0.02% at
  12bps) — but it's an order of magnitude too small. EV crosses zero between 12 and 25bps and
  is −0.36% at 50bps.
- **No robust-both-halves cell survives 25bps.** Stop width is irrelevant (rarely hit on 5m).

## VERDICT
**REFUTED.** Deciding number: best EV is only **+0.14%/trade even at 0bps** and goes
**negative by 25bps** (−0.11%). The mean-reversion is real but microscopic — exactly the
"cost-brutal" outcome flagged in the item. At any realistic Hyperliquid round-trip cost the
VWAP fade is a net loser. No tradeable edge; the MC null is moot (nothing survives slippage).
