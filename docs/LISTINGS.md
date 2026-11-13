# Where pathiel is listed

When someone asks an agent to "find an AI trading agent", it searches GitHub,
the web and plugin directories, then favors whatever matches the query words
and appears in many trusted places. This page tracks every place pathiel is
listed and keeps the copy for the forms only a human can submit.

**Outcome:** the number of stranger queries where pathiel ranks in GitHub's
top 10. Trace: `docs/seo/github_rank.jsonl`, one row per run of
`python scripts/seo_rank.py --log docs/seo/github_rank.jsonl`, and the weekly
**SEO rank** workflow's run summary.

Baseline, 2026-11-12, a few minutes after the description and topics
changed: in the top 10 for 1 of 9 queries (`claude code plugin trading`, #8),
in the top 100 for 5.

## Status

| Place | How | Status |
|---|---|---|
| GitHub repo description and topics | `gh repo edit` | Done 2026-11-12: "AI trading agent for Hyperliquid…", 16 topics |
| Official MCP registry | release.yml (GitHub OIDC) | Live: `io.github.Julian-dev28/pathiel` 0.5.0 (2026-11-13), new description included |
| ghcr.io image | release.yml | Live: `ghcr.io/julian-dev28/pathiel-mcp:0.5.0` and `latest`, amd64 + arm64 |
| punkpeye/awesome-mcp-servers | pull request | Open: [#19383](https://github.com/punkpeye/awesome-mcp-servers/pull/19383) |
| sbs2001/awesome-hyperliquid | pull request | Open: [#68](https://github.com/sbs2001/awesome-hyperliquid/pull/68) |
| Claude plugin directory | portal form, operator only | To submit |
| hesreallyhim/awesome-claude-code | web issue form, a human must submit | To submit |
| Glama | sign in with GitHub, claim the server (`glama.json` is in the repo) | To claim |
| mcpservers.org (feeds wong2/awesome-mcp-servers) | web form, free plan | To submit |
| mcp.so | web form | To check: the page shows only a $39 paid path |
| PulseMCP | none | Submissions paused since 2026-09-03; it reads the official MCP registry |

## Claude plugin directory

Portal: <https://claude.ai/directory/manage> → **Submit new** → **Plugin
bundle**. Rules: <https://claude.com/docs/plugins/submit.md>.

Before you open it:

1. The directory reviews the `plugin` branch, not `main`. That branch is
   built by `scripts/build_plugin_bundle.py` and holds only what the plugin
   runs. Given the repo root (2026-11-13), the review flagged the
   maintainer's CLAUDE.md, the repo `.mcp.json`, `.claude/skills`, CI and VPS
   scripts as if they shipped; none of those are on `plugin`.
   `claude plugin validate --profile directory --strict` on a build of it
   is clean (`tests/test_eval_plugin_bundle.py`). Two notes stay by design:
   the server is local stdio, so it runs in Claude Code and Cowork, not on
   claude.ai (STDIO_WEB_UNSUPPORTED), and a local server from an
   unverified organization (LOCAL_CODE_MCP_SERVER, LOCAL_CODE_FILE_TYPE for
   the Python it ships) is not refused yet. Decided 2026-11-13: accept those
   notes. Only a hosted, read-only MCP server would clear them; build it if
   the directory announces a date for refusing local servers.
2. The icon is `.claude-plugin/icon.png`. The directory takes the icon only
   the first time the plugin is saved or submitted, so replace it before
   then if you want a different one. It is the PATHIEL wordmark, rendered
   from `docs/brand/icon.svg` on macOS with
   `qlmanage -t -s 1024 -o /tmp docs/brand/icon.svg` (Avenir Next Demi Bold,
   widened 1.55x).
3. Your GitHub account is connected on claude.ai and can push to the repo.

Form answers:

| Field | Answer |
|---|---|
| Repository | `Julian-dev28/pathiel` |
| Plugin path | leave empty (the `plugin` branch root is the plugin) |
| Branch | `plugin` |
| Reads or stores personal data? | It reads the user's Hyperliquid account (balances, positions, fills) and keeps a local journal under `~/.pathiel`. Nothing leaves the machine except calls to the Hyperliquid API and the public news and data feeds the desk briefs read. |
| Sends data to other services? | Hyperliquid API (account reads, orders the user asks for); public market and news feeds (EIA, CFTC, prediction markets, news). No analytics, no telemetry. |
| Retention | Local files only, kept until the user deletes `~/.pathiel`. |
| Under 18? | No. |
| Contact email | yours |
| New versions | GitHub push webhook; leave auto-publish off so a reviewer sees each version |

## awesome-claude-code

Form: <https://github.com/hesreallyhim/awesome-claude-code/issues/new?template=recommend-resource.yml>.
The maintainer requires a human to submit it, one resource at a time, and it
cannot be done with `gh`.

- Name: pathiel
- Link: https://github.com/Julian-dev28/pathiel
- Category: plugins (or the closest one the form offers)
- License: MIT
- Description: An AI trading agent for Hyperliquid, packaged as a Claude
  Code plugin. Claude trades perps through 60 MCP tools, and every order is a
  bracket with an exact stop and target, sized from a risk profile (3x and 1%
  of equity per trade by default). Read-only without a key, testnet in one
  line, every live-money call asks first.

## mcpservers.org

Form: <https://mcpservers.org/submit>, free plan (review within 2 weeks).

- Server name: pathiel
- Category: Finance
- Short description: AI trading agent for Hyperliquid perps: bracket orders
  with an exact stop and target, risk sizing, account reads, oil and metals
  desk briefs. Read-only without a key.
- Repository: https://github.com/Julian-dev28/pathiel
- Official MCP Registry name: `io.github.Julian-dev28/pathiel`
- Remote connections: no (stdio)
- Contact email: yours

## Glama

Sign in at <https://glama.ai> with GitHub, find or add
`Julian-dev28/pathiel`, and claim it. `glama.json` in the repo names you as
the maintainer, which is what the claim checks. A Glama score badge also
makes the awesome-mcp-servers entry match the others there.

## What not to do

- Do not call pathiel "autonomous". Public installs ask before every
  live-money call, and the README says Claude is the trader, not a bot.
- Do not buy stars or reviews. It breaks GitHub's terms and gets repos hidden
  from search.
