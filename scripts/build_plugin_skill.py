#!/usr/bin/env python3
"""Generate the plugin's desk-tick skill from the project's.

`.claude/skills/desk-tick/SKILL.md` is the source: the operator's own loop,
run from a checkout with `python3 scripts/...`. The Claude Code plugin ships
the same skill as `skills/desk-tick/SKILL.md`, where the scripts run through
`"${CLAUDE_PLUGIN_ROOT}/scripts/pathiel-py"` (the plugin's venv, the plugin
root as cwd, keys and state under ~/.pathiel) and the operator-specific lines
are generalised. Two files, one text: this script writes the second from the
first, and tests/test_plugin.py fails when the committed copy is stale.

    .venv/bin/python scripts/build_plugin_skill.py          # write
    .venv/bin/python scripts/build_plugin_skill.py --check  # exit 1 if stale
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / ".claude" / "skills" / "desk-tick" / "SKILL.md"
OUT = ROOT / "skills" / "desk-tick" / "SKILL.md"

PY = '"${CLAUDE_PLUGIN_ROOT}/scripts/pathiel-py"'

# (old, new, expected count). A count that drifts means the source skill
# changed shape; update the rule here on purpose rather than let the plugin
# copy silently diverge.
RULES = (
    ("Run on a schedule with /loop 15m /desk-tick oil. Live money on the pathiel wallet.",
     "Run on a schedule with /loop 15m /pathiel:desk-tick oil. Live money on the connected "
     "Hyperliquid account.", 1),
    ("`python3 scripts/desk_slot.py", f"`{PY} scripts/desk_slot.py", 1),
    ("`python3 scripts/oil_levels.py", f"`{PY} scripts/oil_levels.py", 2),
    ("The only\n   wallet this loop tracks is pathiel's own,\n"
     "   `0x2c2e4efF574B3Fd9879AdeF9c5CAC6fD7C916985` (operator, 2026-10-24:\n"
     "   his hand-traded wallet is no longer watched).",
     "The only\n   wallet this loop tracks is the active account's own (the one\n"
     "   `account_overview` reports).", 1),
    ("report to Julien", "report to the operator", 1),
    ("Report to Julien", "Report to the operator", 1),
)

BANNER = ("<!-- GENERATED from .claude/skills/desk-tick/SKILL.md by "
          "scripts/build_plugin_skill.py. Edit the source, then rerun it. -->\n")


def build(source_text: str) -> str:
    text = source_text
    for old, new, n in RULES:
        found = text.count(old)
        if found != n:
            raise SystemExit(f"build_plugin_skill: expected {n} match(es) of {old[:50]!r}, found {found}")
        text = text.replace(old, new)
    # the banner goes right after the frontmatter so the frontmatter stays first
    head, sep, body = text.partition("\n---\n")
    if not sep:
        raise SystemExit("build_plugin_skill: source has no frontmatter")
    return head + sep + "\n" + BANNER + body.lstrip("\n")


def main(argv: list[str]) -> int:
    rendered = build(SOURCE.read_text(encoding="utf-8"))
    if "--check" in argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != rendered:
            print(f"stale: {OUT.relative_to(ROOT)} (rerun scripts/build_plugin_skill.py)", file=sys.stderr)
            return 1
        print("ok")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(rendered, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
