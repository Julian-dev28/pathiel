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

## Remaining gaps

| gap | why it matters | next move | owner |
|---|---|---|---|
| Builder address unfunded | Hyperliquid pays builder fees only to an address holding at least 100 USDC of perps account value | Fund `0x2c2e4efF574B3Fd9879AdeF9c5CAC6fD7C916985` with 100 USDC or more on mainnet perps | Julien |
| 0.4.0 not on `main` | `/plugin marketplace add Julian-dev28/pathiel` clones the default branch, `main`; users get 0.3.0 until it merges | Merge the release branch to `main`, tag `v0.4.0` | Julien |
| Container image not published | `server.json` names `ghcr.io/julian-dev28/pathiel-mcp:0.4.0`; the registry rejects a package it cannot pull | Build `Dockerfile.mcp` and push the tag to ghcr.io, public | Julien |
| Listings not submitted | No directory lists pathiel yet | Submit in this order: official MCP registry (`mcp-publisher login github`, then `mcp-publisher publish`), Smithery, Glama, PulseMCP, mcp.so | Julien |
| `smithery.yaml` still defaults to testnet | The other listings and the plugin default to mainnet; a Smithery user would see a different default | Decide: keep testnet on hosted listings on purpose, or align it | Julien |
| Private vulnerability reporting off | `SECURITY.md` points reporters at GitHub private advisories, which need it on | `gh api -X PUT repos/Julian-dev28/pathiel/private-vulnerability-reporting` | Julien |
| GitHub About text says "Autonomous multi-market trading agent" | Contradicts the README: there is no autonomous bot | Set the description to the README's one-line pitch; add topics `hyperliquid`, `mcp`, `claude-code`, `trading`, `perps` | Julien |
| No legal review | The builder fee is execution revenue, not a sale of advice, but no lawyer has said so | Legal review before any paid tier, and before marketing to any jurisdiction | Julien |
| Record too small | 19 trades cannot show an edge either way | Keep grading; revisit only after enough trades for the interval to clear zero, and never as marketing | desk |

## What not to do

- No paid tier, signal subscription or performance fee until the legal
  review is done.
- No return figures in listings, the README or social posts.
- No fee above the approved 0.01% without a new signature from each user;
  Hyperliquid would reject the orders anyway.
