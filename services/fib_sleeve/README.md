# services/fib_sleeve: the Fibonacci sleeve on its own sub-account

Operator, 2026-11-16: direct 10% of equity to the Fibonacci-retracement rule
whose best backtest row was the 1h zigzag, on a sub-account so it never nets
against the oil desk's Brent position.

**Status: built, DISABLED.** `enabled` is false in code. Nothing is placed,
cancelled or moved until the operator runs `--enable` and a cron runs
`--live`.

**The backtest verdict on this rule is NOISE** (3 trades). Read
`docs/strategy/FIB_RETRACE_TEST_2026-11-16.md` before enabling. The operator
knows and chose to run it small.

**Outcome it moves:** net P&L of sub-account `0x3d3b...c272` against its
starting equity, with a hard stop at 3 losses in a row or -20%.
Trace: one JSON line per live tick in `$PATHIEL_STATE_DIR/fib_sleeve/ticks.jsonl`,
the record rebuilt from the sub-account's public fills (`--status`).

## The rule (variant `zz2_with_L382_limit_atr1_T618_be75`)

| Part | Value | Source in `fib_retrace_test.py` |
|---|---|---|
| Swing | zigzag on closed 1h bars, a pivot confirmed after a reversal of 2 x ATR14; the last confirmed leg | `zigzag_pivots`, `swing_from_pivots`, `ZZ_K["zz2"]` |
| Direction | with the swing: buy a retrace of an up-swing, sell a retrace of a down-swing | `"with"` |
| Entry | resting limit at the 38.2% retracement, one entry per swing, none once price traded through the level | `"limit"`, `fib_price` |
| Stop | the level less 1 x ATR14 (1h), fixed when the order is placed | `stop_price("atr1")` |
| Target | the -61.8% extension beyond the swing extreme, a limit, no 0.5% offset | `target_price(0.618)` |
| Ratchet | stop to the entry once a closed 15m bar after the fill bar covered 75% of the way to target | `ratchet_stop`, `"be75"` |
| Risk | 6% of the SUB-ACCOUNT's equity lost at the stop (price, taker fee both ways, 3 bps slippage) | `risk_capped_notional` |
| Leverage | 20x cap, 15x when the stop is too wide for 20x, refused when too wide for 15x | `next_trade.fit_leverage` |
| Floor | 2R, the stop inside half the distance to liquidation, one position | `bracket.place_bracket` |
| Session | no order in the oil weekend or on the first 15m bar after it; an open position is closed at the weekend open | `weekend_closed`, `reopen_tick` |

Backtest numbers for this row at 6% risk (from the test's `variants.csv`):
beside live on the Brent journal 3 trades, 3 wins, +$240.50; alone on the
journal 5 trades, 3 wins, +$186.65; alone on 50 days of 15m bars 10 trades,
3 wins, +$88.65, max drawdown 26.6%; alone on 208 days of 1h bars 28
trades, 9 wins, +$141.25, max drawdown 47.9%; WTI 208 days -$12.69. The
be50, be75 and off twins are the same trades to the cent on Brent: the
ratchet never changed an exit there. be75 is the operator's choice and the
doc's finding (helps on 7 of 7 datasets, about +0.01R a trade).

The live rule is the backtest's functions called with live data, not a
copy. `tests/test_parity.py` runs the backtest's walker on seeded candles
and asserts `rule.plan_entry` names the same entry, stop, target and
leverage at every fill.

Live differs from the backtest in four places:

1. The stop is fixed at the ATR of the hour the order was placed. The
   walker re-reads ATR every bar until the fill.
2. The sub-account has no live long beside it, so this is the "alone"
   column, not the +$240.50 "beside live" one.
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
.venv/bin/python -m pytest -q services/fib_sleeve tests/test_subaccount_target.py   # 91 tests, no network, about 2 s
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
- **The level is usually spent at confirmation.** A k=2 pivot is confirmed
  by a 2 ATR reversal, which is past the 38.2% level unless the leg was
  longer than 5.2 ATR. Most ticks print `no_order: level_spent`. That is
  the rule, and why the backtest has few trades.
- **A partial fill at the weekend open** is closed and every order on the
  coin cancelled. A manual order on the sub-account's Brent is treated as
  the sleeve's: keep the sub-account for the sleeve only.
- **$20 equity.** 6% is $1.20 a trade; the venue's $10 minimum order is
  refused by `place_bracket` before anything is sent when a wide stop sizes
  under it.
