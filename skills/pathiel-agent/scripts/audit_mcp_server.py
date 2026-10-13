#!/usr/bin/env python3
"""Audit scripts/pathiel-mcp-server.py for tool/handler wiring consistency.

Run from anywhere:

    python3 skills/pathiel-agent/scripts/audit_mcp_server.py [path-to-server]

Parses the server with the `ast` module (no import, no execution) and checks
the invariant from references/mcp-server.md:

    every name in TOOLS is covered by exactly one handler, where
        covered = explicit tool_handlers keys  +  _STUB_TOOL_NAMES (stub list)
    no name appears in both the explicit dict and the stub list
    no duplicate handle_* function definitions
    every explicit tool_handlers value references a defined handle_* function
    no orphan tool_handlers keys without a TOOLS entry
    every inputSchema property is read by its handler (directly or through a
        module-level helper it calls); an advertised-but-ignored param is a
        silent no-op for whatever agent passes it

Exits 0 when CLEAN, 1 on any drift (with an actionable report).
"""
from __future__ import annotations

import ast
import sys
from collections import Counter
from pathlib import Path


def _default_server_path() -> Path:
    # <repo>/skills/pathiel-agent/scripts/audit_mcp_server.py -> <repo>/scripts/...
    return Path(__file__).resolve().parents[3] / "scripts" / "pathiel-mcp-server.py"


def _schema_props(schema: ast.AST | None) -> set[str]:
    """Property names of an inputSchema dict literal."""
    if not isinstance(schema, ast.Dict):
        return set()
    for k, v in zip(schema.keys, schema.values):
        if getattr(k, "value", None) == "properties" and isinstance(v, ast.Dict):
            return {k2.value for k2 in v.keys if isinstance(k2, ast.Constant)}
    return set()


def _strings_reachable(fn_name: str, fns: dict, seen: set) -> set[str]:
    """String constants in a function and every module-level function it calls."""
    if fn_name in seen or fn_name not in fns:
        return set()
    seen.add(fn_name)
    out: set[str] = set()
    for n in ast.walk(fns[fn_name]):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.add(n.value)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            out |= _strings_reachable(n.func.id, fns, seen)
    return out


def audit(path: Path) -> int:
    tree = ast.parse(path.read_text(), filename=str(path))

    fn_nodes = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    tool_props: dict[str, set[str]] = {}
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
                        fields = {getattr(k, "value", None): v for k, v in zip(elt.keys, elt.values)}
                        name_node = fields.get("name")
                        if isinstance(name_node, ast.Constant):
                            tool_names.append(name_node.value)
                            tool_props[name_node.value] = _schema_props(fields.get("inputSchema"))
            elif name == "_STUB_RESPONSES" and isinstance(node.value, ast.Dict):
                # legacy structure (dict of name -> response)
                stub_keys |= {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
            elif name == "_STUB_TOOL_NAMES" and isinstance(node.value, ast.List):
                # current structure: a list of names registered via
                # `for n in _STUB_TOOL_NAMES: tool_handlers[n] = _make_stub_handler(n)`
                stub_keys |= {e.value for e in node.value.elts if isinstance(e, ast.Constant)}
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
        fails.append(f"duplicate handle_* defs (a dup silently overrides the first): {dup_defs}")
    if dup_keys:
        fails.append(f"duplicate tool_handlers keys: {dup_keys}")
    if missing:
        fails.append(f"TOOLS with no handler (explicit or stub): {missing}")
    if orphans:
        fails.append(f"handler entries with no TOOLS definition: {orphans}")
    if both:
        fails.append(f"names in BOTH explicit dict and the stub list: {both}")
    if bad_refs:
        fails.append(f"tool_handlers references undefined functions: {bad_refs}")
    unread = {}
    for name, props in tool_props.items():
        missed = props - _strings_reachable(f"handle_{name}", fn_nodes, set())
        if missed:
            unread[name] = sorted(missed)
    if unread:
        fails.append(f"schema params never read by their handler: {unread}")
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
