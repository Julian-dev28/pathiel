# B16 funding_momentum 💰 — DATA-BLOCKED

## Hypothesis
Funding-rate TREND predicts price: persistent / rising funding = persistent directional pressure
that the price continues (or fades — sweep both in the real run).

## Why blocked
`dataset.json` carries only a single funding SNAPSHOT per coin: `universe[c]['funding']`
(confirmed: cached BTC funding = -2.4458e-06, a scalar). A trend signal needs a funding TIME SERIES,
which only `data_logger` produces (~1-2wk of accumulation). No cached history exists, and the rules
forbid hitting the network. Cannot validate against gates.

## What was built (ready for the day the feed lands)
- `funding_momentum.py`: lookahead-safe decision rule `funding_signal(funding_hist, k=8)` — trailing-k
  slope (2nd-half mean minus 1st-half mean) + level sign -> long / short / None, with a short-history guard.
- Self-test on synthetic series PASSED (rising-positive -> long, falling-negative -> short, flat -> None,
  short-history -> None).

## VERDICT: BLOCKED-DATA
When the funding series is available: join funding_hist per coin/bar, generate signals, fill i+1 open,
run alpha_lib.summarize (OOS both-halves + slippage) AND score continuation-vs-fade BOTH ways against a
matched random baseline (the -44% tape biases the short side). Combine with price-momentum per A15
carry_plus_trend. Until then: parked.
