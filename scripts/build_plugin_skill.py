#!/usr/bin/env python3
"""Generate the plugin's desk-tick skill and agent from the project's.

The project files are the source: `.claude/skills/desk-tick/SKILL.md` (the
dispatcher: slot check, then launch the agent) and `.claude/agents/desk-tick.md`
(the whole tick procedure, pinned to Opus at medium effort). The Claude Code
plugin ships the same two as `skills/desk-tick/SKILL.md` and
`agents/desk-tick.md`, where the scripts run through
`"${CLAUDE_PLUGIN_ROOT}/scripts/pathiel-py"` (the plugin's venv, the plugin
root as cwd, keys and state under ~/.pathiel), the agent is addressed by its
plugin name, and the operator-specific lines are generalised. Two copies, one
text: this script writes the plugin copies from the project ones, and
tests/test_plugin.py fails when a committed copy is stale.

    .venv/bin/python scripts/build_plugin_skill.py          # write
    .venv/bin/python scripts/build_plugin_skill.py --check  # exit 1 if stale
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = '"${CLAUDE_PLUGIN_ROOT}/scripts/pathiel-py"'

# (old, new, expected count). A count that drifts means the source changed
# shape; update the rule here on purpose rather than let the plugin copy
# silently diverge.
SKILL_RULES = (
    ("Run on a schedule with /loop 15m /desk-tick oil. Live money on the pathiel wallet.",
     "Run on a schedule with /loop 15m /pathiel:desk-tick oil. Live money on the connected "
     "Hyperliquid account.", 1),
    ("`python3 scripts/desk_slot.py", f"`{PY} scripts/desk_slot.py", 1),
    ("`subagent_type` `desk-tick`", "`subagent_type` `pathiel:desk-tick`", 1),
    ("(`.claude/agents/desk-tick.md`)", "(the plugin's `agents/desk-tick.md`)", 1),
    ("Report to Julien", "Report to the operator", 1),
)

AGENT_RULES = (
    ("`python3 scripts/desk_slot.py", f"`{PY} scripts/desk_slot.py", 1),
    ("`python3 scripts/oil_levels.py", f"`{PY} scripts/oil_levels.py", 2),
    ("`python3 scripts/thesis_due.py", f"`{PY} scripts/thesis_due.py", 1),
    ("Work from the repo root: the working\ndirectory you are given is the pathiel checkout.",
     "The scripts below run through the plugin's own Python from the plugin\nroot; no checkout is needed.", 1),
    ("The only\n   wallet this loop tracks is pathiel's own,\n"
     "   `0x2c2e4efF574B3Fd9879AdeF9c5CAC6fD7C916985` (operator, 2026-10-24:\n"
     "   his hand-traded wallet is no longer watched).",
     "The only\n   wallet this loop tracks is the active account's own (the one\n"
     "   `account_overview` reports).", 1),
)

JOBS = (
    (ROOT / ".claude" / "skills" / "desk-tick" / "SKILL.md",
     ROOT / "skills" / "desk-tick" / "SKILL.md", SKILL_RULES),
    (ROOT / ".claude" / "agents" / "desk-tick.md",
     ROOT / "agents" / "desk-tick.md", AGENT_RULES),
)


def banner(source: Path) -> str:
    return (f"<!-- GENERATED from {source.relative_to(ROOT).as_posix()} by "
            "scripts/build_plugin_skill.py. Edit the source, then rerun it. -->\n")


def build(source_text: str, rules, source: Path) -> str:
    text = source_text
    for old, new, n in rules:
        found = text.count(old)
        if found != n:
            raise SystemExit(f"build_plugin_skill: {source.name}: expected {n} match(es) of "
                             f"{old[:50]!r}, found {found}")
        text = text.replace(old, new)
    # the banner goes right after the frontmatter so the frontmatter stays first
    head, sep, body = text.partition("\n---\n")
    if not sep:
        raise SystemExit(f"build_plugin_skill: {source.name} has no frontmatter")
    return head + sep + "\n" + banner(source) + body.lstrip("\n")


def render_all():
    return [(out, build(src.read_text(encoding="utf-8"), rules, src)) for src, out, rules in JOBS]


def main(argv: list[str]) -> int:
    stale = 0
    for out, rendered in render_all():
        if "--check" in argv:
            current = out.read_text(encoding="utf-8") if out.exists() else ""
            if current != rendered:
                print(f"stale: {out.relative_to(ROOT)}", file=sys.stderr)
                stale += 1
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered, encoding="utf-8")
        print(f"wrote {out.relative_to(ROOT)}")
    if "--check" in argv:
        print("ok" if not stale else f"{stale} stale (rerun scripts/build_plugin_skill.py)")
        return 1 if stale else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
