"""Dashboard audit fixes (2026-07-10): incremental log reader, bounded tail,
equity-curve sustained-drop acceptance, state-write gating in the server process.

2026-10-17 ux-qa sweep added the tests below: every dashboard GET route now
runs its payload builder off the event loop thread (_run_off_loop), and a
config error ('no HL address resolvable') is treated as a failure the
existing last-known-good cache should survive, not as proof the account is
flat."""
import json

import pytest



def _write_lines(path, events):
    with open(path, "a") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def test_incremental_reader_parses_only_new_bytes(tmp_path, monkeypatch):
    from pathiel import dashboard as db
    log = tmp_path / "sess.jsonl"
    monkeypatch.setattr(db, "_LOG_PATH", log)
    monkeypatch.setitem(db._LOG_STATE, "offset", 0)
    monkeypatch.setitem(db._LOG_STATE, "ino", None)
    db._LOG_STATE["events"].clear()

    _write_lines(log, [{"ts": i, "event": "loop_heartbeat", "equity": 50 + i} for i in range(3)])
    out = db._read_log_lines()
    assert len(out) == 3 and out[-1]["equity"] == 52
    off1 = db._LOG_STATE["offset"]
    # append two more: only the delta is parsed (offset advances, events grow by 2)
    _write_lines(log, [{"ts": 3, "event": "scan"}, {"ts": 4, "event": "scan"}])
    out = db._read_log_lines()
    assert len(out) == 5 and db._LOG_STATE["offset"] > off1
    # no change -> no reparse, same snapshot
    assert len(db._read_log_lines()) == 5


def test_incremental_reader_handles_truncation(tmp_path, monkeypatch):
    from pathiel import dashboard as db
    log = tmp_path / "sess.jsonl"
    monkeypatch.setattr(db, "_LOG_PATH", log)
    db._LOG_STATE.update({"offset": 0, "ino": None})
    db._LOG_STATE["events"].clear()
    _write_lines(log, [{"ts": i, "event": "e"} for i in range(5)])
    assert len(db._read_log_lines()) == 5
    log.write_text('{"ts": 99, "event": "fresh"}\n')     # truncated/rotated
    out = db._read_log_lines()
    assert len(out) == 1 and out[0]["ts"] == 99


def test_tail_reads_bounded_block(tmp_path, monkeypatch):
    from pathiel import session_log as sl
    log = tmp_path / "sess.jsonl"
    monkeypatch.setattr(sl, "SESSION_LOG_FILE", str(log))
    _write_lines(log, [{"i": i} for i in range(500)])
    out = sl.tail(5)
    assert [e["i"] for e in out] == [495, 496, 497, 498, 499]
    assert sl.tail(0) == []


def test_equity_curve_accepts_sustained_drop(monkeypatch):
    from pathiel import dashboard as db
    import time as _t
    now = int(_t.time() * 1000)
    events = ([{"ts": now - 10_000 + i, "event": "loop_heartbeat", "equity": 150.0} for i in range(5)]
              + [{"ts": now - 5_000 + i, "event": "loop_heartbeat", "equity": 60.0} for i in range(4)])
    monkeypatch.setattr(db, "_read_log_lines", lambda: events)
    series = db._equity_curve_payload(86_400)
    eqs = [p["equity"] for p in series]
    # one-tick spikes are still filtered (first 2 sub-70% drops), but the
    # SUSTAINED drop is accepted from the 3rd reading on — no permanent freeze
    assert 60.0 in eqs
    assert eqs[:5] == [150.0] * 5


# test_state_readonly_gates_memory_flush (PATHIEL_STATE_READONLY gating
# pathiel.agents.memory's flush) was removed 2026-10-23, wave 2: memory.py
# is deleted with the rest of the retired autonomous engine, and nothing
# else in the surviving codebase writes a state file PATHIEL_STATE_READONLY
# is meant to gate.


def test_run_off_loop_does_not_block_the_event_loop():
    """Regression (2026-10-17 ux-qa sweep): every dashboard GET route called
    its (possibly blocking) payload builder directly inside its `async def`
    handler, with no `await` or executor offload. /api/dashboard/coin_chart
    makes a real HTTP round trip to Hyperliquid this way
    (_coin_chart_payload -> fetch_hl_candles) — while that call was in
    flight, uvicorn's ONE event loop could not schedule ANY other request,
    for ANY page, for ANY visitor. Confirmed live: a concurrent /analytics
    load showed four unrelated, individually-fast (<10ms) endpoints all
    completing within ~1ms of the slow chart call's ~200ms — the signature
    of head-of-line blocking, not five slow endpoints.

    Proof that _run_off_loop actually fixes this: route a 0.3s SYNCHRONOUS
    (time.sleep) call through it inside an event loop that is also trying to
    make unrelated progress on a ticker. A blocked loop ticks zero times
    during those 0.3s; a free one ticks roughly 25-30."""
    import asyncio
    import time as _t

    from pathiel import dashboard as db

    def slow():
        _t.sleep(0.3)
        return "done"

    async def run():
        progress = {"ticks": 0}

        async def ticker():
            while True:
                await asyncio.sleep(0.01)
                progress["ticks"] += 1

        ticker_task = asyncio.ensure_future(ticker())
        result = await db._run_off_loop(slow)
        ticker_task.cancel()
        return result, progress["ticks"]

    result, ticks = asyncio.run(run())
    assert result == "done"
    assert ticks >= 10, (
        f"event loop was blocked for the duration of the off-loop call — "
        f"only {ticks} ticker ticks landed during the 0.3s window")


def test_positions_uncached_raises_rather_than_silently_reporting_flat(monkeypatch):
    """Regression (2026-10-17 ux-qa sweep): _positions_payload_uncached()
    used to `return []` when no HL address was resolvable — a NORMAL return,
    not an exception, so _positions_payload()'s own except-block (which
    exists specifically to serve the last known good read on a fetch
    failure instead of reporting a false flat) never engaged for this path.

    Reproduced live on the demo deployment: once the positions snapshot aged
    past its 120s freshness window with no address configured, this
    silently overwrote a cache holding 3 real positions with an empty list
    — while the summary KPI's open_positions, sourced from the heartbeat (a
    300s staleness window instead), kept confidently showing 3. Two panels
    on one screen disagreeing about whether the account held anything, with
    nothing on the page saying so."""
    from pathiel import dashboard as db
    monkeypatch.setattr(db, "resolve_user_address", lambda: "")
    with pytest.raises(RuntimeError, match="no HL address resolvable"):
        db._positions_payload_uncached()


def test_positions_payload_serves_last_known_good_when_address_unresolvable(monkeypatch):
    """The point of raising instead of returning []: _positions_payload()'s
    existing except-block now actually sees the failure (instead of a
    falsely-successful empty read) and serves the cached last-known-good
    rows instead of overwriting them with a false flat."""
    import time as _t

    from pathiel import dashboard as db
    good_rows = [{"coin": "BTC", "side": "long"}]
    with db._POSITIONS_CACHE_LOCK:
        # old enough that the next call actually attempts a fresh read
        db._POSITIONS_CACHE["ts"] = _t.time() - db._POSITIONS_CACHE_TTL_S - 1
        db._POSITIONS_CACHE["data"] = good_rows
    monkeypatch.setattr(db, "resolve_user_address", lambda: "")
    assert db._positions_payload() == good_rows
