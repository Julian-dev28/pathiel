"""Cross-process positions snapshot.

The trading loop fetches the full account state (`fetch_account_state`,
~9 HTTP POSTs across the main + HIP-3 clearinghouses) every cycle. The web
dashboard used to fetch the SAME state independently on every poll — two
processes sharing one IP, neither's rate-limiter aware of the other, which
collectively tripped Hyperliquid's per-IP weight limit (429s + read timeouts).

The loop already paid for that fetch, so it now writes the raw position list
to a small snapshot file each cycle. The dashboard reads the snapshot instead
of calling HL, making it a pure file reader for the positions view. Only the
loop talks to HL → the cross-process contention is gone. The snapshot is at
most one loop-cycle stale (~60s), which is invisible for a bot that holds
positions for hours.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOT_FILE = os.environ.get(
    "HERMES_POSITIONS_SNAPSHOT_FILE",
    os.path.join(_REPO_ROOT, ".positions-snapshot.json"),
)


def write_snapshot(asset_positions: List[Dict[str, Any]]) -> None:
    """Atomically persist the raw HL position list. Best-effort, never raises."""
    try:
        payload = {
            "saved_at": int(time.time() * 1000),
            "asset_positions": asset_positions or [],
        }
        tmp = SNAPSHOT_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f)
        os.replace(tmp, SNAPSHOT_FILE)
    except OSError as e:
        logger.warning(f"[snapshot] failed to persist positions: {e}")


def read_snapshot(max_age_s: float = 120.0) -> Optional[Dict[str, Any]]:
    """Return a state-like dict ({"asset_positions": [...]}) from the snapshot,
    or None if the file is missing, unreadable, or older than `max_age_s`.

    A None return signals the caller to fall back to a live fetch — e.g. when
    the loop isn't running, so a standalone dashboard still shows positions.
    """
    try:
        with open(SNAPSHOT_FILE) as f:
            payload = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"[snapshot] file unreadable, ignoring: {e}")
        return None

    saved_at = payload.get("saved_at", 0)
    age_s = (time.time() * 1000 - saved_at) / 1000.0
    if age_s > max_age_s:
        return None
    return {"asset_positions": payload.get("asset_positions", [])}
