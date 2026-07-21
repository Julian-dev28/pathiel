"""Gate tests for the multi-source news-surge recorder (worldmonitor thesis).

Zero-capital recorder: it pools curated firehoses once, entity-matches every
scan candidate against the pool, and records a SHORT signal with the
multi-source surge for the autonomous cycle to grade against the live
single-source news_surge_short. These tests pin the surge math, the
pool-then-match mechanism, the rolling baseline, and that it never trades.
"""
from datetime import datetime, timezone

import pytest

from hermes_trader.agents import news_surge_multi as nsm
from hermes_trader.agents.news_catalyst import Article


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(nsm, "_TS_FILE", str(tmp_path / "ts.json"))
    monkeypatch.setattr(nsm, "_BASELINE_FILE", str(tmp_path / "base.json"))


def _art(title):
    return Article(title=title, url="u", domain="d",
                   seen=datetime.now(timezone.utc), source="cnbc.com")


def _captured(monkeypatch):
    out = []
    monkeypatch.setattr(nsm.shadow_ledger, "record_many",
                        lambda book, rows: out.append((book, rows)) or len(rows))
    return out


# ------------------------------------------------------------------ surge math
def test_surge_no_prior_is_always_neutral():
    """No baseline -> surge 1.0 regardless of count, so a coin can NEVER be
    breaking before it has history. This is the guard that stopped a live entry
    off a single unbaselined spike (news_catalyst xyz:BE, 2026-07-12)."""
    assert nsm._surge(0, []) == 1.0
    assert nsm._surge(5, []) == 1.0        # even a big first read is neutral until baselined


def test_surge_is_count_over_median_baseline():
    assert nsm._surge(6, [1.0, 2.0, 3.0]) == 3.0     # 6 / median(1,2,3)=2
    assert nsm._surge(1, [4.0, 4.0, 4.0]) == 0.25


# ------------------------------------------------------------------ entity match
def test_count_relevant_matches_ticker_and_xyz_alias(monkeypatch):
    # cashtag + ALL-CAPS ticker for crypto; xyz uses the ticker/alias path
    pool = [_art("$BTC breaks out as Bitcoin ETF inflows surge"),
            _art("Random unrelated headline about weather"),
            _art("NVDA earnings crush estimates")]
    assert nsm.count_relevant("BTC", pool) >= 1
    assert nsm.count_relevant("NVDA", pool) >= 1
    assert nsm.count_relevant("DOGE", pool) == 0


def test_count_relevant_skips_stale_headlines(monkeypatch):
    old = Article(title="$BTC old news", url="u", domain="d",
                  seen=datetime(2020, 1, 1, tzinfo=timezone.utc), source="x")
    assert nsm.count_relevant("BTC", [old]) == 0


# ------------------------------------------------------------------ recorder flow
def test_pools_once_and_records_short_side(monkeypatch):
    out = _captured(monkeypatch)
    fetches = {"n": 0}

    def fake_pool(feeds=None, limit=0):
        fetches["n"] += 1
        return [_art("$BTC surges as Bitcoin rallies"),
                _art("Bitcoin ETF sees record inflows, BTC climbs")]

    monkeypatch.setattr(nsm, "rss_headlines", fake_pool)
    percs = [{"coin": "BTC", "mid": 60000.0}, {"coin": "ETH", "mid": 3000.0}]
    n = nsm.maybe_run({}, percs)
    assert n == 2
    # pooled ONCE for the whole universe, not once per coin
    assert fetches["n"] == 1
    book, rows = out[0]
    assert book == "news_surge_multi"
    assert all(r["side"] == "short" for r in rows)          # validated direction
    assert all(r["stop_pct"] == 6.0 and r["horizon_days"] == 1.0 for r in rows)
    assert all(r["meta"]["shadow"] is True for r in rows)   # zero capital


def test_default_config_is_shadow_and_does_not_trade(monkeypatch):
    """Empty config -> shadow_only defaults True -> records only, no capital."""
    _captured(monkeypatch)
    monkeypatch.setattr(nsm, "rss_headlines", lambda **k: [_art("$BTC news")])
    opened = []
    nsm.maybe_run({}, [{"coin": "BTC", "mid": 60000.0}],
                  [], lambda a: opened.append(a) or {"executed": True})
    assert opened == []


def test_baseline_grows_and_drives_the_surge(monkeypatch):
    _captured(monkeypatch)
    monkeypatch.setattr(nsm, "rss_headlines",
                        lambda **k: [_art("$BTC rallies"), _art("Bitcoin BTC climbs")])
    import json
    # 3 passes; each bypasses throttle via a fresh ts file
    for i in range(3):
        monkeypatch.setattr(nsm, "_TS_FILE", str(k := f"/tmp/nsm_ts_{i}.json"))
        try:
            import os
            os.remove(k)
        except OSError:
            pass
        nsm.maybe_run({}, [{"coin": "BTC", "mid": 60000.0}])
    base = json.load(open(nsm._BASELINE_FILE))
    assert "BTC" in base and len(base["BTC"]) == 3          # rolling window accrues


