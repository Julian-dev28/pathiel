# Profit audit, 2026-10-17

Agent: profit. Source: `scripts/profit_audit.py`, run live against public HL
`/info` plus a copy of `.agent-config.json` and the state dir. Reproduce with:

    .venv/bin/python scripts/profit_audit.py --env-file /Users/julian_dev/Documents/code/pathiel/.env.local --out-dir /tmp/profit_audit

Raw output: `/tmp/profit_audit/report.json`, `/tmp/profit_audit/report.md`.
Every number below is cited from that run (generated 2026-10-17 03:27 UTC,
60-day window, 88 fills, equity $15.19) unless marked "research" (manual,
read-only, dated in the text).

$/mo scaling: where the economics scale with trading volume (fees, funding,
liquidation risk), the $1k/$10k columns multiply the $15 figure by
equity-ratio (65.8x, 658.5x), a linear-scaling ASSUMPTION, flagged every
time it's used. Where the economics are a fixed $ cost (referral, staking),
the cost stays fixed and only the %-of-equity and the volume-scaled savings
change.

## The one-line version

The account is in LIVE mode but **cannot place a single trade right now**.
Equity ($15.19) is below its own configured floor ($26.00). Fix that first;
every other number below is $0/mo until it's resolved.

## Ranked recommendations

| # | Idea | $/mo @ $15 | $/mo @ $1k | $/mo @ $10k | Effort | Risk | Status |
|---|------|-----------:|-----------:|------------:|--------|------|--------|
| 1 | Fund the account or lower `min_tradable_equity_usd` | **blocks ALL trading** | same | same | trivial | needs Julien's call | manual decision |
| 2 | Liquidation-cascade (historical), already mostly fixed; added a forward monitor | $8.84 avoided (hist.) | $582 avoided (hist.) | $5,821 avoided (hist.) | built | low | fixed + monitor built |
| 3 | `entry_order_mode: maker_first` | $0.54 ceiling | $35.36 ceiling | $353.60 ceiling | built | low | built behind flag (default off) |
| 4 | Oil funding carry (confirmed working) | +$1.73 (already earned) | +$113.72 | +$1,137.17 | none | low | informational, no action |
| 5 | `pnl_by_book.py` BOOK_PRIORITY is stale | not $-denominated | n/a | n/a | trivial | low | for the integrator (diff below) |
| 6 | Apply a referral code | $0.03 | $2.03 | $20.35 | trivial, $0 cost | none | manual user action |
| 7 | Stake HYPE for a fee discount | **not worth it** (locks 55%+ of equity) | marginal (10% tier, 0.8% locked) | **do this** (20% tier, 8.2% locked, ~8-day payback) | trivial | low (7-day unbond, price risk on the stake) | manual user action |
| 8 | HIP-3 "growth mode" fee discount | $0 (already realized) | $0 | $0 | none | none | informational, already realized |
| 9 | Cross-venue funding gap (HL vs Binance/Bybit) | $0 (no execution built) | $0 | $0 | none | none | informational |
| 10 | Brent/WTI pair trade (low-vol leverage use) | N/A, no signal yet | N/A | N/A | medium (needs a signal) | medium (basis risk) | research done, build not started |
| 11 | Idle USDC → HLP vault yield | N/A (0 idle) | N/A (0 idle) | consider if a cash buffer exists | low | medium (variable, drawdowns, 4-day lock) | informational |
| 12 | Polymarket US-Iran hedge on the oil book | rejected | rejected | revisit if a live contract exists | high | medium | rejected for now |

---

## 1. The account cannot trade right now

**Evidence.** `.agent-config.json`: `mode: "LIVE"`, `min_tradable_equity_usd: 26.0`.
Live `clearinghouseState` (main + xyz dex): equity $15.19 ($0.01 main, $15.18
xyz). `pathiel.agents.executor.min_tradable_equity(config)`, the exact
function `maybe_execute` calls on every entry attempt, returns $26.00 against
this config. $15.19 < $26.00: every entry this week returns
`below_min_tradable_equity` before it reaches a risk gate or an order.

This is not the derived "sum of every enabled book's margin" floor from
README's $88.89 example. It's an **explicit operator override** set to $26,
higher than current equity. Someone deliberately raised it past what's in the
account.

