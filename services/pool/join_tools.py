"""MCP handlers for the research pool's membership tools: pool_status,
pool_join, pool_leave (names and shapes fixed by contracts.pool.MCP_HANDLERS).

Each handler takes the tool's arguments dict and returns exactly one JSON
string; none of them ever raises. A join or leave tool call signs a key only
when the caller already confirmed the shown statement (`confirm=true`), and
the key never appears in the JSON they return.
"""
from __future__ import annotations

import json
from typing import Any, Dict

import contracts.pool as pool
from services.pool import join, signing


def handle_pool_status(params: Dict[str, Any]) -> str:
    """Local read; `params` is unused. Never raises: a broken read reports
    itself instead of crashing the tool call."""
    try:
        return json.dumps(join.status())
    except Exception as e:   # noqa: BLE001 - a tool call must always answer
        return json.dumps({"ok": False, "code": "error", "error": f"{type(e).__name__}: {e}"})


def _confirm_flag(params: Dict[str, Any]) -> bool:
    value = params.get("confirm")
    return value is True or value == "true"


def _handle(kind: str, params: Dict[str, Any]) -> str:
    time_key = "joined_at" if kind == "join" else "left_at"
    try:
        if not _confirm_flag(params):
            info = join.prepare(kind)
            out = {**info, "ok": True, "signed": False,
                  "next": ("Show the user the message exactly. Call again with confirm=true and "
                          f"{time_key}={info[time_key]} only after they say yes.")}
            return json.dumps(out)
        at = params.get(time_key)
        if not at:
            return json.dumps({"ok": False, "code": "no_time", "error": "run it without confirm first"})
        info = join.confirm(kind, at)
        out = {**info, "ok": True, "signed": True, "next": "Give the user pr_url to open the pull request."}
        return json.dumps(out)
    except (pool.PoolError, signing.NoKey) as e:
        code = getattr(e, "code", "no_key")
        out = {"ok": False, "code": code, "error": str(e)}
        if code == "no_key":
            out["master_command"] = join.master_command(kind)
        return json.dumps(out)
    except Exception as e:   # noqa: BLE001 - a tool call must always answer, never crash
        return json.dumps({"ok": False, "code": "error", "error": f"{type(e).__name__}: {e}"})


def handle_pool_join(params: Dict[str, Any]) -> str:
    return _handle("join", params)


def handle_pool_leave(params: Dict[str, Any]) -> str:
    return _handle("leave", params)
