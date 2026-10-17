"""entry_order_mode: "taker" | "maker_first" — see docs/overhaul/PROFIT_AUDIT.md.

Fee audit evidence: 88/88 fills over the trailing 60d were taker (crossed=True,
$0 maker volume), at 3x the maker rate on both the main dex (0.045% vs 0.015%)
and HIP-3 xyz (0.009% observed, growth-mode). `place_entry_order` in
pathiel.client.exchange gates a post-only-first entry behind this config key,
defaulting to "taker" so no existing call site changes behavior until the
operator flips it.

Every test here drives `place_entry_order` directly against a fully-stubbed
exchange surface — no network, no private key, no sleeping (time.sleep is
patched to a no-op so the maker_first poll loop runs instantly).
"""
from __future__ import annotations

import pytest

import pathiel.client.exchange as ex


# ── shared stubs ──────────────────────────────────────────────────────────────

def _resting_raw(oid=42):
    return {"status": "ok", "response": {"data": {"statuses": [{"resting": {"oid": oid}}]}}}


def _rejected_raw(msg="Post only order would have matched immediately"):
    return {"status": "ok", "response": {"data": {"statuses": [{"error": msg}]}}}


class _FakeExchange:
    """Records every .order() call; returns canned raw SDK responses in order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def order(self, coin, is_buy, size, px, order_type, reduce_only=False):
        self.calls.append({
            "coin": coin, "is_buy": is_buy, "size": size, "px": px,
            "tif": order_type.get("limit", {}).get("tif"),
            "reduce_only": reduce_only,
        })
        return self._responses.pop(0) if self._responses else _resting_raw()


def _common_stubs(monkeypatch, *, fake_exchange=None, touch_px=100.0,
                   status_sequence=(), fills=(0.0, 0.0)):
    """Wire every collaborator place_entry_order's maker path touches.

    `status_sequence`: successive fetch_order_status() returns; the last
    value repeats if polling outlasts the sequence (models "stays open until
    timeout" with a single-element sequence).
    `fills`: the (size, avg_px) _fills_for_order should report once polling
    stops — i.e. what actually landed on the resting order.
    """
    monkeypatch.setattr(ex, "get_coin_index", lambda coin: (0, 2, 4))
    monkeypatch.setattr(ex, "_best_touch_price", lambda coin, is_buy, mid: touch_px)
    monkeypatch.setattr(ex.time, "sleep", lambda s: None)

    if fake_exchange is None:
        fake_exchange = _FakeExchange([_resting_raw()])
    monkeypatch.setattr(ex, "_make_exchange", lambda: fake_exchange)

    seq = list(status_sequence) or ["open"]

    def _status(oid):
        return seq.pop(0) if len(seq) > 1 else seq[0]
    monkeypatch.setattr(ex, "fetch_order_status", _status)

    cancels = []
    monkeypatch.setattr(ex, "cancel_orders", lambda oid, coin: cancels.append((oid, coin)) or {"ok": True})

    monkeypatch.setattr(ex, "_fills_for_order", lambda oid, coin, since_ms: fills)

    taker_calls = []

    def _taker(is_buy, size, mid_price, coin, reduce_only=False):
        taker_calls.append({"is_buy": is_buy, "size": size, "mid_price": mid_price,
                            "coin": coin, "reduce_only": reduce_only})
        return {"ok": True, "order_id": "TAKER1", "avg_px": mid_price, "total_sz": size}
    monkeypatch.setattr(ex, "place_hl_order", _taker)

    return fake_exchange, cancels, taker_calls


# ── flag off: byte-identical to the pre-existing direct call ────────────────

def test_flag_off_defaults_to_taker_byte_identical(monkeypatch):
    calls = []

    def _place_hl_order(is_buy, size, mid_price, coin, reduce_only=False):
        calls.append((is_buy, size, mid_price, coin, reduce_only))
        return {"ok": True, "order_id": "X1", "avg_px": mid_price, "total_sz": size}

    monkeypatch.setattr(ex, "place_hl_order", _place_hl_order)

    direct = ex.place_hl_order(True, 1.5, 100.0, "BTC")
    via_wrapper = ex.place_entry_order(True, 1.5, 100.0, "BTC", config={})

    assert via_wrapper == direct
    # Exactly two identical calls recorded: the direct one and the wrapper's
    # pass-through, with no extra args injected.
    assert calls == [(True, 1.5, 100.0, "BTC", False), (True, 1.5, 100.0, "BTC", False)]


def test_explicit_taker_mode_is_also_byte_identical(monkeypatch):
    calls = []
    monkeypatch.setattr(ex, "place_hl_order", lambda *a, **k: calls.append((a, k)) or {"ok": True})
    ex.place_entry_order(False, 2.0, 50.0, "ETH", config={"entry_order_mode": "taker"})
    assert calls == [((False, 2.0, 50.0, "ETH"), {"reduce_only": False})]


def test_unknown_mode_falls_back_to_taker(monkeypatch):
    """A typo'd mode must not silently open a maker order with no safety net."""
    calls = []
    monkeypatch.setattr(ex, "place_hl_order", lambda *a, **k: calls.append(a) or {"ok": True})
    ex.place_entry_order(True, 1.0, 10.0, "SOL", config={"entry_order_mode": "makerfirst_typo"})
    assert calls == [(True, 1.0, 10.0, "SOL")]


def test_no_config_at_all_defaults_to_taker(monkeypatch):
    calls = []
    monkeypatch.setattr(ex, "place_hl_order", lambda *a, **k: calls.append(a) or {"ok": True})
    ex.place_entry_order(True, 1.0, 10.0, "SOL")
    assert calls == [(True, 1.0, 10.0, "SOL")]


# ── maker_first: fills immediately ───────────────────────────────────────────

def test_maker_first_fills_immediately_no_taker_fallback(monkeypatch):
    fake, cancels, taker_calls = _common_stubs(
        monkeypatch,
        status_sequence=["filled"],      # first poll already shows filled
        fills=(1.0, 99.5),               # the whole size, at the maker price
    )
    res = ex.place_entry_order(True, 1.0, 100.0, "BTC",
                               config={"entry_order_mode": "maker_first"})
    assert res == {"ok": True, "order_id": "42", "avg_px": 99.5, "total_sz": 1.0}
    assert not taker_calls, "a fully-filled maker leg must never taker the remainder"
    # The ALO leg actually posted post-only, joining the touch (100.0, the
    # default touch_px from _common_stubs, exactly representable at 2dp):
    assert fake.calls[0]["tif"] == "Alo"
    assert fake.calls[0]["px"] == 100.0


def test_maker_first_fills_immediately_stops_polling_on_first_check(monkeypatch):
    """No busy-waiting once the fill is visible — status is checked once, not
    looped to the timeout."""
    poll_count = {"n": 0}
    fake, cancels, taker_calls = _common_stubs(monkeypatch, fills=(1.0, 99.0))

    def _status(oid):
        poll_count["n"] += 1
        return "filled"
    monkeypatch.setattr(ex, "fetch_order_status", _status)

    ex.place_entry_order(True, 1.0, 100.0, "BTC", config={"entry_order_mode": "maker_first"})
    assert poll_count["n"] == 1


# ── maker_first: partial fill, then timeout, then taker remainder ──────────

def test_maker_first_partial_fill_then_timeout_takes_taker_remainder(monkeypatch):
    fake, cancels, taker_calls = _common_stubs(
        monkeypatch,
        status_sequence=["open"],        # never reports filled -> exhausts the poll budget
        fills=(0.4, 99.0),                # 0.4 of 1.0 rested and filled at the maker price
    )
    res = ex.place_entry_order(True, 1.0, 100.0, "BTC",
                               config={"entry_order_mode": "maker_first",
                                       "maker_first_timeout_s": 1.0,
                                       "maker_first_poll_interval_s": 0.25})

    assert cancels == [("42", "BTC")] or cancels == [(42, "BTC")]
    assert len(taker_calls) == 1
    assert taker_calls[0]["size"] == 0.6   # exactly the unfilled remainder
    assert taker_calls[0]["is_buy"] is True
    # Blended avg price: size-weighted mean of the maker fill (0.4@99.0) and
    # the taker fill (0.6@mid_price=100.0, per the taker stub's echo).
    assert res["ok"] is True
    assert res["total_sz"] == pytest.approx(1.0)
    expected_avg = (0.4 * 99.0 + 0.6 * 100.0) / 1.0
    assert res["avg_px"] == pytest.approx(expected_avg)


# ── maker_first: price runs away (never fills at all) ───────────────────────

def test_maker_first_price_runs_away_full_timeout_zero_fill(monkeypatch):
    fake, cancels, taker_calls = _common_stubs(
        monkeypatch,
        status_sequence=["open"],
        fills=(0.0, 0.0),                 # the book ran away; nothing ever rested into a fill
    )
    res = ex.place_entry_order(True, 2.0, 100.0, "BTC",
                               config={"entry_order_mode": "maker_first",
                                       "maker_first_timeout_s": 0.5,
                                       "maker_first_poll_interval_s": 0.1})

    assert len(taker_calls) == 1
    assert taker_calls[0]["size"] == 2.0   # the FULL original size, nothing was saved
    assert res == {"ok": True, "order_id": "TAKER1", "avg_px": 100.0, "total_sz": 2.0}


def test_maker_first_timeout_budget_bounds_poll_count(monkeypatch):
    """timeout_s / poll_interval_s caps how many times status is checked —
    this is what keeps maker_first from stalling the scan loop indefinitely."""
    poll_count = {"n": 0}
    _common_stubs(monkeypatch, status_sequence=["open"], fills=(0.0, 0.0))

    def _status(oid):
        poll_count["n"] += 1
        return "open"
    monkeypatch.setattr(ex, "fetch_order_status", _status)

    ex.place_entry_order(True, 1.0, 100.0, "BTC",
                         config={"entry_order_mode": "maker_first",
                                 "maker_first_timeout_s": 2.0,
                                 "maker_first_poll_interval_s": 0.5})
    assert poll_count["n"] == 4   # 2.0 / 0.5


# ── maker_first: post-only rejected outright ─────────────────────────────────

def test_maker_first_post_only_rejected_goes_straight_to_taker(monkeypatch):
    fake = _FakeExchange([_rejected_raw()])
    fake, cancels, taker_calls = _common_stubs(monkeypatch, fake_exchange=fake)

    status_calls = []
    monkeypatch.setattr(ex, "fetch_order_status",
                        lambda oid: status_calls.append(oid) or "open")

    res = ex.place_entry_order(True, 1.0, 100.0, "BTC",
                               config={"entry_order_mode": "maker_first"})

    assert not status_calls, "a rejected post-only never rested — nothing to poll"
    assert not cancels, "nothing rested — nothing to cancel"
    assert len(taker_calls) == 1
    assert taker_calls[0]["size"] == 1.0
    assert res["ok"] is True
    assert res["order_id"] == "TAKER1"


# ── maker_first never worse than plain taker on an unexpected failure ───────

def test_maker_first_degrades_to_taker_on_unexpected_exception(monkeypatch):
    """Any exception anywhere in the maker path (here: coin resolution itself)
    must fall back to EXACTLY what taker mode would have produced — the
    headline guarantee of this feature."""
    monkeypatch.setattr(ex, "get_coin_index", lambda coin: (_ for _ in ()).throw(ValueError("boom")))
    monkeypatch.setattr(ex, "place_hl_order",
                        lambda is_buy, size, mid, coin, reduce_only=False:
                            {"ok": True, "order_id": "SAFE", "avg_px": mid, "total_sz": size})

    maker_result = ex.place_entry_order(True, 1.0, 100.0, "BTC",
                                        config={"entry_order_mode": "maker_first"})
    taker_result = ex.place_hl_order(True, 1.0, 100.0, "BTC")

    assert maker_result == taker_result


def test_maker_first_invalid_price_matches_taker_error_shape(monkeypatch):
    res = ex.place_entry_order(True, 1.0, 0.0, "BTC", config={"entry_order_mode": "maker_first"})
    assert res == {"ok": False, "error": "invalid price for BTC"}


def test_maker_first_invalid_size_matches_taker_error_shape(monkeypatch):
    res = ex.place_entry_order(True, 0.0, 100.0, "BTC", config={"entry_order_mode": "maker_first"})
    assert res == {"ok": False, "error": "invalid size for BTC"}


# ── _best_touch_price: the maker price source ────────────────────────────────

def test_best_touch_price_buy_joins_best_bid(monkeypatch):
    class _Info:
        def l2_snapshot(self, coin):
            return {"levels": [[{"px": "99.5"}], [{"px": "100.5"}]]}
    monkeypatch.setattr(ex, "_get_info", lambda: _Info())
    assert ex._best_touch_price("BTC", True, 100.0) == 99.5


def test_best_touch_price_sell_joins_best_ask(monkeypatch):
    class _Info:
        def l2_snapshot(self, coin):
            return {"levels": [[{"px": "99.5"}], [{"px": "100.5"}]]}
    monkeypatch.setattr(ex, "_get_info", lambda: _Info())
    assert ex._best_touch_price("BTC", False, 100.0) == 100.5


def test_best_touch_price_falls_back_to_mid_on_l2_failure(monkeypatch):
    def _boom():
        raise RuntimeError("l2 fetch failed")
    monkeypatch.setattr(ex, "_get_info", _boom)
    assert ex._best_touch_price("BTC", True, 123.45) == 123.45


# ── _fills_for_order: reads the account's own fill record for one oid ───────

def test_fills_for_order_sums_matching_oid_only(monkeypatch):
    monkeypatch.setattr(ex, "resolve_user_address", lambda: "0xUSER")

    def _post(path, payload):
        assert payload["type"] == "userFillsByTime"
        return [
            {"coin": "BTC", "oid": 42, "sz": "0.5", "px": "100.0"},
            {"coin": "BTC", "oid": 42, "sz": "0.5", "px": "102.0"},
            {"coin": "BTC", "oid": 999, "sz": "10.0", "px": "1.0"},   # different order, excluded
            {"coin": "ETH", "oid": 42, "sz": "5.0", "px": "1.0"},     # same oid, different coin, excluded
        ]
    monkeypatch.setattr(ex, "_http_post", _post)

    total_sz, avg_px = ex._fills_for_order(42, "BTC", since_ms=1_000_000)
    assert total_sz == 1.0
    assert avg_px == 101.0


def test_fills_for_order_no_match_returns_zero(monkeypatch):
    monkeypatch.setattr(ex, "resolve_user_address", lambda: "0xUSER")
    monkeypatch.setattr(ex, "_http_post", lambda path, payload: [])
    assert ex._fills_for_order(1, "BTC", since_ms=0) == (0.0, 0.0)


def test_fills_for_order_no_address_returns_zero(monkeypatch):
    monkeypatch.setattr(ex, "resolve_user_address", lambda: "")
    assert ex._fills_for_order(1, "BTC", since_ms=0) == (0.0, 0.0)
