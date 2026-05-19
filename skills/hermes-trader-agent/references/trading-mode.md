## Trading Mode – Interaction Rules (User Explicit Preference)

When the user issues commands such as:

- "continue monitoring and trading"
- "run trades"
- "restart the trading loop"
- "stop what you are doing and run trades"

**Behavior:**
- Execute the requested action immediately without asking for confirmation.
- Report **only** concrete results, errors, or the most recent feed/status lines.
- **Never** add plans, explanations, meta-commentary, or "what I will do next".
- Keep responses short, direct, low-friction.

This rule takes precedence over normal helpfulness when the above phrases appear. (Captured 2026-05-19)

## Standardized Restart Sequence

```bash
pkill -f trading_loop.py || true
pkill -f hermes-mcp-server.py || true
sleep 2
ps aux | grep -E "(trading_loop|hermes-mcp-server)" | grep -v grep || echo "All cleared"
cd /Users/julian_dev/Documents/code/hermes-trader && \
  python3 scripts/trading_loop.py --env prod --daemon
```

MCP server is transient (stdio); it respawns automatically on the next tool call.

---

**Related support file:** `references/trading-mode.md` — full transcript of user corrections that led to this rule.