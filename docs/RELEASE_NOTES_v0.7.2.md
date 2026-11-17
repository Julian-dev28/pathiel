# pathiel 0.7.2

Released 2026-11-17. A security and fix release: the full list is in
[CHANGELOG.md](../CHANGELOG.md). pathiel stays free: no builder fee.

## Security

- **A main account key is refused for trading.** If the key you connect
  belongs to your main account (it can withdraw), the six live-money tools
  refuse and `state` says "Trading: BLOCKED". Use an API wallet key: it can
  trade and can never withdraw.
- **No pathiel process holds a key it does not need.** The MCP server and
  the desk scripts skip `HYPERLIQUID_MASTER_PRIVATE_KEY`, and every `claude`
  process pathiel starts runs with no keys, tokens or passwords in its
  environment, so a prompt injection in a news headline finds nothing to
  steal.
- Code scanning fixes: a crafted post can no longer stall the headline
  watch, and error answers no longer echo exception text.

## New

- **Partial closes.** `close_position` takes `fraction` (0.5 closes half).
  The stop and target stay on what is left.

## Fixed

- **A stop set for the whole position in Hyperliquid's app now counts.**
  Those orders carry size 0, so pathiel read the position as unprotected,
  and the desk tick closes an unprotected position.
- **Expired Polymarket markets stay out of the thesis.** One still listed as
  open eight days after it ended woke the oil desk.
- **A new thesis must pay at least 2R,** the floor `place_bracket` enforces,
  so a thesis can no longer pass and then be refused when you place it.

Update: `claude plugin update pathiel@pathiel`, then reconnect with `/mcp`.

Not financial advice. Leveraged perps can lose the whole account.
