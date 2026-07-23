# syntax=docker/dockerfile:1

# ---------- builder: install deps into an isolated venv ----------
FROM python:3.12-slim AS builder

# Build deps for psycopg2-binary et al. (kept out of the final image).
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create the venv we'll carry into the runtime stage.
ENV VIRTUAL_ENV=/opt/venv
RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /build

# Install deps first so the layer caches unless requirements change.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ---------- runtime: slim image, non-root, only what's needed ----------
FROM python:3.12-slim AS runtime

# Don't buffer stdout/err (logs stream immediately); no .pyc clutter.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    HERMES_ENV=prod \
    HERMES_LOG_LEVEL=INFO

# Non-root runtime user.
RUN groupadd --system --gid 1000 hermes \
    && useradd --system --uid 1000 --gid hermes --no-create-home --home /app hermes

WORKDIR /app

# Carry the venv (deps only) from the builder — no build toolchain in runtime.
COPY --from=builder /opt/venv /opt/venv

# Copy only the application package. WORKDIR=/app + package at /app/app
# makes `app.main:app` importable.
COPY --chown=hermes:hermes app ./app

USER hermes

EXPOSE 8080

# Fails the container health if /health stops answering. Uses stdlib urllib
# (no curl in slim). Non-2xx / connection error -> exit 1 -> unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4).status == 200 else 1)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
