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

EMPTY_RETURNS = {"[]", "{}", "None", "0", "0.0", "False"}

# Helpers that report the failure themselves. A handler delegating to one of
# these is not silent.
REPORTERS = ("log", "_quarantine", "warn")


def test_nothing_returns_an_empty_value_from_a_broad_except_without_saying_so():
    """The whole class in one check, across the whole package.

    A broad `except` that returns an empty value and says nothing is a failure
    wearing the costume of a normal result. The first version of this test
    scoped itself to a hand-written list of live-book filenames, and that
    whitelist was the weak part: book_helpers.py is not named after any book,
    yet load_seen/last_pass_ms/load_state are the dedup and throttle files for
    all four live books, and all three silently switched their rate limiting
    off on a corrupt read. Scope is now the whole package.
    """
    offenders = []
    for path in (ROOT / "hermes_trader").rglob("*.py"):
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
            reports = any(
                isinstance(n, ast.Call)
                and any(r in ast.unparse(n.func).lower() for r in REPORTERS)
                for n in ast.walk(handler))
            if reports:
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


# ── book_helpers: a corrupt file must not switch a rate limiter off ──────────

def test_a_corrupt_throttle_file_fails_closed(tmp_path, caplog):
    """The old behaviour returned 0 and documented it as "the safe default (the
    book's interval check then always fires)". Firing is the unsafe direction:
    a damaged throttle file switched the throttle OFF. Now it reads as "just
    ran", which costs one delayed pass and heals on the next mark_pass."""
    from hermes_trader.agents.book_helpers import last_pass_ms

    f = tmp_path / "ts.json"
    f.write_text("{ this is not json")
    with caplog.at_level(logging.WARNING):
        got = last_pass_ms(str(f))
    import time as _t
    assert abs(got - int(_t.time() * 1000)) < 10_000, "throttle failed OPEN"
    assert (tmp_path / "ts.json.corrupt").exists(), "evidence was not preserved"
    assert any("quarantin" in r.message for r in caplog.records)


def test_a_missing_throttle_file_lets_the_book_run(tmp_path, caplog):
    """A book that has never run should run now — that is a cold start, not a
    fault, and must not warn."""
    from hermes_trader.agents.book_helpers import last_pass_ms

    with caplog.at_level(logging.WARNING):
        assert last_pass_ms(str(tmp_path / "absent.json")) == 0
    assert not caplog.records


def test_a_corrupt_dedup_file_is_loud_and_quarantined(tmp_path, caplog):
    """An empty dedup map makes every coin read as never-opened, so the book
    re-enters what it already traded today."""
    from hermes_trader.agents.book_helpers import load_seen

    f = tmp_path / "seen.json"
    f.write_text('["not", "a", "map"]')
    with caplog.at_level(logging.WARNING):
        assert load_seen(str(f)) == {}
    assert (tmp_path / "seen.json.corrupt").exists()
    assert any("expected an object" in r.message for r in caplog.records)


def test_one_unparseable_dedup_entry_does_not_discard_the_rest(tmp_path, caplog):
    """Dropping the whole map over a single bad key would re-open every coin."""
    from hermes_trader.agents.book_helpers import load_seen

    f = tmp_path / "seen.json"
    f.write_text(json.dumps({"ETH:2026-08-31": 1, "BTC:2026-08-31": "junk"}))
    with caplog.at_level(logging.WARNING):
        got = load_seen(str(f))
    assert got == {"ETH:2026-08-31": 1}
    assert any("unparseable dedup entry" in r.message for r in caplog.records)


def test_good_files_still_round_trip(tmp_path):
    from hermes_trader.agents.book_helpers import (
        last_pass_ms, load_seen, load_state, mark_pass, save_seen, save_state)

    s, t, st = (str(tmp_path / n) for n in ("seen.json", "ts.json", "state.json"))
    save_seen(s, {"ETH:2026-08-31": 7})
    mark_pass(t, 1234567)
    save_state(st, {"last_poll_ms": 99})
    assert load_seen(s) == {"ETH:2026-08-31": 7}
    assert last_pass_ms(t) == 1234567
    assert load_state(st) == {"last_poll_ms": 99}


# ── the dashboard must never report "flat" because a fetch failed ────────────

def test_a_failed_position_fetch_serves_the_last_good_read(monkeypatch, caplog):
    """An empty list means FLAT to the operator view, the public view and
    /api/positions. Returning [] because the fetch threw tells the operator
    they have no positions while they may have several open and unmanaged."""
    import hermes_trader.dashboard as db

    good = [{"coin": "ETH", "szi": 1.0}]
    monkeypatch.setitem(db._POSITIONS_CACHE, "ts", 0.0)
    monkeypatch.setitem(db._POSITIONS_CACHE, "data", good)
    monkeypatch.setattr(db, "_positions_payload_uncached",
                        lambda: (_ for _ in ()).throw(RuntimeError("HL 502")))
    with caplog.at_level(logging.WARNING):
        assert db._positions_payload() == good, "reported flat on a failed fetch"
    assert any("last good read" in r.message for r in caplog.records)


def test_a_genuinely_flat_account_still_reads_flat(monkeypatch):
    """The fix must not make an empty position list impossible to express."""
    import hermes_trader.dashboard as db

    monkeypatch.setitem(db._POSITIONS_CACHE, "ts", 0.0)
    monkeypatch.setitem(db._POSITIONS_CACHE, "data", [{"coin": "ETH"}])
    monkeypatch.setattr(db, "_positions_payload_uncached", lambda: [])
    assert db._positions_payload() == []
