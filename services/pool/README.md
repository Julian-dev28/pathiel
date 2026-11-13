# services/pool: the opt-in research pool

A pathiel user can opt in to a research pool. They register a Hyperliquid
address with a signed statement; from the time they sign, pathiel grades
that address's public fills with the `services/review` harness (ledger,
stats, luck: fluke vs edge with confidence intervals) and publishes graded
rows on a public scoreboard, at least 24 hours late. Nobody uploads
anything, and nothing here places an order. Design:
`docs/overhaul/RESEARCH_POOL_2026-11-12.md`. Contract: `contracts/pool/`
(`contracts/pool/README.md`).

**Outcome it moves:** graded trades in the pool, from 19 (the maintainer
alone, the 2026-11-12 audit) toward 10 or more independent trades per
slice, the floor `services/review` sets before a slice can say EDGE or
LEAK.

Trace: `docs/scoreboard/scoreboard.json`'s `counts.graded_trades`,
`counts.independent_trades` and `counts.slices_at_min_n`; the `POOL`
summary line every build prints; one `POOL-NEW <short address>
since=<date>` line per address that gained a row the build just wrote that
had none in the scoreboard it replaced.

## Why it never feeds the thesis prompt

Rule: pool data feeds grading only. It never reaches a thesis prompt or a
tick decision. Why: SCORECARD_TEST_2026-10-31 put the desk's own graded
record into the thesis prompt and the money result got worse. The pool is
a bigger graded record than that one was; the same rule holds.

Enforced, not just stated:

- `contracts/pool/tests/test_thesis_boundary.py`: three static walls, run
  in the commit gate on every tracked and untracked file. Only the pool
  itself, its tests, and the MCP server's tool registration may import
  `services.pool` or `contracts.pool`; the thesis and tick side
  (`services/thesis`, `services/llm`, `services/intel`,
  `services/strategies`, the desk-tick skill and agent files) may never
  name a pool path, a pool tool, or the scoreboard; the pool may never
  import the thesis side or read a theses directory. Each wall is run on a
  planted breach, so it is proof, not a comment.
- `tests/test_pool_thesis_canary.py`: the dynamic wall. It plants a canary
  address and a canary token in every place the pool writes (the ledger,
  the outbox, the repo member file, the scoreboard JSON), builds the oil
  desk's real thesis prompts from the same fixtures production uses, with
  the scorecard section on and off, and asserts the canary is in none of
  them, and that nothing the thesis side ran changed what sits under the
  pool's own state directory.
- The build itself passes `[]` for theses and `{}` for theses by desk to
  the grading audit, so a member's trade record never carries a thesis
  field, and no thesis dimension is in `PUBLISHED_DIMENSIONS`.

## Run

```
python -m services.pool verify [--root DIR] [--json]
python -m services.pool verify --paths P [P ...] [--online] [--fresh] [--json]
python -m services.pool verify --pr-base REF [--json]
python -m services.pool join   [--master-prompt] [--yes] [--json]
python -m services.pool leave  [--master-prompt] [--yes] [--json]
python -m services.pool status [--json]
python -m services.pool build  [--as-of ISO] [--offline BUNDLE] [--save-bundle DIR] [--out DIR] [--no-ledger] [--dry-run]
python -m services.pool report [--address 0x...] [--offline BUNDLE] [--json]
```

Exit codes: `0` done; `1` a rule failed (`verify`); `2` refused (bad input,
testnet, a pool file that does not verify, no key; for `verify --pr-base`
also a shallow clone or a `REF` that `HEAD` does not contain); `3` the
venue could not be read.

### Operator runbook

**Branch protection on `main`** (one-time setup, repository Settings ->
Branches): require these four status checks before merging -
`Tests (Python 3.11)`, `Tests (Python 3.12)`, `Tests (Python 3.13)`,
`Pool members` - plus "Require branches to be up to date before merging".
`Pool members` runs on every pull request, not only ones that touch
`pool/`: GitHub cannot scope a required check to one path, and a required
check that a path filter skips never reports, which blocks every other
pull request. On a pull request with no membership change it prints
`no membership change` and passes at once.

**Merging a membership pull request:**

1. A first-time contributor's workflow run waits for "Approve and run".
   Read the diff first: it must touch one or two files, only under
   `pool/members/` or `pool/leaves/`, nothing else (`mixed_change`
   otherwise).
2. Both required checks must be green, on the current `main`.
3. If the `Pool members` run is more than 24 hours old (its last line
   carries `at=<time>`), press "Update branch" or re-run the job before
   merging: the check reads the clock and the venue when it runs, not when
   the merge happens, and an API wallet can be revoked or a week can pass
   in between.
4. Never use the admin bypass on a membership pull request. It skips the
   freshness window and the agent-approval read entirely.
5. Merge.

**Building and publishing:** `git pull`, then
`python3 -m services.pool build`, read its `POOL` line, commit
`docs/scoreboard/scoreboard.json`, push. Match the run's `POOL-NEW` lines
against the pull requests merged since the last build: each one should
name an address from a pull request you merged, not one that appeared on
its own.

