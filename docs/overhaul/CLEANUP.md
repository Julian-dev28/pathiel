# Cleanup sweep report (agent: cleanup, 2026-10-17)

Branch: `worktree-agent-ae03a9ec738b37870` off `overhaul-2026-10-17` (merged
in at 9005b45 — the worktree had been created one commit behind and was
missing `contracts/strategies/` and this file's own directory; fast-forward
merged before starting any work, no conflicts, nothing local to lose).

## Measurable outcome

| | before (9005b45) | after | delta |
|---|---|---|---|
| tracked files | 1088 | 817 | **-271** |
| tracked bytes at HEAD | 13,203,166 (12.6 MB) | 11,607,330 (11.07 MB) | **-1,595,836 (-1.52 MB)** |
| tracked Python lines | 106,553 | 100,435 | **-6,118** |
| gate suite | 1984 passed, 11 skipped, 24 deselected | 1991 passed, 11 skipped, 24 deselected | **+7 tests** (new guards, zero removed) |
| gate suite wall time | 40.76s | 38.00s | **-2.76s, i.e. faster, not slower** |

The task brief quoted a baseline of "1995 passed in about 72s." My own
before-measurement in this isolated worktree, run the same way (`PATHIEL_STATE_DIR=$(mktemp -d)
.venv/bin/python -m pytest -q -p no:cacheprovider`), came back 1984/40.76s —
close but not identical, most likely because that baseline was taken on the
main checkout or after other agents' worktrees had already merged in, not
from this worktree's starting point. Every number in the table above is a
same-environment, same-command before/after measured in this worktree, which
is the controlled comparison that actually answers "did the sweep make things
better or worse."

File/byte/line counts are `git ls-files | wc -l`, `git ls-tree -r -l HEAD`
summed, and `git ls-files '*.py' | xargs wc -l`, respectively — rerunning
those three commands against any two commits reproduces this table exactly.

## Removal table

| path | reason | evidence | files / bytes / py-lines |
|---|---|---|---|
| `web/` | dead Next.js scaffold, never built past the initial commit | zero references anywhere outside itself and the ownership table; `app/`, `components/`, `lib/` were empty; last touched by a rename-only commit | 4 files, ~9.0K lines (mostly `package-lock.json`) |
| `.qwen/`, `.forge/`, `.agents/`, `skills-lock.json` | triple-vendored copy of the caveman skill, already installed via the Claude Code plugin system | zero code/CI/doc reference outside `pyproject.toml`'s ruff exclude (updated) and `SWARM_RULES.md`'s ownership table; this session's own skill listing already has `caveman:*` from the plugin marketplace | 66 files |
| `pathiel_data.db`, `alerts.json`, `supervisor.json` | tracked runtime state, not config — a committed binary (CLAUDE.md bans this) plus two JSON dumps that land in the repo root by construction | `scripts/_state_env.py:state_dir()` falls back to repo ROOT when `PATHIEL_STATE_DIR` is unset; `alerts.json`/`supervisor.json` each had 4-5 commits re-adding fresh snapshots; `pathiel_data.db`'s `api_keys` table is rebuilt idempotently by `Base.metadata.create_all()` on boot, so committing it was never load-bearing | 3 files untracked (kept out of the byte table above since they were tiny: db 45KB, json dumps a few KB each) |
| `scripts/install_launchd_jobs.py` | dead: launchd was an abandoned intermediate fix, also TCC-blocked, superseded by `scheduler.py` run from `restart.sh` | `scripts/scheduler.py`'s own docstring: "launchd: PermissionError ... Neither cron nor launchd has that grant" — the real fix scheduler.py describes is itself, not this script; zero references anywhere else; last touched by a rename-only commit | 1 file, 151 lines |
| `research/signal_ranking/` | abandoned one-off study: one script + 170 regenerable per-coin candle cache files, zero external references, no saved result | repo-wide grep found no import/test/doc/finding citation; only 3 commits ever touched the path, all whole-repo rename sweeps | 171 files |
| `research/rebuild_2026_07_18/` | superseded prior rebuild-planning docs (BRIEF/PLAN/DEMOLITION_MANIFEST/etc.), 3 months before this overhaul | zero live/test dependency; one prose citation from `research/alpha_swarm/findings/reverse_refuted_direction_audit.md` (a citation trail, not a functional dependency — left as-is, see "Known trade-offs" below) | 8 files |
| `research/regime_2026_09/` (18 of 20 files) | one-off hypothesis sweep (C2-C12, H2-H5, `ev_now.py`, `ev_projection.py`, `regime_read.py`); kept `C1_session_structure.py` and `H1_funding_carry.py` | AST/grep-verified: only those two are ever imported by name from outside the directory — `scripts/grade_forward_ledger.py` (`load_panel`, `DAY_MS`, `SLIP_PCT`, `_f`) and `services/events/fetch_events.py` (`load_panel`). The other 18 only import each other (mostly `C1`) | 18 files |

