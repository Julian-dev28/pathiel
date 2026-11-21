# pathiel 0.8.0

Released 2026-11-21. Two new desks and a clearer start: the full list is in
[CHANGELOG.md](../CHANGELOG.md). pathiel stays free: no builder fee.

## Works out of the box, with or without trading

- **Research only:** market data, order books, funding, candles, desk
  briefs and read-only views of any wallet. No account, no key.
- **Research and trading:** add a Hyperliquid API wallet with one
  signature and every trade is a bracket order sized from your risk
  profile.

## Security

- **pathiel leaves your own trades alone.** Every order it places carries
  a pathiel order id. Closing, moving a stop or target, cancelling, adding
  to or sweeping a position or order you placed yourself is refused unless
  that call says `operator_override: true`.

## New

- **HYPE desk (`hype`).** Trades HYPE long or short on the main dex, off by
  default. New entries are vetoed against the 120-hour trend; its backtest
  found that gate is what gives the rule an edge on HYPE.
- **S&P 500 desk (`sp500`).** Trades `xyz:SP500` long or short, off by
  default, with its own stop, target and leverage cap.
- **Desks wait for you.** On HYPE and the S&P, a desk does nothing while a
  position or order it did not place sits on the coin.
- **Calendar and headlines cover both.** FOMC and CPI hold new entries on
  every desk; token unlocks reach the HYPE thesis, megacap earnings the S&P.

## Fixed

- Two route tests no longer fail on weekends.

Update: `claude plugin update pathiel@pathiel`, then reconnect with `/mcp`.

Not financial advice. Leveraged perps can lose the whole account.