**A leave:** merges the same way as a join; the next build drops the row.
**A removal request** (no signature, a GitHub issue): the maintainer
deletes the member file directly; the next build drops the row the same
way. **A member coming back:** only a new signed join, never a restored or
edited leave file; the `Pool members` check refuses the other kind of
change (`reinstated`, below).

## Pieces

| Module | Unit | What it does |
|---|---|---|
| `signing.py` | join | EIP-191 signs the join or leave statement with the configured key; the key is read, used for one signature and dropped. |
| `approval.py` | join | Reads `extraAgents` and `userRole` from the venue and calls `contracts.pool.agent_approved`. |
| `join.py` | join | Builds the statement, calls signing, writes the outbox file, builds the pull request link. |
| `join_cli.py` | join | `python -m services.pool join` / `leave` / `status`: the same flow as the CLI's own text. |
| `verify_cli.py` | join | `python -m services.pool verify`: the commit gate, CI's test job, and the `Pool members` pull request check (`--pr-base`). |
| `join_tools.py` | join | The MCP handlers for `pool_status`, `pool_join`, `pool_leave`. |
| `fetch.py` | grade | The venue reads one build needs per member (fills, orders) and the candles per pool coin, paged and retried. |
| `grade.py` | grade | Turns fills and orders into graded trade records, through `services.strategies.evals.loss_audit` and `services.review.ledger`. |
| `scoreboard.py` | grade | Builds the `Scoreboard` document from the graded records and validates it before anything is written. |
| `build_cli.py` | grade | `python -m services.pool build`: one run end to end, the `POOL` and `POOL-NEW` lines. |
| `report.py` | grade | One member's own graded report, read locally. Nothing it does publishes anything. |
| `report_cli.py` | grade | `python -m services.pool report`. |
| `report_tool.py` | grade | The MCP handler for `pool_report`. |
| `paths.py` | shared (frozen) | Where the pool reads and writes: the repo side (`pool/`, `docs/scoreboard/`) and the state side (outbox, ledger, work). |
| `venue.py` | shared (frozen) | The pool's one way to the venue: a POST to the public `/info` endpoint, read only, one retry. |
| `account.py` | shared (frozen) | Which address this install trades or reads. No key is read here. |
| `__main__.py` | shared (frozen) | `python -m services.pool <command>`, dispatched through `contracts.pool.CLI_COMMANDS`. |

And the contract itself, `contracts/pool/` (`contracts/pool/README.md`):
the whole offline definition of membership, the signed statement, the
scoreboard schema and its validator, and the glue names every module above
shares. See also `docs/overhaul/RESEARCH_POOL_2026-11-12.md` for the full
design.

## Data

**The member file**, `pool/members/<address>.json` (a join) or
`pool/leaves/<address>.json` (a leave): schema, kind, network, address,
signer, a time (`joined_at` or `left_at`), the exact statement text, and
the signature. Eight keys, no more. `contracts/pool/README.md` has the
full shape.

**The outbox**, `<state>/pool/outbox/{members,leaves}/<address>.json`:
statements signed on this machine that are not yet in the repo. `join`
writes here; `pool_status` and `pool_report` read it together with the
repo copy (`contracts.pool.local_standing`), so a member who just signed
sees `signed_not_merged` at once, before any pull request lands.

