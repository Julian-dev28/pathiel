"""Gate tests for the unlock_short_runin LIVE book (operator flip 2026-07-11)."""
import time

import pytest

from hermes_trader.agents import unlock_short_live as usl

_NOW = int(time.time() * 1000)
_H = usl._HOUR_MS


class _FakeClaims:
    def __init__(self, others=None):
        self.claimed = []
        self.released = []
        self._others = set(others or ())

    def prune_to(self, held, book):
        pass

    def claimed_by_others(self, book):
        return set(self._others)

    def claim(self, coin, book):
        self.claimed.append(coin)
        return True

    def release(self, coin, book):
        self.released.append(coin)

    def save(self):
        pass


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(usl, "_SEEN_FILE", str(tmp_path / "seen.json"))
    monkeypatch.setattr(usl, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(usl, "active_position_coins", lambda: {})


def _wire(monkeypatch, upcoming, claims=None):
    monkeypatch.setattr(usl.unlock_recorder, "_load_state",
                        lambda: {"upcoming": upcoming})
    c = claims or _FakeClaims()
    monkeypatch.setattr(usl, "get_claims_registry", lambda: c)
    return c


def _cfg(**over):
    base = {"unlock_short": {"enabled": True, "shadow_only": False,
                             "notional_usd": 20.0, "leverage": 1,
                             "stop_pct": 15.0, "min_volume_usd": 5e6}}
    base["unlock_short"].update(over)
    return base


def _uni(coin="ARB", vol=1e7):
    return [{"coin": coin, "midPx": 1.0, "dayNtlVlm": vol}]


def _ev(coin="ARB", hours_out=60.0, pct=3.0):
    return {"coin": coin, "t_ms": _NOW + hours_out * _H, "pct": pct}


def test_opens_short_inside_runin_window(monkeypatch):
    _wire(monkeypatch, [_ev(hours_out=60)])
    calls = []

    def execute(a):
        calls.append(a)
        return {"executed": True}

    rec = usl.maybe_run(_cfg(), _uni(), [], execute)
    assert rec["opened"] == 1
    a = calls[0]
    assert a["strategy_book"] == "unlock_short_runin"
    assert a["side"] == "short" and a["strategy_book_notional"] == 20.0
    assert a["leverage_override"] == 1
    # exits AT the unlock: timeout ~= hours_to_unlock
    assert 59 * 60 <= a["dsl_exit_override"]["hard_timeout_minutes"] <= 61 * 60
    assert a["dsl_exit_override"]["max_loss_pct"] == 15.0
    assert a["dsl_exit_override"]["protect_pct"] == 9999.0   # no trail


def test_window_pct_and_volume_bounds(monkeypatch):
    _wire(monkeypatch, [
        _ev("LATE", hours_out=20),          # inside 24h — recorder arm, not this book
        _ev("FAR", hours_out=80),           # beyond window
        _ev("SMALL", hours_out=60, pct=0.5),  # below min pct
        _ev("THIN", hours_out=60, pct=5.0),   # volume floor
    ])
    uni = (_uni("LATE") + _uni("FAR") + _uni("SMALL")
           + _uni("THIN", vol=1e6))
    calls = []
    rec = usl.maybe_run(_cfg(), uni, [], lambda a: calls.append(a) or {"executed": True})
    assert rec is None and calls == []


def test_event_dedup_and_held_skip(monkeypatch):
    _wire(monkeypatch, [_ev(hours_out=60)])
    n = usl.maybe_run(_cfg(), _uni(), [], lambda a: {"executed": True})
    assert n["opened"] == 1
    again = usl.maybe_run(_cfg(), _uni(), [], lambda a: {"executed": True})
    assert again["opened"] == 0 and again["skipped"]["seen"] == 1
    # held coin skipped
    _wire(monkeypatch, [_ev("HELD", hours_out=60)])
    held_pos = [{"position": {"coin": "HELD", "szi": "1.0"}}]
    rec = usl.maybe_run(_cfg(), _uni("HELD"), held_pos, lambda a: {"executed": True})
    assert rec["opened"] == 0 and rec["skipped"]["held"] == 1


def test_blocked_execute_releases_claim(monkeypatch):
    claims = _FakeClaims()
    _wire(monkeypatch, [_ev(hours_out=60)], claims)
    rec = usl.maybe_run(_cfg(), _uni(), [],
                        lambda a: {"executed": False, "blocked_by": ["gate"]})
    assert rec["opened"] == 0 and rec["skipped"]["blocked"] == 1
    assert claims.released == ["ARB"]
    # not marked seen -> retries next cycle
    rec2 = usl.maybe_run(_cfg(), _uni(), [], lambda a: {"executed": True})
    assert rec2["opened"] == 1


def test_biggest_unlock_first_and_max_new(monkeypatch):
    _wire(monkeypatch, [_ev("A", 60, pct=2.0), _ev("B", 60, pct=8.0)])
    calls = []
    rec = usl.maybe_run(_cfg(max_new_per_cycle=1), _uni("A") + _uni("B"), [],
                        lambda a: calls.append(a["coin"]) or {"executed": True})
    assert calls == ["B"] and rec["opened"] == 1


def test_kill_switches(monkeypatch):
    _wire(monkeypatch, [_ev(hours_out=60)])
    assert usl.maybe_run(_cfg(enabled=False), _uni(), [], lambda a: {"executed": True}) is None
    assert usl.maybe_run(_cfg(shadow_only=True), _uni(), [], lambda a: {"executed": True}) is None


def test_book_is_claims_allowlisted():
    from hermes_trader.agents.rebalancer_owned import active_claim_books
    assert "unlock_short_runin" in active_claim_books()
