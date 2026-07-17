# PNL FORENSICS — where $260 became $19

Window: 2026-06-01 00:00:00 UTC -> 2026-07-17 19:45:14 UTC (last fill at fetch time).
Fetched 2026-07-18 from the Hyperliquid `/info` API (`userFillsByTime`, `userFunding`,
`userNonFundingLedgerUpdates`, `portfolio`, `clearinghouseState`) for
`0x2c2e...6985`. All timestamps in this doc are UTC. Fills are ms-epoch; the
loop log is local (+0800) and was converted. Raw data + scripts:
`/private/tmp/claude-501/-Users-julian-dev-Documents-code-hermes-trader/79fc4494-d85c-45f4-b633-3f5e34eb766a/scratchpad/`
(`fetch_data.py`, `analyze.py`, `match_bot_manual.py`, `details.py`, `raw_data.json`).

Pagination: 2 pages (2000 + 722), 1 duplicate at the boundary deduped by tid.
`aggregateByTime:true` cross-check returns 2126 rows with identical closedPnl and
fee sums (delta 0.0000). Nothing truncated.

## 0. The accounting identity (exchange truth, closes to ~$1)

| item | value |
|---|---|
| account value 2026-06-03 (portfolio API, perp+spot) | $196.18 |
| peak equity (heartbeat, 2026-05-27 / intraday 06-08) | $261.44 / $281.08 |
| external deposit 2026-06-25 (UETH spotTransfer in, sold to USDC 06-26) | +$73.25 |
| external withdrawals since June 1 | $0.00 |
| realized closedPnl (2,718 perp fills) | **-$105.97** |
| trading fees (100% taker, USDC) | **-$144.66** |
| funding | **-$1.40** |
| **total trading destruction** | **-$252.03** |
| account value 2026-07-17 (last portfolio point) | $17.91 |
| clearinghouse now: main $9.41 + xyz $8.47 + dust | ~$17.90 |

196.18 + 73.25 - 252.03 = 17.40 vs 17.91 actual; residual < $1 (open-position
unrealized at the endpoints, spot dust, one -$0.26 IP settlement). The "~$260"
was the late-May peak. Every dollar is accounted for: **no leak, no hack, no
withdrawal — the money was traded away, and most of the net bleed was fees.**
The ~40 ledger "send" entries are internal perp<->spot<->xyz shuttles
(self-to-self, net $0); the 06-08 "account cut to $35" was USDC parked on spot
for the qwen test, not a loss.

## 1. Realized PnL and fees by week (UTC, weeks start Monday)

| week | fills | gross closedPnl | fees | funding | net | notional traded |
|---|---|---|---|---|---|---|
| 2026-06-01 | 828 | +114.11 | 47.44 | -0.57 | **+66.09** | $158,211 |
| 2026-06-08 | 822 | -31.31 | 37.75 | +0.98 | **-68.08** | $110,250 |
| 2026-06-15 | 417 | -17.29 | 14.84 | -1.09 | **-33.23** | $54,745 |
| 2026-06-22 | 241 | -10.91 | 10.00 | -0.16 | **-21.08** | $32,086 |
| 2026-06-29 | 274 | -86.00 | 17.67 | +0.07 | **-103.60** | $67,075 |
| 2026-07-06 | 84 | -66.04 | 16.30 | -0.39 | **-82.72** | $46,423 |
| 2026-07-13 | 55 | -8.53 | 0.65 | -0.23 | **-9.41** | $2,361 |
| **TOTAL** | **2,721** | **-105.97** | **144.66** | **-1.40** | **-252.03** | **$471,152** |

One positive week (the first). $471k notional churned against a $100-250
account = roughly 2,000x turnover in 6.5 weeks, at 3.07 bps average taker fee.

## 2. Per-coin realized (net = closedPnl - fee), top 10 each way

Losers: XRP -72.11 (n=71, mostly $600-1,700 notional shorts+longs chopped),
xyz:SP500 -34.41, ZEC -22.78 (41 episodes of churn), VINE -22.12 (one day),
DOGE -21.59 (single 15-day long), xyz:BIRD -21.00 (a -64.06 liquidation offset
by one +55 win), xyz:ARM -18.00, KAITO -17.93, XPL -14.86, xyz:SMSN -14.39.

