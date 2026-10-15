"""Exchange-side closes detected inside close_position_market's flat branches.

UNI 2026-10-15 incident: the exchange-side SL trigger bracket filled at
14:47:49Z, 42s BEFORE the dsl-fast max_loss close ran. The close hit the
`already_flat` branch, DROPPED the live DSL tracker and booked nothing —
deregistering destroyed the only evidence, so rehydrate_from_exchange's
stale-tracker net (settle_stale_closes) never saw it: ghost OPEN, no CLOSE
row, no loss cooldown until a restart's reconcile.

The fix: every flat branch (already_flat / zero_szi /
no_fill_position_already_flat) consults _settle_exchange_side_flat BEFORE
touching the tracker — flat read + live tracker = an exchange-side close,
settled through the same stale-close path (fill attribution, CLOSE row,
outcome store, cooldown, B.36 guard). Unsettled trackers are KEPT for the
next-cycle retry, never silently dropped.

Offline: fills fetch, account state, memory and config are all injected.
"""
import json
import time

import pytest

from hermes_trader import ledger
from hermes_trader.agents import dsl_exit, executor
from hermes_trader import stale_close_settle


@pytest.fixture
def ledger_file(tmp_path):
    path = tmp_path / "trades.jsonl"
    old = ledger.LEDGER_FILE
    ledger.LEDGER_FILE = str(path)
    yield path
    ledger.LEDGER_FILE = old


@pytest.fixture
def clean_state(tmp_path, monkeypatch):
    """Isolate the tracker registry, its state file, and the B.36 dedupe map."""
    monkeypatch.setattr(dsl_exit, "DSL_STATE_FILE", str(tmp_path / "dsl-state.json"))
    saved = dict(dsl_exit._active_positions)
    dsl_exit._active_positions.clear()
    saved_dedupe = dict(executor._recently_closed_full)
    executor._recently_closed_full.clear()
    yield
    dsl_exit._active_positions.clear()
    dsl_exit._active_positions.update(saved)
    executor._recently_closed_full.clear()
    executor._recently_closed_full.update(saved_dedupe)


class FakeMemory:
    def __init__(self):
        self.closes = []
        self.cooldowns = {}

    def record_close(self, c):
        self.closes.append(c)

    def set_loss_cooldown(self, coin, until_ms):
        self.cooldowns[coin] = int(until_ms)

    def pop_entry_context(self, coin, side):
        return {}


@pytest.fixture
def fakes(monkeypatch, clean_state):
    """Patch executor's exchange/memory/config seams. Returns a handle bag."""
    class Bag:
        orders = []
        states = []
        fills = []
        fills_calls = 0
        cancels = []
        memory: object = None
    bag = Bag()
    bag.orders, bag.states, bag.fills, bag.cancels = [], [], [], []

    monkeypatch.setattr(executor, "resolve_user_address", lambda: "0xUSER")
    monkeypatch.setattr(executor, "fetch_account_state",
                        lambda u, **kw: bag.states.pop(0))
    monkeypatch.setattr(executor, "get_hl_price", lambda c: 7.30)
    monkeypatch.setattr(executor, "place_hl_order",
                        lambda **kw: bag.orders.append(kw) or dict(_ORDER_RESULT))
    monkeypatch.setattr(executor, "cancel_open_orders_for_coin",
                        lambda c: bag.cancels.append(c))
    fake_mem = FakeMemory()
    monkeypatch.setattr(executor, "memory", fake_mem)
    monkeypatch.setattr(executor, "read_agent_config",
                        lambda: {"loss_cooldown_min": 180,
                                 "stop_loss_cooldown_min": 360})

    def fake_fetch(resolve_user_address):
        bag.fills_calls += 1
        return list(bag.fills)
    monkeypatch.setattr(stale_close_settle, "_fetch_user_fills", fake_fetch)

    bag.memory = fake_mem
    return bag


_ORDER_RESULT = {"ok": True, "avg_px": 7.3084, "total_sz": 5.6, "order_id": "999"}


def _tracker(coin="UNI", side="short", entry_px=6.9493, size=5.6,
             leverage=5, entry_time=None):
    t = dsl_exit.DSLTracker(coin, side, entry_px,
                            entry_time or (time.time() - 3600),
                            dsl_exit.ExitPolicy(), leverage=leverage)
    t.size = size
    dsl_exit._active_positions[f"{coin}_{side}"] = t
    return t


def _flat_state(equity="150.0"):
    return {"asset_positions": [], "equity": equity}


def _pos_state(coin="UNI", szi="-5.6", entry_px="6.9493", equity="150.0"):
    return {"asset_positions": [{"position": {"coin": coin, "szi": szi,
                                              "entryPx": entry_px}}],
            "equity": equity}


def _close_fill(coin="UNI", dirn="Close Short", px=7.3084, sz=5.6,
                closed_pnl="-2.01096", ts=None):
    return {"coin": coin, "dir": dirn, "px": str(px), "sz": str(sz),
            "time": int((ts or time.time()) * 1000), "closedPnl": closed_pnl}


