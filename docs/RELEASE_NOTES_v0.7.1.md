# pathiel 0.7.1

Released 2026-11-14. A fix release: the full list is in
[CHANGELOG.md](../CHANGELOG.md). pathiel stays free: no builder fee.

## Security

- **msgpack 1.2.3** (GHSA-j586-36cw-2gc2). Plugin installs from 0.7.0 still
  pinned 1.2.2. Order signing is unchanged: the signing bytes of six real
  action shapes are identical on both versions, and a test now pins them.

## Fixed

- **The desk-tick loop reads your balance again on a plugin install.** It
  asked for `account_overview`, a tool 0.7.0 stopped listing; it now uses
  `get_account`.
- The README, the `pathiel-agent` skill and the tools' own messages use the
  0.7.0 tool names and counts.

## Fixes reach you sooner

A daily check now fails if a fix to anything the plugin ships waits on
`main` more than 7 days without a release (2 days for dependency pins), and
another fails on any dead link in the docs.

Not financial advice. Leveraged perps can lose the whole account.
