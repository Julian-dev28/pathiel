from hermes_trader.agents.risk_gates import book_block_event


def test_block_surfaces_blocked_by_list():
    analysis = {"coin": "FOGO", "side": "long", "strategy_book": "extreme_fade"}
    result = {"executed": False, "blocked_by": ["market 24h volume $0.22M below floor $0.70M"]}
    evt = book_block_event(analysis, result)
    assert evt == {
        "event": "execute", "executed": False, "coin": "FOGO", "side": "long",
        "book": "extreme_fade",
        "blocked_by": ["market 24h volume $0.22M below floor $0.70M"],
    }


def test_block_falls_back_to_reason_then_gate_results():
    analysis = {"coin": "X", "side": "short", "strategy_book": "engulf_short"}
    assert book_block_event(analysis, {"executed": False, "reason": "thin"})["blocked_by"] == "thin"
    assert book_block_event(analysis, {"executed": False, "gate_results": {"liquidity": False}})[
        "blocked_by"] == {"liquidity": False}


def test_executed_entry_emits_nothing():
    analysis = {"coin": "X", "side": "long", "strategy_book": "vol_breakout_long"}
    assert book_block_event(analysis, {"executed": True}) is None


def test_non_book_entry_emits_nothing():
    # Main-engine entries already get their own execute event from the loop.
    analysis = {"coin": "X", "side": "long"}
    assert book_block_event(analysis, {"executed": False, "reason": "blocked"}) is None


def test_malformed_result_emits_nothing():
    analysis = {"coin": "X", "side": "long", "strategy_book": "engulf_short"}
    assert book_block_event(analysis, None) is None
    assert book_block_event(analysis, "oops") is None
