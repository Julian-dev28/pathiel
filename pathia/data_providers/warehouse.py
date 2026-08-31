"""Tiny append-only JSONL warehouse for provider exhaust.

This keeps external-provider data outside live agent memory. The file layout is
simple on purpose so DuckDB/Polars/Python scripts can ingest it directly later:

    <PATHIA_STATE_DIR or repo> / warehouse / <table>.jsonl
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Iterable, List

from pathia.agents.rebalancer_owned import state_file

_TABLE_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class JsonlWarehouse:
    def __init__(self, root: str | None = None) -> None:
        self.root = root or state_file("warehouse")
        os.makedirs(self.root, exist_ok=True)

    def path_for(self, table: str) -> str:
        if not _TABLE_RE.match(table):
            raise ValueError(f"invalid table name: {table!r}")
        return os.path.join(self.root, f"{table}.jsonl")

    def append(self, table: str, record: Dict[str, Any]) -> None:
        path = self.path_for(table)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as fh:
            fh.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")

    def append_many(self, table: str, records: Iterable[Dict[str, Any]]) -> int:
        path = self.path_for(table)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        n = 0
        with open(path, "a") as fh:
            for rec in records:
                fh.write(json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n")
                n += 1
        return n

    def read_all(self, table: str) -> List[Dict[str, Any]]:
        path = self.path_for(table)
        if not os.path.exists(path):
            return []
        out: List[Dict[str, Any]] = []
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out
