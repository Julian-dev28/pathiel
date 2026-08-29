#!/usr/bin/env python3
"""Fail if any tracked Python file hardcodes an absolute path into a home dir.

This repo's CI was red on every push for weeks while the suite was green
locally. The cause was 50 files carrying
`REPO = "/Users/julian_dev/Documents/code/hermes-trader"`. Nothing caught it,
because on the one machine that mattered the path existed.

Deterministic and offline, so it runs as the first CI step: when it fails, the
error names the real problem instead of surfacing as a FileNotFoundError
several minutes into the suite.

    python scripts/check_no_absolute_paths.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Absolute paths into a user's home directory on macOS or Linux. A bare "/tmp"
# or "/usr/local/bin" is fine — those exist on any box. "/Users/<name>" and
# "/home/<name>" are machine-bound by construction.
PATTERN = re.compile(r"['\"](/Users/[^/'\"]+|/home/[^/'\"]+)/")

# Files whose whole job is to describe a specific machine.
ALLOWED = {
    ".env.local.example",
}


def tracked_python_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT,
                         capture_output=True, text=True, check=True)
    return [ROOT / line for line in out.stdout.splitlines() if line]


def main() -> int:
    offenders: list[str] = []
    for path in tracked_python_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel in ALLOWED or not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if PATTERN.search(line):
                offenders.append(f"{rel}:{i}: {line.strip()[:100]}")

    if offenders:
        print("Machine-bound absolute paths found. These make the suite pass on "
              "one laptop and fail everywhere else:\n", file=sys.stderr)
        for o in offenders:
            print(f"  {o}", file=sys.stderr)
        print("\nResolve paths from __file__ instead (e.g. "
              "Path(__file__).resolve().parents[N]).", file=sys.stderr)
        return 1

    print(f"OK — no machine-bound absolute paths in "
          f"{len(tracked_python_files())} tracked Python files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
