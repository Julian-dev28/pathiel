# pathiel 0.9.0

Released 2026-11-21. The research server ships: the full list is in
[CHANGELOG.md](../CHANGELOG.md). pathiel stays free: no builder fee.

## New: pathiel research, no key, no trading

A second plugin in the same marketplace. It reads markets and never places
an order: it cannot import order or signing code, and a gate test proves it.

| Tool | What it answers |
|---|---|
| `research_brief` | One brief per coin: levels, catalysts, headlines, odds and the desk's stored call |
| `levels` | Mid, recent ranges and ATR from Hyperliquid's public data |
| `events` | The scheduled-event calendar: FOMC, CPI, OPEC+, Congress, sanctions dates |
| `headlines` | Recent news for a coin or a topic |
| `prediction_odds` | Polymarket and Kalshi odds |
| `fred_series` | Any FRED macro series, with your own free FRED key |

Coins: Brent, HYPE, BTC, ETH, S&P 500.

Install: `/plugin marketplace add Julian-dev28/pathiel`, then
`/plugin install pathiel-research@pathiel`.

Not financial advice.
