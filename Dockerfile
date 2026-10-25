# Base image pinned by digest (the multi-arch index for python:3.11-slim,
# Debian 13, resolved 2026-10-25). A tag is a pointer Docker Hub can move; the
# digest is the bytes. Bump it on purpose:
#   docker buildx imagetools inspect python:3.11-slim | grep Digest
FROM python:3.11-slim@sha256:b7a9e5f6a93d6d4a31f129bbd4bdee69efccb6ded0fb230d78aafaf37af98377 AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# The app runs as this user, never as root (security review 2026-10-25).
# docker-entrypoint.sh starts as root only where it has to (a Fly volume is
# mounted root-owned), hands /data to this user, and drops to it with setpriv
# before exec. k8s sets runAsUser to the same uid, so there not even the
# entrypoint has root.
RUN groupadd --system --gid 10001 pathiel \
 && useradd --system --uid 10001 --gid pathiel --home-dir /app --shell /usr/sbin/nologin pathiel

WORKDIR /app

# Install deps first so `pip install` is cached across code changes.
#
# From requirements.lock, hash-checked, then the package itself with
# --no-deps. Until 2026-10-25 this was a bare `pip install -e .`, which
# resolved the RANGES in pyproject.toml fresh on every build: the image ran
# whatever PyPI served that day, not the set CI tested, and nothing checked a
# single file's hash. (requests and websocket-client still arrive as
# transitive deps of hyperliquid-python-sdk; the lock lists them.) The lock
# also carries the dev extras (pytest, ruff); they cost a few MB and keep one
# lock for CI and prod. No compiler needed: every pin ships a manylinux wheel.
COPY requirements.lock ./
RUN pip install --require-hashes -r requirements.lock
COPY pyproject.toml ./
COPY pathiel/__init__.py pathiel/__init__.py
RUN pip install --no-deps -e .

# Copy every top-level source package a managed process actually imports at
# runtime. `scripts/restart.sh` is the source of truth for what runs:
#   pathiel/              core package — server, dashboard, agents, client
#                                (imported by every process below)
#   scripts/                    the MCP server, log_rotate.py, the desk-tick
#                                tools (desk_slot.py, oil_levels.py), CLI tooling
#   services/intel/             desk news/policy/catalyst/odds/positioning
#                                packet (services.intel.api.build_packet —
#                                imported by services/strategies)
# `services/pathiel_data_api` is its OWN deploy unit — own Dockerfile, own
# Postgres, own requirements.txt (sqlalchemy/psycopg2, never installed here)
# — deliberately NOT bundled into this image. `docs/`, `tests/`, and
# `skills/` are excluded via .dockerignore; see that file for the full list
# and why. (`services/trend_engine` and `research/` were deleted 2026-10-25.)
COPY pathiel/ pathiel/
# The shared pydantic contracts every service imports at module load
# (pathiel.agents.config_store -> contracts.risk.models on the server's
# first import). Missing until 2026-10-25: the image built fine and
# `python -m pathiel.server` died with ModuleNotFoundError.
COPY contracts/ contracts/
COPY scripts/ scripts/
# Auth is on the request path for every gated route, so a missing copy here is
# not a degraded feature, it is a server that 500s on the first page load.
COPY services/auth/ services/auth/
# server.py mounts this router unconditionally (/api/desks, /desks/...) —
# same "500 on first import" reasoning as auth above.
COPY services/strategies/ services/strategies/
# Zero-LLM news/policy/catalyst/odds/positioning packet services/strategies
# imports (services.intel.api.build_packet) — same reasoning again.
COPY services/intel/ services/intel/
# LLM gateway (local Claude Code CLI only) and the desk thesis writer on top
# of it. The image has no claude CLI, so in a container write_thesis degrades
# to the previous thesis or a flat placeholder instead of crashing.
COPY services/llm/ services/llm/
COPY services/thesis/ services/thesis/
# Account registry, vault and credentials (the connect flow). server.py mounts
# services.accounts.api, so it must ship: same reasoning as auth above.
COPY services/accounts/ services/accounts/
# server.py mounts services.risk.api (risk_limits/risk_size_trade and the
# place_bracket feasibility check both derive through it).
COPY services/risk/ services/risk/
COPY conftest.py ./

