# Silver desk (live from 2026-10-31)

Operator, 2026-10-31, after the 30-day test (`SILVER_THESIS_TEST_2026-10-31.md`):
"add it to the desks, backend frontend and ai, keep the use only 10% of our
equity on it, add silver ticks", then "oil 95% silver 5%", then the same
morning "increase size to 10% silver": oil 90%, silver 10%.

## The setup

| Item | Oil | Silver |
|---|---|---|
| Desk id | `oil` | `metals` (silver primary, gold for the ratio) |
| Coin | xyz:BRENTOIL | xyz:SILVER (venue max 25x) |
| Equity share | 90% | 10% |
| Leverage | 20x | 20x |
| Stop | 0.75 beyond the 24h edge | 1.3 x 1h ATR14 beyond the 24h edge |
| Entry, target, gate | market off the 24h range, far 24h edge minus 0.5%, 2R | same |
| Ticks | `/desk-tick oil` every 15 min | `/desk-tick metals` every 15 min |

Config (`.agent-config.json`, through the `config` tool):

    "active_desks": ["oil", "metals"],
    "desk_equity_share": {"oil": 0.9, "metals": 0.1},
    "coin_allowlist": ["xyz:BRENTOIL", "xyz:CL", "xyz:SILVER"]

## How the split works

- `services/strategies/desk_share.py` maps a coin to its active desk and that
  desk's share (e.g. oil 0.9, metals 0.1). Every sizing path uses it: `place_bracket`, `risk_size_trade`,
  `desk_brief`'s "Your size", and the page's `POST /api/risk/size`.
- One slot per desk with a share: an oil position never blocks the silver
  tick (`scripts/desk_slot.py` per-desk mode, `bracket.slot_taken_by` with
  one slot per desk) and one position per desk (`bracket.desk_coin_held`).
- A new entry is also capped by the xyz dex's free margin, rounded down: the
  other desk's isolated margin and unrealized profit are not spendable.
- Fail closed: shares over 100%, a share outside (0, 1], or an active desk
  without one are refused by the `config` tool and by `place_bracket`.
- No `desk_equity_share` saved = the old rule: one desk, whole equity, one slot.

## The thesis

Silver's venue history starts 2025-12-26, so its "all-time low" was the low
of a falling market. The realism cap now skips an all-time extreme set in
the last 14 days (price discovery) and keeps the reach-quantile bounds;
Brent's 117.58 high (2026-03-09) is stale, so oil's cap is unchanged
(`services/thesis/desk_lookup.is_fresh_extreme`). Rerun of the 2026-10-30
silver day: short 51.97, stop 53.60, target 49.11 (was flat).

## Size at today's equity

$121.29 x 10% = $12.13 of margin, about $240 of silver at 20x, risking about
$3 per stop-out (risk_size_trade, short 50.52 stop 51.20: $3.23). Oil: $109 of
margin, about $2,180 of Brent at 20x.

## Risks

- One regime tested: silver fell 19% in the test month. A wrong side at 20x
  loses the silver slice fast; at 10% that is about $12.
- Two tick loops and two thesis writers: silver's rewrites run on the live
  thesis model (Fable), the same weekly meter oil uses.
