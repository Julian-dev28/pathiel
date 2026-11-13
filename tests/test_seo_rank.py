"""Gate for scripts/seo_rank.py: the rank math and the log row, offline."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("seo_rank", ROOT / "scripts" / "seo_rank.py")
seo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seo)


def test_rank_is_one_based_and_case_insensitive():
    names = ["a/x", "julian-dev28/PATHIEL", "b/y"]
    assert seo.rank_of(names) == 2
    assert seo.rank_of(["a/x"]) is None
    assert seo.rank_of([]) is None


def test_summary_counts_top_ten_and_best():
    s = seo.summarize({"q1": 1, "q2": 10, "q3": 11, "q4": None})
    assert s == {"queries": 4, "in_top_100": 3, "in_top_10": 2, "best": 1}
    assert seo.summarize({"q": None})["best"] is None


def test_table_names_misses():
    t = seo.table({"q1": 3, "q2": None}, {"q1": "a/x ★5"})
    assert "| q1 | #3 | a/x ★5 |" in t
    assert "| q2 | not in top 100 |  |" in t


def test_main_appends_one_row(tmp_path, monkeypatch):
    fake = {q: [{"full_name": "other/repo", "stargazers_count": 9},
                {"full_name": seo.REPO, "stargazers_count": 1}] for q in seo.QUERIES}
    monkeypatch.setattr(seo, "search", lambda q, token: fake[q])
    monkeypatch.setattr(seo, "_token", lambda: "t")
    monkeypatch.setattr(seo.time, "sleep", lambda s: None)
    log = tmp_path / "rank.jsonl"
    assert seo.main(["--log", str(log)]) == 0
    assert seo.main(["--log", str(log)]) == 0
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(rows) == 2
    assert rows[0]["ranks"] == {q: 2 for q in seo.QUERIES}
    assert rows[0]["in_top_10"] == len(seo.QUERIES)
    assert rows[0]["leaders"][seo.QUERIES[0]] == "other/repo ★9"


def test_queries_are_unique():
    assert len(set(seo.QUERIES)) == len(seo.QUERIES)
