"""Gate tests for the W-N3 news-catalyst shadow recorder."""
import time

import pytest

from hermes_trader.agents import news_catalyst_live as ncl
from hermes_trader.agents.news_catalyst import Article, CatalystReport


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(ncl, "_TS_FILE", str(tmp_path / "ts.json"))


def _captured(monkeypatch):
    rows_out = []

    def fake_many(book, rows):
        rows_out.append((book, rows))
        return len(rows or [])

    monkeypatch.setattr(ncl.shadow_ledger, "record_many", fake_many)
    return rows_out


def _report(breaking=False, surge=1.0, n=2):
    return CatalystReport(
        query="X", n_recent=n, breaking=breaking, surge_x=surge,
        headlines=[Article(title=f"headline {i}", url="u", domain="d", seen=None)
                   for i in range(4)],
    )


def test_records_breaking_and_nonbreaking_rows(monkeypatch):
    out = _captured(monkeypatch)
    monkeypatch.setattr(ncl, "coin_catalyst",
                        lambda c: _report(breaking=(c == "HOT"), surge=4.0 if c == "HOT" else 0.5))
    percs = [{"coin": "HOT", "mid": 2.5}, {"coin": "COLD", "mid": 1.0}]
    assert ncl.maybe_run({}, percs) == 2
    book, rows = out[0]
    assert book == "news_catalyst"
    by_coin = {r["coin"]: r for r in rows}
    assert by_coin["HOT"]["meta"]["breaking"] is True
    assert by_coin["COLD"]["meta"]["breaking"] is False   # the built-in null
    assert by_coin["HOT"]["side"] == "long"
    assert by_coin["HOT"]["stop_pct"] == 15.0
    assert len(by_coin["HOT"]["meta"]["top3_titles"]) == 3


def test_throttle_one_pass_per_interval(monkeypatch):
    out = _captured(monkeypatch)
    monkeypatch.setattr(ncl, "coin_catalyst", lambda c: _report())
    percs = [{"coin": "A", "mid": 1.0}]
    assert ncl.maybe_run({}, percs) == 1
    assert ncl.maybe_run({}, percs) == 0   # inside the 30-min window
    assert len(out) == 1


def test_throttle_marks_before_reads_so_failures_dont_storm(monkeypatch):
    _captured(monkeypatch)

    def boom(c):
        raise OSError("rss down")

    monkeypatch.setattr(ncl, "coin_catalyst", boom)
    assert ncl.maybe_run({}, [{"coin": "A", "mid": 1.0}]) == 0
    # even after total failure the pass is marked — no immediate retry
    assert ncl._last_pass_ms() > 0


def test_bounded_coins_dedup_and_bad_mids(monkeypatch):
    out = _captured(monkeypatch)
    monkeypatch.setattr(ncl, "coin_catalyst", lambda c: _report())
    percs = ([{"coin": "DUP", "mid": 1.0}, {"coin": "DUP", "mid": 1.0},
              {"coin": "NOMID", "mid": 0.0}]
             + [{"coin": f"C{i}", "mid": 1.0} for i in range(20)])
    n = ncl.maybe_run({}, percs)
    assert n <= ncl._MAX_COINS_PER_PASS
    coins = [r["coin"] for r in out[0][1]]
    assert coins.count("DUP") == 1 and "NOMID" not in coins


def test_hot_kill(monkeypatch):
    out = _captured(monkeypatch)
    assert ncl.maybe_run({"news_catalyst": {"enabled": False}},
                         [{"coin": "A", "mid": 1.0}]) == 0
    assert out == []
