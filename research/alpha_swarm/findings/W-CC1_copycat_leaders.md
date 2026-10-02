# W-CC1: copycat leader profile and sizing viability, 10 wallets

2026-10-02. Operator wants a "copycat" book that mirrors 10 leaderboard
wallets at once, each on an equal slice of a 25% sleeve
(`our_equity x 0.25 / 10` per leader). Before any code trades real money this
asks the only question that matters first: at our actual equity, is a typical
copy of each leader's typical trade even large enough to clear Hyperliquid's
$10.50 order minimum?

This is a sizing audit, not a backtest. It does not validate the copy rule's
edge. Script: `hypotheses/W-CC1_leader_profile.py`. Data:
`hypotheses/W-CC1_results.json`. Read-only `/info` calls only, no signed
requests, nothing placed.

## Our account

Resolved the way the repo resolves it (`resolve_user_address()`, master over
wallet): `0x2c2e4efF574B3Fd9879AdeF9c5CAC6fD7C916985`.

**Equity: $1.29** (main perp $1.28 + xyz perp $0.01). The account is
effectively unfunded.

## The 10 leaders

His equity is perp account value (main + xyz) plus spot USDC, the same
definition `copy_trade_live.LiveVenue.leader_state` uses (his spot USDC is
his collateral, per W-CP1). "Typical open" is the median notional of his
`dir == "Open Long"/"Open Short"` fills in the last 30 days. "Opens/day" and
"xyz fraction" are fill-count based, not notional-weighted.

| leader | role | equity | eff. lev | fills/30d | opens/day | coins | med. open | xyz % | med. hold | trips | 30d PnL | all-time PnL |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0x95da8596 | user | $2,053,963 | 0.00x | 6,320 | 76.7 | 23 | $500 | 1.1% | 2.8h | 57 | +$234,007 | +$1,094,485 |
| 0xac142fee | user | $923,473 | 0.27x | 3,587 | 44.0 | 18 | $981 | 27.9% | 9.5h | 95 | +$73,758 | +$305,014 |
| 0xe2823659 | user | $1,357,514 | 2.39x | 772 | 9.7 | 7 | $1,504 | 16.6% | 24.4h | 9 | +$2,339,083 | +$6,392,097 |
| 0x0b38c011 | user | $400,001 | 0.00x | 11,865 | 187.8 | 3 | $1,000 | 100.0% | 11.2h | 19 | +$191,642 | +$441,897 |
| 0xf97ad670 | user | $1,001,193 | 0.13x | 3,518 | 42.0 | 24 | $1,485 | 18.9% | 6.1h | 119 | +$78,458 | +$3,579,559 |
| 0x04a97ae7 | user | $942,670 | 1.47x | 1,490 | 26.8 | 4 | $3,746 | 12.9% | 19.6h | 14 | +$252,959 | +$580,891 |
| 0xe09726ff | user | $7,350,722 | 1.36x | 22,454 | 449.6 | 18 | $456 | 2.2% | 18.1h | 42 | +$1,976,676 | +$7,016,246 |
| 0xf5079c84 | user | $3,254,317 | 0.73x | 3,577 | 30.9 | 13 | $405 | 96.3% | 28.1h | 20 | +$381,361 | +$1,330,326 |
| 0xd260b221 | user | $2,859,046 | 2.07x | 20,908 | 337.8 | 9 | $561 | 0.0% | 38.3h | 8 | +$1,156,879 | +$7,758,214 |
| 0xbe10fd36 | user | $3,346,738 | 2.50x | 7,005 | 135.6 | 11 | $416 | 9.6% | 26.0h | 7 | +$1,502,099 | +$3,244,976 |

`userRole` reports all 10 as plain `user` accounts, none `vault` or
`subAccount`. That is the API's own classification, not an inference; it
doesn't rule out one being a subaccount whose master we don't have, since HL
has no public "is X someone's subaccount" lookup from the subaccount's own
address alone.

Full per-leader detail (positions with leverage/liquidation, staked HYPE,
spot balances, liquidation distance at max isolated leverage per coin) is in
`W-CC1_results.json`.

## Viability at our actual equity ($1.29)

