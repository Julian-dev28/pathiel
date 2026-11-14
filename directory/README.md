# pathiel

An AI trading agent for Hyperliquid: Claude trades perps from Claude Code,
with an exact stop and target on every order.

This is the plugin directory's build of pathiel: setup and research-pool
commands only. The pathiel MCP server (30 tools: market data, account
reads, risk sizing, bracket orders, desk briefs) runs as a container you
install once; `/pathiel:setup` walks you through it.

- Mainnet by default: with a key, orders use real money. Testnet is one
  line in `~/.pathiel/.env.local`.
- Default risk profile: at most 3x leverage, 1% of equity at risk at the
  stop, one position at a time. Every order is a bracket with an exact stop
  and target; a trade under 2R is refused.
- Read-only without a key. The API wallet key it trades with can never
  withdraw.
- Free: no builder fee.

Leveraged perps can lose the whole account. There is no demonstrated edge
and no return is promised. This is software, not financial advice.
Hyperliquid does not serve restricted jurisdictions, including US persons.

Source, the full plugin (with the desk loop) and docs:
https://github.com/Julian-dev28/pathiel

License: MIT.
