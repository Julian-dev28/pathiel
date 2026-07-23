"""Integration tests for the numerology_eth book against the REAL ClaimsRegistry.

Covers the two states and their shared-state effects: SHADOW (records, never executes,
never claims) and ARMED (claims ETH + routes through execute_fn), plus the collision and
failed-execute claim-release paths. Uses the real registry, not a mock, because the whole
point of the wiring is cross-book claim safety.
"""
from datetime import datetime, timezone

import pytest

from hermes_trader.agents import numerology_recorder as rec
from hermes_trader.agents import rebalancer_owned as ro


@pytest.fixture
def fresh(monkeypatch, tmp_path):
    """Isolate the daily-state file and reset the claims singleton to a clean tmp file."""
    monkeypatch.setattr(rec, "_STATE_FILE", str(tmp_path / "num_state.json"))
    monkeypatch.setattr(ro, "_CLAIMS_FILE", str(tmp_path / "claims.json"))
    monkeypatch.setattr(ro, "_claims_registry", None)
    return ro.get_claims_registry()


class Spy:
    def __init__(self, executed=True):
        self.calls = []
        self._executed = executed

    def __call__(self, analysis):
        self.calls.append(analysis)
        return {"executed": self._executed}


def _cfg(**over):
    base = {"enabled": True, "hour": 0, "leverage": 40, "equity_frac": 0.5, "horizon_days": 1.0}
    base.update(over)
    return {"numerology_eth": {**base}}


def test_book_is_registered_for_claims():
    assert "numerology_eth" in ro.active_claim_books()


def test_shadow_records_but_never_executes_or_claims(fresh):
    spy = Spy()
    uni = [{"coin": "ETH", "midPx": 2500.0}]
    n = rec.maybe_record(uni, _cfg(shadow_only=True), positions=[], execute_fn=spy)
    assert n == 1
    assert spy.calls == []                              # never routed live
    assert fresh.owner_of("ETH") is None               # never claimed


def test_armed_executes_and_claims_eth(fresh):
    spy = Spy(executed=True)
    uni = [{"coin": "ETH", "midPx": 2500.0}]
    n = rec.maybe_record(uni, _cfg(shadow_only=False), positions=[], execute_fn=spy)
    assert n == 1
    assert len(spy.calls) == 1
    a = spy.calls[0]
    assert a["strategy_book"] == "numerology_eth" and a["coin"] == "ETH"
    assert a["leverage_override"] == 40 and a["strategy_book_equity_frac_override"] == 0.5
    expect = "long" if rec.day_root_odd_dir(datetime.now(timezone.utc)) > 0 else "short"
    assert a["side"] == expect
    assert fresh.owner_of("ETH") == "numerology_eth"    # claim registered


def test_armed_stop_is_inside_liquidation(fresh):
    spy = Spy()
    rec.maybe_record([{"coin": "ETH", "midPx": 2500.0}], _cfg(shadow_only=False),
                     positions=[], execute_fn=spy)
    a = spy.calls[0]
    liq = 100.0 / a["leverage_override"]                # 2.5% at 40x
    assert a["backup_sl_pct_override"] < liq            # stops before it liquidates
    assert a["dsl_exit_override"]["max_loss_pct"] < liq


def test_armed_yields_to_an_existing_claim(fresh):
    fresh.claim("ETH", "xs_momentum")                   # another book already owns ETH
    fresh.save()
    spy = Spy()
    rec.maybe_record([{"coin": "ETH", "midPx": 2500.0}], _cfg(shadow_only=False),
                     positions=[], execute_fn=spy)
    assert spy.calls == []                              # did not trade
    assert fresh.owner_of("ETH") == "xs_momentum"       # collision respected


def test_armed_failed_execute_releases_claim(fresh):
    spy = Spy(executed=False)                            # executor blocks it
    rec.maybe_record([{"coin": "ETH", "midPx": 2500.0}], _cfg(shadow_only=False),
                     positions=[], execute_fn=spy)
    assert len(spy.calls) == 1
    assert fresh.owner_of("ETH") is None                # claim cleaned up, no leak
