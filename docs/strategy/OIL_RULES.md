# Oil desk rules

The rules the oil desk trades by, and where each one lives in the code. This
page carries rules only, no results. Each number below is read from the file
named next to it; when the code and this page disagree, the code wins.

## What it trades

Brent, as `xyz:BRENTOIL`: a HIP-3 perp on the xyz dex, margined separately
from Hyperliquid's main dex. WTI (`xyz:CL`) is tracked alongside it.

pathiel runs on mainnet unless `PATHIEL_NETWORK=testnet` is set. Brent is
delisted on Hyperliquid testnet, so on testnet the desk trades WTI
(`xyz:CL`) as its primary instead, and the brief opens with a line that says
so: prices, levels and sizes there are WTI, not Brent. The stand-in is the
`testnet_substitute` on the Brent instrument in
`services/strategies/desks/oil.py`, applied by
`services/strategies/desks/__init__.py` only on testnet and only while the
venue does not list Brent as live. Mainnet always trades Brent. On any
network pathiel refuses a new entry on a coin that network does not list,
with a plain message, and never refuses a close, a cancel or a sweep for that
reason.

## One position

One position per account. `place_bracket` refuses a second position, or a
second resting entry, while the account's one slot is held
(`pathiel/agents/bracket.py`, `slot_taken_by`).

## Entry

The desk buys (or, under a short thesis, sells) at the market, at the live
mid, when all of these hold:

- the thesis calls a side; a flat or WAIT thesis places nothing;
- the trade pays at least the minimum reward to risk (below);
- never chase: no long above the thesis entry, no short below it;
- no entry after a break: no long while the mid is under the 24h low, no
  short while it is over the 24h high;
- the stop passes the leverage line (below).

If any one fails, nothing happens on that tick. No setup, no trade.

The rule is in `services/strategies/next_trade.py`, and the desk brief
prints its verdict for the current price.

## Stop

On Brent the stop sits 0.75 beyond the 24h range's own edge: 0.75 under the
24h low for a long, 0.75 over the 24h high for a short
(`BRENT_STOP_BUFFER` in `services/strategies/next_trade.py`). On other coins
the distance is the larger of 1.3 times the 1h ATR14 and 0.8% of the mid
(`stop_buffer_for`).

Stops fire at market so they always fill. A stop never moves: no breakeven,
no trail.

## Target

The far edge of the 24h range (the 24h high for a long), moved 0.5% of price
toward the entry (`TARGET_OFFSET_FRAC` in `pathiel/agents/bracket.py`).
Targets rest as limit orders at that price, so a target never fills below
it. `set_target` can move a target; it never touches the stop.

## Minimum reward to risk

`place_bracket` refuses any bracket that pays under 2R to its target, where
1R is the distance from entry to stop (`DEFAULT_MIN_R` in
`pathiel/agents/bracket.py`). The R is judged on the offset target.

## The leverage line

The stop must sit inside half the distance to liquidation at the leverage
used, or `place_bracket` refuses (`check_leverage`,
`DEFAULT_MAX_STOP_FRAC_OF_LIQ` in `pathiel/agents/bracket.py`). When the
stop is too wide for the leverage, the trade is refused; on Brent, silver
and gold a lower leverage is tried first (`fit_leverage` in
`services/strategies/next_trade.py`). The stop is never pulled closer to
fit.

## When the desk stands aside

- **Weekend flat.** From Friday 16:00 to Sunday 18:00 New York time (the
  oil futures close to reopen), no new Brent or WTI entry or top-up, and an
  open oil position is closed at the window open
  (`services/strategies/weekend.py`).
- **EIA report.** No new Brent or WTI entry or top-up from 2 hours before
  to 2 hours after the weekly EIA report
  (`services/strategies/event_blackout.py`). Open positions, stops and
  targets are not touched.

## When the thesis is rewritten

The thesis is the one model-written step: news and market data in, a side
with an entry, a stop and targets out. `scripts/thesis_due.py` says when it
is due: once a day; after a scheduled catalyst it listed (the API and EIA
inventory reports, OPEC, FOMC, CPI, payrolls, elections); after a position
closes; when price has moved 1.5% from where it stood when the thesis was
written; when a closed 1h candle breaks the thesis's own stop; or after a
headline alert on the desk's coins. No rewrite runs inside an hour of the
last one, except a headline alert, which waits 30 minutes.

A flat or WAIT thesis never closes an open position. That position keeps
its own stop and target.

## Size

Size comes from your risk profile, not from this page. `risk_size_trade`
sizes a trade before you place it, and `place_bracket` sizes the real order
the same way (`services/risk/derive.py`). The public install starts at most
3x leverage, risking 1% of equity at the stop, one position at a time
(`services/risk/presets.py`). The daily loss figure in the profile is shown,
not enforced.

## What is not known

The live audit of 2026-11-12 graded 36 thesis calls: the direction was right
32% of the time. It also counted 19 closed trades, and their average result
cannot be told apart from zero. There is no demonstrated edge. These rules
limit how much one trade can lose; they do not make a trade more likely to
win.
