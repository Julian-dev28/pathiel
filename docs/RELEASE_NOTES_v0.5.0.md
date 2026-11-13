# pathiel 0.5.0

Released 2026-11-13. The full list of changes is in
[CHANGELOG.md](../CHANGELOG.md). pathiel stays free: no builder fee.

## The research pool (opt-in)

Share your public Hyperliquid fills on the pool coins (`xyz:BRENTOIL`,
`xyz:CL`, `xyz:SILVER`, `xyz:GOLD`) and pathiel grades them with the same
fluke-or-edge harness the project runs on its own trades.

- **Join with one signed statement.** `/pathiel:share` shows the exact text
  before anything is signed. The statement authorizes no trade, transfer,
  fee or payment, and the key is never printed or stored.
- **What you get:** your own graded report through `pool_report`, and
  pool-level stats on which desks and setups hold up across members.
- **What is public:** a scoreboard at least 24 hours late, never live, with
  only fills after your join. [PRIVACY.md](PRIVACY.md) has the full list
  and how to leave.
- **What it is not:** no payout, refund or reward tied to your returns, no
  copy-trading feed, and no edge is demonstrated by joining.

Four new tools on public installs: `pool_status`, `pool_report`,
`pool_join`, `pool_leave` (64 tools in total).

## Upgrading

`claude plugin update pathiel@pathiel`, or the **Installed** tab in
`/plugin`, then reconnect pathiel in `/mcp`. Nothing changes until you
choose to join.