Winners: MANTA +43.67, ETH +43.46, xyz:CBRS +34.37, ADA +32.48, ONDO +21.41,
LIT +18.95, SOL +18.87, BTC +16.18, xyz:SPCX +13.49, BNB +13.33.

Splits: HIP-3 (colon coins, 57 of them) net **-136.71**; main dex net
**-113.92** (main dex was gross +6.62 before its $120.54 of fees). Long
episodes 726, net **-189.42**; short episodes 129, net **-61.44**. Gross wins
+1,133.84 vs gross losses -1,239.81 (payoff 0.91, win rate ~49% — a coin-flip
engine paying taker both ways).

Liquidations: 5 fills, closedPnl **-73.35** (xyz:BIRD -63.92 on 06-18,
CASHCAT -4.41, xyz:DRAM -2.92 on 06-01 00:00, xyz:DELL -1.06, xyz:CRCL -1.04).

## 3. Bot vs manual

Method: bot event index built from the session log (`execute` executed:true,
`dsl_exit`/`ai_close` executed:true with their logged order-ids, `book_open`,
executed book events, `hard_killswitch` as an all-coin marker) plus loop-log
"LIVE opened" lines (+0800 -> UTC). A fill is bot if its oid matches a logged
bot close oid, else if a same-coin bot event sits within +/-3 min, else if a
killswitch fired within +/-3 min.

Fill-level (the requested method): 1,125 fills matched by exact oid, 1,216 by
+/-3min event, 32 by killswitch window; 345 unmatched ("manual") carrying net
-140.43. **That fill-level manual number is inflated and should not be
believed**: 5 of the unmatched fills are exchange liquidations (-73.35 gross)
and many others are bot-placed resting stop/TP orders that fire with no
contemporaneous log line.

Episode-level (owner = whoever opened; resting closes cannot fake manual):
**bot 801 episodes, net -238.60; manual 54 episodes, net -12.26** (-19.93 if
loop-log evidence is excluded — method sensitivity ~$8). Biggest manual
episodes: BTC long 07-09 +36.64, BTC short 07-09 -12.45, xyz:NBIS short 06-30
-12.22, ARB long 07-12 -11.51 (this one tripped the killswitch at -12).
Confidence: medium-high. Caveat: the 07-09 wake-day opens tagged manual
(BTC/ETH/LDO/UNI in a 13-second burst at 01:57) look programmatic; if those
were the zombie loop trading while logging was broken, the true manual share is
even smaller. Verdict either way: **the bot did ~95% of the damage; manual
trading cost at most ~$20 net and possibly nothing.**

## 4. Fees: fee-dominated or direction-dominated?

- Fees = $144.66 = **11.7% of gross losses** ($1,239.81), but **57.4% of the
  net bleed** (fees $144.66 vs direction -$105.97 vs funding -$1.40).
- Every one of the 2,721 fills was taker (crossed=true). Zero maker flow.
- 602 of 855 episodes were held under 2 hours; those sub-2h episodes net
  **-385.59** while everything held longer made **+134.96**. Median hold 43 min.

Answer: **fee-dominated at the margin, direction-negative at the core.** The
directional engine was a ~49% coin flip that lost $106 on its own; paying
3.07 bps twice per round trip on 2,000x turnover added $145 and made the same
engine lethal. Cutting churn (not picking better coins) was the single biggest
available save.

## 5. The three worst days (realized net, incl. funding)

