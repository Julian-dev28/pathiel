# pathiel 0.6.0

Released 2026-11-13. The full list of changes is in
[CHANGELOG.md](../CHANGELOG.md). pathiel stays free: no builder fee.

## Connect your Hyperliquid wallet with one signature

No more copying an API wallet key out of the Hyperliquid app. In Claude
Code, `/pathiel:setup` (or the new `wallet_connect` tool) does it for you:

1. pathiel makes a new API wallet key on your computer.
2. A one-time local page opens. Your browser wallet (MetaMask, Rabby, OKX
   and others) signs one approval, Hyperliquid's `approveAgent`.
3. pathiel checks that Hyperliquid lists the new wallet, then saves the key
   to `~/.pathiel/.env.local` (mode 600).

Your main key never leaves your wallet. The new key can trade and can never
withdraw. Hyperliquid lists it as `pathiel-plugin`, and keeps it approved
for 90 days: the result tells you the date, and connecting again before it
renews it. Pasting a key by hand still works.

Checked live on mainnet with OKX Wallet in Chrome. Other wallets and
browsers use the same standard (EIP-6963); if one fails, nothing is saved
and nothing is approved twice.

## A website

https://julian-dev28.github.io/pathiel/ is a landing page for pathiel and
the research pool scoreboard.

## Upgrading

`claude plugin update pathiel@pathiel`, or the **Installed** tab in
`/plugin`, then reconnect pathiel in `/mcp`. A key you already set keeps
working; `wallet_connect` never replaces it without asking.
