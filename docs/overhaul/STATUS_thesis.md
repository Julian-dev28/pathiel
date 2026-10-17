# thesis agent status

DONE. `services/llm/` (CLI gateway) + `services/thesis/` (write_thesis) built,
tested, evaluated twice against the real CLI. Final eval: mean score 0.857
(threshold 0.85, PASS) on oil fixture + synthetic btc. 206 offline unit/e2e
tests (48 llm + 158 thesis), full repo gate green (2197 passed).

Known gap: `tokens_within_budget` still fails both desks (output
~2800-4000 tokens vs 1800 budget) despite schema length caps + prompt
terseness instruction - partially mitigated, not solved. See
services/thesis/README.md "Real-eval findings" for the fix log and next
step.

Run tests: `PATHIEL_STATE_DIR="$(mktemp -d)" .venv/bin/python -m pytest
services/llm/tests services/thesis/tests -q`

Run eval (paid): `PATHIEL_STATE_DIR="$(mktemp -d)" .venv/bin/python -m
services.thesis.evals.run_eval`

For the integrator: Dockerfile needs `COPY services/llm/ services/llm/` +
`COPY services/thesis/ services/thesis/` (added by me, outside my
ownership, to keep tests/test_deploy_config.py green - see commit).
