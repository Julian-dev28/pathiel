"""MCP handler for `pool_report` (contracts.pool.MCP_HANDLERS). Never raises:
every failure comes back as one JSON string `{"ok": false, "code": ..., "error": ...}`."""
from __future__ import annotations

import json
from typing import Any, Dict

import contracts.pool as pool
from services.pool import account as ACC
from services.pool import report as RPT
from services.pool.venue import VenueError


def handle_pool_report(params: Dict[str, Any]) -> str:
    try:
        address = ACC.configured_account()
        if not address:
            return json.dumps({"ok": False, "code": "no_account",
                               "error": "no account is configured on this install"})
        return json.dumps(RPT.personal(address))
    except pool.PoolError as e:
        return json.dumps({"ok": False, "code": e.code, "error": str(e)})
    except VenueError as e:
        return json.dumps({"ok": False, "code": "venue", "error": str(e)})
    except Exception as e:   # noqa: BLE001 - a handler must never raise
        return json.dumps({"ok": False, "code": "error", "error": f"{type(e).__name__}: {e}"})