def test_throttle_and_hot_kill(monkeypatch):
    out = _captured(monkeypatch)
    monkeypatch.setattr(nsm, "rss_headlines", lambda **k: [_art("$BTC")])
    percs = [{"coin": "BTC", "mid": 60000.0}]
    assert nsm.maybe_run({}, percs) == 1
    assert nsm.maybe_run({}, percs) == 0                    # throttled inside window
    assert nsm.maybe_run({"news_surge_multi": {"enabled": False}}, percs) == 0
    assert len(out) == 1


def test_failed_fetch_marks_pass_and_does_not_storm(monkeypatch):
    _captured(monkeypatch)

    def boom(**k):
        raise OSError("all firehoses down")
    monkeypatch.setattr(nsm, "rss_headlines", boom)
    assert nsm.maybe_run({}, [{"coin": "BTC", "mid": 60000.0}]) == 0
    assert nsm._last_pass_ms() > 0                          # marked, won't retry-storm


# ------------------------------------------------------------------ LIVE arm
def _live_cfg(**ov):
    c = {"enabled": True, "shadow_only": False, "notional_usd": 20.0,
         "leverage": 10, "stop_pct": 6.0, "hold_days": 1.0, "max_new_per_cycle": 1}
    c.update(ov)
    return {"news_surge_multi": c}


@pytest.fixture()
def _live_iso(tmp_path, monkeypatch):
    monkeypatch.setattr(nsm, "_SEEN_FILE", str(tmp_path / "seen.json"))
    monkeypatch.setattr(nsm, "active_position_coins", lambda: {})

    class _Claims:
        def prune_to(self, held, book): pass
        def claimed_by_others(self, book): return set()
        def claim(self, coin, book): return True
        def release(self, coin, book): pass
        def save(self): pass
    monkeypatch.setattr(nsm, "get_claims_registry", lambda: _Claims())


def _breaking_pool():
    # 4 recent BTC-relevant headlines -> count>=3
    return [_art("$BTC surges as Bitcoin ETF inflows hit record"),
            _art("Bitcoin BTC climbs past resistance"),
            _art("BTC rally accelerates, Bitcoin dominance rises"),
            _art("Analysts eye $BTC breakout")]


def test_live_arm_opens_short_only_on_a_BASELINED_breaking_surge(monkeypatch, _live_iso):
    _captured(monkeypatch)
    monkeypatch.setattr(nsm, "rss_headlines", lambda **k: _breaking_pool())
    # seed a low baseline so count=4 reads as a big surge (>=3x)
    monkeypatch.setattr(nsm, "_load_baseline", lambda: {"BTC": [1.0, 1.0, 1.0]})
    monkeypatch.setattr(nsm, "_save_baseline", lambda b: None)
    opened = []
    n = nsm.maybe_run(_live_cfg(), [{"coin": "BTC", "mid": 60000.0}],
                      [], lambda a: opened.append(a) or {"executed": True})
    assert n == 1 and len(opened) == 1
    a = opened[0]
    assert a["strategy_book"] == "news_surge_multi" and a["side"] == "short"
    assert a["leverage_override"] == 10 and a["strategy_book_notional"] == 20.0
    assert a["dsl_exit_override"]["max_loss_pct"] == 6.0


def test_live_arm_does_not_open_without_a_baseline(monkeypatch, _live_iso):
    """No baseline -> surge 1.0 -> not breaking -> no trade, even live. The
    unbaselined-spike guard protects the live arm too."""
    _captured(monkeypatch)
    monkeypatch.setattr(nsm, "rss_headlines", lambda **k: _breaking_pool())
    monkeypatch.setattr(nsm, "_load_baseline", lambda: {})   # fresh coin
    monkeypatch.setattr(nsm, "_save_baseline", lambda b: None)
    opened = []
    nsm.maybe_run(_live_cfg(), [{"coin": "BTC", "mid": 60000.0}],
                  [], lambda a: opened.append(a) or {"executed": True})
    assert opened == []


def test_live_arm_daily_dedup(monkeypatch, _live_iso, tmp_path):
    _captured(monkeypatch)
    monkeypatch.setattr(nsm, "rss_headlines", lambda **k: _breaking_pool())
    monkeypatch.setattr(nsm, "_load_baseline", lambda: {"BTC": [1.0, 1.0, 1.0]})
    monkeypatch.setattr(nsm, "_save_baseline", lambda b: None)
    opened = []
    ex = lambda a: opened.append(a) or {"executed": True}
    nsm.maybe_run(_live_cfg(), [{"coin": "BTC", "mid": 60000.0}], [], ex)
    assert len(opened) == 1
    monkeypatch.setattr(nsm, "_TS_FILE", str(tmp_path / "ts2.json"))  # bypass throttle
    nsm.maybe_run(_live_cfg(), [{"coin": "BTC", "mid": 60000.0}], [], ex)
    assert len(opened) == 1   # same UTC day -> no second live open
