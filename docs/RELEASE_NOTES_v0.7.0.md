# pathiel 0.7.0

Released 2026-11-13. The full list of changes is in
[CHANGELOG.md](../CHANGELOG.md). pathiel stays free: no builder fee.

## 30 tools instead of 65

A plugin install now lists 30 tools, one per job, every name a verb and a
noun: `get_market` for one coin, `list_markets` for many, `get_account` for
your money, `get_positions`, `get_orders`, `get_history` for your ledger,
`size_trade` before every `place_bracket`. Each description says when to use
the tool, which one to use instead, what it refuses and what it returns, so
Claude picks the right one the first time. In a live test a fresh Claude Code
session picked the intended tool for 7 of 7 plain questions.

Nothing was taken away. Every older tool name still answers when called.

## Move a stop tighter: `set_stop`

`set_stop {coin, stop_px}` moves an open position's stop toward the price,
never away. The new stop goes on before the old one is cancelled, so the
position is never unprotected. A wider stop, or one past the price, is
refused and nothing is sent. Checked live on mainnet on a small gold
position.

## Fixed

- Fill history returned a wallet's oldest fills instead of its newest. It
  now keeps the newest, and a time window works for any wallet's fills.

## For operators

`PATHIEL_TOOLSET=v2` in `.mcp.json`'s env lists the same toolset on your own
install (26 tools: wallet connect and the pool stay off there). Without it,
your install lists the same 60 tools as before, byte for byte.

Not financial advice. Leveraged perps can lose the whole account.
