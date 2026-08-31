"""A live book must never fail in a way that looks like a quiet market.

Every book here reads state or an upstream that can break. The dangerous shape
is always the same: the failure produces the same value as "nothing happening",
so the book stops trading and nothing says so. This file pins the two found in
the live books on 2026-08-31, and the scan that found them.
"""
from __future__ import annotations

import ast
import json
import logging
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


# ── news_surge_multi: a corrupt baseline silently disables the book ──────────

def test_a_corrupt_baseline_is_logged_not_swallowed(tmp_path, monkeypatch, caplog):
    """Returning {} makes every `prior` empty, so `_surge` returns 1.0 for
    every coin, nothing is ever `breaking`, and the book cannot fire — while
    looking exactly like a quiet news day. _save_baseline then overwrites the
    damaged file, so the history is gone too."""
    from hermes_trader.agents import news_surge_multi as M

    bad = tmp_path / "baseline.json"
    bad.write_text("{not json at all")
    monkeypatch.setattr(M, "_BASELINE_FILE", str(bad))
    with caplog.at_level(logging.WARNING):
        assert M._load_baseline() == {}
    assert any("unreadable" in r.message for r in caplog.records), \
        "a corrupt baseline disabled the book with no log line"


def test_a_baseline_of_the_wrong_shape_is_logged(tmp_path, monkeypatch, caplog):
    from hermes_trader.agents import news_surge_multi as M

    bad = tmp_path / "baseline.json"
    bad.write_text(json.dumps(["not", "an", "object"]))
    monkeypatch.setattr(M, "_BASELINE_FILE", str(bad))
    with caplog.at_level(logging.WARNING):
        assert M._load_baseline() == {}
    assert any("expected an object" in r.message for r in caplog.records)


def test_a_missing_baseline_is_a_cold_start_not_a_fault(tmp_path, monkeypatch, caplog):
    """A fresh install has no baseline. Warning on that would train the operator
    to ignore the warning that matters."""
    from hermes_trader.agents import news_surge_multi as M

    monkeypatch.setattr(M, "_BASELINE_FILE", str(tmp_path / "absent.json"))
    with caplog.at_level(logging.WARNING):
        assert M._load_baseline() == {}
    assert not caplog.records


def test_a_good_baseline_still_loads(tmp_path, monkeypatch):
    from hermes_trader.agents import news_surge_multi as M

    f = tmp_path / "baseline.json"
    f.write_text(json.dumps({"ETH": [1, 2, 3]}))
    monkeypatch.setattr(M, "_BASELINE_FILE", str(f))
    assert M._load_baseline() == {"ETH": [1.0, 2.0, 3.0]}


def test_an_empty_baseline_reads_as_neutral_never_breaking():
    """The guard that stopped a live entry off a single unbaselined spike
    (xyz:BE, 2026-07-12). Kept pinned because the fix above touches this path."""
    from hermes_trader.agents.news_surge_multi import _surge

    assert _surge(count=99, prior=[]) == 1.0


# ── news_surge_short: a failed regime read corrupts the record ───────────────

def test_an_unavailable_macro_regime_is_logged(monkeypatch, caplog):
    """Recorded in meta, not gated on — so it does not change the trade, it
    corrupts the record used to judge the book. Analysis splitting by
    macro_regime would read the None bucket as a regime."""
    from hermes_trader.agents import news_surge_short_live as S

    import hermes_trader.agents.market_regime as MR
    monkeypatch.setattr(MR, "detect_regime",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("upstream down")))
    with caplog.at_level(logging.WARNING):
        assert S._macro_regime("ETH") is None
    assert any("macro regime unavailable" in r.message for r in caplog.records)


# ── the scan that found them ─────────────────────────────────────────────────

LIVE_MODULES = ("news_surge_short_live", "news_surge_multi", "social_trending",
                "unlock", "news_catalyst", "perception", "executor", "dsl_exit")
EMPTY_RETURNS = {"[]", "{}", "None", "0", "0.0", "False"}


def test_no_live_book_path_returns_an_empty_value_without_logging():
    """The whole class in one check. A broad `except` that returns an empty
    value and says nothing is a failure wearing the costume of a normal result.
    """
    offenders = []
    for path in (ROOT / "hermes_trader" / "agents").rglob("*.py"):
        if not any(k in path.name for k in LIVE_MODULES):
            continue
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for handler in (n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)):
            broad = handler.type is None or (
                isinstance(handler.type, ast.Name)
                and handler.type.id in ("Exception", "BaseException"))
            if not broad:
                continue
            logs = any(isinstance(n, ast.Call) and "log" in ast.unparse(n.func).lower()
                       for n in ast.walk(handler))
            if logs:
                continue
            for ret in (n for n in ast.walk(handler) if isinstance(n, ast.Return)):
                value = ast.unparse(ret.value) if ret.value is not None else "None"
                if value in EMPTY_RETURNS:
                    offenders.append(
                        f"{path.relative_to(ROOT)}:{ret.lineno}: returns {value} "
                        f"from a broad except with no log")
    assert not offenders, (
        "these failures are indistinguishable from a quiet market:\n"
        + "\n".join(offenders))


def test_the_scan_can_actually_fail():
    """A scanner that matches nothing passes forever."""
    src = ("def f():\n    try:\n        return g()\n"
           "    except Exception:\n        return []\n")
    tree = ast.parse(src)
    handler = next(n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler))
    rets = [n for n in ast.walk(handler) if isinstance(n, ast.Return)]
    assert rets and ast.unparse(rets[0].value) in EMPTY_RETURNS
