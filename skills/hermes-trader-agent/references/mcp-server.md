# MCP Server Structure

`scripts/hermes-mcp-server.py` — a stdio JSON-RPC MCP server exposing 100 tools.
Registered in `~/.hermes/config.yaml` under `mcp_servers.hermes-trader`.

## Layout

| Piece | Role |
|-------|------|
| `TOOLS` (list) | One dict per tool: `{"name", "description", "inputSchema"}`. Drives `tools/list`. |
| `handle_<name>(params)` | Real handlers — module-level functions returning a JSON string. |
| `_STUB_RESPONSES` (dict) | Tools whose SDK call is not yet wired — `name -> fixed payload`. |
| `_make_stub_handler(payload)` | Factory: builds a handler returning `{**payload, "note": "SDK method pending"}`. |
| `tool_handlers` (dict, in `run()`) | `name -> handler`. Real handlers listed explicitly; stub handlers generated from `_STUB_RESPONSES`. |

## Adding a tool

**Real tool — 3 edits:**
1. Append a `{"name", "description", "inputSchema"}` dict to `TOOLS`.
2. Add a module-level `def handle_<name>(params: Dict[str, Any]) -> str:`.
3. Add `"<name>": handle_<name>,` to the `tool_handlers` dict in `run()`.

**Stub tool — 1 edit:** add a `name -> payload` entry to `_STUB_RESPONSES`. The
`tool_handlers` dict picks it up automatically via the `_make_stub_handler` loop.

## Handler boilerplate

Use the existing helpers — do not invent new ones:

```python
def handle_get_xxx(params: Dict[str, Any]) -> str:
    try:
        from hermes_agent.client.exchange import _get_info
        from hermes_agent.client.hl_client import resolve_user_address
        user = resolve_user_address()
        if not user:
            return json.dumps({"error": "no configured user address"}, default=str)
        return json.dumps({"result": _get_info().user_fills(user)}, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, default=str)
```

- `_get_info()` — shared read-side `Info` client.
- `_make_exchange()` — shared write-side `Exchange` client (needs `HYPERLIQUID_PRIVATE_KEY`).
- `resolve_user_address()` (in `hl_client`) — master address, else wallet, else `""`.
- A bad import from `hermes_agent.client.exchange` fails at *handler-call* time,
  not file load — a `python -c 'import ...'` smoke test will not catch it. Check
  the import against the actual module.

## Audit invariant

Every tool in `TOOLS` must resolve to exactly one handler, with no orphans:

```
len(TOOLS) == len(tool_handlers explicit keys) + len(_STUB_RESPONSES)
no tool name appears in both the explicit dict and _STUB_RESPONSES
no duplicate handle_* function definitions (a dup silently shadows the first)
every TOOLS entry has a resolvable handler; no unwired handlers; no orphan dict keys
```

Do not check this by hand — run the bundled `scripts/audit_mcp_server.py` before
and after any tool change. It parses the server via `ast` (no import, no
execution) and exits non-zero on drift.

## Restart

The server is a separate process; after editing it run `pkill -f hermes-mcp-server.py`
— the next tool call respawns it with fresh code. A committed fix that "doesn't
take" almost always means a stale server process.
