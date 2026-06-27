# C1 oi_divergence — DATA-BLOCKED

## Hypothesis
Price-up + OI-up = new-money continuation (go long); price-up + OI-down = short-covering
that fades (go short). The price/OI quadrant tells you continuation vs fade.

## Why blocked
`dataset.json` carries only a **single** `openInterest` snapshot per coin
(`universe[coin]['openInterest']`), and candle rows are `[t,o,h,l,c,v]` — volume only,
no OI. The signal needs a per-bar OI **time series** (Δprice vs ΔOI each bar), which only
arrives once `data_logger` has ~1-2wk of OI history wired. Cannot backtest from cache.

## What was done instead
`scratchpad/oi_divergence.py` stubs + unit-tests the classification logic on synthetic
data (5 cases, all pass): the 4-quadrant taxonomy (long_buildup / short_covering /
short_buildup / long_unwinding) and the implied tradeable side. Ready to bolt onto real
OI deltas the moment the feed exists.

## VERDICT
**BLOCKED-DATA** — no OI time series in cache (only a snapshot). Logic stubbed + green.
Revisit when data_logger OI history is available; then score continuation vs fade as
excess over a matched random-entry baseline like every other lane item.
