# W-WH1: wallet 0xe282...df29, reverse-engineered

2026-09-13. The operator brought this wallet
(`hyperdash.com/address/0xe2823659be02e0f48a4660e4da008b5e1abfdf29`) as a trader
who "never lost a trade", with Hyperdash screenshots showing **30d win rate 100%,
drawdown 0.0%, Sharpe 4.65, 30d perps PnL +$5.69M**.

Those panels are accurate about what they measure. What they measure is six
trades. Everything below comes from the wallet's full public record on the
Hyperliquid info API, not from the screenshots.

- fetch: `hypotheses/W-WH1_fetch.py` (22,850 fills from 2025-02-27, 6,198
  funding rows, 469 ledger entries, live clearinghouse, 4h candles)
- analysis: `hypotheses/W-WH1_reverse_engineer.py` + `W-WH1_lib.py`
- numbers: `hypotheses/W-WH1_results.json`
- gate tests: `tests/test_wh_ladder.py`

## The reconstruction matches Hyperdash to the cent

A trip is one position from flat back to flat, which is the definition
Hyperdash's TRADES tab uses. Rebuilt from raw fills, net of fees and funding:

| trip | Hyperdash | rebuilt |
|---|---:|---:|
| BTC long 2026-09-04 to 09-11 | +11,830.17 | +11,830.17 |
| WLD long 2026-06-26 to 07-01 | -20,481.28 | -20,481.22 |
| BTC long 2025-10-10 to 2026-08-21 | +196,642.18 | +196,624.62 |
| ETH long 2025-10-10 to 2026-08-21 | +549,545.01 | +549,477.22 |
| XRP short 2025-07-25 to 07-26 | -10,359.28 | -10,204.54 |

The XRP gap is $154.73 of funding. Hyperliquid reports 2025 funding as daily
aggregates, and Hyperdash drops those rows.

**One bug worth knowing.** Hyperliquid stamps every fill of an order that
sweeps the book with the same millisecond, and their `tid`s are not in
execution order. Sorting by `(time, tid)` replayed one 60,000 XRP close as a
string of partial closes and reopens. `W-WH1_lib.chain_order` chains fills by
`startPosition` instead. A test pins it.

## Did he ever lose?

| | closed perp trips | wins | losses | win rate | net |
|---|---:|---:|---:|---:|---:|
| **last 30 days** | 6 | 6 | 0 | **100%** | +$778,622 |
| **all time** | 43 | 29 | 14 | **67.4%** | +$1,474,311 |

Losing trips total **-$274,548**. Biggest: UNI long -$108,716 and ONDO long
-$88,497, both closed 2025-10-10. The operator's own screenshots show WLD
-$20,481, XRP -$10,359 / -$2,004 / -$1,640, ONDO -$2,619, HYPE -$2,083.

**He was liquidated.** 2025-10-10 21:17 UTC: backstop liquidation of 10,355.88
HYPE ($350,919 notional) with **account value $8,818** left. One minute
earlier, at 21:16, every other position (BTC, ETH, AAVE, LINK, UNI, ONDO, CRV)
was closed with market orders. At 21:19 he deposited $402,826. By 21:26 he was
long ETH, BTC and HYPE again. Those are the two 314-day trades now on the
Hyperdash screen.

**Flow-neutral PnL**, from Hyperliquid's own `portfolio` curve (deposits
netted out, weekly samples, so the true trough is at least this deep):

| | date | perp PnL |
|---|---|---:|
| peak | 2026-01-14 | +$2,104,119 |
| **trough** | **2026-06-10** | **-$4,742,925** |
| now | 2026-09-13 | +$4,596,959 |

That is a **$6.85M drawdown**. The Hyperdash 30d window opens around 2026-08-14,
with the account at about -$1.2M, one week before ETH and BTC recovered to his
average entry. "Drawdown 0.0%" is the window's start date, not his risk.

Outcome markets (`#` coins, which Hyperdash's perp stats leave out): **-$100,191**
net. #2020 -$62,466, #2050 -$27,500, #1810 -$12,519.

Capital: $18.17M deposited plus $7.88M sent in; $14.40M withdrawn plus $14.02M
sent out. He also leads vault `0x73ce...` (+$25,222 in leader commissions).

## The strategy, from the fills

**1. Long, and mostly majors and DeFi large caps.** 85.8% of opened notional is
long. Coins: AAVE, ETH, BTC, HYPE, LINK, ZEC, CRV, NEAR, UNI.

**2. Buy the drawdown.** Median first fill is **-21.1%** under the trailing 30d
high (quartiles -32.0% / -11.6%, the 18 long trips >= $250K).

**3. Average down, never stop out.** **81.7%** of added notional was bought
*below* the running average entry. Trips >= $1M used a median of 17 separate buy
orders. Median lowest buy was 25.7% under the first. Worst: AAVE, 70.0% under.

| trip | worst mark-to-market | vs avg entry | price vs first fill | buy orders | final |
|---|---:|---:|---:|---:|---:|
| ETH long 2025-10-10 to 2026-08-21 | **-$4,571,097** | -33.6% | -54.2% | 54 | +$549,477 |
| BTC long 2025-10-10 to 2026-08-21 | **-$2,678,550** | -25.6% | -46.4% | 27 | +$196,625 |
| HYPE long 2025-10-10 to 2026-01-28 | -$489,835 | -32.4% | -38.0% | 15 | +$72,449 |
| AAVE long 2025-03-06 to 2025-10-10 | -$262,957 | -20.7% | -60.5% | 47 | +$113,522 |

11 of the 29 wins spent time >= 5% under their average entry. **Those 11
produced $1.55M of the $1.75M won (88%).** The winners are the losers he
refused to close. On ETH he paid **$305,458 in funding** to wait 315 days for
+$549K.

**4. Exit at breakeven-plus, with resting limits parked under round numbers.**
87.2% of closing notional was maker (resting reduce-only limits). **69.4%** of
maker closing notional sat one tick under a round number (109.99, 2449.9,
76999, 69.999, 10.999). His maker *opening* orders, as a control: 6.8%. Median
winning exit: **+2.72%** over average entry (+5.14% on trips >= $250K). Right now
one order rests: sell 20,000 GMX at 80.0, reduce-only.

**5. Lever up and refill.** Hyperdash shows 3.01x account leverage today. The
ETH and BTC trips alone bought $35.1M of notional, while the account held
$1.4M to $5.4M (January to June 2026). When the account broke on 2025-10-10,
fresh capital arrived within minutes.

## Open book, 2026-09-13

Nine longs, $2.27M notional, **+$996,395 unrealized, 67% of it one ZEC
position** (1,000 ZEC at $420.99 avg, marked $1,090; the account's liquidation
price on it is $461.22). AAVE has been
open since 2025-10-10 and once sat at **-$2,134,028** (-46.8% vs average entry).

## Verdict

**This is a martingale on large-cap mean reversion.** There is no stop, and
fresh capital covers every drawdown. The closed-trade win rate stays high
because losing positions stay open until they recover. The two biggest losses
he did book (UNI, ONDO) were closed in the same minute as his liquidation. Any
30-day window that starts after a recovery will read 100%.

The edge worth testing is the entry and exit rule. The deposits are not an
edge, and a bounded account cannot copy them. That test is
`W-WH2_scale_in_ladder.md`.