**2026-07-09, -56.90 (equity 159 -> 53 intraday).** The wake-from-zombie day.
Heartbeats were dark 07-01 through 07-08 (Mac asleep; zero fills 07-04..07-08),
positions sat unmanaged for 8 days. On wake: 20 `loop_start` events, watchdog
"hung 3648s — re-exec", 10 errors. The damage: a fresh $5.1k-notional
xyz:SP500 short opened 13:19-13:32 and stopped at 17:07 for -33.66 (dsl
max_loss, 25.9% ROE at ~25x); VINE long -18.94 in 2 minutes (plus two more
VINE attempts, -3.19); KAITO long->short->long flip-flop, 14 fills, -17.18;
late BTC short flipped long -12.45 (manual-tagged). Partly offset by +40.72 on
the 01:57 BTC long. Realized -57; the equity gap to -106 was unrealized
markdown that realized as further losses 07-10..07-17.

**2026-06-29, -49.53 (133 fills).** Killswitch-storm day. The kill fired 8
times; its daily-PnL baseline was broken (fired at "-167.0" against a -40
limit at 05:58, then five more times 13:15-14:37 at ~-40 each, flattening 1-2
positions per shot). The 05:59 flatten dumped 7 positions at once, with MANTA
at 5.66% and IP at 5.08% spot loss — far beyond the 2.5% cap, i.e. stops were
not being enforced before the kill. XRP long, $1.7k notional, lost 20.17 in
1.3h (11:35->12:54). dsl_monitor was eating 429s the same day. AI closed
ZEC/PUMP/TURBO longs on HTF bearish flips near the lows.

**2026-06-09, -40.27 (195 fills).** The AI mass-close day. Between 00:53 and
05:15 UTC the researcher issued 35 `ai_close` orders, closing every long on
the book (SOL, ADA, AVAX, ETH, ZEC, XPL, TON, ENA, VVV...) with "bearish HTF
structure" reasoning — realizing the whole drawdown at the local low. Plus
xyz small-cap stop-outs (SKHX -10.91, SMSN -10.47). 38 dsl_monitor read
timeouts. Equity 143.91 -> 108.27.

Honorable mentions: **06-18** xyz:BIRD long, $801 notional on the xyz dex,
liquidated 8 minutes after open for **-64.06** — the single worst episode of
the entire window, 25% of all direction losses (day net only -32.32 because a
CBRS long won +27.78 the same afternoon). **06-23** first killswitch at -30.14.

## 6. Book attribution (scripts/pnl_by_book.py --days 48, span 05-31 -> 07-18)

main-engine -169.72 (938 eps, 87% of them long), vol_breakout_long -50.55,
vol_breakout_wide -22.43, engulf_short -12.88, extreme_fade -9.01,
news_catalyst -3.48, neg_funding_fade -0.75, rally_exhaustion -0.62,
crash_continue -0.59. Caveat: exact book footprints only cover 6.5% of
episodes (`book_open` events exist since 07-16, loop log since 06-26), so June
book trades default into main-engine; main-engine is overstated but the sign
pattern (everything negative) is not in doubt.

## 7. Causal ranking — why $260 became $19

Nothing was stolen, withdrawn, or lost to funding; the account traded itself to
death and the ledger closes to within a dollar. Ranked by dollars: (1) **churn
times taker fees** — 2,718 all-taker fills, $471k notional, median 43-minute
holds; fees took $144.66, and the sub-2-hour churn bucket alone net -385.59
while everything held longer than 2h made +135; (2) **a long-biased coin-flip
engine in a down tape** — 726 long episodes lost $189 while 129 shorts lost
$61, gross payoff 0.91 on ~49% win rate, so direction contributed -$105.97 on
its own; (3) **three operational disasters worth ~$170** — the xyz:BIRD
HIP-3 liquidation (-64 in 8 minutes on ~$65 of margin), the 07-09
wake-from-zombie day (-57 realized after 8 days of unmanaged positions), and
the 06-29 killswitch storm (-50, kill firing on a broken -167 baseline while
stops were already blown to 2x their cap); (4) **risk plumbing that amplified
instead of protecting** — DSL monitor dead in 429 storms, stops enforced late
or not at all, AI mass-closing the entire book at local lows (06-09); (5)
**manual trading, a rounding error** at roughly -$12 to -$20 net. The bot did
~95% of the damage, and the fastest structural fix is not better signals: it is
trading 10x less often, 10x cheaper, at holds measured in days.