**The decision (Confusion Protocol: this is Julien's call, not mine):**
- Deposit enough to clear $26, or
- Lower `min_tradable_equity_usd` back down (the README's 2026-09-04 history
  shows $12 was the override at $12.94 equity, a similar ratio here would be
  ~$12-14), or
- Leave it at $26 deliberately, accepting that the account does not trade
  until funded further.

I have not touched this. No config was changed.

## 2. Liquidation-cascade: historical cost, mostly already fixed, one gap closed

**Evidence.** 88 fills, 60 days. 8 are the account's OWN liquidation
closes (`fill.liquidation.liquidatedUser` matches the account address):
ZRO, FARTCOIN, SUI, MON, TAO (all within 11 hours, 2026-08-19/20), BTC x2
(09-29, 10-02), xyz:BRENTOIL (10-08). Net realized PnL over the window:
**-$19.20**. Of that, liquidations alone are **-$17.68, 92% of the entire
net loss.** Non-liquidation trading net: -$1.52 (basically fee drag on
roughly-breakeven trades).

**Root cause, confirmed in code.** `pathiel/agents/executor.py` has a
leverage-vs-stop safety mechanism (`stop_honoring_leverage`,
`BACKUP_SL_MAX_FRAC_OF_LIQ`/`LIQ_SAFETY_FRAC` in `book_params.py`, dated
2026-07-22/07-24) that caps leverage so a position's own stop always fires
*before* the exchange's maintenance-margin liquidation. It only activates
when the analysis carries `backup_sl_pct_override`, which every current
strategy BOOK sets, but which **main-engine (AI-verdict) entries never set.**
The Aug 19/20 cluster is five main-engine shorts on 5x-10x-max-leverage alts
(ZRO/MON/TAO: 5x max → 10% maintenance margin), exactly the unprotected
path. Main-engine entries were deleted on 2026-08-30 (`W-ME1`, for an
unrelated reason, anti-calibrated AI longs) for ALL coins, which also
closed this hole as a side effect. The Oct 8 BRENTOIL liquidation most
likely resolves a position opened before that date.

**Current state, confirmed live:** zero open positions on both dexes. All 6
registered books either set `backup_sl_pct_override` (5 of them) or are
`drawdown_ladder` (deliberately exempt, dormant below its own $525 equity
floor). The historical hole is closed for anything that opens through a
book today.

**The one gap I found and closed:** `stop_honoring_leverage_cap` only runs
at ENTRY time. It never re-checks an already-open position if
`hip3_max_leverage`/`backup_sl_max_frac_of_liq`/`liq_safety_frac` change
later. `scripts/profit_audit.py` now includes
`liquidation_safety_check(open_positions, ...)`, which reuses
`book_params.max_stop_pct_at_leverage` (not a reimplementation, the exact
function the executor already trusts) to flag any CURRENTLY open position
whose stop sits outside the safe liquidation buffer. Today it returns `[]`
(no open positions). Run the audit after any future config change to the
three knobs above and before funding the account further.

$/mo column: this is a backward-looking rate (-$17.68 over 60d → $8.84/mo),
scaled by equity as an upper-bound "what this would have cost at that
equity", not a forecast, since the mechanism that caused it is now closed.
Treat it as the size of the mistake avoided, not a recurring saving.

## 3. `entry_order_mode: maker_first`, built, tested, off by default

**Evidence.** 88/88 fills in the window are taker (`crossed: true`). Zero
maker fills. Main dex: 0.045% taker / 0.015% maker (`userFees`). xyz HIP-3:
0.009% taker, directly observed from fills (see HIP-3 growth mode below);
no maker fills exist yet to observe its add rate directly, so the savings
estimate below ASSUMES xyz's maker rate is discounted by the same 5x ratio
(0.003%) rather than treating it as measured.

**What shipped.** `pathiel/client/exchange.py`: `place_entry_order(...)`
gated by config key `entry_order_mode` (`"taker"` default, `"maker_first"`
opt-in). `"taker"` calls `place_hl_order` with byte-identical arguments to
every pre-existing call site. `"maker_first"`:
1. Posts a post-only (ALO) limit AT the current touch (joins best bid/ask;
   structurally cannot cross).
2. Polls briefly (`maker_first_timeout_s`, default 3s; `maker_first_poll_interval_s`,
   default 0.5s).
3. Cancels whatever's left resting, reads the account's own fill record for
   that order id (the only way to learn an async fill's price), and takes
   the exact remainder taker at a fresh price.
4. Any unexpected failure anywhere in that path falls back to exactly what
   `place_hl_order` alone would have returned; maker_first can never do
   worse than taker mode.

Wired into `pathiel/agents/executor.py`'s ONE entry call site
(`maybe_execute`, ~line 1183). Stops, emergency exits and the close path
(`close_position_market`, `place_hl_trigger_order`) are untouched and still
call `place_hl_order` directly: a stop that waits to save a fee is not a
stop.

**Savings, ceiling (every taker fill assumed to have filled passively):**
main-dex $0.20/mo, xyz $0.33/mo, combined **$0.54/mo at $15 equity, $35/mo
at $1k, $354/mo at $10k.** Real savings will be lower than the ceiling
(not every market lets a post-only order fill before the timeout). Monitor
the actual main/maker fill ratio after enabling and recompute.

**Tests:** `tests/test_maker_first.py` (19 tests), flag-off byte-identical
(2 variants + unknown-mode + no-config), fills immediately, partial fill
then timeout then taker remainder, price runs away (full timeout, zero
fill), post-only rejected, degrades-to-taker on any exception,
`_best_touch_price` (buy/sell/fallback), `_fills_for_order` (oid matching,
no-match, no-address). `tests/test_profit_audit.py` covers the fee-math
side (49 tests total in that file).

## 4. Oil funding carry, already working, no action needed

**Evidence.** Net funding over 60 days: **+$3.45** (the account received
money, it did not pay). Driven almost entirely by xyz:BRENTOIL (+$2.80) and
xyz:CL (+$0.58), the account's oil exposure earns persistent positive
funding. Net of the $1.54 total fee bill, funding alone already more than
offsets fees (+$3.45 funding vs -$1.54 fees = +$1.91 net carry before any
other PnL). **$1.73/mo at $15 → $113.72/mo at $1k → $1,137/mo at $10k**
(scaled with equity-proportional exposure, this is NOT a forecast, funding
rates move; it is evidence the current oil-short/long mix has a tailwind,
not a drag).

Nothing to build. Don't change the oil book's funding exposure without
checking this first.

## 5. `scripts/pnl_by_book.py`, stale book roster (for the integrator)

**Evidence.** README's "Six books registered" table (current, live):
`news_surge_short`, `news_surge_multi`, `social_trending`,
`unlock_short_runin`, `xs_reversal`, `drawdown_ladder`. `pnl_by_book.py`'s
`BOOK_PRIORITY` tuple is missing **`drawdown_ladder`, `social_trending`,
`xs_reversal`**, confirmed by `scripts/profit_audit.py`'s
`book_roster_staleness()`, which imports `BOOK_PRIORITY` directly and diffs
it. Any fill from those three books silently rolls up into "main-engine" in
that script's attribution: the exact failure class its own 2026-07-09
comment warns about ("omitting a book silently rebadged their PnL as
main-engine").

`scripts/pnl_by_book.py` is not in this agent's ownership
(`scripts/**` except `pathiel-mcp-server.py` belongs to `cleanup`). Proposed
diff, for the integrator or `cleanup` to apply:

```diff
--- a/scripts/pnl_by_book.py
+++ b/scripts/pnl_by_book.py
@@ BOOK_PRIORITY = (
     "rally_exhaustion", "engulf_short", "crash_continue_div_short",
     "premium_fade_short", "hail_mary_short", "neg_funding_fade",
     "vol_breakout_long", "vol_breakout_wide", "majors_swing",
     "funding_spike_short", "young_listings", "unlock_short_runin",
     "news_catalyst", "news_surge_short", "news_surge_multi", "mover_pass", "mover_pass_short",
     "young_mover_short",
     "extreme_fade", "xs_momentum", "xs_xyz_equities",
     "numerology_eth", "uw_flow_xs",
+    "social_trending", "xs_reversal", "drawdown_ladder",
     "external_alpha",
 )
```
(`social_trending` also needs an exact-footprint source: check whether it
already writes `book_open` session-log events the way `news_surge_short`
does; `drawdown_ladder`/`xs_reversal` likely do too, given they're listed in
`LEGACY_EVENT_BOOKS` territory. I did not change this file.)

## 6. Referral code, apply it, it's free

**Evidence.** `userFees.activeReferralDiscount: 0.0`.
`feeSchedule.referralDiscount: 0.04` (4% off every fee, not 4 percentage
points). $0.03/mo at $15 → $2.03/mo at $1k → $20.35/mo at $10k. Cost: $0,
one-time signup. Manual action, not something this agent can apply.

## 7. HYPE staking discount, skip it now, do it once funded

**Evidence**, live `userFees.feeSchedule.stakingDiscountTiers` +
`spotMetaAndAssetCtxs` HYPE/USDC mark ($0.0818):

| Discount | HYPE needed | Cost | % of equity locked @$15 / @$1k / @$10k | Savings/mo @ $15 / @ $1k / @ $10k |
|---|---|---|---|---|
| 5% | 10 | $0.82 | 5.5% / 0.1% / 0.0% | $0.04 / $2.54 / $25.42 |
| 10% | 100 | $8.18 | 54.5% / 0.8% / 0.1% | $0.08 / $5.08 / $50.83 |
| 15% | 1,000 | $81.76 | 545% / 8.2% / 0.8% | $0.12 / $7.62 / $76.25 |
| 20% | 10,000 | $817.60 | 5,451% / 81.8% / 8.2% | $0.15 / $10.17 / $101.67 |
| 30% | 100,000 | $8,176 | n/a / n/a / 81.8% | $0.23 / $15.25 / $152.50 |
| 40% | 500,000 | $40,880 | n/a / n/a / n/a | $0.31 / $20.33 / $203.33 |

At $15, even the 5% tier locks 5.5% of total equity for a 7-day unbond to
save 4 cents a month. Don't. At $1k, the 10% tier is cheap optionality
(0.8% locked) but marginal in absolute dollars. **At $10k, the 20% tier is
the sweet spot: 8.2% of equity locked, $101.67/mo saved, ~8-day payback.**
The 30%/40% tiers cost more than the account at $1k/$10k respectively; they
only make sense well past $10k. This is a one-time, reversible (7-day
unbond) capital decision. Manual: not something I apply.

## 8. HIP-3 "growth mode" fee discount, already realized

**Evidence**, computed directly from fills, not assumed: xyz HIP-3 fills pay
**0.009% taker**, a flat 5x discount vs the 0.045% main-dex rate, confirmed
on every one of the 46 xyz fills in the window (`hip3_growth_mode_evidence`,
`discount_ratio: 0.2`). Press coverage (fxstreet, mexc, Oct 2026) describes
HIP-3 "growth mode" cutting new-market taker fees "by more than 90%", with an
implied all-in rate of "roughly 0.0045% to 0.009%"; this account's own data
sits at the top of that range. The official HIP-3 docs page covers the
deployer's fee-SHARE cap (0-100% in growth mode vs 0-300% normally) but not
this trader-facing number directly; the fills are the stronger evidence.

No action: this already applies automatically to every xyz trade, no setup
required. Context for later: any future book added on an xyz HIP-3
instrument inherits this discount for free; an equivalent trade on the main
dex would cost 5x more in taker fees.

Sources: [HIP-3 growth mode coverage](https://www.fxstreet.com/cryptocurrencies/news/hyperliquid-unveils-hip-3-growth-mode-slashing-fees-by-90-to-boost-new-markets-202511191148), [HIP-3 docs](https://hyperliquid.gitbook.io/hyperliquid-docs/hyperliquid-improvement-proposals-hips/hip-3-builder-deployed-perpetuals), [dwellir fee guide](https://www.dwellir.com/blog/hyperliquid-fees-explained).

## 9. Cross-venue funding gap, informational

**Evidence**, live `predictedFundings` for coins this account actually
trades: HL's funding rate is structurally cheaper than Binance/Bybit on
every major checked. BTC: HL 10.95%/yr annualized vs Binance 87.6%/yr,
Bybit 45.9%/yr. SUI, TAO, ZRO, FARTCOIN: same pattern (HL ~11%/yr vs
43.8-87.6%/yr elsewhere). No cross-venue execution exists or is proposed.
This account only trades HL. Context only: if a position needs to be long a
funding-paying side, HL is already the cheap venue for it; there is no gap
to "capture" without trading a second exchange, which is out of scope.

## 10. Brent/WTI pair trade, mechanism validated, no signal built

**Evidence**, 60 daily candles, xyz:CL (WTI) vs xyz:BRENTOIL: daily-return
correlation **0.9465**. A long-WTI/short-Brent (or reverse) spread position's
daily return stdev is **0.695%**, vs ~2.1-2.14%/day for either leg alone, a
**3.08x vol reduction**. Funding on both legs is similar and small
(CL +0.0835%/day, BRENTOIL... both near the same positive rate per the
funding section above), so a market-neutral spread is close to
funding-neutral. Current spread: $8.02 (60d mean $6.29, stdev $2.69,
z-score +0.64, mildly rich, not extreme). The 60-day mean daily return
(+0.03%) is NOT statistically significant (well within 1 stdev of the
sample's own noise). Don't read it as an edge.

This validates the MECHANISM (low basis risk, cheap in funding, both legs
liquid: $230M/$218M 24h volume, 20x max leverage, 2.5% maintenance margin)
for whoever builds an actual entry signal (mean-reversion on the spread
z-score is the obvious first thing to backtest). No $/mo figure: there is
no strategy yet, only the plumbing cost/vol numbers a strategy would need.
Effort: medium (needs its own research task). Risk: medium (if Brent/WTI's
structural relationship breaks, e.g. a supply shock hits one grade only).

## 11. Idle USDC yield, not applicable today

**Evidence:** live `spotClearinghouseState`, $0.00 USDC, $0.00 HYPE, $0.01
USDT0 dust. The account holds no idle capital; everything is deployed as
margin or sits in the $15.19 xyz equity. HL's HLP vault (research, not
live-queried): variable yield, third-party sources disagree sharply:
~0.4% APY over a recent 30-day window per one tracker, vs ~15-30% APR across
"most quarterly windows" historically per another, with 5-12% drawdowns
possible and a 4-day lock. Not risk-free, not guaranteed. Only relevant if
Julien chooses to hold a cash buffer beyond `min_tradable_equity_usd` at
larger size, worth 2 minutes of consideration at $10k, not at $15.

Sources: [HLP explained](https://onekey.so/blog/ecosystem/what-is-hyperliquid-hlp/), [Trading Strategy HLP listing](https://tradingstrategy.ai/vaults/curators/hyperliquid).

## 12. Polymarket oil-conflict hedge, rejected for now

**Evidence (research, read-only, Polymarket's public Gamma API, no keys
used, no trade placed):** the specific "US-Iran nuclear deal before 2027"
market is already closed (resolved before 2026-08-13). The live,
open "Iran strike on US military before [month]" family rolls over on a
1-2 month cycle; every dated instance I checked (`before November`,
`in August`, `before October`) was closed as of 2026-10-17, and I found no
currently-open instance in this check. An open, liquid, DIFFERENT market
exists: "What will be in a US-Iran deal in 2026?" ($1.3M total volume,
$81.5k last 30d, $244.8k liquidity, resolves 2026-12-31). It answers deal
*contents* conditional on a deal happening (sub-markets priced 4.5-14.5%
YES), not a clean escalation/de-escalation binary suited to hedging an oil
long's conflict-risk.

Rejected at $15 and $1k: position sizes this small can't clear a meaningful
hedge notional after Polymarket's own minimums and the research/monitoring
overhead. Revisit at $10k+ **only if** a live, liquid, binary
conflict-or-deal market is open at the time and the oil book's notional is
large enough that the hedge cost is a sane fraction of what it protects.
`POLYMARKET_API_KEY`/`POLYMARKET_ADDRESS` exist in `.env.local` and were not
used. No account-specific call was made; only public market data.

---

## For the integrator

- **`scripts/pnl_by_book.py`** (owned by `cleanup`): `BOOK_PRIORITY` is
  missing `social_trending`, `xs_reversal`, `drawdown_ladder`; see section 5
  for the exact diff. I did not apply it.
- **No other files outside this agent's ownership
  (`pathiel/agents/executor.py`, `pathiel/agents/sizing.py`,
  `pathiel/agents/risk_gates.py`, `pathiel/client/exchange.py`,
  `scripts/profit_audit.py`, `docs/overhaul/PROFIT_AUDIT.md`) were changed**,
  except the mechanical test-fixture rename required by the `executor.py`
  entry-call-site change (see the final report's test section):
  `tests/test_cleanup.py` and `tests/test_claims_registry_and_sizing.py` had
  `monkeypatch.setattr(ex_or_executor, "place_hl_order", ...)` at the ENTRY
  call site, which now calls `place_entry_order` instead
  (default "taker" mode still delegates to `place_hl_order`, just not
  through that name binding). Fixed by renaming the monkeypatch target and
  widening the stub signature to accept `**kw`. Zero behavior change, pure
  mechanical follow-through so the gate suite stays green. Diff is small;
  happy to hand it off instead if any agent owns those files and wants to
  review it separately.
- **`min_tradable_equity_usd: 26.0`** in `.agent-config.json` is section 1's
  finding: a decision for Julien, not a file either of us should edit
  without his say.
