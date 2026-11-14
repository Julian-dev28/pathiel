#!/usr/bin/env python3
"""Score pathiel's MCP tool definitions the way Glama's TDQS does, locally.

Glama (glama.ai/mcp/servers/Julian-dev28/pathiel/admin/tdqs) rates every
tool 1-5 on six dimensions and averages them: Purpose, Usage Guidelines,
Behavior, Parameters, Completeness, Conciseness. v0.6.0 (65 public tools)
scored 3.6/5. This runs the same rubric through the project's LLM gateway
(services/llm, local Claude Code), so a change to a description is scored
before it ships instead of after.

Calibration: --surface legacy grades the old 65-tool public list. If it
lands near Glama's 3.6, the judge reads the rubric the way Glama does and
the --surface public score is worth trusting; the report prints both.

    .venv/bin/python scripts/tdqs_eval.py                     # public toolset
    .venv/bin/python scripts/tdqs_eval.py --surface legacy    # the 65 of v0.6.0
    .venv/bin/python scripts/tdqs_eval.py --surface both

Writes <out>/<surface>.json (default /tmp/pathiel-tdqs) and prints a table.
Paid: one LLM call per batch of tools. tests/test_eval_tdqs.py (marked live)
runs it with a pass threshold.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SERVER = ROOT / "scripts" / "pathiel-mcp-server.py"
DIMENSIONS = ("purpose", "usage", "behavior", "parameters", "completeness", "conciseness")
GLAMA_LEGACY_SCORE = 3.6   # v0.6.0, 65 tools, read 2026-11-13

RUBRIC = """You grade MCP tool definitions for how well they help an AI agent select and use the tool. Grade each tool 1-5 on six dimensions, strictly, the way a directory's Tool Definition Quality Score does:

purpose: Does the description clearly state what the tool does (a specific verb and resource) and how it differs from similar sibling tools? 5 = specific verb + resource + explicit differentiation from the closest siblings.
usage: Does it explain when to use this tool, when not to, and what alternatives exist ("use X instead of Y when Z")? 5 = explicit when, when-not and named alternatives.
behavior: Beyond the annotations (readOnlyHint/destructiveHint), does it disclose side effects, auth/key requirements, refusals, ordering, pagination, limits and what happens on failure? 5 = everything an agent must know before calling it.
parameters: Does the description clarify parameter syntax, constraints, interactions and defaults beyond the schema? If the schema documents every parameter, the baseline is 3; the description must add meaning (interactions, modes, valid ranges) for 4-5. Undocumented parameters lower it.
completeness: Given the tool's complexity, can an agent succeed on the first attempt? Includes what the call returns (there is no output schema, so the description must say). Simple tools need less.
conciseness: Appropriately sized, front-loaded, no filler, readable; every sentence earns its place. Long is fine when every clause carries information.

Be a strict, calibrated grader: 5 is rare and earned; a terse fragment with no guidance is 2-3. Return JSON only."""

SCHEMA = {
    "type": "object",
    "properties": {
        "tools": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    **{d: {"type": "integer", "minimum": 1, "maximum": 5} for d in DIMENSIONS},
                    "weakest": {"type": "string", "description": "one sentence: what would raise the lowest score"},
                },
                "required": ["name", *DIMENSIONS, "weakest"],
            },
        },
    },
    "required": ["tools"],
}


def _server(public: bool):
    if public:
        os.environ["PATHIEL_INSTALL"] = "public"
    spec = importlib.util.spec_from_file_location(f"pathiel_tdqs_{int(public)}", SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def surface_tools(surface: str) -> List[Dict[str, Any]]:
    """The tools/list a directory sees. public: the scoped toolset. legacy:
    v0.6.0's 65, rebuilt from the operator's TOOLS the way the old
    server_tools() did (description overrides, then wallet_connect and the
    four pool tools)."""
    mod = _server(public=True)
    if surface == "public":
        return mod.server_tools()
    tools = copy.deepcopy(mod.TOOLS)
    for tool in tools:
        override = mod._PUBLIC_DESCRIPTION_OVERRIDES.get(tool["name"])
        if override is not None:
            tool["description"] = override
    return tools + copy.deepcopy(mod.WALLET_CONNECT_TOOLS + mod.POOL_TOOLS)


def _grade_batch(batch: List[Dict[str, Any]], siblings: List[str]) -> List[Dict[str, Any]]:
    from services.llm import complete
    user = ("Sibling tools on this server (for differentiation): " + ", ".join(siblings) + "\n\n"
            "Grade each of these tool definitions:\n\n"
            + json.dumps([{k: t[k] for k in ("name", "description", "inputSchema", "annotations") if k in t}
                          for t in batch], indent=1))
    res = complete(system=RUBRIC, user=user, max_output_tokens=4000, json_schema=SCHEMA, tag="tdqs_eval")
    data = json.loads(res.text) if isinstance(res.text, str) else res.text
    return data["tools"]


def grade(tools: List[Dict[str, Any]], batch_size: int = 8) -> Dict[str, Any]:
    siblings = [t["name"] for t in tools]
    batches = [tools[i:i + batch_size] for i in range(0, len(tools), batch_size)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        graded = [row for rows in pool.map(lambda b: _grade_batch(b, siblings), batches) for row in rows]
    by_name = {r["name"]: r for r in graded}
    rows = []
    for t in tools:
        r = by_name.get(t["name"])
        if r is None:
            continue
        score = round(statistics.mean(r[d] for d in DIMENSIONS), 2)
        rows.append({"name": t["name"], "score": score, **{d: r[d] for d in DIMENSIONS},
                     "weakest": r.get("weakest", "")})
    rows.sort(key=lambda r: r["score"])
    mean = round(statistics.mean(r["score"] for r in rows), 2) if rows else 0.0
    by_dim = {d: round(statistics.mean(r[d] for r in rows), 2) for d in DIMENSIONS} if rows else {}
    return {"tools_listed": len(tools), "tools_graded": len(rows), "mean": mean,
            "by_dimension": by_dim, "rows": rows}


def report(surface: str, result: Dict[str, Any]) -> str:
    lines = [f"## {surface}: {result['mean']}/5 over {result['tools_graded']} of {result['tools_listed']} tools",
             "by dimension: " + ", ".join(f"{d} {v}" for d, v in result["by_dimension"].items()), "",
             f"{'tool':24} score " + " ".join(d[:5] for d in DIMENSIONS)]
    for r in result["rows"]:
        lines.append(f"{r['name']:24} {r['score']:5.2f} " + " ".join(f"{r[d]:5d}" for d in DIMENSIONS))
    return "\n".join(lines)


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--surface", choices=("public", "legacy", "both"), default="public")
    ap.add_argument("--out", default="/tmp/pathiel-tdqs")
    ap.add_argument("--batch", type=int, default=8)
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    # services/llm logs every call under $PATHIEL_STATE_DIR (default ".", the
    # checkout): keep this eval's usage log with its report, out of the tree.
    os.environ.setdefault("PATHIEL_STATE_DIR", str(out))
    surfaces = ("legacy", "public") if args.surface == "both" else (args.surface,)
    for surface in surfaces:
        result = grade(surface_tools(surface), batch_size=args.batch)
        (out / f"{surface}.json").write_text(json.dumps(result, indent=1))
        print(report(surface, result))
        if surface == "legacy":
            print(f"calibration: Glama scored this surface {GLAMA_LEGACY_SCORE}/5")
        print(f"written: {out / (surface + '.json')}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
