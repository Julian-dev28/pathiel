"""No request handler blocks the event loop.

uvicorn serves every request, for every page and every visitor, on one event
loop. A blocking call made inside an `async def` handler (a Hyperliquid read,
a model call, an order, a file read) holds that loop for its whole duration,
and every other request waits behind it.

2026-10-17: dashboard.py's routes did this (coin_chart's candle read); fixed
there with _run_off_loop. 2026-10-19: pathiel/server.py still had 20 such
handlers: every /api/hl/* read, the agent scan (dozens of venue reads),
research (a model call, tens of seconds), execute and the manual order routes.
Each would have frozen the site the way the dashboard polls did. They are
plain `def` now (FastAPI runs those on its thread pool), or read their body on
the loop and hand the work to asyncio.to_thread.

Four layers:
  1. static: every `async def` route in the app's route modules calls nothing
     on the loop beyond an allowlist of in-memory helpers;
  2. runtime: each server.py route's venue, model, order and state calls run
     on a worker thread (the fakes check for a running loop);
  3. behavior: with one shared loop, as in production, /api/health answers
     while a slow venue read is in flight;
  4. the two places where moving work to threads could have changed meaning:
     the scan's 30 s window and the config save's read-merge-write.
"""
from __future__ import annotations

import ast
import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
ROUTE_MODULES = sorted({ROOT / "pathiel" / "server.py", ROOT / "pathiel" / "dashboard.py",
                        *ROOT.glob("services/*/api.py"), *ROOT.glob("services/*/router.py")})
_ROUTE_DECORATORS = {"get", "post", "put", "patch", "delete", "api_route", "websocket"}

# Calls an `async def` handler may make on the loop: nothing here does I/O or
# waits. Anything else belongs in a plain `def` handler or behind an await
# (asyncio.to_thread, dashboard._run_off_loop).
_ALLOWED_CALLS = {
    # building the response
    "JSONResponse", "HTMLResponse", "Response", "StreamingResponse", "RedirectResponse",
    "PlainTextResponse", "HTTPException",
    # builtins
    "len", "str", "int", "float", "bool", "dict", "list", "tuple", "set", "isinstance",
    "max", "min", "abs", "round", "sorted", "any", "all", "getattr",
    # clocks
    "time.time", "time.monotonic",
    # dashboard.py, in memory only
    "_tail_log_sse",         # builds the SSE generator; streaming, it stats the log once a
                             # second and reads only its new lines: small, bounded, on the loop
}
# Methods that only touch an in-memory value, allowed on a handler's own
# local names (its parameters and what it assigned), never on a module.
_LOCAL_METHODS = {"get", "items", "keys", "values", "lower", "upper", "strip", "split",
                  "startswith", "endswith", "join", "format", "copy", "append"}


def _route_handlers(tree: ast.Module):
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and any(
                isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                and d.func.attr in _ROUTE_DECORATORS for d in node.decorator_list):
            yield node


def _local_names(fn: ast.AsyncFunctionDef) -> set:
    names = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
    return names


