---
name: desk-tick
description: One tick of a desk's trading loop on pathiel, for the desk id given as the argument (oil, metals, ai, gas). Sweep, levels, brief, one decision, one log line. Run on a schedule with /loop 15m /desk-tick oil. Live money on the pathiel wallet.
---

# desk-tick $ARGUMENTS

One tick for desk `$ARGUMENTS` (default `oil` if empty). One position per
account until the wallet is funded for more (the risk profile's
`max_open_positions`). Leverage up to the risk profile's `max_leverage`
(20x since 2026-10-24; `place_bracket` caps it and runs the 20x rule: the
stop must use under half the distance to liquidation). Never chase. Never
add to an open position: the operator wants the whole stake in ONE
position, so the NEXT bracket sizes full-port, and a filled position is
left alone until its stop or target fires. Every number comes from a tool
or a script, never from mental arithmetic.

## Steps, in order

0. **Slot first, before anything else.** `python3 scripts/desk_slot.py --desk $ARGUMENTS`.
   If `slot` is `held` and `mine` is false, another desk's coin holds the
   account's one slot: write the log line
   `DESK-TICK $ARGUMENTS <utc> action=SKIP slot held by <holder>` and STOP.
   No brief, no levels, no MCP calls. The desk that holds the slot is the
   only desk that ticks (operator rule, 2026-10-23).
1. **Thesis first.** `desk_brief {desk: "$ARGUMENTS"}` (quick). The call's
   instrument is COIN for the rest of the tick (oil: xyz:BRENTOIL, metals:
   xyz:GOLD or xyz:SILVER, ai: whichever equity the call names, gas:
   xyz:NATGAS). The card shows "Pays X.XR"; a call under 1.5R never trades.
   Call `desk_refresh` only when the brief says the thesis is stale or
   expired, a headline moved the market more than 1.5%, or the thesis's
   target has already been hit (the card would otherwise keep describing
   a trade that is over); at most once an hour per desk (about one dollar
   a rewrite with the current model).
2. **Housekeeping.** `bracket_sweep {coin: COIN}`. Cancels the sibling left
   after a stop or target fired; reports a resting entry with its attached
   stop and target; says whether an open position is covered. "NOT FULLY
   COVERED" means a 10x position is naked: `close_position` it in the same
   tick, sweep again, report to Julien at once.
3. **Levels.** `python3 scripts/oil_levels.py --coin COIN`: mid, last closed
   1h candle, 8h/24h/48h range, ATR14, 15m micro range.
   `python3 scripts/oil_levels.py --check ENTRY STOP TP --lev 20` judges a
   level: the R gate and the 20x line.
4. **Account slot.** Step 0 already answered it: a free slot or ours.
   (`place_bracket` refuses a taken slot on its own as well.) The only
   wallet this loop tracks is pathiel's own,
   `0x2c2e4efF574B3Fd9879AdeF9c5CAC6fD7C916985` (operator, 2026-10-24:
   his hand-traded wallet is no longer watched).
5. **Decide.** Exactly one of:
   - **In a position on COIN:** HOLD. `close_position` only if the thesis
     invalidation triggered on a CLOSED hourly candle, or a headline from
     the brief's `what_changes_my_mind` landed. After any close, sweep again.
   - **Flat with a resting entry on COIN:** keep it if `--check` still
     passes AND the entry is within one ATR14 of the mid or still on a
     current structural edge (within 0.25 ATR of the 8h or 24h range low
     for a long, high for a short). Otherwise `cancel_order {order_id, coin}`
     and fall through.
   - **Flat, slot free, nothing resting:** one setup in the thesis
     direction, anchored on structure: a pullback to the 8h or 24h range
     edge, or a breakout retest. Stop beyond the structure, target at the
     next structure, `--check` must say ok. `place_bracket {coin, entry_px,
     stop_px, tp_px}` with `entry_px` set (resting limit). Let the tool
     size it. No setup: no trade, one clause why.
6. **Log.** Append one line to `/tmp/oil-loop/progress.log` and print it:
   `DESK-TICK $ARGUMENTS <utc> coin=<COIN> mid=<mid> pos=<size@entry or flat> action=<HOLD|PLACED|CANCELLED|CLOSED|NONE> <reason, under 15 words>`

## Operator asks between ticks

- **"Set leverage to Nx" on an open position:** `set_leverage {coin,
  leverage}`. The tool keeps the position's own margin type (isolated on
  HIP-3) and reports a venue refusal as an error; confirm with
  `get_asset_positions {coin}` that `leverage.value` changed. Hyperliquid
  keeps the isolated margin in place, so the liquidation price does not
  move. Run `--check ENTRY STOP TP --lev N` first: the stop must still
  clear the 20x line at the new leverage.
- **"Where is my position":** the Desk page (`http://localhost:8000/`),
  Wallets card, the account marked Active: entry, mark, P&L, R, leverage,
  margin in use, stop, target. A flat watch-only wallet shows no card.
- **Dashes or "No open positions" on the page while the venue shows a
  position:** read `GET /api/health/system` -> `hl_budget`. It lists the
  weight granted and refused per request type and caller; a refused
  `frontendOpenOrders` means the server's venue budget is starved. Fix the
  spender, never the page.

## Hard rules

- A market entry only when `--check` with the live mid as ENTRY passes.
- Never widen a stop. Never add. One position per account until funded.
- If `place_bracket` or `bracket_sweep` is missing from the tool list, do
  nothing and log `action=NONE reconnect pathiel MCP (/mcp)`.
- Any tool error on a live-money call: stop the tick, report it verbatim.
- Report to Julien only when something changed. A HOLD tick is the log
  line only.
