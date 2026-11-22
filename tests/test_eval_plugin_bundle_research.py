"""Eval: would the plugin directory's review of the RESEARCH bundle come
back clean? Mirrors tests/test_eval_plugin_bundle.py for the research
server (scripts/build_plugin_bundle.py --plugin research).

Rows (every one must meet its target):

  bundle files / repo files                   < 1/3
  paths that escape ${CLAUDE_PLUGIN_ROOT}      0
  install commands in shipped Markdown         0   (docker run/build, git clone)
  dead relative links in shipped Markdown      0
  banned trading/credential files shipped      0
  forbidden module loaded, fresh import        0
  MCP server declarations, named interpreter   1, bash
  server tools, bundle vs checkout             equal, 6
  claude plugin validate --profile directory   0 findings, 0 warnings (--strict)

The validator row needs the claude CLI; without it the row says "not run"
in its got column and does not fail (CI has no claude).

    .venv/bin/python -m pytest -q -s tests/test_eval_plugin_bundle_research.py
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location("_eval_plugin_bundle_research_checks",
                                               HERE / "test_plugin_bundle_research.py")
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
    paths = B.research_build(B.INDEX, bundle)
    rows: List[dict] = []
    rows.append(row("bundle files / repo files", f"{len(paths)}/{len(files)}", "< 1/3",
                    len(paths) * 3 < len(files)))

    escapees = []
    import posixpath
    for p in paths:
        dest = B._research_dest(p)
        if dest.startswith("..") or dest.startswith("/") or posixpath.normpath(dest) != dest:
            escapees.append(dest)
    rows.append(row("paths that escape ${CLAUDE_PLUGIN_ROOT}", len(escapees), 0, not escapees))

    shipped = set(paths) | {B._research_dest(p) for p in paths}
    readme = (bundle / "README.md").read_text(encoding="utf-8")
    installs = sum(readme.count(n) for n in ("docker run", "docker build", "git clone"))
    dead = sum(1 for m in re.finditer(r"\]\((?!https?://|mailto:|#)([^)\s#]+)", readme)
              if m.group(1) not in shipped)
    rows.append(row("install commands in shipped Markdown", installs, 0, installs == 0))
    rows.append(row("dead relative links in shipped Markdown", dead, 0, dead == 0))

    banned_prefixes = ("pathiel/agents/bracket.py", "pathiel/agents/executor.py",
                      "pathiel/client/exchange.py", "services/strategies/next_trade.py",
                      "services/accounts/")
    banned = [p for p in paths if any(p == b or p.startswith(b) for b in banned_prefixes)]
    rows.append(row("banned trading/credential files shipped", len(banned), 0, not banned))

    import subprocess as sp
    import sys as _sys
    code = ("import sys, json\n"
            f"sys.path.insert(0, {str(bundle)!r})\n"
            "from services.research_mcp import server\n"
            f"forbidden = {sorted(B.RESEARCH_FORBIDDEN_MODULES)!r}\n"
            "hits = [m for m in sys.modules if any(m == f or m.startswith(f + '.') for f in forbidden)]\n"
            "print(json.dumps(hits))\n")
    proc = sp.run([_sys.executable, "-c", code], capture_output=True, text=True, timeout=30,
                  cwd=str(bundle))
    hits = json.loads(proc.stdout.strip()) if proc.returncode == 0 else ["<import failed>"]
    rows.append(row("forbidden module loaded, fresh import", hits, [], not hits))

    plugin = json.loads((bundle / ".claude-plugin" / "plugin.json").read_text())
    servers = plugin.get("mcpServers", {})
    decl = len(servers) + int((bundle / ".mcp.json").exists())
    cmd = servers.get("pathiel-research", {}).get("command")
    rows.append(row("MCP server declarations, interpreter", f"{decl}, {cmd}", "1, bash",
                    decl == 1 and cmd == "bash"))

    from_bundle = T._tools(bundle / "scripts" / "pathiel-research-mcp.py", public_env)
    from_checkout = T._tools(ROOT / "scripts" / "pathiel-research-mcp.py", public_env)
    rows.append(row("server tools, bundle vs checkout", f"{len(from_bundle)} vs {len(from_checkout)}",
                    "equal, 6", from_bundle == from_checkout and len(from_bundle) == 6))

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


def test_eval_plugin_bundle_research_meets_every_target(tmp_path, public_env, scorecard):
    card = scorecard("plugin_bundle_research", run(tmp_path / "pathiel-research", public_env))
    failed = [r for r in card["rows"] if not r["ok"]]
    assert not failed, f"research plugin bundle eval: {card['passed']}/{card['total']}; failed: {failed}"
