"""Read/write .agent-config.json.

Translation of lib/agent/config-store.ts.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict

logger = logging.getLogger(__name__)

CONFIG_PATH = os.path.join(os.getcwd(), ".agent-config.json")

DEFAULT_CONFIG: Dict[str, Any] = {"mode": "OFF"}


def read_agent_config() -> Dict[str, Any]:
    """Read the agent config from .agent-config.json."""
    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(DEFAULT_CONFIG)


def write_agent_config(cfg: Dict[str, Any]) -> None:
    """Write the agent config to .agent-config.json."""
    import os as _os
    # Write atomically
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    _os.replace(tmp, CONFIG_PATH)
    logger.info(f"[config] written {len(cfg)} keys to {CONFIG_PATH}")
