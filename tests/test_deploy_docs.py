"""The deploy docs' shell blocks must paste and run as written.

DEPLOY.md and k8s/README.md both shipped a `kubectl create secret` whose last
line ended in a backslash: the next command (`kubectl apply -k k8s/`) was
glued onto it as extra arguments, or the shell sat waiting at the fence.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ["DEPLOY.md", "k8s/README.md", "README.md"]
FENCE = re.compile(r"```(?:bash|sh|shell)\n(.*?)```", re.S)


def dangling_continuations(text: str) -> list[str]:
    bad = []
    for block in FENCE.findall(text):
        lines = block.split("\n")
        for i, line in enumerate(lines):
            if not line.rstrip().endswith("\\"):
                continue
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if not nxt.startswith((" ", "\t")):
                bad.append(line.strip())
    return bad


@pytest.mark.parametrize("doc", DOCS)
def test_no_shell_line_continues_into_the_next_command(doc):
    assert dangling_continuations((ROOT / doc).read_text()) == []


def test_the_check_catches_the_shipped_bug():
    shipped = ("```bash\nkubectl create secret generic s \\\n"
               "  --from-literal=A=\"$A\" \\\nkubectl apply -k k8s/\n```\n")
    assert dangling_continuations(shipped) == ['--from-literal=A="$A" \\']


def test_deploy_md_names_every_way_pathiel_runs():
    text = (ROOT / "DEPLOY.md").read_text()
    for needle in ("/desk-loops", "scripts/trigger_watch.py", "scripts/vps/push.sh",
                   "scripts/vps/pull_state.sh", "Install as a Claude Code plugin",
                   "One trader per account"):
        assert needle in text, needle
