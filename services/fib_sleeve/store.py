"""The sleeve's two files under `<state dir>/fib_sleeve/`:

  state.json   {"epoch_ms", "starting_equity_usd", "kill": {"reason", "at_ms"} | null}
               epoch_ms: fills before it are not the sleeve's record.
               kill: the latched kill switch; only `rearm` clears it.
  ticks.jsonl  one line per LIVE tick: the decision, as printed.

A read-only store (the dry run) loads and never writes.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict


class ReadOnlyStore(RuntimeError):
    """A dry run asked to write the sleeve's state."""


class Store:
    def __init__(self, directory: str, read_only: bool = False) -> None:
        self.directory = directory
        self.read_only = read_only

    @property
    def state_path(self) -> str:
        return os.path.join(self.directory, "state.json")

    @property
    def log_path(self) -> str:
        return os.path.join(self.directory, "ticks.jsonl")

    def load(self) -> Dict[str, Any]:
        """The state, {} when there is none yet. A file that exists and
        cannot be read raises: a lost kill latch must not read as "armed"."""
        if not os.path.isfile(self.state_path):
            return {}
        with open(self.state_path) as fh:
            raw = json.load(fh)
        if not isinstance(raw, dict):
            raise ValueError(f"{self.state_path}: must be a JSON object")
        return raw

    def save(self, state: Dict[str, Any]) -> None:
        if self.read_only:
            raise ReadOnlyStore("state write refused: dry run")
        os.makedirs(self.directory, exist_ok=True)
        tmp = self.state_path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, self.state_path)

    def append_log(self, row: Dict[str, Any]) -> None:
        if self.read_only:
            raise ReadOnlyStore("log write refused: dry run")
        os.makedirs(self.directory, exist_ok=True)
        with open(self.log_path, "a") as fh:
            fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")