def _rows(path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def test_already_flat_with_tracker_and_fill_books_close(fakes, ledger_file):
    """THE UNI SHAPE: flat read + live tracker + attributable fill →
    CLOSE booked, tracker deregistered, loss cooldown armed, no order sent."""
    _tracker()
    fakes.states.append(_flat_state())
    fakes.fills.append(_close_fill())

    res = executor.close_position_market("UNI", "max_loss (5.35% spot)")

    assert res["noop"] == "already_flat"
    assert fakes.orders == [], "flat branch must never send an order"
    rows = _rows(ledger_file)
    assert len(rows) == 1 and rows[0]["event"] == "CLOSE"
    assert rows[0]["coin"] == "UNI" and rows[0]["side"] == "short"
    assert rows[0]["exit_type"] == "exchange_close"
    assert rows[0]["realized_pnl_usd"] == pytest.approx(-2.011, abs=1e-4)
    assert "UNI_short" not in dsl_exit._active_positions
    assert "UNI" in fakes.memory.cooldowns, "losing exchange close arms cooldown"
    assert len(fakes.memory.closes) == 1, "outcome store booked"


def test_already_flat_with_tracker_no_fill_keeps_tracker(fakes, ledger_file):
    """No fill evidence yet → book NOTHING and KEEP the tracker so
    rehydrate_from_exchange retries next cycle (never destroy evidence)."""
    _tracker(coin="ZRO")
    fakes.states.append(_flat_state())
    fakes.fills = []  # fill not propagated to userFills yet

    res = executor.close_position_market("ZRO", "max_loss (5.05% spot)")

    assert res["noop"] == "already_flat_settle_pending"
    assert "ZRO_short" in dsl_exit._active_positions, "tracker must survive"
    assert not ledger_file.exists() or _rows(ledger_file) == []
    assert fakes.memory.closes == []


def test_already_flat_no_tracker_legacy_behaviour(fakes, ledger_file):
    """No live tracker (e.g. a duplicate close after the booked one) →
    legacy no-op, settle path never touched."""
    fakes.states.append(_flat_state())

    res = executor.close_position_market("ARB")

    assert res["noop"] == "already_flat"
    assert fakes.fills_calls == 0, "settle path must not run without a tracker"


def test_already_flat_untrusted_read_keeps_tracker(fakes, ledger_file):
    """equity <= 0 = a failed read looks exactly like a flat account —
    trackers KEPT, no settle attempt (same rule as the no-fill branch)."""
    _tracker(coin="PONS")
    fakes.states.append(_flat_state(equity="0"))

    res = executor.close_position_market("PONS", "max_loss")

    assert res["noop"] == "already_flat_settle_pending"
    assert "PONS_short" in dsl_exit._active_positions
    assert fakes.fills_calls == 0, "untrusted read must not hit userFills"


def test_zero_szi_with_tracker_books_close(fakes, ledger_file):
    """Position entry present but szi==0 (closed, basis lingering) — same
    settle treatment as `not pos`."""
    _tracker(coin="LIT", entry_px=1.0, size=10.0)
    fakes.states.append(_pos_state(coin="LIT", szi="0", entry_px="1.0"))
    fakes.fills.append(_close_fill(coin="LIT", px=1.05, sz=10.0,
                                   closed_pnl="-0.5"))

    res = executor.close_position_market("LIT", "max_loss")

    assert res["noop"] == "zero_szi"
    rows = _rows(ledger_file)
    assert len(rows) == 1 and rows[0]["coin"] == "LIT"
    assert "LIT_short" not in dsl_exit._active_positions


def test_no_fill_already_flat_branch_with_tracker_books_close(fakes, ledger_file):
    """Reduce-only order matched nothing + confirmed-flat positive-equity
    read + live tracker → settle BEFORE deregistering (the fill was the
    exchange-side trigger, not our order)."""
    _tracker(coin="MON")
    fakes.states.append(_pos_state(coin="MON"))   # pre-order: still open
    fakes.states.append(_flat_state())            # post-order verify: flat
    fakes.fills.append(_close_fill(coin="MON"))
    global _ORDER_RESULT
    _ORDER_RESULT = {"ok": True}  # no avg_px → no-fill verification branch

    res = executor.close_position_market("MON", "max_loss")

    assert res["noop"] == "no_fill_position_already_flat"
    rows = _rows(ledger_file)
    assert len(rows) == 1 and rows[0]["coin"] == "MON"
    assert rows[0]["exit_type"] == "exchange_close"
    assert "MON_short" not in dsl_exit._active_positions
    assert "MON" in fakes.cancels, "stranded bracket still cancelled"


def test_no_double_book_when_executor_already_booked(fakes, ledger_file):
    """Executor books a full close (fill path) → tracker gone → a second
    close call is the legacy already_flat no-op, never a second row."""
    _tracker(coin="ETH")
    fakes.states.append(_pos_state(coin="ETH", szi="-5.6", entry_px="6.9493"))
    global _ORDER_RESULT
    _ORDER_RESULT = {"ok": True, "avg_px": 7.3084, "total_sz": 5.6,
                     "order_id": "1"}
    res1 = executor.close_position_market("ETH", "max_loss")
    assert res1.get("noop") is None and res1["ok"]

    fakes.states.append(_flat_state())
    fakes.fills.append(_close_fill(coin="ETH"))
    res2 = executor.close_position_market("ETH", "max_loss")

    assert res2["noop"] == "already_flat"
    rows = _rows(ledger_file)
    assert len(rows) == 1, "exactly one CLOSE row — no double-book"
