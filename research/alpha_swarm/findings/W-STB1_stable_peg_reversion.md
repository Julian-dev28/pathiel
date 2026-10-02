# W-STB1: stablecoin peg reversion on Hyperliquid spot

2026-09-14. Operator request: "build a stable coin arbitrage strategy". Pathiel
has keys for one venue, so the arbitrage it can run is on Hyperliquid itself:
buy a stablecoin that trades under $1 against USDC, sell it back at the peg.

## What is not tested, and why

- **Cross-venue** (HL against Binance, Curve, a mint): needs funded accounts
  and a bridge on each side. Pathiel has neither.
- **Redemption** (buy under peg, redeem at $1): USDT0, USDE and USDH are not
  redeemable from an HL account.
- **Two-sided market making around the peg**: the P&L lives in queue position,
  and HL serves no historical order book. Under `EVIDENCE_DOCTRINE.md` a signal
  with no retrievable history cannot be validated, so it is not built.
- **Triangles through HYPE** (HYPE/USDC vs HYPE/USDT0 vs USDT0/USDC): HYPE/USDT0
  and HYPE/USDH show no mid, and HYPE/USDE traded $110 in 24h.

## Facts measured before any backtest

Fee rates paid on real fills, 2026-09-14 (`recentTrades` users, then their
`userFillsByTime`):

| pair | coin | stable-pair discount | maker | taker |
|---|---|---|---:|---:|
| USDT0/USDC | @166 | yes | 0.0 bps seen (tiered makers) | |
| USDE/USDC | @150 | yes | | 1.34 bps |
| USDH/USDC | @230 | yes | 0.77 bps | 1.35 bps |
| FEUSD/USDC | @153 | **no** | 4.02 bps | |

This matches the docs: "Spot pairs between two spot quote assets have 80% lower
taker fees, maker rebates" (`scaleIfStablePair = 0.2`). FEUSD is not a quote
asset, so it pays the full 4 / 7 bps.

## PRE-REGISTRATION (frozen before any candle was fetched)

### The rule

- A resting bid at **1 - θ** against USDC. It fills only when a trade prints
  **below** that price (low < 1 - θ). Touching it is not a fill, because the
  queue ahead of the order is unknown.
- Once filled, a resting ask at **1.0000**. It fills only when a trade prints
  **above** it (high >= 1.0001, one tick up).
- If the ask has not filled **168 hours** after entry, sell at that bar's close
  as a taker.
- One position per pair at a time. The next bid rests from the bar after the
  exit.

### Cells: 4 pairs x 3 depths = 12

Pairs: USDT0, USDE, USDH, FEUSD, each against USDC. θ: 10, 25, 50 bps.
Bonferroni alpha **0.05 / 12 = 0.0042**.

### Data

Hyperliquid `candleSnapshot` 1h, the 5,000 bars HL serves (about 208 days),
cached by `hypotheses/W-STB1_fetch.py`. A bar with no volume is not a trade and
cannot fill anything.

### Costs, two tiers, verdict on the first

1. **Doctrine: 25 bps round trip**, the same bar every Pathiel book clears.
2. **Measured**: stable pairs 0.8 bps maker in, 0.8 bps maker out, 1.4 bps
   taker on a forced exit. FEUSD 4 / 4 / 7 bps.

The doctrine's 25 bps was calibrated on perp slippage and is about 15 times
what a maker-only stable-pair round trip costs. It is kept as the verdict tier
anyway. Changing the cost standard after seeing results is how books that lost
money got promoted. If a cell passes at measured fees and fails at 25 bps, this
file says so, and the cost standard for spot is an operator decision.

### Statistics

- **Primary:** total return, the sum of per-trade net returns on the capital in
  the position, over the full span.
- **Matched null:** the same machine with the anchor moved. Instead of the
  peg, each new bid rests θ under the close of a bar drawn at random from the
  previous 168 hours, and the exit ask rests at that same close. Same θ, same
  hold, same fill rule. The question is whether **the peg** attracts price or
  any recent level would. 2,000 draws. p = P(null total >= observed).
- **Out of sample:** the span split at its midpoint, both halves > 0.

### Verdict rule

