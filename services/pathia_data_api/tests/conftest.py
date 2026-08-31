"""Pytest fixtures for the Pathia Data API.

Everything here is offline and hermetic:

  * The app is pointed at a throwaway sqlite file (its own tmp dir) via
    ``PATHIA_DATABASE_URL`` **before** ``app`` is imported, so the module-level
    engine in ``app.db`` binds to the test DB and never touches the real one.
  * The Hyperliquid resource seam (``app.resources.hl_resource.get_ohlc``) is
    monkeypatched to a deterministic async stub, so no test hits the network.
  * A demo API key is seeded so ``Authorization: Bearer demo-token`` authenticates.

``app.main`` is imported defensively. The integration owner wires ``main.py``
last; until then these fixtures ``pytest.skip`` with a clear message instead of
erroring the whole collection.
"""
from __future__ import annotations

import os
import tempfile

import pytest

# --------------------------------------------------------------------------- #
# 1. Environment — MUST be set before `app` is imported.
#    `app.db` builds its Engine at import time from `get_settings().database_url`,
#    and `get_settings()` is lru-cached, so the env has to win before that runs.
# --------------------------------------------------------------------------- #
_TMPDIR = tempfile.mkdtemp(prefix="pathia_data_api_test_")
_DB_PATH = os.path.join(_TMPDIR, "test.db")

os.environ["PATHIA_DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["PATHIA_ENV"] = "test"
os.environ["PATHIA_LOG_JSON"] = "false"          # human-readable test logs
# Rate-limit knobs: high enough that ordinary 1-2 request tests never trip,
# low enough that the dedicated hammer test below reliably exceeds the burst.
os.environ["PATHIA_RATE_LIMIT_PER_MIN"] = "120"
os.environ["PATHIA_RATE_LIMIT_BURST"] = "30"
# The licensed options-flow adapter slot MUST stay unset so net-flow 501s
# honestly (clean-room: we never fabricate a feed we don't license).
os.environ.pop("PATHIA_OPTIONS_FLOW_UPSTREAM", None)

DEMO_TOKEN = "demo-token"

# Drop any settings cached by an earlier import so the tmp DB URL wins.
try:  # pragma: no cover - defensive
    from app.config import get_settings as _get_settings

    _get_settings.cache_clear()
except Exception:  # noqa: BLE001 - config may not import yet; the guard below reports it
    pass

# --------------------------------------------------------------------------- #
# 2. Import the app defensively. If main.py isn't wired yet, record why and let
#    the fixtures skip with that message rather than exploding at collection.
# --------------------------------------------------------------------------- #
_IMPORT_ERROR: Exception | None = None
create_app = None
init_db = None
ApiKeyStore = None
hl_resource = None

try:
    from app.main import create_app  # type: ignore[no-redef]
    from app.db import ApiKeyStore, init_db  # type: ignore[no-redef]
    from app.resources import hl_resource  # type: ignore[no-redef]
except Exception as exc:  # noqa: BLE001 - integration owner wires main.py last
    _IMPORT_ERROR = exc


def _require_app() -> None:
    """Skip the requesting test cleanly if the app couldn't be imported."""
    if _IMPORT_ERROR is not None or create_app is None:
        pytest.skip(
            "app.main not importable yet (integration owner wires main.py last): "
            f"{type(_IMPORT_ERROR).__name__ if _IMPORT_ERROR else 'create_app is None'}"
            f": {_IMPORT_ERROR}"
        )


# --------------------------------------------------------------------------- #
# 3. Deterministic, network-free OHLC stub for the Hyperliquid seam.
# --------------------------------------------------------------------------- #
async def _stub_get_ohlc(coin: str, interval: str = "1d", limit: int = 100) -> list[dict]:
    """Return `limit` synthetic bars, oldest-first, +1%/bar so momentum is a
    known positive number and OHLC has a stable, assertable shape."""
    n = max(1, min(int(limit), 400))
    base_ms = 1_700_000_000_000
    day_ms = 86_400_000
    price = 100.0
    bars: list[dict] = []
    for i in range(n):
        price *= 1.01
        bars.append(
            {
                "t": base_ms + i * day_ms,
                "o": round(price * 0.995, 6),
                "h": round(price * 1.010, 6),
                "l": round(price * 0.990, 6),
                "c": round(price, 6),
                "v": 1000.0 + i,
            }
        )
    return bars


# --------------------------------------------------------------------------- #
# 4. Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _patch_hl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the HL resource for every test so nothing can reach the network.

    No-op when the app didn't import (the test will skip in the `client`
    fixture); patching a missing module would raise instead of skip.
    """
    if _IMPORT_ERROR is not None or hl_resource is None:
        return
    monkeypatch.setattr(hl_resource, "get_ohlc", _stub_get_ohlc)


@pytest.fixture(scope="session")
def _db_ready() -> None:
    """Create the schema and seed the demo key once for the session."""
    _require_app()
    assert init_db is not None and ApiKeyStore is not None
    init_db()
    ApiKeyStore.seed_demo_key(DEMO_TOKEN)


@pytest.fixture
def client(_db_ready):  # noqa: ANN001, ANN201 - TestClient type is fine untyped here
    """A TestClient over a freshly-built app, sharing the seeded test DB.

    Uses the context-manager form so FastAPI startup/shutdown (lifespan) runs.
    """
    _require_app()
    from fastapi.testclient import TestClient

    assert create_app is not None
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {DEMO_TOKEN}"}


@pytest.fixture
def api_key_store():  # noqa: ANN201 - returns the ApiKeyStore class
    """The seed/lookup surface, for tests that need a bespoke key (e.g. rate limit)."""
    _require_app()
    return ApiKeyStore