**Total: 272 files deleted, 1 added, 5 modified.**

## Verified, NOT removed (candidates that turned out legitimate)

Every one of these was in the brief's candidate list or my own first-pass
suspicion list. Listed because the brief explicitly asked me to verify, not
take its word — these are the "verified clean" results, not an oversight.

- **`research/alpha_swarm/`** (453 files, 4.6MB) — the evidentiary backbone.
  `tests/test_evidence_doctrine.py` asserts every live book is named
  somewhere in `research/alpha_swarm/findings/`; `tests/test_wh_ladder.py`
  and `tests/test_drawdown_ladder.py` dynamically `importlib`-load
  `W-WH2_ladder_backtest.py` straight out of `hypotheses/`. Kept as a unit —
  further per-file pruning inside it was out of scope for this pass (453
  files is disproportionate to hand-audit one at a time; the ones the task
  named as examples, W-XSR1 and W-WH2, are both in here and both confirmed
  load-bearing).
- **`research/killa_xbt/`** + `scripts/research_killa_xbt.py` +
  `scripts/backtest_range_structure.py` + `pathiel/indicators/range_structure.py`
  — a deliberately-scoped "research-only" indicator family.
  `tests/test_range_structure.py` has a guard (`banned =
  "pathiel.indicators.range_structure"`) asserting it is never imported by
  live code. Not dead, just fenced off on purpose.
- **`research/market_folklore/`** (6 files) — `tests/test_market_folklore.py`
  dynamically loads `run.py` directly.
- **`research/worldmonitor/`** (2 files) — cited by the live
  `pathiel/agents/news_surge_multi.py`.
- **`research/EVIDENCE_DOCTRINE.md`** — the methodology doctrine
  `tests/test_evidence_doctrine.py` enforces.
