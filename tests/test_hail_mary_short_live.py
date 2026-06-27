import json

from hermes_trader.agents import hail_mary_short_live as hms
from hermes_trader.agents.rebalancer_owned import ClaimsRegistry, active_claim_books


def _bear_bars(n=60):
    closes = [120.0 - i * 0.7 for i in range(n)]
    closes[-1] = closes[-2] - 4.0
    out = []
    for i, close in enumerate(closes):
        out.append({
            "t": i * hms._DAY_MS,
            "o": close + 0.4,
            "h": close + 0.8,
            "l": close - 0.8,
            "c": close,
            "v": 1_000_000,
        })
    return out


def _flat_bars(n=60):
    out = []
    for i in range(n):
        close = 100.0 + (i % 3) * 0.1
        out.append({
            "t": i * hms._DAY_MS,
            "o": close,
            "h": close + 0.5,
            "l": close - 0.5,
            "c": close,
            "v": 1_000_000,
        })
    return out


def _universe(vol=100_000_000):
    return [
        {"coin": "xyz:NVDA", "type": "perp", "dex": "xyz", "dayNtlVlm": vol},
        {"coin": "xyz:AMD", "type": "perp", "dex": "xyz", "dayNtlVlm": vol},
        {"coin": "xyz:SMH", "type": "perp", "dex": "xyz", "dayNtlVlm": vol},
        {"coin": "BTC", "type": "perp", "dex": None, "dayNtlVlm": vol},
    ]


def _cfg(**overrides):
    cfg = {
        "enabled": True,
        "shadow_only": True,
        "names": ["NVDA", "AMD", "SMH"],
        "proxy_coins": ["xyz:SMH"],
        "scan_interval_hours": 0,
        "entry_window_hours": 24,
        "min_volume_usd": 1,
        "executor_short_volume_floor_usd": 1,
        "min_breadth_bearish_pct": 0.5,
        "history_bars": 60,
        "min_history_bars": 24,
        "breakdown_lookback_days": 20,
        "recent_drop_days": 5,
        "min_recent_drop_pct": 2.0,
        "stop_pct": 12.0,
        "notional_usd": 20.0,
        "max_new_per_cycle": 1,
    }
    cfg.update(overrides)
    return {"hail_mary_short": cfg}


def _setup_files(monkeypatch, tmp_path):
    monkeypatch.setattr(hms, "_TS_FILE", str(tmp_path / ".hail_mary_short_ts"))
    monkeypatch.setattr(hms, "_SEEN_FILE", str(tmp_path / ".hail_mary_short_seen.json"))
    last_t = _bear_bars()[-1]["t"]
    monkeypatch.setattr(hms.time, "time", lambda: (last_t + hms._DAY_MS + 60_000) / 1000.0)


def _fetch_factory(bars_by_coin):
    def fetch(coin, interval, n):
        assert interval == "1d"
        return bars_by_coin.get(coin, _flat_bars())[-n:]
    return fetch


def test_shadow_mode_logs_candidates_without_executing(monkeypatch, tmp_path):
    _setup_files(monkeypatch, tmp_path)
    monkeypatch.setattr(hms, "log_event", lambda rec: None)
    fetch = _fetch_factory({
        "xyz:NVDA": _bear_bars(),
        "xyz:AMD": _bear_bars(),
        "xyz:SMH": _bear_bars(),
    })
    calls = []

    rec = hms.maybe_run(_cfg(), _universe(), [], fetch, lambda a: calls.append(a))

    assert rec["shadow"] is True
    assert rec["context"]["risk_off"] is True
    assert rec["signals"] >= 1
    assert calls == []


def test_live_open_claims_and_sends_strategy_book_analysis(monkeypatch, tmp_path):
    _setup_files(monkeypatch, tmp_path)
    monkeypatch.setattr(hms, "log_event", lambda rec: None)
    registry = ClaimsRegistry(str(tmp_path / "claims.json"), active_books=active_claim_books()).load()
    monkeypatch.setattr(hms, "get_claims_registry", lambda: registry)
    fetch = _fetch_factory({
        "xyz:NVDA": _bear_bars(),
        "xyz:AMD": _bear_bars(),
        "xyz:SMH": _bear_bars(),
    })
    calls = []

    def execute(analysis):
        calls.append(analysis)
        return {"executed": True}

    rec = hms.maybe_run(_cfg(shadow_only=False), _universe(), [], fetch, execute)

    assert rec["opened"] == 1
    assert len(calls) == 1
    assert calls[0]["strategy_book"] == "hail_mary_short"
    assert calls[0]["verdict"] == "SHORT"
    assert calls[0]["strategy_book_notional"] == 20.0
    assert registry.owner_of(calls[0]["coin"]) == "hail_mary_short"
    seen = json.loads((tmp_path / ".hail_mary_short_seen.json").read_text())
    assert calls[0]["coin"] in seen


def test_blocked_live_open_releases_claim(monkeypatch, tmp_path):
    _setup_files(monkeypatch, tmp_path)
    monkeypatch.setattr(hms, "log_event", lambda rec: None)
    registry = ClaimsRegistry(str(tmp_path / "claims.json"), active_books=active_claim_books()).load()
    monkeypatch.setattr(hms, "get_claims_registry", lambda: registry)
    fetch = _fetch_factory({
        "xyz:NVDA": _bear_bars(),
        "xyz:AMD": _bear_bars(),
        "xyz:SMH": _bear_bars(),
    })

    rec = hms.maybe_run(
        _cfg(shadow_only=False),
        _universe(),
        [],
        fetch,
        lambda analysis: {"executed": False, "reason": "blocked"},
    )

    assert rec["opened"] == 0
    assert rec["skipped"]["blocked"] == 1
    assert registry.claims() == {}


def test_no_signals_when_proxy_is_not_bearish(monkeypatch, tmp_path):
    _setup_files(monkeypatch, tmp_path)
    monkeypatch.setattr(hms, "log_event", lambda rec: None)
    fetch = _fetch_factory({
        "xyz:NVDA": _bear_bars(),
        "xyz:AMD": _bear_bars(),
        "xyz:SMH": _flat_bars(),
    })

    rec = hms.maybe_run(_cfg(), _universe(), [], fetch, lambda analysis: None)

    assert rec["context"]["risk_off"] is False
    assert rec["signals"] == 0
