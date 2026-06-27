from hermes_trader.agents import extreme_fade_live
from hermes_trader.agents import hail_mary_short_live
from hermes_trader.agents import rally_exhaustion_live
from hermes_trader.agents import xs_momentum_live


LIVE_BOOKS = (extreme_fade_live, rally_exhaustion_live, hail_mary_short_live, xs_momentum_live)


def test_legacy_none_spy_is_success_but_explicit_false_is_not():
    for module in LIVE_BOOKS:
        assert module._execute_opened(None) is True
        assert module._execute_opened({"executed": True}) is True
        assert module._execute_opened({"ok": True}) is True
        assert module._execute_opened(False) is False
        assert module._execute_opened({"executed": False, "reason": "blocked"}) is False


def test_block_detail_prefers_executor_gate_payload():
    for module in LIVE_BOOKS:
        assert module._execute_block_detail({"blocked_by": ["runner_gate_blocked"]}) == [
            "runner_gate_blocked"
        ]
        assert module._execute_block_detail({"gate_results": {"risk": {"pass": False}}}) == {
            "risk": {"pass": False}
        }
