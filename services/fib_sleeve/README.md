# services/fib_sleeve: the Fibonacci sleeve on its own sub-account

Operator, 2026-11-16: direct 10% of equity to a Fibonacci-retracement rule
on a sub-account so it never nets against the oil desk's Brent position.
The swing anchor the rule retraces from switched 2026-11-16 ~09:00Z from
the 1h zigzag to the 24h range ("for the fib make it [the] low high
range"), the same 24h range `services.strategies.levels` already reports.
The zigzag stays in the code as an option (`swing: "zigzag"` in the
override file); it is not the default.

**Status: built, enabled.** Nothing is placed, cancelled or moved while
`enabled` is false; the operator ran `--enable` and a cron runs `--live`.

**The backtest verdict on the live row is FAIL** (10+ trades beside live,
none pass or lean - worse evidence than the old zigzag row's NOISE, which
only meant "too few trades to judge"). Read
`docs/strategy/FIB_RETRACE_TEST_2026-11-16.md` before trusting it further.
The operator knows and asked for this anchor anyway, same footing as
running a NOISE row small.

**Outcome it moves:** net P&L of sub-account `0x3d3b...c272` against its
starting equity, with a hard stop at 3 losses in a row or -20%.
Trace: one JSON line per live tick in `$PATHIEL_STATE_DIR/fib_sleeve/ticks.jsonl`,
the record rebuilt from the sub-account's public fills (`--status`).

## The rule (variant `range24_with_L382_limit_atr1_T272_be50`, the live default)

| Part | Value | Source in `fib_retrace_test.py` / `rule.py` |
|---|---|---|
| Swing | the 24h range: the low and high of the last 24 CLOSED 1h bars - the same window `services.strategies.levels.price_range` reports (the Desk page's Levels card, `scripts/oil_levels.py`); direction is the window's LATER extreme (high after low = up, buy the retrace; low after high = down, sell it) | `swing_from_window(hours[-24:], atr, "r24")`, `rule.current_swing_range24` |
| Direction | with the swing: buy a retrace of an up-swing, sell a retrace of a down-swing | `"with"` |
| Entry | resting limit at the 38.2% retracement, one entry per swing, none once price traded through the level | `"limit"`, `fib_price` |
| Stop | the level less 1 x ATR14 (1h), fixed when the order is placed | `stop_price("atr1")` |
| Target | the -27.2% extension beyond the swing extreme, a limit, no 0.5% offset | `target_price(0.272)` |
| Ratchet | stop to the entry once a closed 15m bar after the fill bar covered 50% of the way to target | `ratchet_stop`, `"be50"` |
| Risk | 6% of the SUB-ACCOUNT's equity lost at the stop (price, taker fee both ways, 3 bps slippage) | `risk_capped_notional` |
| Leverage | 20x cap, 15x when the stop is too wide for 20x, refused when too wide for 15x | `next_trade.fit_leverage` |
| Floor | 2R, the stop inside half the distance to liquidation, one position | `bracket.place_bracket` |
| Session | no order in the oil weekend or on the first 15m bar after it; an open position is closed at the weekend open | `weekend_closed`, `reopen_tick` |

**Example (the operator's pasted levels, 2026-11-16 ~08:30Z):** 24h range
91.800-94.108, the high last (an up-swing, buy the retrace): 38.2%
93.226344, 50% 92.954000, 61.8% 92.681656.

**Why this row.** The doc's own "best variant per family" table prints the
24h-range-with-the-swing family's ceiling across EVERY entry type, and that
ceiling uses `"confirm"` (a 15m close back beyond the level, market next
open) - this sleeve only places a resting limit (`"limit"`), per the
existing safety ("only the resting-limit entry is built", `config.
validate`). `range24_with_L382_limit_atr1_T272_be50` is that same family's
best row restricted to `entry="limit"` (123 variants; re-run off the doc's
own cache with `swing/direction/entry` fixed to `range24`/`with`/`limit`,
everything else the full grid). At 6% risk, Brent journal beside live: 10
trades, 50% wins, +$117.11, max drawdown 22.6% (vs the live book's 27.9%),
avg R +0.98. Replay: 20 trades, +$99.18. WTI: 13 trades, +$73.75. Alone it
loses: -$14.86 on the journal (23 trades), -$127.14 on 208 days of Brent 1h
bars, -$119.65 on WTI. **Verdict FAIL**, the family's own bar in the doc
(10+ trades beside live, none pass or lean).

The old zigzag row (`zz2_with_L382_limit_atr1_T618_be75`): zigzag k 2 on 1h
bars, same direction/entry/stop, target the -61.8% extension, ratchet be75.
3 trades, 3 wins, +$240.50 beside live on the journal; alone 208 days 28
trades, 9 wins, +$141.25, max DD 47.9%; WTI -$12.69; verdict NOISE (3
trades, not enough to judge). Still runs if `swing` is set back to
`"zigzag"` in the override file - but that alone does NOT restore
`target`/`ratchet` to 0.618/be75; override those too for this exact row
(every value is independently valid, part of the grid the test crossed,
just not the one above).

The live rule is the backtest's functions called with live data, not a
copy. `tests/test_parity.py` runs the backtest's walker on seeded candles
and asserts `rule.plan_entry` names the same entry, stop, target and
leverage at every fill, for BOTH swing modes.

**A resting entry or an open position already working under the OTHER
swing mode when `swing` changes is left alone**, managed to completion
with its own levels; the active `swing` only governs a brand new entry
(`tick.py`'s flat-with-a-resting-entry branch tries the entry's own mode
before calling it stale; an open position never reads `cfg.swing` at all -
its stop and target are whatever already rests on the venue, and the
break-even ratchet never reads the swing either, so it is
swing-change-safe by construction). Nothing was open or resting at this
switch, so it took effect immediately.

Live differs from the backtest in four places:

1. The stop is fixed at the ATR of the hour the order was placed. The
   walker re-reads ATR every bar until the fill.
2. The sub-account has no live long beside it, so this is the "alone"
   column, not the "beside live" one above.
3. A stale entry is cancelled on one tick and the new one placed on the next.
4. The tick runs when the cron runs. Between ticks the venue holds the
   entry, the stop and the target; only the ratchet and the weekend close
   wait for a tick.

## How the sub-account is traded

- A sub-account has no key. The master's API wallet signs and the action
  carries `vaultAddress = <sub-account>` (Hyperliquid docs, exchange
  endpoint, "Subaccounts and vaults"; "Nonces and API wallets": "A master
  account can approve API wallets to sign on behalf of the master account
  or any of the sub-accounts"). SDK 0.24.0: `Exchange(vault_address=...)`
  puts it in the signed hash and the payload.
- `pathiel.client.exchange.account_target(addr)` is the opt-in switch;
  `pathiel.client.subaccount.AccountExchange(addr)` binds the whole exchange
  module to it and is passed to `bracket.place_bracket(..., ex=...)`.
  Without it every existing path builds the same SDK client as before with
  `vaultAddress` null (`tests/test_subaccount_target.py`).
- Reads pass the sub-account's own address, on the coin's own dex.
- The sub-account (`pathiel`, master `0x2c2e...6985`) is in **unified
  account** mode: its money is the spot USDC balance, which margins
  xyz:BRENTOIL directly. No transfer to the xyz dex is needed. Equity =
  spot USDC + unrealized P&L (`subaccount.account_equity`). In standard mode
  the same function reads the xyz dex's accountValue, and the money would
  have to sit on xyz.
- No new approval is needed: the API wallet `pathiel` is already approved
  by the master. A live tick first asks the venue (`userRole`) that the
  account is a sub-account of the signing master and refuses otherwise.

## Kill switch

Checked every tick, from the sub-account's own fills since the first live
tick (`ledger.py`). Any one of:

- 3 losing round trips in a row (a break-even ratchet exit pays fees, so it
  counts as a loss),
- realized loss of 20% of the starting equity,
- flat, and equity 20% under the starting equity.

It latches in `state.json`, is logged in `ticks.jsonl`, cancels a resting
entry and blocks every new one. An open position keeps its stop and target.
Only `--rearm` clears it. Starting equity = the equity at the first live
tick (or `starting_equity_usd` in the override file).

## Run

```
python3 scripts/fib_sleeve_tick.py --dry-run     # read, decide, print; signs nothing, writes nothing
python3 scripts/fib_sleeve_tick.py --status      # config, kill switch, record; read only
python3 scripts/fib_sleeve_tick.py --enable      # enabled=true in $PATHIEL_STATE_DIR/fib_sleeve/config.json
python3 scripts/fib_sleeve_tick.py --live        # one live tick; "disabled" and no read while enabled=false
python3 scripts/fib_sleeve_tick.py --disable
python3 scripts/fib_sleeve_tick.py --rearm       # after a kill: clear it, new record from the next live tick
```

Cron line for the loops window, every 15 minutes, one minute after the bar
closes (not created by this change):

```
1,16,31,46 * * * * cd <repo> && .venv/bin/python scripts/fib_sleeve_tick.py --live >> /tmp/fib-sleeve/cron.log 2>&1
```

To turn it on: fund the sub-account (done: $20.06 spot USDC on 2026-11-16),
run `--dry-run` and read the decision, run `--enable`, add the cron line.
To stop: `--disable` (a resting entry stays until cancelled by hand or by a
kill; an open position keeps its bracket).

Decisions a tick can print (`action`): `disabled`, `would_place` / `place`,
`resting`, `cancel_entry`, `hold`, `ratchet`, `close`, `no_order`, `killed`,
`blocked`, `refused`, `place_incomplete`, `error`. `why` says which rule.

## Pieces

| File | Does |
|---|---|
| `config.py` | Defaults = the variant; override file; refuses untested values |
| `rule.py` | Pure: swing, level, stop, target, gates, size, ratchet |
| `ledger.py` | Pure: round trips from fills, the kill switch |
| `store.py` | `state.json` and `ticks.jsonl`; read-only in a dry run |
| `tick.py` | One tick; everything it touches is injected (`Deps`) |
| `evals/live_dry_run.py` | The dry-run tick on live public reads; fails on any signing attempt |
| `scripts/fib_sleeve_tick.py` | The cron entry point |

## Tests and eval

```
.venv/bin/python -m pytest -q services/fib_sleeve tests/test_subaccount_target.py   # 113 tests, no network, about 2 s
.venv/bin/python -m services.fib_sleeve.evals.live_dry_run                          # read only, public reads, exit 0 = pass
```

## Where it breaks

- **The first live order is the proof** that this API wallet may act for
  this sub-account on a HIP-3 dex. The docs and the SDK say yes; nothing
  here could test it without placing an order. A refusal comes back as the
  order's error: the tick prints `refused` and nothing rests.
- **One API wallet, two signers.** The desk and the sleeve sign with the
  same API wallet, which has one nonce set (docs: "separate subaccounts
  signed by the same API wallet will share the nonce tracker"). The SDK's
  nonce is the millisecond clock, so a clash needs both to sign in the same
  millisecond; the loser gets a venue error and retries next tick. A second
  API wallet for the sleeve removes it.
- **Unified-account equity while a position is open** is spot USDC +
  unrealized P&L. If the venue moves isolated margin out of the spot total,
  that number reads low until the position closes. Sizing only happens flat,
  and the drawdown check on equity only runs flat, so neither is affected;
  the realized-loss check uses fills.
- **A read that fails** (position, equity, candles) is an `error` tick:
  nothing is placed. A failed fills read undercounts the record for that
  tick; the latch, once set, does not depend on it.
- **The level is usually already spent.** The zigzag's k=2 pivot is
  confirmed by a 2 ATR reversal, already past the 38.2% level unless the
  leg ran over 5.2 ATR. The 24h range has no such confirmation delay (the
  extreme is known the moment its own bar closes), but the window itself
  slides forward every hour, so the level it names can already be behind
  where price has since moved. Most ticks print `no_order: level_spent`
  either way. That is the rule, and why the backtest has few trades.
- **A partial fill at the weekend open** is closed and every order on the
  coin cancelled. A manual order on the sub-account's Brent is treated as
  the sleeve's: keep the sub-account for the sleeve only.
- **$20 equity.** 6% is $1.20 a trade; the venue's $10 minimum order is
  refused by `place_bracket` before anything is sent when a wide stop sizes
  under it.