def _eager_calls(fn: ast.AsyncFunctionDef):
    """Calls evaluated on the loop: not awaited, not inside a lambda or a
    nested def (those run later, wherever they are handed)."""
    awaited = set()
    stack = list(fn.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
            awaited.add(id(node.value))
        if isinstance(node, ast.Call) and id(node) not in awaited:
            yield node
        stack.extend(ast.iter_child_nodes(node))


def _blocking_suspects(tree: ast.Module, label: str):
    for fn in _route_handlers(tree):
        local = _local_names(fn)
        for call in _eager_calls(fn):
            name = ast.unparse(call.func)
            if name in _ALLOWED_CALLS:
                continue
            f = call.func
            if (isinstance(f, ast.Attribute) and f.attr in _LOCAL_METHODS
                    and isinstance(f.value, ast.Name) and f.value.id in local):
                continue
            yield f"{label}:{call.lineno} {fn.name}() calls {name}()"


def test_no_async_route_calls_anything_blocking_on_the_loop():
    found = [s for path in ROUTE_MODULES
             for s in _blocking_suspects(ast.parse(path.read_text()), str(path.relative_to(ROOT)))]
    assert not found, (
        "an `async def` route makes these calls on the event loop, where they hold up "
        "every other request. Make the handler a plain `def` (FastAPI runs it on a worker "
        "thread), or await the work via asyncio.to_thread / dashboard._run_off_loop. If a "
        "call really is in-memory only, allowlist it here with the reason:\n  "
        + "\n  ".join(found))


def test_the_static_check_bites():
    """The checker's own mutation test: the 2026-10-19 server.py shape is
    caught, the fixed shapes are not."""
    bad = ast.parse('''
@app.get("/api/hl/all-mids")
async def get_all_mids():
    mids = fetch_all_mids(include_hip3=_hip3_on())
    return JSONResponse(content=mids)

@app.post("/api/x")
async def x(request: Request):
    body = await request.json()
    state = await asyncio.to_thread(account_state, body.get("u"), _hip3_on())
    return JSONResponse(content={**state, "page": requests.get("https://x.test").text})
''')
    # _hip3_on() reads a file, and runs on the loop even as an argument to
    # to_thread; requests.get is a module's method, not a local value's
    assert sorted(s.split(" calls ")[1] for s in _blocking_suspects(bad, "bad")) == [
        "_hip3_on()", "_hip3_on()", "fetch_all_mids()", "requests.get()"]
    good = ast.parse('''
@app.post("/api/x")
async def x(request: Request):
    body = await request.json()
    out = await asyncio.to_thread(lambda: fetch_all_mids(include_hip3=_hip3_on()))
    return JSONResponse(content={"n": len(out), "u": body.get("u")})
''')
    assert list(_blocking_suspects(good, "good")) == []
    assert any(p.name == "server.py" for p in ROUTE_MODULES)
    assert any(p.parts[-2:] == ("strategies", "api.py") for p in ROUTE_MODULES)


# ── runtime: the work runs on a worker thread ───────────────────────────────

ADDR = "0x" + "ab" * 20


def _local_client(app, **kw) -> TestClient:
    """A loopback caller in local mode, which the session gate lets through
    (tests/test_operator_auth.py::test_session_gate_skipped_for_local_mode)."""
    return TestClient(app, client=("127.0.0.1", 50000), **kw)


def _no_deployment(monkeypatch) -> None:
    from services.auth import integration
    for marker in integration._DEPLOYMENT_MARKERS:
        monkeypatch.delenv(marker, raising=False)


def _on_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


class Calls:
    """Fakes for everything server.py's routes block on; each records whether
    it ran on a thread that is running an event loop."""

    def __init__(self):
        self.seen = []
        self.lock = threading.Lock()

    def fake(self, name, result=None):
        def _fake(*args, **kwargs):
            with self.lock:
                self.seen.append((name, _on_event_loop()))
            return result(*args, **kwargs) if callable(result) else result
        return _fake

    def names(self):
        return {n for n, _ in self.seen}

    def on_loop(self):
        return sorted({n for n, loop in self.seen if loop})


@pytest.fixture
def calls(monkeypatch):
    import pathiel.client.exchange as exchange
    import pathiel.client.hl_client as hl_client
    import pathiel.server as srv
    from pathiel.client import account_snapshot

    c = Calls()
    _no_deployment(monkeypatch)
    monkeypatch.setattr(srv, "resolve_user_address", lambda: ADDR)
    account = {"equity": 15.21, "total_ntl": 0.0, "spot_balances": [],
               "queried_dexes": {"", "xyz"}, "dex_equity": {"": 0.01, "xyz": 15.2},
               "asset_positions": [{"position": {"coin": "BTC", "szi": "0.001", "entryPx": "80000",
                                                 "unrealizedPnl": "0.1"}}]}
    monkeypatch.setattr(account_snapshot, "FETCH", c.fake("account", dict(account)))
    monkeypatch.setattr(account_snapshot, "LIST_DEXES", c.fake("list_dexes", ["xyz"]))
    monkeypatch.setattr(srv, "fetch_account_state", c.fake("fetch_account_state", dict(account)))
    monkeypatch.setattr(srv, "fetch_all_mids", c.fake("fetch_all_mids", {"BTC": "80000"}))
    monkeypatch.setattr(srv, "get_universe", c.fake("get_universe", [{"coin": "BTC"}]))
    monkeypatch.setattr(srv, "fetch_hl_candles", c.fake("fetch_hl_candles", []))
    monkeypatch.setattr(srv, "read_agent_config", c.fake("read_agent_config", {}))
    monkeypatch.setattr(srv, "update_agent_config", c.fake("update_agent_config", {}))
    monkeypatch.setattr(srv, "render_metrics", c.fake("render_metrics", (b"", "text/plain")))
    monkeypatch.setattr(srv.session_log, "append", c.fake("session_log.append"))
    monkeypatch.setattr(srv.session_log, "tail", c.fake("session_log.tail", []))
    monkeypatch.setattr(hl_client, "_http_post", c.fake("_http_post", {"levels": [[], []]}))
    import pathiel.log_setup as _ls
    monkeypatch.setattr(_ls, "check_disk_guard", c.fake(
        "check_disk_guard", SimpleNamespace(critical=False, warn=False,
                                            free_bytes=10 ** 12, log_dir_bytes=0)))
    for name, result in (("get_hl_price", 80000.0), ("set_leverage", None), ("get_hl_atr", 900.0),
                         ("min_entry_notional_usd", 10.0), ("entry_size_for_notional", 0.001),
                         ("place_hl_order", {"ok": True, "avg_px": "80000", "total_sz": "0.001"}),
                         ("place_hl_trigger_order", {"ok": True}), ("cancel_orders", {"ok": True})):
        monkeypatch.setattr(exchange, name, c.fake(name, result))
    return c


ROUTES = [
    # method, path, body, calls that must have happened (off the loop)
    # 2026-10-23 (wave 2): GET/POST /api/agent/state, scan, research/{coin},
    # execute and GET /api/agent/trades are retired with the rest of the
    # autonomous engine (the scan/research/execute pipeline and its memory
    # ledger). /api/agent/session-log and /api/agent/config survive.
    ("GET", "/api/agent/session-log", None, {"session_log.tail"}),
    ("GET", "/api/agent/config", None, {"read_agent_config"}),
    ("POST", "/api/agent/config", {"mode": "OFF"}, {"update_agent_config"}),
    ("GET", "/api/hl/account", None, {"fetch_account_state"}),
    ("GET", "/api/hl/all-mids", None, {"fetch_all_mids"}),
    ("GET", "/api/hl/universe", None, {"get_universe"}),
    ("GET", "/api/hl/price?coin=BTC", None, {"fetch_all_mids"}),
    ("GET", "/api/hl/candles?coin=BTC", None, {"fetch_hl_candles"}),
    ("GET", "/api/hl/portfolio", None, {"fetch_account_state", "fetch_all_mids"}),
    ("GET", "/api/hl/orderbook?coin=BTC", None, {"_http_post"}),
    ("POST", "/api/hl/place-order", {"coin": "BTC", "side": "long", "riskPct": 0.1},
     {"get_hl_price", "set_leverage", "account", "place_hl_order", "place_hl_trigger_order",
      "session_log.append"}),
    ("POST", "/api/hl/close-position", {"coin": "BTC"},
     {"fetch_account_state", "get_hl_price", "place_hl_order", "session_log.append"}),
    ("POST", "/api/hl/cancel-order", {"oid": 7, "coin": "BTC"}, {"cancel_orders"}),
    # 2026-10-25: the loop/feed/ai_brain checks are gone with the engine they
    # watched; the disk guard (a statvfs + a directory walk) is what is left.
    ("GET", "/api/health/system", None, {"check_disk_guard"}),
    ("GET", "/metrics", None, {"render_metrics"}),
]


@pytest.mark.parametrize("method,path,body,expected", ROUTES, ids=[f"{m} {p}" for m, p, _, _ in ROUTES])
def test_each_route_does_its_blocking_work_off_the_loop(calls, method, path, body, expected):
    import pathiel.server as srv
    r = _local_client(srv.app).request(method, path, json=body)
    assert r.status_code in (200, 503), (path, r.status_code, r.text[:300])   # 503: an unhealthy system check
    assert expected <= calls.names(), f"{path} never reached {sorted(expected - calls.names())}"
    assert calls.on_loop() == [], f"{path} blocked the event loop in {calls.on_loop()}"


def test_the_account_route_answers_json(calls):
    """/api/hl/account returned 500 on every call: the state carries a set
    (queried_dexes) and json.dumps refuses sets."""
    import pathiel.server as srv
    r = _local_client(srv.app).get("/api/hl/account")
    assert r.status_code == 200, r.text
    assert sorted(r.json()["queried_dexes"]) == ["", "xyz"]


def test_every_server_route_is_covered_here():
    """A new server.py route gets a row in ROUTES (or is the liveness probe,
    or a PID-file route that touches no venue and no state store)."""
    tree = ast.parse((ROOT / "pathiel" / "server.py").read_text())
    paths = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in node.decorator_list:
                if (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                        and d.func.attr in _ROUTE_DECORATORS and d.args):
                    paths.add(d.args[0].value)
    covered = {p.split("?")[0].replace("/research/BTC", "/research/{coin}") for _, p, _, _ in ROUTES}
    exempt = {"/api/health"}
    assert paths - covered - exempt == set()


# ── behavior: one shared loop, as under uvicorn ─────────────────────────────

def test_a_slow_venue_read_does_not_hold_up_other_requests(monkeypatch):
    """`with TestClient(...)` runs every request on one event loop, the way
    uvicorn does. Before 2026-10-19 /api/health waited the full 0.6 s behind
    /api/hl/all-mids here."""
    import pathiel.server as srv
    started = threading.Event()

    def slow_mids(include_hip3=False):
        started.set()
        time.sleep(0.6)
        return {"BTC": "80000"}
    _no_deployment(monkeypatch)
    monkeypatch.setattr(srv, "fetch_all_mids", slow_mids)
    monkeypatch.setattr(srv, "read_agent_config", lambda: {})
    with _local_client(srv.app) as client:
        slow = threading.Thread(target=client.get, args=("/api/hl/all-mids",))
        slow.start()
        assert started.wait(5), "the slow read never started"
        t0 = time.monotonic()
        assert client.get("/api/health").status_code == 200
        waited = time.monotonic() - t0
        slow.join(5)
    assert waited < 0.3, f"/api/health waited {waited:.2f} s behind a venue read"


# ── meaning kept: the config save ───────────────────────────────────────────
#
# test_two_scans_landing_together_start_one and
# test_a_failed_universe_read_gives_the_window_back (the scan rate-limit
# window's atomic check-and-claim) were removed 2026-10-23 (wave 2):
# POST /api/agent/scan and the `_last_scan_at` window it guarded are retired
# with the rest of the autonomous engine.

class _Body:
    def __init__(self, raw=b""):
        self.raw = raw

    async def body(self):
        return self.raw

    async def json(self):
        import json
        return json.loads(self.raw)


@pytest.mark.parametrize("raw,empty_ok,expect", [
    (b'{"a": 1}', False, {"a": 1}),
    (b"", True, {}),
    (b"", False, "invalid JSON"),
    (b"{", False, "invalid JSON"),
    (b"[1, 2]", False, "JSON body must be an object"),
])
def test_the_body_helper_answers_400_for_anything_but_an_object(raw, empty_ok, expect):
    """_json_object replaced six copies of the parse; a list body used to be a
    500 (AttributeError on .get) on the order routes."""
    import pathiel.server as srv
    from fastapi import HTTPException
    if isinstance(expect, dict):
        assert asyncio.run(srv._json_object(_Body(raw), empty_ok=empty_ok)) == expect
        return
    with pytest.raises(HTTPException) as err:
        asyncio.run(srv._json_object(_Body(raw), empty_ok=empty_ok))
    assert err.value.status_code == 400 and err.value.detail == expect


# ── grading background task (PATHIEL_GRADE_LOOP) ────────────────────────────

def test_grade_loop_runs_thesis_then_trade_grading_on_an_interval(monkeypatch):
    """The lifespan-started background task (server.py's _grade_loop) calls
    facade.grade_due() THEN facade.grade_due_trades() every sweep, both via
    asyncio.to_thread so the shared loop is never held for the call's
    duration, and survives a sweep that raises."""
    import pathiel.server as srv
    import services.strategies.facade as facade

    calls = []

    def fake_grade_due():
        calls.append(("theses", _on_event_loop()))
        return 3

    def fake_grade_due_trades(desk_id=None):
        calls.append(("trades", _on_event_loop()))
        return 2

    _no_deployment(monkeypatch)
    monkeypatch.setenv("PATHIEL_GRADE_LOOP", "1")
    monkeypatch.setenv("PATHIEL_GRADE_INITIAL_DELAY_S", "0")
    monkeypatch.setenv("PATHIEL_GRADE_INTERVAL_MIN", "0.001")   # ~0.06s between sweeps
    monkeypatch.setattr(facade, "grade_due", fake_grade_due)
    monkeypatch.setattr(facade, "grade_due_trades", fake_grade_due_trades)
    monkeypatch.setattr(srv, "read_agent_config", lambda: {})

    with _local_client(srv.app):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and sum(1 for n, _ in calls if n == "trades") < 2:
            time.sleep(0.02)

    assert sum(1 for n, _ in calls if n == "theses") >= 2
    assert sum(1 for n, _ in calls if n == "trades") >= 2
    assert all(not on_loop for _, on_loop in calls), "grading ran on the event loop, not a worker thread"
    # Every sweep grades theses before trades.
    first_two = [n for n, _ in calls[:2]]
    assert first_two == ["theses", "trades"]


def test_grade_loop_survives_a_failing_sweep(monkeypatch):
    """A sweep that raises (a bad desk directory, a 429) is logged and the
    loop tries again next interval instead of dying."""
    import pathiel.server as srv
    import services.strategies.facade as facade

    attempts = []

    def flaky_grade_due():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("boom")
        return 0

    _no_deployment(monkeypatch)
    monkeypatch.setenv("PATHIEL_GRADE_LOOP", "1")
    monkeypatch.setenv("PATHIEL_GRADE_INITIAL_DELAY_S", "0")
    monkeypatch.setenv("PATHIEL_GRADE_INTERVAL_MIN", "0.001")
    monkeypatch.setattr(facade, "grade_due", flaky_grade_due)
    monkeypatch.setattr(facade, "grade_due_trades", lambda desk_id=None: 0)
    monkeypatch.setattr(srv, "read_agent_config", lambda: {})

    with _local_client(srv.app):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and len(attempts) < 2:
            time.sleep(0.02)

    assert len(attempts) >= 2, "the loop stopped retrying after the first sweep raised"


def test_grade_loop_off_by_default_in_the_test_session():
    """conftest.py sets PATHIEL_GRADE_LOOP=0 so the gate suite never starts
    this background task against real services.strategies state."""
    import os
    assert os.environ.get("PATHIEL_GRADE_LOOP") == "0"


def test_two_config_saves_landing_together_keep_both_changes(monkeypatch):
    """On the loop two saves could not interleave. On worker threads, without
    the lock, both read the old config and the second write drops the first
    save's change."""
    import pathiel.server as srv
    from pathiel.agents import config_store as cs
    store = {"cfg": {"mode": "OFF"}}

    def write(cfg):
        time.sleep(0.1)                    # a save that takes a moment
        store["cfg"] = dict(cfg)
    # _merge_config -> config_store.update_agent_config, which reads the SAVED
    # file and writes it back; fake both ends of that file.
    monkeypatch.setattr(cs, "read_agent_config_file", lambda: dict(store["cfg"]))
    monkeypatch.setattr(cs, "read_agent_config", lambda: dict(store["cfg"]))
    monkeypatch.setattr(cs, "write_agent_config", write)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(srv._merge_config, [{"a": 1}, {"b": 2}]))
    assert store["cfg"] == {"mode": "OFF", "a": 1, "b": 2}
