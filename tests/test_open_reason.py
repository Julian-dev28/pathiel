"""The dashboard open-positions 'why this opened' reason line."""
from hermes_trader.agents.memory import memory
from hermes_trader.dashboard import _rows_from_state


def test_peek_entry_context_is_nondestructive():
    memory.record_entry_context("ZZZT", "long", {"book": "main-engine", "reason": "TA breakout 54.5"})
    assert memory.peek_entry_context("ZZZT", "long")["book"] == "main-engine"
    # peeking again still returns it (not popped)
    assert memory.peek_entry_context("ZZZT", "long")["reason"] == "TA breakout 54.5"
    # pop clears it; peek then empty
    assert memory.pop_entry_context("ZZZT", "long")["book"] == "main-engine"
    assert memory.peek_entry_context("ZZZT", "long") == {}


def _state(coin, szi):
    return {"asset_positions": [{"position": {
        "coin": coin, "szi": str(szi), "entryPx": "1.0", "positionValue": str(abs(szi) * 1.0),
        "unrealizedPnl": "0", "marginUsed": str(abs(szi)), "leverage": {"value": 1}}}]}


def test_row_carries_open_reason_and_book():
    memory.record_entry_context("ABCT", "short", {"book": "engulf_short",
                                                  "reason": "bearish full-body engulf (body-ratio 1.01)"})
    rows = _rows_from_state(_state("ABCT", -5))
    assert len(rows) == 1
    assert rows[0]["open_book"] == "engulf_short"
    assert "bearish full-body engulf" in rows[0]["open_reason"]
    memory.pop_entry_context("ABCT", "short")


def test_row_open_reason_empty_when_no_context():
    rows = _rows_from_state(_state("NOCTX", 3))
    assert rows[0]["open_reason"] == ""
    assert rows[0]["open_book"] == ""


def test_reload_entry_ctx_rereads_disk(tmp_path, monkeypatch):
    import json
    from hermes_trader.agents import memory as memmod
    # simulate the loop process having flushed an entry ctx to disk
    f = tmp_path / "mem.json"
    f.write_text(json.dumps({"entryCtx": {"RLDT_long": {"book": "main-engine", "reason": "x"}}}))
    monkeypatch.setattr(memmod, "MEMORY_FILE", str(f))
    memory._entry_ctx = {}                      # a separate consumer frozen at startup
    memory.reload_entry_ctx()                   # re-read from disk
    assert memory.peek_entry_context("RLDT", "long")["book"] == "main-engine"
    memory.pop_entry_context("RLDT", "long")


def test_open_reason_sanitized_for_html():
    memory.record_entry_context("SANT", "long", {"book": "x", "reason": 'a "quote" <tag>'})
    rows = _rows_from_state(_state("SANT", 1))
    r = rows[0]["open_reason"]
    assert '"' not in r and "<" not in r and ">" not in r
    memory.pop_entry_context("SANT", "long")