# ── Runtime state ────────────────────────────────────────────────────────────
# State lives on a persistent volume mounted at /data (k8s
# volumeClaimTemplates, or `docker run -v`) so `web` keeps one source of truth
# across restarts and redeploys.
#
# Almost every state path in this app routes through PATHIEL_STATE_DIR (see
# pathiel/state.py:state_file — backup receipts and other process-local
# state files all resolve relative to it; also services/*/env.py).
# Pointing that ONE var at /data/.state redirects all of them together.
# docker-entrypoint.sh creates the real /data/.state directory at container
# start (the volume is empty/fresh at that point, not at image-build time).
#
# 2026-10-23 (wave 2): PATHIEL_DSL_STATE_FILE and PATHIEL_AGENT_MEMORY_FILE
# are dropped — pathiel.agents.dsl_exit and pathiel.agents.memory, their only
# readers, are retired with the rest of the autonomous engine. The
# scripts/scheduler.py symlink workaround this section used to carry is
# dropped the same wave: scheduler.py itself is deleted. 2026-10-25:
# PATHIEL_POSITIONS_SNAPSHOT_FILE goes too — the loop that wrote the snapshot
# is gone, so the dashboard reads positions live (cached) and the module
# that read the file is deleted.

# HOME on the volume: pathiel/client/universe.py caches under ~/.pathiel and
# scripts/backup_state.py defaults to ~/pathiel-backups. As root HOME was
# /root; the app user may not write there, and on k8s the root filesystem is
# read-only, so the one writable, persistent place is /data.
ENV HOME=/data/home

ENV SESSION_LOG_PATH=/data/session-log.jsonl \
    PATHIEL_AGENT_CONFIG_FILE=/data/.agent-config.json \
    PATHIEL_STATE_DIR=/data/.state

# ── Networking ───────────────────────────────────────────────────────────────
# pathiel/server.py defaults PATHIEL_BIND to 127.0.0.1 (a 2026-07-10
# security fix for running bare on an operator's Mac, so the dashboard isn't
# reachable from the LAN by default). Inside a container that default makes
# the process unreachable from anywhere OUTSIDE its own network namespace —
# `docker run -p` and k8s probes/Services all connect from outside it, so the
# request never lands and every health check times out. 0.0.0.0 is correct
# and safe here: the container/pod boundary is the actual security perimeter
# (the k8s Service, or whatever fronts `docker run`), not the loopback
# interface.
ENV PATHIEL_BIND=0.0.0.0

# No LLM credential is baked in or expected. The one LLM step (the desk thesis
# writer, services/thesis via services/llm) shells out to a LOCALLY
# AUTHENTICATED Claude Code CLI session: an operator's desktop subscription,
# not a credential that can be passed as a secret. In a container
# desk_refresh degrades to the previous thesis or a flat placeholder; the
# operator refreshes theses from their own machine through the MCP server.
# (The AI_BRAIN_PROVIDER / OPENROUTER_API_KEY pair this section used to set
# left with pathiel/agents/ai_brain.py on 2026-10-25.)

# Every managed process shares this one bootstrap step (see the script for
# why) before running its real command.
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh \
 && mkdir -p /app/logs /data/.state /data/home \
 && chown -R pathiel:pathiel /app/logs /data
# Source stays root-owned and read-only to the app user: code that is
# compromised at runtime cannot rewrite itself for the next restart. Only
# /app/logs (log_rotate.py) and /data (state) are the app's to write.
ENTRYPOINT ["docker-entrypoint.sh"]

EXPOSE 8000

# Default command runs the FastAPI server (dashboard + API). The log rotator
# runs as a second k8s container sharing this same image — see
# k8s/statefulset.yaml. (There is no trading loop or scheduler process since
# 2026-10-23; Claude is the trader, via the MCP server on the operator's
# machine. Fly.io was dropped as a target 2026-10-25.)
CMD ["python3", "-m", "pathiel.server"]
