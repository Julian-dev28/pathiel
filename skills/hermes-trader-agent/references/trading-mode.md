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

Use **`scripts/restart.sh`** — the canonical restart. It handles SIGTERM→SIGKILL
stop, startup grace (429-storm protection), and launches the loop + dashboard server.

```bash
scripts/restart.sh           # restart loop + server
scripts/restart.sh loop      # loop only
scripts/restart.sh status    # show PIDs
scripts/restart.sh stop      # stop both
```

Do NOT hand-roll `pkill … && python3 scripts/trading_loop.py` — that old pattern
skips the startup grace and the server. The **MCP server
(`scripts/hermes-mcp-server.py`) is intentionally NOT managed by restart.sh** —
it's transient (stdio), spawned on demand by an MCP client, and shares
`.agent-memory.json` with the loop. See `references/restart-sequence.md`.

---

**Related support file:** `references/trading-mode.md` — full transcript of user corrections that led to this rule.