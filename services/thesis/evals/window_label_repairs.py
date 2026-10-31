"""How many thesis repair rounds were only about window labels ("24h", "8h", "48h").

2026-10-31: the scorecard replay found that about 85% of thesis drafts paid a
second (repair) LLM call, and that most of the flagged numbers were the
analytics' own lookback windows: the number extractor read "24h low" as the
number 24, then the grounding and scenario-range checks rejected it. The fix
(services/thesis/prompt.py `_DURATION_AFTER`) skips a number glued to a time
unit. This eval reads the `[thesis] ... draft failed validation, repairing:`
lines that replay runs log and counts, per run, how many repair rounds had
ONLY window-label errors (they no longer happen) and how many had at least
one other error (they still would). Deterministic, no LLM calls.

    python -m services.thesis.evals.window_label_repairs LOG [LOG ...]
"""
from __future__ import annotations

import re
import sys
from typing import Dict, Iterable, List

WINDOW_NUMBERS = {"8", "24", "48"}      # the analytics' 8h / 24h / 48h ranges
_ERR_NUM = re.compile(r"mentions (-?[\d.]+), (?:which is not grounded|outside its own implied)")
_REPAIR = "draft failed validation, repairing: "
_DONE = re.compile(r"\[thesis\] \S+: (\d+) call\(s\)")


def classify(errors_text: str) -> str:
    """"window_only" when every flagged number is a window label, "other"
    when any error is something else (or names another number)."""
    errors = [e.strip() for e in errors_text.split("; ") if e.strip()]
    if not errors:
        return "other"
    for e in errors:
        m = _ERR_NUM.search(e)
        if not m or m.group(1).rstrip("0").rstrip(".") not in WINDOW_NUMBERS:
            return "other"
    return "window_only"


def summarize(lines: Iterable[str]) -> Dict[str, int]:
    out = {"theses": 0, "repairs": 0, "window_only": 0, "other": 0}
    for line in lines:
        if _DONE.search(line):
            out["theses"] += 1
        i = line.find(_REPAIR)
        if i >= 0:
            out["repairs"] += 1
            out[classify(line[i + len(_REPAIR):])] += 1
    return out


def main(argv: List[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    total = {"theses": 0, "repairs": 0, "window_only": 0, "other": 0}
    for path in argv:
        with open(path, errors="replace") as fh:
            s = summarize(fh)
        for k in total:
            total[k] += s[k]
        print(f"{path}: {s}")
    t = total
    print(f"\nall runs: {t['theses']} theses, {t['repairs']} repair rounds; "
          f"{t['window_only']} were window labels only (now avoided), {t['other']} had another error")
    if t["theses"]:
        print(f"calls per thesis: before {(t['theses'] + t['repairs']) / t['theses']:.2f}, "
              f"after about {(t['theses'] + t['other']) / t['theses']:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
