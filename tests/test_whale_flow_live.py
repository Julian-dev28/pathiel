"""Gate tests for the whale_flow order-flow recorder."""
import time

import pytest

from hermes_trader.agents import whale_flow_live as wf
from hermes_trader.agents.crypto_whale import WhaleReport


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "_TS_FILE", str(tmp_path / "ts.json"))


def _captured(monkeypatch):
    out = []
    monkeypatch.setattr(wf.shadow_ledger, "record_many",
                        lambda book, rows: out.append((book, rows)) or len(rows))
    return out


def _rep(bias, net=500_000.0):
    return WhaleReport(symbol="X", window_n=50, whale_n=5,
                       buy_usd=max(net, 0), sell_usd=max(-net, 0),
                       net_usd=net, bias=bias, min_usd=100_000.0)


def test_bias_maps_to_side_and_controls(monkeypatch):
    out = _captured(monkeypatch)
    reps = {"UP": _rep("whale_buying"), "DOWN": _rep("whale_selling", -400_000),
            "FLAT": _rep("balanced", 0)}
    monkeypatch.setattr(wf, "crypto_whale_signal", lambda c, **kw: reps[c])
    percs = [{"coin": c, "mid": 1.0} for c in ("UP", "DOWN", "FLAT")]
    assert wf.maybe_run({}, percs) == 3
    rows = {r["coin"]: r for r in out[0][1]}
    assert rows["UP"]["side"] == "long" and rows["UP"]["meta"]["control"] is False
    assert rows["DOWN"]["side"] == "short"
    assert rows["FLAT"]["meta"]["control"] is True


def test_equities_skipped_throttle_and_kill(monkeypatch):
    out = _captured(monkeypatch)
    monkeypatch.setattr(wf, "crypto_whale_signal", lambda c, **kw: _rep("balanced"))
    percs = [{"coin": "xyz:BE", "mid": 200.0}, {"coin": "BTC", "mid": 60000.0}]
    assert wf.maybe_run({}, percs) == 1                    # xyz skipped
    assert wf.maybe_run({}, percs) == 0                    # throttled
    assert wf.maybe_run({"whale_flow": {"enabled": False}}, percs) == 0
    assert [r["coin"] for r in out[0][1]] == ["BTC"]


def test_fetch_failure_never_raises(monkeypatch):
    _captured(monkeypatch)

    def boom(c, **kw):
        raise OSError("binance down")

    monkeypatch.setattr(wf, "crypto_whale_signal", boom)
    assert wf.maybe_run({}, [{"coin": "BTC", "mid": 1.0}]) == 0
