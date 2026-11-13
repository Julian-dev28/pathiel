"""Eval: is the Pages site ready for a search engine and an LLM crawler?

Outcome: https://julian-dev28.github.io/pathiel/ answered 404 (2026-11-13)
while every research doc under docs/ was served as plain text. The site is
now one landing page plus the scoreboard. This scores the page's search
metadata (tests/test_site.py seo_rows) and what the gh-pages tree holds.

Rows (every one must meet its target): the nine seo_rows, plus

  markdown files in the site tree       0
  files outside the page and scoreboard 0

Offline.

    .venv/bin/python -m pytest -q -s tests/test_eval_site_seo.py
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("_eval_site_checks", HERE / "test_site.py")
T = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(T)


def run() -> list:
    rows = [{"check": c, "got": g, "want": w, "ok": bool(ok)} for c, g, w, ok in T.seo_rows()]
    entries = T.S.site_entries(T.B.ls_tree(T.B.INDEX))
    md = [p for p in entries if p.endswith(".md")]
    extra = [p for p in entries if p not in T.S.FIXED and not p.startswith("scoreboard/")]
    rows.append({"check": "markdown files in the site tree", "got": len(md), "want": 0, "ok": not md})
    rows.append({"check": "files outside the page and scoreboard", "got": len(extra), "want": 0,
                 "ok": not extra})
    return rows


def test_eval_site_seo_meets_every_target(scorecard):
    card = scorecard("site_seo", run())
    failed = [r for r in card["rows"] if not r["ok"]]
    assert not failed, f"site eval: {card['passed']}/{card['total']}; failed: {failed}"