**The ledger**, `<state>/pool/ledger/<address>.jsonl`: one append-only file
per graded member, written by `services.review.ledger.update_ledger`.
Frozen once settled (12 hours after the exit, the same rule
`services/review`'s own ledger follows), so a rebuild weeks later cannot
drift a record that has already stopped moving.

**A bundle**: one member's raw fills and orders, plus the pool coins'
candles, saved under `--save-bundle DIR` (or `<state>/pool/work/<stamp>/`
for an ordinary build). `--offline BUNDLE` replays a saved bundle with no
network call at all, for a deterministic rerun or a test fixture - the
same shape `services/review`'s own saved bundles use.

**What the build reads from the venue**, per active member, mainnet,
public, no key:

| request | body |
|---|---|
| fills | `{"type": "userFillsByTime", "user": <address>, "startTime": <count_from_ms>, "endTime": <as_of_ms>, "aggregateByTime": false}` |
| orders | `{"type": "historicalOrders", "user": <address>}` |

Fills page at 2000 rows; a full page means asking again from the last
row's time and dropping rows already seen by `tid`. Orders carry a trade's
opening stop and target.

## Rules

- **The delay.** `contracts.pool.cutoff_ms`: midnight UTC of the day
  before the build day. A trade publishes only once it has closed before
  that cutoff, so every published trade is between 24 and 48 hours old at
  least, and two builds on the same UTC day write the same bytes.
- **Independent trades.** Trades on one coin and side, entered within 24
  hours of their cluster's first entry, are one bet
  (`contracts.pool.cluster_ids`, `CLUSTER_SPAN_MS`). The window is
  anchored to the first entry, not chained to the entry before it, so no
  cluster ever spans more than 24 hours; without that bound, a coin and
  side that never goes quiet folds its whole history into one cluster and
  the count of independent trades stops growing no matter how much
  evidence arrives. Why 24 hours and not 60 minutes: these are
  measurements of the rule on shared synthetic noise, not a result of any
  strategy. `services/pool/evals/cluster_eval.py` measures how often a
  pool row over pure noise, shared by 12 members, says EDGE or LEAK: 49.5%
  counting every trade as its own bet, 33% with a 60 minute window, 5.5%
  with the 24 hour anchored rule the contract uses.
- **Graded means an opening stop.** A closed trade without a stop that can
  be found has no R, so it counts as closed but not graded - the same rule
  `services/review` applies to its own trades. Every pathiel bracket has a
  stop, so pathiel's own trades always grade.
- **Labels.** Reused from `services/review.stats`: EDGE and LEAK need at
  least 10 independent trades and a confidence range that does not cross
  zero; under that floor a slice is NOISE, FLUKE or LEANING at best.
  Nothing under the floor is ever EDGE or LEAK, in the pool or anywhere
  else.
- **The way back in.** `contracts.pool.reinstatement_problem` is the rule
  the `Pool members` check runs on every pull request (code
  `reinstated`): an address may become active again only through a join
  newer than every join and leave the repository holds or has ever held
  for it. A deleted leave file, an older leave swapped in, or a member
  file replayed from git history all fail this check. What is accepted,
  on purpose: the key that signed a join may still sign that account's
  leave even after the account has since revoked it
  (`contracts.pool.leave_signer_allowed`) - a leave only removes published
  rows, moves no money, and the account can always rejoin with its current
  key.

## MCP tools

| tool | class | handler | reads or writes |
|---|---|---|---|
| `pool_status` | local read | `join_tools.handle_pool_status` | local files only |
| `pool_report` | account read | `report_tool.handle_pool_report` | the member's own ledger; publishes nothing |
| `pool_join` | local write | `join_tools.handle_pool_join` | signs with the configured key; writes the outbox |
| `pool_leave` | local write | `join_tools.handle_pool_leave` | signs with the configured key; writes the outbox |

Names and shapes: `contracts.pool.MCP_TOOLS`, `MCP_HANDLERS`,
`MCP_TOOL_CLASS`, `mcp_handler`. Listed on public installs only; the
operator's `tools/list` stays byte-identical (Open question 4 in the
design doc).

## Tests and evals

**Gate** (offline, under 2 seconds total, run on every commit):
`contracts/pool/tests` (the contract's own suite, including
`test_thesis_boundary.py`); each unit's own `services/pool/tests/`; the
frozen `services/pool/tests/test_skeleton.py` and
`test_cluster_eval_smoke.py`; this unit's `tests/test_pool_copy.py`,
`tests/test_pool_share_command.py` and `tests/test_pool_thesis_canary.py`.

**Periodic evals** (offline, seeded, no network, no LLM call):

```
python -m services.pool.evals.cluster_eval    the independent-trade rule, 200 seeds, ~40s, gated at 10% false EDGE/LEAK on noise
python -m services.pool.evals.join_eval       statements accepted and refused (the join unit's own gate)
python -m services.pool.evals.pool_eval       the delay, leaves, and labels on planted data (the grade unit's own gate)
```

`join_eval` and `pool_eval` are each unit's own; run them with `--help`
for their current gates.

## Failure modes

Condensed from `docs/overhaul/RESEARCH_POOL_2026-11-12.md`'s full table;
rows 19 to 24, the ones specific to membership state and the container
build:

| # | What | Where it breaks | What holds |
|---|---|---|---|
| 19 | A leave file is deleted, or swapped for an older one, while the member file stays. | A scan reads the files that are there: the old join is back in force from the old `joined_at`, and the time the owner believed they were out gets graded. | `reinstated` at the pull request. Not covered: a direct push to `main`, which skips every check; the build's `POOL-NEW` lines show it after the fact. |
| 20 | A member file removed on request is put back from git history inside the 7 day freshness window. | The old signature is still valid and still fresh. | `reinstated` reads the history of both paths: a replayed statement is not a new one. |
| 21 | An API wallet the account has since revoked signs a leave. | The account is taken out by a key its owner no longer trusts. | Accepted: a leave only removes published rows, and the account can rejoin with its current key. |
| 22 | A membership pull request is merged on a failing, missing or stale check. | Freshness and agent approval are measured when the check ran, not when the merge happens. | Branch protection requires both checks on an up-to-date branch; the merge runbook re-runs a check over 24 hours old; an admin can still tick the bypass box. |
| 23 | A chain of entries with no quiet hour. | Chained on the previous entry, one coin and side would fold into a single cluster forever and independent-trade count would stop growing. | The span is anchored to the first entry and bounded at 24 hours; gated by `cluster_eval` and a contract test. |
| 24 | `.dockerignore` excludes `docs/` whole. | `COPY docs/scoreboard/` in `Dockerfile.mcp` fails the image build ("not found"). | `!docs/scoreboard/` after the `docs/` exclusion, confirmed against real Docker (BuildKit and the legacy builder); `tests/test_pool_docker_context.py` applies Docker's own matching rule to every `COPY` source of both Dockerfiles. |
