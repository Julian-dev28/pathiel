FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install deps first so `pip install` is cached across code changes. hatchling
# only needs pyproject.toml + pathia/__init__.py (it reads the package
# version from __init__.py) to resolve the full dependency set — including
# `requests` and `websocket-client`, which pyproject.toml never lists
# directly but which land anyway as transitive deps of hyperliquid-python-sdk
# (verified 2026-08-29: `pip show hyperliquid-python-sdk` -> Requires:
# eth-account, eth-utils, msgpack, requests, websocket-client). No compiler
# needed on this base image — every dependency ships a manylinux wheel.
COPY pyproject.toml ./
COPY pathia/__init__.py pathia/__init__.py
RUN pip install -e .

# Copy every top-level source package a managed process actually imports at
# runtime. `scripts/restart.sh` is the source of truth for what runs:
#   pathia/              core package — server, dashboard, agents, v2,
#                                client, data_providers (imported by every process below)
#   scripts/                    trading_loop.py, scheduler.py, log_rotate.py,
#                                autonomous_cycle.py, and CLI tooling
#   services/trend_engine/      /trends lanes (services.trend_engine.run —
#                                imported by dashboard.py, scheduler.py)
# `services/pathia_data_api` is its OWN deploy unit — own Dockerfile, own
# Postgres, own requirements.txt (sqlalchemy/psycopg2, never installed here)
# — deliberately NOT bundled into this image. `research/` (159MB, dev-only
# artifacts — nothing under pathia/scripts/services imports it at
# runtime), `docs/`, `tests/`, and `skills/` are excluded via .dockerignore;
# see that file for the full list and why.
COPY pathia/ pathia/
COPY scripts/ scripts/
COPY services/trend_engine/ services/trend_engine/
COPY conftest.py ./

# ── Runtime state ────────────────────────────────────────────────────────────
# State lives on a persistent volume mounted at /data (fly.toml [[mounts]] /
# k8s volumeClaimTemplates) so the loop, server, scheduler, and rotator all
# share one source of truth across restarts and redeploys.
#
# Almost every state path in this app routes through PATHIA_STATE_DIR (see
# pathia/agents/rebalancer_owned.py:state_file — the claims registry,
# per-strategy timers, and pathia/agents/shadow_ledger.py's
# <state>/shadow_ledger/<book>.jsonl all resolve relative to it; also
# services/trend_engine/env.py).
# Pointing that ONE var at /data/.state redirects all of them together.
#
# scripts/scheduler.py's own job-run bookkeeping (.state/scheduler.json —
# last_run per job, so a restart doesn't refire every job's catch-up logic
# and burn its LLM/API budget) is the one holdout: it hardcodes
# `<repo root>/.state` at import time and does not read PATHIA_STATE_DIR.
# scripts/scheduler.py is out of this workstream's ownership (belongs to the
# supervision workstream), so the fix lives here instead: a symlink that
# makes its hardcoded path resolve onto the same volume. docker-entrypoint.sh
# creates the real /data/.state directory at container start (the volume is
# empty/fresh at that point, not at image-build time, so it can't be created
# here). If scheduler.py is ever made PATHIA_STATE_DIR-aware, this symlink
# becomes a no-op and can be deleted.
RUN mkdir -p /data && ln -s /data/.state /app/.state

ENV SESSION_LOG_PATH=/data/session-log.jsonl \
    PATHIA_DSL_STATE_FILE=/data/.dsl-state.json \
    PATHIA_AGENT_CONFIG_FILE=/data/.agent-config.json \
    PATHIA_AGENT_MEMORY_FILE=/data/.agent-memory.json \
    PATHIA_STATE_DIR=/data/.state \
    PATHIA_POSITIONS_SNAPSHOT_FILE=/data/.positions-snapshot.json

# ── Networking ───────────────────────────────────────────────────────────────
# pathia/server.py defaults PATHIA_BIND to 127.0.0.1 (a 2026-07-10
# security fix for running bare on an operator's Mac, so the dashboard isn't
# reachable from the LAN by default). Inside a container that default makes
# the process unreachable from anywhere OUTSIDE its own network namespace —
# `docker run -p`, Fly's http_service checks, and k8s probes/Services all
# connect from outside it, so the request never lands and every health check
# times out. 0.0.0.0 is correct and safe here: the container/pod boundary is
# the actual security perimeter (Fly's edge proxy / the k8s Service), not the
# loopback interface.
ENV PATHIA_BIND=0.0.0.0

# AI features (pathia/agents/ai_brain.py research calls, the
# Polymarket forecaster, the /trends narrative pass) default to
# AI_BRAIN_PROVIDER=openrouter — a plain HTTPS call authenticated by
# OPENROUTER_API_KEY. The alternative `claude_cli` provider shells out to a
# LOCALLY AUTHENTICATED Claude Code CLI session (an operator's desktop
# subscription — not a credential that can be baked into an image or passed
# as a secret) and cannot run in a stateless cloud container as configured;
# see DEPLOY.md "AI provider" for the tradeoff and what still degrades
# gracefully (services/trend_engine/ai.py's narrative pass) vs. what needs
# openrouter to function at all (the Polymarket forecaster's default path).
ENV AI_BRAIN_PROVIDER=openrouter

# Every managed process shares this one bootstrap step (see the script for
# why) before running its real command.
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["docker-entrypoint.sh"]

EXPOSE 8000

# Default command runs the FastAPI server (dashboard + API). The trading
# loop, scheduler, and log rotator each run as separate
# Fly processes / k8s containers sharing this same image — see
# fly.toml [processes] and k8s/statefulset.yaml.
CMD ["python3", "-m", "pathia.server"]
