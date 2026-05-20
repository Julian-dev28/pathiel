# Restart Sequence (canonical short form)

This pattern is the one the user repeatedly requests for hermes-trader maintenance:

```bash
pkill -f trading_loop.py && pkill -f hermes-mcp-server.py || true; \
python3 scripts/trading_loop.py --env prod --daemon
```

After any edit to trading_loop.py, ta_filter.py, system_prompt.py, .env.local, or .agent-config.json, run the above before the next scan cycle.

Verify command:
```bash
ps aux | grep -E "(trading_loop|hermes-mcp-server)" | grep -v grep || echo "All cleared"
```

MCP server is intentionally transient — it only needs to be killed on code/config changes; it respawns on the next tool call via the hermes config.
