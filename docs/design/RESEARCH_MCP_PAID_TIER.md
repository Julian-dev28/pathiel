# research MCP: the paid tier (design, not built)

v1 (`services/research_mcp`) is local stdio, free, keyless, no hosting.
This sketches v2: a hosted HTTP MCP with API-key metering, for after v1 has
proven usage.

## Shape

- **Transport**: HTTP MCP (streamable HTTP, per the current MCP spec)
  behind a thin auth layer, deployed as its own service (own container, own
  deploy unit; CLAUDE.md's services-first rule holds here too: it does not
  become a mode flag on v1's stdio server). Same six tools, same
  `tools.py` logic; only the transport and the metering wrap around it.
- **Auth**: an API key per caller, issued on signup, sent as a bearer
  token. No key, no call; this is the one place v2 adds a gate v1
  deliberately has none of.
- **Metering**: one row per call (key, tool, timestamp, latency, whether a
  source failed) in a small Postgres table, not the local JSON counter
  (`usage.py`) v1 ships with. Rate limit per key (a free tier, a paid tier
  above it) enforced at the same layer that checks the key.
- **Billing**: usage-based, metered at the tool-call level (a `fred_series`
  call and a `research_brief` call cost the same unit; the value is "one
  research question answered," not FLOPs). Stripe metered billing or
  equivalent; no card on file blocks every call past the free tier's cap.
- **What stays the same**: still read-only, still no key/account/order
  access, still no hosted LLM call inside the server (FRED stays
  bring-your-own-key; an LLM-backed tool, if one is ever added, would be
  the same local-Claude-Code route the trading server uses, metered
  separately).

## Why not now

v1's job is to prove usage exists before anyone is asked to pay or to trust
a hosted endpoint with a key. Building metering and billing before that
signal is real backfill work for a number nobody has yet. The first
concrete trigger to start v2: the `usage.json` counter (or its holder's own
report of install/call counts) shows real, sustained call volume from
outside the operator's own machine.
