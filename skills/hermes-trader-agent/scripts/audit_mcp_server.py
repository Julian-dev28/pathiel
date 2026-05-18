#!/usr/bin/env python3
"""Audit scripts/hermes-mcp-server.py for tool/handler wiring consistency.

Run from anywhere:

    python3 skills/hermes-trader-agent/scripts/audit_mcp_server.py [path-to-server]

Parses the server with the `ast` module (no import, no execution) and checks
the invariant from references/mcp-server.md:

    every name in TOOLS is covered by exactly one handler, where
        covered = explicit tool_handlers keys  +  _STUB_RESPONSES keys
    no name appears in both the explicit dict and _STUB_RESPONSES
    no duplicate handle_* function definitions
    every explicit tool_handlers value references a defined handle_* function
    no orphan tool_handlers keys without a TOOLS entry

Exits 0 when CLEAN, 1 on any drift (with an actionable report).
"""
from __future__ import annotations

import ast
import sys
from collections import Counter
from pathlib import Path


def _default_server_path() -> Path:
    # <repo>/skills/hermes-trader-agent/scripts/audit_mcp_server.py -> <repo>/scripts/...
    return Path(__file__).resolve().parents[3] / "scripts" / "hermes-mcp-server.py"


def audit(path: Path) -> int:
    tree = ast.parse(path.read_text(), filename=str(path))

    handler_defs: list[str] = []
    tool_names: list[str] = []
    stub_keys: set[str] = set()
    handler_keys: list[str] = []
    handler_refs: set[str] = set()  # handle_* names referenced by tool_handlers

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("handle_"):
            handler_defs.append(node.name)

        targets = (
            node.targets if isinstance(node, ast.Assign)
            else [node.target] if isinstance(node, ast.AnnAssign)
            else []
        )
        for tgt in targets:
            name = getattr(tgt, "id", None)
            if name == "TOOLS" and isinstance(node.value, ast.List):
                for elt in node.value.elts:
                    if isinstance(elt, ast.Dict):
                        for k, v in zip(elt.keys, elt.values):
                            if getattr(k, "value", None) == "name" and isinstance(v, ast.Constant):
                                tool_names.append(v.value)
            elif name == "_STUB_RESPONSES" and isinstance(node.value, ast.Dict):
                stub_keys = {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
            elif name == "tool_handlers" and isinstance(node.value, ast.Dict):
                for k, v in zip(node.value.keys, node.value.values):
                    if isinstance(k, ast.Constant):
                        handler_keys.append(k.value)
                    if isinstance(v, ast.Name):
                        handler_refs.add(v.id)

    tools = set(tool_names)
    explicit = set(handler_keys)
    covered = explicit | stub_keys
    def_counts = Counter(handler_defs)

    fails: list[str] = []
    warns: list[str] = []

    dup_tools = [n for n, c in Counter(tool_names).items() if c > 1]
    dup_defs = sorted(n for n, c in def_counts.items() if c > 1)
    dup_keys = [n for n, c in Counter(handler_keys).items() if c > 1]
    missing = sorted(tools - covered)
    orphans = sorted(covered - tools)
    both = sorted(explicit & stub_keys)
    bad_refs = sorted(r for r in handler_refs if r not in def_counts)
    unwired = sorted(d for d in def_counts if d not in handler_refs)

    if dup_tools:
        fails.append(f"duplicate TOOLS names: {dup_tools}")
    if dup_defs:
        fails.append(f"duplicate handle_* defs (a dup silently shadows): {dup_defs}")
    if dup_keys:
        fails.append(f"duplicate tool_handlers keys: {dup_keys}")
    if missing:
        fails.append(f"TOOLS with no handler (explicit or stub): {missing}")
    if orphans:
        fails.append(f"handler entries with no TOOLS definition: {orphans}")
    if both:
        fails.append(f"names in BOTH explicit dict and _STUB_RESPONSES: {both}")
    if bad_refs:
        fails.append(f"tool_handlers references undefined functions: {bad_refs}")
    if unwired:
        warns.append(f"handle_* defs never wired into tool_handlers: {unwired}")

    print(f"server          : {path}")
    print(f"TOOLS           : {len(tool_names)}")
    print(f"explicit handlers: {len(handler_keys)}")
    print(f"stub responses  : {len(stub_keys)}")
    print(f"handle_* defs   : {len(handler_defs)}")
    print(f"covered         : {len(covered)}  (explicit + stub)")
    for w in warns:
        print(f"WARN: {w}")
    if fails:
        for f in fails:
            print(f"FAIL: {f}")
        print("RESULT: DRIFT")
        return 1
    print("RESULT: CLEAN")
    return 0


if __name__ == "__main__":
    server = Path(sys.argv[1]) if len(sys.argv) > 1 else _default_server_path()
    if not server.is_file():
        print(f"ERROR: server file not found: {server}")
        sys.exit(2)
    sys.exit(audit(server))
