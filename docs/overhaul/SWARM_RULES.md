# Overhaul swarm rules (2026-10-17)

Every agent reads this first. Breaking a rule in section 1 can lose real money.

## 1. Live money

`.env.local` holds live Hyperliquid keys. The account trades real money.

- Never place, modify or cancel an order. Never call `execute`, `close_position`,
  `set_leverage`, `cancel_order`, or the `/api/hl/place-order`, `/api/hl/cancel-order`,
  `/api/hl/close-position`, `/api/agent/execute`, `/api/agent/start` routes.
- Never run `scripts/trading_loop.py`, `scheduler.py`, `autonomous_cycle.py`,
  `monitor_loop.py`, `supervise_processes.py`, `restart.sh`, `preflight_live.py`,
  `funded_dry_run.py`, `treasury.py`, `install_launchd_jobs.py`, `record_capital_flows.py`.
- Never run `pytest -m live`. Never set `PATHIEL_E2E=1`.
- Never write to the live state dir. Export a throwaway one for every test, script
  or server you start: `export PATHIEL_STATE_DIR="$(mktemp -d)"`.
  Reading live state is allowed only by copying it to a temp dir first.
- Never edit `.env.local`. Never print secret values.
- Read-only network is fine: Hyperliquid public `/info`, news sites, free public APIs.
- A dev server you start binds 127.0.0.1 on a port in 8700-8799 and is killed before you finish.

## 2. Worktree setup

    ln -s /Users/julian_dev/Documents/code/pathiel/.venv .venv   # at worktree root, once
    export PATHIEL_STATE_DIR="$(mktemp -d)"
    PY=.venv/bin/python

`.venv` is gitignored. Do not commit it. Playwright and Chromium are installed in that venv.

## 3. LLM

Local Claude Code CLI only: `/Users/julian_dev/.local/bin/claude -p`. No hosted API.
Default model is the best available one; verify the id with a one-line ping before
hardcoding it. Gate tests never call an LLM; use fakes. Keep real calls during
development to a handful; they cost money and time.

## 4. Tests (the user asked for extensive unit AND e2e tests)

- Gate tests: offline, deterministic, fast. Every new module gets unit tests.
- `@pytest.mark.online` for real network reads. `@pytest.mark.browser` for Playwright e2e.
- Every user-facing flow you add or change gets a Playwright e2e test.
- Every feature with an LLM step gets an eval suite under `services/<svc>/evals/`
  with a pass threshold, runnable as a script.
- Before your final commit, all green:

      $PY -m ruff check .
      PATHIEL_STATE_DIR="$(mktemp -d)" $PY -m pytest -q -x -p no:cacheprovider
      PATHIEL_STATE_DIR="$(mktemp -d)" $PY -m pytest -q -m browser -p no:cacheprovider <your browser tests>

## 5. Git

- Commit on your worktree branch through the pre-commit hook. `--no-verify` is banned.
- Do not push. Do not merge. Do not rebase onto other branches. The integrator merges.
- Commit message trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
- Stay inside the files you own (section 6). Need a change elsewhere? Put it in your
  final report under "for the integrator" with the exact diff.
- Root `README.md` and `CLAUDE.md` are integrator-only. `pyproject.toml`: you may add
  markers or deps; list them in your report.

## 6. Ownership

| agent | owns |
|---|---|
| desk-core | `services/strategies/**`, non-breaking additions to `contracts/strategies/**` |
| intel | `services/intel/**` |
| thesis | `services/llm/**`, `services/thesis/**` |
| desk-ui | `pathiel/templates/desks.html`, `pathiel/templates/desk.html`, `pathiel/static/desks.*`, `tests/browser/test_desks_*.py`, `tests/test_desks_ui*.py` |
| ux-qa | every other template, `pathiel/static/*` except desks.*, `pathiel/dashboard.py`, `pathiel/server.py`, `services/wallet_ui/**`, `tests/browser/**` except desks, `tests/test_dashboard*.py` |
| cleanup | repo-root files, `web/`, `.qwen/`, `.forge/`, `.agents/`, `docs/**` except `docs/overhaul/`, `research/**`, deploy configs, `scripts/**` except `pathiel-mcp-server.py`, `pathiel/agents/**` except executor.py/sizing.py/risk_gates.py, `services/{auth,demo,events,pathiel_data_api,trend_engine}/**` |
| mcp | `scripts/pathiel-mcp-server.py`, `pathiel/client/hl_client.py`, `pathiel/agents/hyperfeed.py`, `tests/test_mcp*.py` |
| profit | `pathiel/agents/executor.py`, `pathiel/agents/sizing.py`, `pathiel/agents/risk_gates.py`, `pathiel/client/exchange.py`, `scripts/profit_audit.py`, `docs/overhaul/PROFIT_AUDIT.md` |

## 7. User

Julien. Has ADHD. Wants one decision per screen, the call first, details on demand.
Goal: become a master oil trader. Write for him: direct, short, concrete, numbers.
No em dashes. No AI filler words (delve, robust, comprehensive, crucial, ...).

## 8. Final report (your last message)

Branch name. Commit hashes. What changed and why, one line each. Tests added
(unit/e2e/eval counts) and the exact commands to run them. Anything left undone and
why. "For the integrator" section with required cross-ownership edits.