Per-leader allocation = $1.29 x 0.25 / 10 = **$0.032**. Every leader's typical
copy notional rounds to $0.00. All 10 are below the $10.50 minimum and below
the $2.625 quarter-minimum floor. **All 10 leaders are uncopyable at our
current size, by five to seven orders of magnitude.** This isn't a marginal
shortfall to fix with a config tweak; the account needs to be funded before
this book can place a single order for any leader.

## Minimum equity for a median copy to clear $10.50

`min_equity = $10.50 x 10 / (0.25 x ratio)`, where `ratio` = his typical open
notional / his equity. Sorted cheapest to fund first:

| leader | ratio (open/his equity) | min. equity for median copy >= $10.50 |
|---|---:|---:|
| 0x04a97ae7 | 0.003974 | $105,689 |
| 0x0b38c011 | 0.002499 | $168,074 |
| 0xf97ad670 | 0.001483 | $283,154 |
| 0xe2823659 | 0.001108 | $379,025 |
| 0xac142fee | 0.001063 | $395,189 |
| 0x95da8596 | 0.000243 | $1,724,880 |
| 0xd260b221 | 0.000196 | $2,139,165 |
| 0xf5079c84 | 0.000124 | $3,374,681 |
| 0xbe10fd36 | 0.000124 | $3,382,170 |
| 0xe09726ff | 0.000062 | $6,775,008 |

Reading this as "fund to $105,689 and one leader works": at that equity only
0x04a97ae7's *median* trade clears the floor. Copying the other nine still
drops most of their individual trades under $10.50 (a median is not a floor
on the distribution), and this 10-leader sleeve is designed to run all 10 at
once, not one at a time. The honest number for "most trades from most
leaders clear the floor most of the time" is close to the $6.78M top of this
table, not the $105.7K bottom. These leaders are leaderboard whales; a $1.29
or even a $10,000 account cannot express their position sizes without either
skipping almost every signal or bumping every order up to many multiples of
the intended size (which `min_order_bump_max_mult` exists specifically to
cap).

One sizing footnote: the task's requested "25% of the minimum" floor
($2.625) is a viability marker, not the code's actual bump threshold. With
`min_order_bump_max_mult` at its current config value of 2.0, the executor's
real bump floor is $10.50 / 2.0 = **$5.25** (50% of the minimum, not 25%). A
copy notional between $2.625 and $5.25 clears neither floor as the executor
is configured today.

## The honest caveat: there is no backtest here

This script answers "can the account even place the order," not "is the
rule any good." `research/EVIDENCE_DOCTRINE.md` requires a backtest before a
book earns capital. One of these 10 leaders, 0xe2823659...df29, already has
one: `W-CP1_copy_0xe282.md` backtested copying this exact wallet with
leverage and it was REFUTED. Every start month from 2025-03 to 2025-10 ends
liquidated at every multiplier from 0.5x to 3x, and the one surviving window
loses to random entry timing (p 0.79-0.85). It is live anyway, by operator
override, at 1x, with a -50% mark-to-market demote.

Nothing in this script, and no fill/PnL number in the table above, is
evidence that copying any of the other 9 wallets would make money net of
real costs, survive out-of-sample, beat a matched null, or survive
multiple-comparison correction. Their 30d and all-time PnL are what the
leader himself made trading his own account at his own size and skill; it is
not what a mechanical copier following him at a fraction of his size, with
execution lag, slippage, and the $10.50 floor's rounding, would have made.
Treat the whole 10-leader "copycat" idea as unvalidated until each leader
gets the same backtest treatment W-CP1 gave 0xe282, or until the operator
overrides doctrine again and accepts that the book trades on sizing
plausibility alone, not proven edge.

## Addendum 2026-10-02: fixed-dollar sizing replaces proportional at this equity

Everything above assumed `sizing_mode: proportional` (our notional = his
fraction of his own equity, applied to our allocation), which is the
original build. The operator added fixed-dollar sizing the same day,
specifically because proportional sizing is mathematically unable to ever
place an order for an account this small: the leaders run $400K-$7.35M, so
even their biggest single-day add is a rounding error against a $0.03
per-leader allocation (`$1.31 x 0.25 / 10`). `sizing_mode: fixed_margin`
(now the default) instead commits a fixed dollar MARGIN per leader,
`fixed_margin_usd` (default $0.25), at whatever leverage `leverage_mode`
computes for that coin: `notional = fixed_margin_usd x exchange_leverage`.
`sizing_mode: fixed_notional` sizes the position directly in notional
dollars instead (default $10.50, the exchange floor).

