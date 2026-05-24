FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install deps first so `pip install` is cached across code changes.
COPY pyproject.toml ./
COPY hermes_trader/__init__.py hermes_trader/__init__.py
RUN pip install -e .

# Copy the rest of the source.
COPY hermes_trader/ hermes_trader/
COPY scripts/ scripts/
COPY conftest.py ./

# State lives on a Fly volume mounted at /data; defaults are overridden via env
# in fly.toml so the loop + server + MCP all share one source of truth.
RUN mkdir -p /data
ENV SESSION_LOG_PATH=/data/session-log.jsonl \
    HERMES_DSL_STATE_FILE=/data/.dsl-state.json \
    HERMES_AGENT_CONFIG_FILE=/data/.agent-config.json \
    HERMES_AGENT_MEMORY_FILE=/data/.agent-memory.json

EXPOSE 8000

# Default command runs the FastAPI server (dashboard + API). The trading loop
# runs as a separate Fly process — see [processes] in fly.toml.
CMD ["python3", "-m", "hermes_trader.server"]
