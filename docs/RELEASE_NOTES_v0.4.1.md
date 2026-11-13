# pathiel 0.4.1

Released 2026-11-13. The full list of changes is in
[CHANGELOG.md](../CHANGELOG.md).

## pathiel is free

The builder fee is waived. No order pathiel places carries a Hyperliquid
builder fee, so you pay only Hyperliquid's own trading fee, and there is
nothing to approve. `/pathiel:setup` no longer has an approval step.

If you approved the fee under 0.4.0, it is no longer charged. The approval
stays on Hyperliquid until you revoke it; how to do that is in
[BUILDER_FEE.md](BUILDER_FEE.md). If pathiel ever charges a fee again, it
will ship as a new version with the rate in these notes.

## Also in this release

- **A cleaner plugin listing.** The Claude plugin directory now reviews a
  bundle that holds only what the plugin runs, published to the `plugin`
  branch.
- **One less shell trick.** The launcher no longer uses `eval` to read your
  `PATHIEL_HOME`, so a path is always treated as text.

## Upgrading

`claude plugin update pathiel@pathiel`, or the **Installed** tab in
`/plugin`, then reconnect pathiel in `/mcp`. Your keys, config and state in
`~/.pathiel` are untouched.
