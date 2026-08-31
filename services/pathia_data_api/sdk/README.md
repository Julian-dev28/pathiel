# Pathia Data API — Python SDK

A small, typed client for the Pathia Data API. Bearer auth, `{"data": ...}`
envelope unwrapped for you, RFC7807 problems raised as exceptions.

## Install

The only runtime dependency is `httpx` (already pinned by the project):

```bash
pip install "httpx>=0.27,<1"
```

Then use `pathia_client.py` directly (copy it into your project, or add
`services/pathia_data_api/sdk` to your `PYTHONPATH`):

```python
from pathia_client import PathiaDataClient, PathiaAPIError
```

## Usage

```python
from pathia_client import PathiaDataClient, PathiaAPIError

# base_url is the API root; api_key is your tenant Bearer token.
with PathiaDataClient("https://data.pathia.example", api_key="sk_live_...") as hc:

    # Liveness — no auth needed.
    hc.health()                      # {"status": "ok", ...}

    # OHLCV bars, oldest-first. Backed by OUR Hyperliquid / xyz-token candles.
    bars = hc.ohlc("BTC", interval="1d", limit=100)
    # [{"t": 1700000000000, "o": ..., "h": ..., "l": ..., "c": ..., "v": ...}, ...]

    # First-party momentum signal (trailing return, decimal fraction).
    mom = hc.momentum("BTC", lookback=7)
    # {"coin": "BTC", "lookback": 7, "trailing_return": 0.062}

    # Options net-flow is a LICENSED-DATA ADAPTER SLOT — 501 until wired.
    try:
        flow = hc.net_flow("BTC", date="2026-07-23")
    except PathiaAPIError as e:
        if e.status_code == 501:
            print("net-flow needs a licensed options feed:", e.detail)
        else:
            raise
```

### Errors

Every non-2xx raises `PathiaAPIError`, populated from the server's
`application/problem+json` body:

```python
try:
    hc.ohlc("BTC")
except PathiaAPIError as e:
    e.status_code   # 401 / 429 / 501 / ...
    e.title         # RFC7807 title
    e.detail        # RFC7807 detail (occurrence-specific)
    e.problem       # full parsed problem document (dict)
    e.retry_after   # seconds to wait, on a 429 (float | None)
```

On `429` back off using `e.retry_after` before retrying.

### Bring your own `httpx.Client`

Inject a client to add retries, proxies, or a test transport:

```python
import httpx
transport = httpx.HTTPTransport(retries=3)
hc = PathiaDataClient(base_url, api_key, session=httpx.Client(transport=transport, timeout=15))
```

When you pass `session=`, you own its lifecycle; `hc.close()` won't close it.

### Smoke test against a local server

```bash
python pathia_client.py --base-url http://127.0.0.1:8000 --api-key demo-token --ticker BTC
```

## Auto-generating a full client from OpenAPI

This hand-written client stays deliberately small (health + the three data
endpoints). For a client that tracks **every** endpoint and model automatically,
generate one from the live OpenAPI schema.

FastAPI serves the schema at `GET /openapi.json` (and `app.openapi()` returns the
same dict in-process — handy for committing a snapshot). Generate with
[`openapi-python-client`](https://github.com/openapi-generators/openapi-python-client):

```bash
pip install openapi-python-client

# From a running server:
openapi-python-client generate --url http://127.0.0.1:8000/openapi.json

# ...or from a committed snapshot produced in-process:
python -c "import json; from app.main import create_app; \
print(json.dumps(create_app().openapi()))" > openapi.json
openapi-python-client generate --path openapi.json
```

That emits a fully-typed package (pydantic models per schema, one method per
operation). Use the generated client when you need full coverage; use
`PathiaDataClient` when you want a tiny, readable dependency with sensible
defaults for the common calls.