### What the dry run actually opened, real reads, real market prices, zero orders sent

`.venv/bin/python scripts/copycat_dry_run.py --cycles 2 --interval 30`,
real equity $1.307, default config (`fixed_margin`, `leverage_mode: max`,
`max_position_leverage: 50`, `fixed_margin_usd: $0.25`), 2026-10-02. First
cycle opened 3 copies; the second (30s later) opened/added/closed nothing
because nothing in the 10 leaders' books changed in that window:

| coin | leader | leverage | notional | margin | liq. distance |
|---|---|---:|---:|---:|---:|
| XRP | 0xac142fee46f8dacbfef23e1e41d79f1f7233487e | 20x | $10.50 | $0.525 | 2.50% |
| ETH | 0x04a97ae7f350a22cd0cdb6b1875e8905b76495aa | 25x | $10.50 | $0.420 | 2.00% |
| BTC | 0xf5079c84c34051d7c4cd87494fda534fe7ecdf6d | 40x | $10.50 | $0.263 | 1.25% |

Liquidation distance is the same approximation used earlier in this doc and
in W-CP1 (maintenance margin roughly half the initial margin fraction, so
the adverse move to liquidate at isolated leverage L is roughly
`1 / (2L)`). At 40x that is 1.25%: BTC moving against the position by
little more than one percent wipes the $10.50 position and costs the
$0.2625 margin behind it.

Main-dex margin: **$1.2782 available before this run, $1.2075 used by
these 3 copies (94%), $0.0707 left.** A fourth copy needs at least
$2.625 x leverage/(that coin's own leverage) in computed notional before
even reaching the bump floor, and $0.07 of free margin cannot back one at
any leverage this book would compute. 23 other coins were evaluated and
skipped this cycle: most for being more than 10% worse than the leader's
own entry price (`max_worse_entry_pct`, unrelated to sizing), the rest for
landing under the $10.50 floor even after the bump (some because the
margin room was already mostly spent by XRP/ETH/BTC before their turn came
-- leaders are processed in config order, so whoever owns a coin earlier in
`copycat.leaders` gets first claim on the shrinking room each cycle).

### The "$0.25 x 50x = $12.50, ~5 copies" estimate needs one correction

The simple version of fixed_margin sizing is `$0.25 x leverage`. That is
only what actually happens when the coin's own leverage is high enough that
`0.25 x leverage` already clears $10.50 on its own -- i.e. leverage >= 42.
None of the three real candidates above reached that (20x, 25x, 40x), so
`min_order_bump` rounded every one of them UP to exactly the $10.50 floor
instead of their raw `$0.25 x leverage` product ($5.00, $6.25, $10.00).
Margin at the floor is `$10.50 / leverage`, which is **larger** than the
nominal $0.25 for any coin under 42x -- $0.525 for XRP's 20x is more than
double the "flat $0.25" assumption. Only a coin whose real exchange max
leverage is 42x or higher gets the clean $0.25-margin behavior the round
estimate assumes (few coins on Hyperliquid go that high; BTC's is 40x).
The real, measured number today is **3 concurrent copies fit in $1.28 of
main-dex margin, not ~5** -- fewer, because real per-coin leverage caps
and the bump-to-floor interaction push actual margin per copy above the
nominal $0.25 in every case measured so far. The qualitative risk framing
holds regardless of the exact count: each copy's maximum loss is bounded to
its own margin (not the whole sleeve), and that margin is realistically
$0.25-$0.55 per copy at today's prices and leverage caps, lost outright on
a 1-2.5% adverse move with no stop behind it.

This remains sizing-plausibility only, not a backtest. Fixed-dollar sizing
makes the book able to place an order; it says nothing about whether
following the leader who opened XRP, ETH, or BTC today will be a position
worth having tomorrow.
