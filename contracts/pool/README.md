# contracts/pool

The research pool's contract: who is a member, what they signed, what is
published. `services/pool` and the MCP server's pool tools code against this
package and nowhere else. Design: `docs/overhaul/RESEARCH_POOL_2026-11-12.md`.

`CONTRACT_VERSION = "1.1.0"` (1.1.0, 2026-11-12 review: the cluster rule is anchored
with a 24 hour span, and `reinstatement_problem` is new). Stdlib only at import, plus `contracts.runtime`.
`recover_signer` imports `eth_account` (already in `requirements.lock`) when
called. Nothing here reads the network or a key.

## What it fixes

| thing | where | note |
|---|---|---|
| The signed text | `statement_text(kind, address, signer, at)` | Version 1, pinned byte for byte by a test. One changed character is a `STATEMENT_VERSION` bump. |
| The member and leave file | `build_statement_file`, `dump_statement_file` | `pool/members/<address>.json`, `pool/leaves/<address>.json` |
| Offline verification | `verify_statement`, `load_statement` | schema, canonical text, EIP-191 signature, file name |
| Who is in | `scan`, `active_members`, `membership`, `local_standing` | a leave at or after the join wins; a bad file fails the build, never a silent skip |
| The house row | `load_house` | `pool/house.json`; the address must be `runtime.DEFAULT_BUILDER_ADDRESS` |
| The 24 hour delay | `cutoff_ms`, `publishable` | cutoff = 00:00:00Z of the day before the build day |
| Pull request freshness | `fresh_problem` | at most 7 days old, at most 10 minutes ahead, on the clock of whoever runs the check (not the merge) |
| Coming back in | `reinstatement_problem(before, after, held_ms)` | an address becomes active only through a join newer than every statement the base branch holds or ever held for it. Refuses a deleted leave, an older leave swapped in, a member file put back from history. |
| Agent approval | `agent_approved(address, signer, extraAgents reply, userRole reply, now)` | pure decision on two venue replies |
| Who may sign a leave | `leave_signer_allowed` | the account, the key that joined, or an approved API wallet. The key that joined needs no venue read, so it can still leave after the account revoked it (accepted, documented in the function). |
| Independent trades | `cluster_ids`, `CLUSTER_SPAN_MS` | same coin and side, entered within 24 hours of the cluster's first entry: one bet. Anchored, so no cluster spans more than 24 hours. Gated by `python -m services.pool.evals.cluster_eval`. |
| Venue request bodies | `fills_request`, `orders_request`, `extra_agents_request`, `user_role_request` | checked against hyperliquid-python-sdk 0.24.0 and the venue docs |
| The scoreboard | `Scoreboard`, `MemberRow`, `SliceRow`, `Counts`, `validate_scoreboard`, `dump_scoreboard`, `empty_scoreboard` | no dollars, prices, sizes or positions, enforced by key name |
| Pull request link | `pr_new_file_url` | GitHub's prefilled new-file form |
| Glue | `MCP_TOOLS`, `MCP_HANDLERS`, `MCP_TOOL_CLASS`, `mcp_handler`, `CLI_COMMANDS` | names every unit shares |

## Member file

```json
{
  "schema": 1,
  "kind": "join",
  "network": "mainnet",
  "address": "0x<40 lowercase hex: the account>",
  "signer": "0x<40 lowercase hex: the key that signed>",
  "joined_at": "2026-11-12T14:03:22Z",
  "message": "<statement_text('join', address, signer, joined_at)>",
  "signature": "0x<130 lowercase hex>"
}
```

A leave file has `"kind": "leave"` and `left_at` in place of `joined_at`. No
other key is allowed. `signer == address` is the master role; anything else is
an agent, whose approval is checked against the venue once, when the pull
request is checked.

## Tests

`contracts/pool/tests`, offline, under 2 seconds:

- `test_contract.py`: the statement text, the fixtures, every tamper refused
  with its code, join / leave / rejoin, the delay, freshness, the agent
  table, clustering (the anchor, the span bound, a stream with no quiet
  hour), the reinstatement rule (a deleted leave, an older leave, a replayed
  join), the scoreboard validator catching each broken promise,
  and the constants pinned to `services/review` (labels, minimum n, the
  dimensions, the desk coins).
- `test_repo_pool.py`: every file under the repo's own `pool/` verifies. It
  runs in the commit gate and in CI's test job (`permissions: contents: read`).
- `test_thesis_boundary.py`: pool data feeds grading only. Three static
  walls: who may import the pool, what the thesis and tick side may never
  name, what the pool may never import or read.

Keys in tests are made in the test and dropped.

The cluster rule also has an eval, in the service because it grades with
`services/review`: `python -m services.pool.evals.cluster_eval` (offline,
seeded, 200 seeds, about 40 seconds). On pure noise shared by 12 members it
measures how often the pool row says EDGE or LEAK: 5.5% with this rule, 33%
with a 60 minute window, 49.5% with every trade counted. Gate: 10%.

## Fixtures

`fixtures/join_master.json`, `join_agent.json`, `leave_agent.json`,
`scoreboard.json`. Made by `python -m contracts.pool.fixtures._build` with
keys created in that run and kept nowhere; every number in the scoreboard
fixture is made up and sits on a made-up address. Regenerate only when a
schema or the statement version changes.
