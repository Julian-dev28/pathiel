# Restart Sequence (canonical short form)

This pattern is the one the user repeatedly requests for hermes-trader maintenance:

```bash
pkill -f trading_loop.py && pkill -f hermes-mcp-server.py && sleep 1 && \
cd /Users/julian_dev/Documents/code/hermes-trader && \
python3 scripts/trading_loop.py --env prod --daemon
```

The exact command the user repeatedly pastes (and that works reliably for them) is shown above. It includes the explicit `sleep 1` + `cd` so the new process starts in the correct working directory with a clean process table.

**Correct pattern for Hermes terminal tool (background process):**
```bash
pkill -f trading_loop.py && pkill -f hermes-mcp-server.py && sleep 1
terminal(
  action="run",
  command="cd /Users/julian_dev/Documents/code/hermes-trader && python3 scripts/trading_loop.py --env prod --daemon",
  background=true
)
```

The `--env prod --daemon` flags are informational but kept because they match the user's standardized restart ritual.

After any edit to trading_loop.py, ta_filter.py, system_prompt.py, .env.local, or .agent-config.json, run the above before the next scan cycle.

Verify command:
```bash
ps aux | grep -E "(trading_loop|hermes-mcp-server)" | grep -v grep || echo "All cleared"
```

MCP server is intentionally transient — it only needs to be killed on code/config changes; it respawns on the next tool call via the hermes config.
