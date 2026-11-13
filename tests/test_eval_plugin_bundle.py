"""Eval: would the plugin directory's review of the bundle come back clean?

The outcome this unit exists for: the directory's review of the 2026-11-13
whole-repo submission flagged ROOT_CLAUDE_MD, RUNTIME_FETCH_EXEC (14 files),
SERVER_START_LINE_NOT_ACCEPTED, START_FILE_AS_PROGRAM, DECLARED_IN_TWO_PLACES
and LOCAL_CODE_FILE_TYPE (.claude/skills). Every one of those traces to a
file the bundle (scripts/build_plugin_bundle.py) leaves out or a line it
changed. This scores the built bundle on each, plus the local validator.

Rows (every one must meet its target):

  bundle files / repo files                   < 1/3
  files the directory flagged on the root     0
  download-and-run lines in shipped text      0
  eval in shipped shell                       0
  MCP server declarations, named interpreter  1, bash
  unclassified data files                     0
  server tools, bundle vs checkout            equal
  claude plugin validate --profile directory  0 findings, 0 warnings (--strict)

The validator row needs the claude CLI; without it the row says "not run"
in its got column and does not fail (CI has no claude). The directory runs
more checks than the local validator, so a clean card is necessary, not
sufficient: LOCAL_CODE_MCP_SERVER and STDIO_WEB_UNSUPPORTED stay, since the
plugin is a local stdio server by design.

    .venv/bin/python -m pytest -q -s tests/test_eval_plugin_bundle.py
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location("_eval_plugin_bundle_checks",
                                               HERE / "test_plugin_bundle.py")
T = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(T)
B = T.B


def _validate(bundle: Path) -> dict:
    proc = subprocess.run(["claude", "plugin", "validate", "--profile", "directory", "--strict",
                           "--json", str(bundle)], capture_output=True, text=True, timeout=120)
    report = json.loads(proc.stdout)
    parts = [report["manifest"], *report.get("contents", [])]
    return {"exit": proc.returncode,
            "errors": sum(len(p["errors"]) for p in parts),
            "warnings": sum(len(p["warnings"]) for p in parts),
            "findings": len(report["directory"]["findings"]) + len(report["directory"]["undecided"])}


def run(bundle: Path, public_env) -> List[dict]:
    def row(check: str, got, want, ok: bool) -> dict:
        return {"check": check, "got": got, "want": want, "ok": bool(ok)}

    files = B.ls_tree(B.INDEX)
    paths = B.build(B.INDEX, bundle)
    rows: List[dict] = []
    rows.append(row("bundle files / repo files", f"{len(paths)}/{len(files)}", "< 1/3",
                    len(paths) * 3 < len(files)))
    flagged = T.flagged_paths(paths)
    rows.append(row("files the directory flagged on the repo root", len(flagged), 0, not flagged))
    fx = T.fetch_exec_hits(bundle, paths)
    rows.append(row("download-and-run lines in shipped text", len(fx), 0, not fx))
    ev = T.shell_eval_hits(bundle, paths)
    rows.append(row("eval in shipped shell", len(ev), 0, not ev))

    plugin = json.loads((bundle / ".claude-plugin" / "plugin.json").read_text())
    servers = plugin.get("mcpServers", {})
    decl = len(servers) + int((bundle / ".mcp.json").exists())
    cmd = servers.get("pathiel", {}).get("command")
    rows.append(row("MCP server declarations, interpreter", f"{decl}, {cmd}", "1, bash",
                    decl == 1 and cmd == "bash"))
    un = T.unclassified_data(files)
    rows.append(row("unclassified data files", len(un), 0, not un))

    from_bundle = T._tools(bundle / "scripts" / "pathiel-mcp-server.py", public_env)
    from_checkout = T._tools(ROOT / "scripts" / "pathiel-mcp-server.py", public_env)
    rows.append(row("server tools, bundle vs checkout", f"{len(from_bundle)} vs {len(from_checkout)}",
                    "equal", from_bundle == from_checkout))

    if shutil.which("claude"):
        v = _validate(bundle)
        rows.append(row("claude plugin validate --profile directory --strict",
                        f"exit {v['exit']}, {v['errors']} errors, {v['warnings']} warnings, "
                        f"{v['findings']} findings", "exit 0, all 0",
                        v["exit"] == 0 and not (v["errors"] or v["warnings"] or v["findings"])))
    else:
        rows.append(row("claude plugin validate --profile directory --strict",
                        "not run (no claude CLI on PATH)", "exit 0, all 0", True))
    return rows


def test_eval_plugin_bundle_meets_every_target(tmp_path, public_env, scorecard):
    card = scorecard("plugin_bundle", run(tmp_path / "pathiel", public_env))
    failed = [r for r in card["rows"] if not r["ok"]]
    assert not failed, f"plugin bundle eval: {card['passed']}/{card['total']}; failed: {failed}"