- **`pathiel/agents/**`** (all 29 files in cleanup's ownership, i.e. every
  agent module except `executor.py`/`sizing.py`/`risk_gates.py`/
  `hyperfeed.py`) — zero dead modules. Every file is reachable, directly or
  transitively, from `scripts/trading_loop.py`, `scripts/autonomous_cycle.py`,
  `pathiel/server.py`, `pathiel/dashboard.py`, or `pathiel/__main__.py`.
  Verified by hand AND by the new `test_every_cleanup_owned_agent_module_is_reachable`
  guard (which I also confirmed catches a real orphan by injecting one).
  Specifically: **`pathiel/agents/copy_trade_live.py`** is NOT a leftover
  from the copycat product that moved to its own repo
  (~/Documents/code/copycat, 2026-10-02). It is a separate, still-registered
  live book — `("copy_trade", "copy_trade", ...)` in `pathiel/dashboard.py`'s
  `_BOOKS`, imported and called every cycle by `scripts/trading_loop.py`
  (`_copy_trade_maybe_run`), graded by `scripts/autonomous_cycle.py`. It
  mirrors one specific wallet (0xe282) per finding W-CP1 (REFUTED, but live
  "by operator override 2026-09-21" per `trading_loop.py`'s own comment) —
  a one-wallet research-backed book, unrelated to the multi-wallet,
  user-set-leverage copycat product that was ripped out in `b776084`.
- **`services/auth`, `services/demo`, `services/pathiel_data_api`,
  `services/trend_engine`** — all confirmed alive and reachable
  (`services/trend_engine` is the scheduled `trends-price` job;
  `services/demo` is `api/index.py`'s Vercel public-demo generator;
  `services/pathiel_data_api` is a real, documented side product with its
  own Dockerfile/tests/SDK, cited in the root README's auth section;
  `services/auth` is on every dashboard/API request path).
- **`services/events`** — honestly self-documented as "deliberately not
  wired to a book" (its own README), but real: has a test
  (`tests/test_x_budget.py`), writes `.state/events_calendar.json` for a
  human to read, and its `fetch_events.py` is one of the two consumers that
  kept `research/regime_2026_09/C1_session_structure.py` alive. Not dead,
  just manual.
- **Deploy configs** (`Dockerfile`, `docker-entrypoint.sh`, `fly.toml`,
  `k8s/`, `DEPLOY.md`, `vercel.json`, `.vercelignore`, `api/index.py`) — two
  real, parallel, cross-validated deploy paths. `tests/test_deploy_config.py`
  statically asserts `restart.sh`'s four managed processes exist in both
  `fly.toml` and `k8s/statefulset.yaml`, and that the Dockerfile COPYs every
  package the app actually imports. `vercel.json` + `api/index.py` +
  `services/demo` is the separate, intentional public-demo path. Nothing
  here is dead; `socket_full.txt` was already correctly gitignored and never
  tracked, so nothing to do there either.
- **Every `scripts/*.py`** except `install_launchd_jobs.py` — each is
  scheduled (`scheduler.py`'s `JOBS`), managed (`restart.sh`), imported by
  another script/test, or documented. Two (`grade_forward_ledger.py`,
  `phase3_replay.py`) had drifted to zero references once
  `research/regime_2026_09/ev_now.py` — which had cited the former in a
  comment — was pruned in this same sweep; fixed by documenting both in
  `DEPLOY.md`'s "Not containerized" section rather than deleting working
  tools or leaving the new guard test permanently red.
- **Dependencies**: `python-dotenv` looked unused (zero direct `import
  dotenv` in `pathiel/`/`scripts/`/`services/`) until checking
  `pydantic_settings`' own source — `sources/providers/dotenv.py` does
  `from dotenv import dotenv_values`, and `services/pathiel_data_api/app/config.py`
  sets `env_file = ".env"`, so it's a real transitive requirement.
  `msgpack` looked unused in our own code (only a comment in
  `pathiel/client/exchange.py`) until checking the installed
  `hyperliquid-python-sdk`'s `utils/signing.py`, which does `import
  msgpack` directly for the action-hashing/signing scheme — also real.
  Neither was removed. `requirements.txt` already exactly mirrors
  `pyproject.toml`'s runtime deps minus dev extras, by design (its own
  header comment explains why), so no drift to fix there either.
- **`vulture --min-confidence 80`** over every directory in cleanup's
  ownership (`scripts/`, `services/{auth,demo,events,pathiel_data_api,trend_engine}`,
  `pathiel/agents` minus the 4 excluded files) found exactly 3 hits, all
  `signal, frame` / `component` callback-parameter false positives
  (required by the function signature they implement, e.g. a signal
  handler) — no real dead functions.
- **`docs/{AI_BRAIN_OPERATOR_WIRING,ARCHITECTURE,CONFIG,LOGGING,RUNBOOK,SECRETS}.md`**
  — checked each for stale references to `copycat`, `install_launchd_jobs`,
  or the old `pathia` name. None found; all current.

## Bonus fix: a real bug the sweep's own commit caught live

While committing the `web/` removal, `tests/test_preflight_secrets.py::test_env_file_gitignored_and_untracked_passes`
failed under `git commit` (the pre-commit hook) and passed immediately after,
run directly with the identical command. Root cause: `scripts/preflight_secrets.py`'s
`check_env_files_gitignored`/`check_env_files_not_tracked`/
`check_git_index_for_secrets` all shell out to `git` with `cwd=repo_root` but
no `env=` override, so they inherit whatever `GIT_DIR`/`GIT_WORK_TREE`/
`GIT_INDEX_FILE` the parent process has. A git hook sets exactly those,
pointing at the commit in progress — and `scripts/hooks/pre-commit` runs this
file's own test suite as a hook, so every throwaway repo
`tests/test_preflight_secrets.py::_git_repo()` builds inherited the pollution
too.

For the gitignore/tracked checks this was flaky-test noise. For
`check_git_index_for_secrets` — the one check whose entire job is catching a
leaked credential in the git index — it is a real security gap: proved by
hand that `git grep --cached` with `GIT_DIR` pointed at a clean repo and
`cwd` at a repo that DOES track the secret returns "no match" every time,
i.e. a false PASS on an actual leak.

Fixed in `scripts/preflight_secrets.py` (new `_git_subprocess_env()`, applied
to all 4 git subprocess calls) and `tests/test_preflight_secrets.py` (same
helper, applied to all 9 of its own git subprocess calls). Added two
regression tests that monkeypatch `GIT_DIR`/`GIT_WORK_TREE` to this repo's
own git-dir and assert the isolated repo's answer still wins. Verified both
new tests fail without the fix (one via a genuine false-PASS, one that
initially gave a false-pass-for-the-wrong-reason because the shared
`FAKE_PRIVATE_KEY` test constant is itself tracked in this very file — fixed
by generating a `uuid` instead) and pass with it restored. Commit
`dba82bd`.

## Archive tags (not pushed — integrator's call)

| tag | covers |
|---|---|
| `archive/web-nextjs-scaffold-2026-10-17` | `web/` |
| `archive/vendored-caveman-skills-2026-10-17` | `.qwen/`, `.forge/`, `.agents/`, `skills-lock.json` |
| `archive/install-launchd-jobs-2026-10-17` | `scripts/install_launchd_jobs.py` |
| `archive/research-signal-ranking-2026-10-17` | `research/signal_ranking/` |
| `archive/research-rebuild-2026-07-18-2026-10-17` | `research/rebuild_2026_07_18/` |
| `archive/research-regime-2026-09-unused-2026-10-17` | the 18 removed `research/regime_2026_09/*.py` files (C1/H1 untouched, still live) |
| `archive/tracked-runtime-state-2026-10-17` | `pathiel_data.db`, `alerts.json`, `supervisor.json` — convenience pointer only; see "Known trade-offs" below, these were never truly "lost" |

All seven point at `9005b45` (the commit this sweep started from), signed,
not pushed per `SWARM_RULES.md` section 5.

## Known trade-offs / for the integrator

- **`pathiel_data.db`'s one `api_keys` row (a SHA-256 token hash, not a
  plaintext secret) is still recoverable from every pre-2026-10-17 commit on
  every branch that has this history** — `git rm --cached` does not scrub
  it, only stops future commits from re-adding it. If that key is real and
  live, Julien should rotate it. A full history scrub (`git filter-repo` /
  BFG) rewrites every commit hash on every branch and is a much bigger,
  more disruptive call than this agent should make unilaterally — flagging,
  not doing.
- **`research/alpha_swarm/findings/reverse_refuted_direction_audit.md`**
  cites `research/rebuild_2026_07_18/VERIFIED_TRADERS.md` by path in prose.
  That citation is now a dead link (content still recoverable from the
  archive tag above) — a trade-off of archiving the directory, not a
  functional break (nothing imports it).
- **`docs/overhaul/SWARM_RULES.md`'s own ownership table still names
  `install_launchd_jobs.py`** in the section-1 "never run" list. That file
  is outside this agent's ownership (`docs/overhaul/` is explicitly carved
  out of cleanup's `docs/**`), so left alone — a one-line edit for whoever
  owns that file next.
- **`pathiel/indicators/range_structure.py`** is unowned by anyone in the
  SWARM_RULES table (it's under `pathiel/` but not `pathiel/agents/`, and no
  other agent's row names it either). It's not dead — `tests/test_range_structure.py`
  tests it and explicitly guards it OUT of live imports by design — so
  there was nothing to remove, just flagging the ownership gap in case it
  matters later.
- Byte savings here (1.52MB) are smaller than the brief's "890MB
  node_modules" / "176MB research/" figures because those were measured
  against the MAIN checkout's on-disk state (installed `node_modules/`,
  gitignored research caches from runs that happened there) — this fresh
  worktree never had those untracked artifacts to begin with, so there was
  nothing on disk to delete beyond what `git` tracked. The tracked-file and
  tracked-byte reduction above is the real, portable number: what every
  future clone of this repo gets smaller by.

## Tests added

`tests/test_repo_hygiene.py` — 5 new gate tests (no tracked file over 5MB or
containing a NUL byte, no `.qwen`/`.forge`/`.agents` paths, every
`scripts/*.py` justified, every `pathiel/agents/*.py` module reachable).
`tests/test_preflight_secrets.py` — 2 new gate tests (GIT_DIR-immunity
regression). All 7 verified against their own failure mode by injecting it,
confirming red, then confirming green after removing the injection or
restoring the fix. Total suite: 1991 passed, 11 skipped, 24 deselected,
38.00s.

Run them:

    PATHIEL_STATE_DIR="$(mktemp -d)" .venv/bin/python -m pytest -q -p no:cacheprovider
    .venv/bin/python -m pytest tests/test_repo_hygiene.py tests/test_preflight_secrets.py -v