**VALIDATED** only if, at 25 bps: full-span total > 0, both halves > 0, and
null p < 0.0042. Anything else is **REFUTED** and nothing is built.

---

## RESULTS (first and only run)

`hypotheses/W-STB1_backtest.py` -> `W-STB1_results.json`. 1h bars 2026-02-17
to 2026-09-13 (about 4,990-5,000 bars per pair). Returns are summed in basis
points on the capital in the position.

| cell | trades (target / timeout / open) | 25 bps total | measured total | measured halves | random-anchor median (measured) | p (measured) | verdict |
|---|---|---:|---:|---:|---:|---:|---|
| USDT0 10 | 35 (23 / 12 / 0) | -622 | +190 | +116 / +69 | +343 | 1.000 | REFUTED |
| USDT0 25 | 5 (3 / 2 / 0) | -19 | +97 | +39 / +73 | +84 | 0.065 | REFUTED |
| USDT0 50 | 0 | 0 | 0 | 0 / 0 | 0 | 1.000 | REFUTED |
| USDE 10 | 17 (2 / 15 / 0) | -363 | +26 | +3 / +20 | +28 | 0.672 | REFUTED |
| USDE 25 | 1 (0 / 1 / 0) | -5 | +18 | +18 / 0 | 0 | 0.000* | REFUTED |
| USDE 50 | 0 | 0 | 0 | 0 / 0 | 0 | 1.000 | REFUTED |
| USDH 10 | 35 (30 / 4 / 1) | -728 | +88 | 0 / +88 | +409 | 0.921 | REFUTED |
| USDH 25 | 35 (30 / 4 / 1) | -202 | +614 | 0 / +614 | +1,043 | 0.888 | REFUTED |
| USDH 50 | 31 (26 / 4 / 1) | +578 | +1,301 | 0 / +1,301 | +1,693 | 0.788 | REFUTED |
| FEUSD 10 | 69 (52 / 16 / 1) | -1,644 | -522 | -409 / -103 | -99 | 1.000 | REFUTED |
| FEUSD 25 | 35 (18 / 16 / 1) | -595 | -51 | -189 / +149 | +574 | 1.000 | REFUTED |
| FEUSD 50 | 28 (12 / 15 / 1) | +144 | +572 | +207 / +462 | +802 | 0.902 | REFUTED |

\* One trade, and the null almost never trades at that depth, so p is
degenerate. The cell fails anyway: its second half has no trades.

## VERDICT

**REFUTED, all 12 cells. Nothing is built.**

It does not come down to the 25 bps doctrine. **No cell passes at measured fees
either.** The test that kills it is the null. In 8 of the 9 cells that traded
and have a usable null, a bid anchored on a random close from the last week
earned as much as or more than the bid anchored on the peg. The ninth, USDT0 at
25 bps, beat the null median (+97 vs +84 bps) on 5 trades at p 0.065, nowhere
near 0.0042. The peg does not pull price back any harder than a recent price
does.

What the positive measured totals on USDH and USDT0 are actually paying for:
wicks. A thin book (USDH trades $5-13K a day, bid 0.99811 / ask 0.99989 on
2026-09-14) now and then takes a market sell straight through its bids and
snaps back. A resting bid under the recent price catches that print. That is
liquidity provision to whoever sold into an empty book. It is not arbitrage,
and it is not what was pre-registered.

The observation is **found, not tested**. It fell out of the null. Testing it
on these same 208 days would mean choosing a rule after seeing the answer, the
selection this doctrine exists to stop. HL serves 5,000 1h bars, so a fair test
needs bars from after 2026-09-14. At Pathiel's size ($10.50 orders) it would
also earn cents: USDH 50 bps at measured fees was +1,301 bps summed over 31
trades, about $1.37 on a $10.50 position over 208 days.

## What would have to change for a stablecoin book to exist

1. A pre-registered wick-provision rule, tested on HL bars after 2026-09-14.
2. A cost standard for maker-only spot pairs, which is an operator decision.
   The doctrine's 25 bps was measured on perps.
3. A depeg bound. USDH and USDE are not redeemable from this account, so a real
   depeg leaves the position holding the loss, and 208 days had none to test
   against.
