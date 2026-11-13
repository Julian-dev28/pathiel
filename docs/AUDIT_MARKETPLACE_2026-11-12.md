# Marketplace audit, updated for 0.4.0 (2026-11-12)

The question: can pathiel be sold through an MCP or Claude marketplace? The
first audit said no, and said what to ship instead. This update checks
0.4.0 against each finding and lists what is still open.

## Verdict

Do not sell access. Ship pathiel free and earn through the Hyperliquid
builder fee, which users opt into. 0.4.0 does that. Three things stand
between 0.4.0 and the first fee collected: the builder address needs 100
USDC of perps account value, the release has to reach `main`, and the
listings have to be submitted.

## Findings and what 0.4.0 shipped

| # | finding | what 0.4.0 shipped | status |
|---|---|---|---|
| 1 | Paid MCP listings do not fit. At least 5 free Hyperliquid MCP servers exist: edkdev/hyperliquid-mcp, TradingBalthazar/hyperliquid-mcp-server, 6rz6/HYPERLIQUID-MCP-Server, samimiair/hyperliquid-mcp, caiovicentino/hyperliquid-mcp-server. A paywall loses to free on day one | pathiel is free. Revenue is a 0.01% builder fee per fill (`f = 10`) to `0x2c2e4efF574B3Fd9879AdeF9c5CAC6fD7C916985`, charged only after the user approves it. The pitch is the exact stop on every order and the 3x, 1%, one-position default, which the free servers do not offer | done |
| 2 | Anthropic's Claude Marketplace launched 2026-09-23 with no published revenue share for independent developers. No income path there | Distribution through this repo's own plugin marketplace (`/plugin marketplace add Julian-dev28/pathiel`) and the free MCP directories. Listing files ready: `server.json`, `smithery.yaml`, `glama.json` | ready, not submitted |
| 3 | The record cannot back a price (numbers below) | No performance claim in any public copy. A lint (`tests/test_public_copy.py`) checks the README, the manifests, the listing files and every doc they link. `docs/strategy/OIL_RULES.md` states what is not known. `desk_track_record` grades every call in the open | done |
| 4 | Legal exposure of paid trading calls: selling trade calls on commodity perps raises the CFTC commodity trading advisor (CTA) question, and Hyperliquid is not available to US persons | No paid tier, no subscription, no sold signals. The user decides and confirms every order. The README, setup and release notes carry the not-financial-advice and restricted-jurisdiction notices; pathiel does not check location and says so | mitigated, not reviewed by a lawyer |
| 5 | Portability: the code assumed the maintainer's machine, network and 20x config | Public launcher, container (`Dockerfile.mcp`), per-network state, public risk profile in `config/public/`, neutral thesis persona, no machine-bound paths (gate test), operator-only scripts refuse on public installs | done |

## The record (audit only, never marketing)

Measured 2026-11-12 on the maintainer's live account.

| measure | value |
|---|---|
| Oil thesis direction hit rate | 32% over 36 graded calls |
| Brier score | 0.81 |
| Closed trades | 19 |
| Mean result per trade (R) | +0.41 |
| 95% confidence interval (R) | -0.56 to +1.55 |
| Class | NOISE: the interval spans zero |
| Realized | about +35 USD |

A price needs an edge a buyer can check. This record does not show one,
and 19 trades cannot tell one from luck. It stays out of public copy.

## Closed on release day (2026-11-12)

| gap | how it closed |
|---|---|
| 0.4.0 not on `main` | `main` fast-forwarded to the release; tag `v0.4.0`; GitHub release with notes attached |
| Container image not published | `ghcr.io/julian-dev28/pathiel-mcp:0.4.0` and `:latest`, amd64 and arm64, public, smoke-tested over stdio with no key |
| Not in the official MCP registry | Published as `io.github.Julian-dev28/pathiel` 0.4.0, status active |
| Private vulnerability reporting off | Turned on |
| GitHub About text said "Autonomous multi-market trading agent" | Description, homepage and 10 topics set |
| smithery.yaml pointed at a dead flow | Removed: Smithery's hosted builds shut down 2026-10-31 |

## Remaining gaps

| gap | why it matters | next move | owner |
|---|---|---|---|
| Builder balance sits on the xyz dex | Hyperliquid: "The builder must have at least 100 USDC in perps account value and must use standard as the account abstraction mode" (hyperliquid.gitbook.io, builder-codes). On 2026-11-12 the address held $0.009 on main perps and $213.77 on the xyz HIP-3 dex. No doc says whether a HIP-3 dex balance counts. Julien chose to leave it on xyz (the oil desk trades it) | Once public users approve the fee, check fills for a builder fee. If none arrive, move 100 USDC to main perps | Julien |
| Builder fees on HIP-3 orders unconfirmed | The builder-codes doc never mentions HIP-3. The oil and metals desks trade HIP-3 (xyz) perps. If the venue refuses the fee there, pathiel resends without it: the order lands, no fee is earned | Confirm on the first approved public order on xyz | Julien |
| Directory listings beyond the official registry | The official MCP registry lists `io.github.Julian-dev28/pathiel` 0.4.0 (published 2026-11-13 01:57 UTC); the others do not list it yet | Submit to Glama, PulseMCP and mcp.so (web forms; Glama also indexes public GitHub repos with a `glama.json`). Smithery is directory-only since 2026-10-31 (hosted builds shut down, smithery.ai/future), so pathiel ships no smithery.yaml | Julien |
| No legal review | The builder fee is execution revenue, not a sale of advice, but no lawyer has said so | Legal review before any paid tier, and before marketing to any jurisdiction | Julien |
| Record too small | 19 trades cannot show an edge either way | Keep grading; revisit only after enough trades for the interval to clear zero, and never as marketing | desk |

## What not to do

- No paid tier, signal subscription or performance fee until the legal
  review is done.
- No return figures in listings, the README or social posts.
- No fee above the approved 0.01% without a new signature from each user;
  Hyperliquid would reject the orders anyway.
