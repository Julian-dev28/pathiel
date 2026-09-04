"""xs_reversal — the gates that stand between this book and a bad trade.

Live from its first bar with real capital, so these are not "does it compute a
number" tests. Each one is a way the book could lose money that the code is
supposed to prevent.
"""
from __future__ import annotations

import json
import time

import pytest

from pathia.agents import xs_reversal_live as XSR

DAY_MS = 86_400_000
BASELINE = 1.25e-05


@pytest.fixture
def panel(tmp_path, monkeypatch):
    """A writable panel in the shape data_logger produces."""
    path = tmp_path / ".data_funding_oi.jsonl"
    monkeypatch.setattr(XSR, "_panel_path", lambda: str(path))
    return path


def write_panel(path, coins, now_ms, days=8, per_day=8, funding=None, vol=5_000_000):
    """`coins` maps coin -> (start_px, end_px). Funding defaults to awake."""
    n = int(days * per_day)
    with open(path, "w") as fh:
        for i in range(n):
            ts = now_ms - int((days - i * days / n) * DAY_MS)
            rows = []
            for c, (p0, p1) in coins.items():
                frac = i / max(n - 1, 1)
                f = funding(c, i) if funding else 3.0e-05      # off baseline
                rows.append({"c": c, "px": p0 + (p1 - p0) * frac,
                             "f": f, "oi": 1000.0, "v": vol})
            fh.write(json.dumps({"ts": ts, "rows": rows}) + "\n")


def universe(coins):
    return [{"coin": c, "dayNtlVlm": 5_000_000} for c in coins]


def cfg(**over):
    base = {"enabled": True, "shadow_only": False, "notional_usd": 11.0,
            "leverage": 1, "stop_pct": 15.0, "hold_hours": 24.0,
            "min_universe": 3, "max_new_per_cycle": 1}
    base.update(over)
    return {"xs_reversal": base}


def run(monkeypatch, config, coins, executed=True):
    """Invoke the book, capturing what it tried to trade."""
    seen = []

    class _Claims:
        def prune_to(self, *a): pass
        def claimed_by_others(self, *a): return set()
        def claim(self, *a): return True
        def release(self, *a): pass
        def save(self): pass

    monkeypatch.setattr(XSR, "get_claims_registry", lambda: _Claims())
    recorded = []
    monkeypatch.setattr(XSR.shadow_ledger, "record",
                        lambda book, **kw: recorded.append(kw) or {})

    def execute_fn(analysis):
        seen.append(analysis)
        return {"executed": executed}

    out = XSR.maybe_run(config, universe(coins), [], execute_fn)
    return out, seen, recorded


# ── the trade itself ────────────────────────────────────────────────────────

def test_it_shorts_the_most_extended_coin(panel, monkeypatch):
    now = int(time.time() * 1000)
    write_panel(panel, {"WINNER": (100, 140), "MID": (100, 101),
                        "FLAT": (100, 100), "LOSER": (100, 70)}, now)
    out, seen, _ = run(monkeypatch, cfg(), ["WINNER", "MID", "FLAT", "LOSER"])
    assert out and out["opened"] == 1
    assert seen[0]["coin"] == "WINNER"
    assert seen[0]["side"] == "short" and seen[0]["verdict"] == "SHORT"


def test_it_never_shorts_the_losers(panel, monkeypatch):
    """The control that makes this a strategy rather than a short bias: in the
    study, shorting the bottom decile LOSES 0.772%/trade."""
    now = int(time.time() * 1000)
    write_panel(panel, {"A": (100, 130), "B": (100, 110),
                        "C": (100, 60), "D": (100, 50)}, now)
    _, seen, _ = run(monkeypatch, cfg(), ["A", "B", "C", "D"])
    assert seen and seen[0]["coin"] == "A"
    assert {s["coin"] for s in seen}.isdisjoint({"C", "D"})


# ── the gate that carries the edge ──────────────────────────────────────────

def test_a_dead_funding_market_is_never_traded(panel, monkeypatch):
    """The whole finding. Coins pinned at the venue baseline returned -0.443%
    in the study: no positioning means nothing to unwind. The most extended coin
    on the board must still be skipped when its funding never moves.

    Twenty coins, not four: a decile of four coins is one coin, so filtering it
    would leave nothing to trade and the test would pass for the wrong reason.
    """
    now = int(time.time() * 1000)
    coins = {f"C{i}": (100, 100 + i) for i in range(18)}
    coins["PINNED"] = (100, 300)          # most extended on the board
    coins["AWAKE"] = (100, 250)           # second, and its funding moves
    write_panel(panel, coins, now,
                funding=lambda c, i: BASELINE if c == "PINNED" else 3.0e-05)
    _, seen, _ = run(monkeypatch, cfg(), list(coins))
    assert seen, "nothing traded at all"
    assert seen[0]["coin"] == "AWAKE", "did not fall through to the awake coin"
    assert all(s["coin"] != "PINNED" for s in seen), "shorted a dead funding market"


def test_a_coin_that_woke_up_yesterday_does_not_count_as_awake(panel, monkeypatch):
    """Awake is measured over the trailing window, not the latest tick. One
    print must not qualify a coin that has been asleep all week."""
    now = int(time.time() * 1000)

    def late(c, i):
        if c != "LATE":
            return 3.0e-05
        return 3.0e-05 if i >= 62 else BASELINE      # only the last few samples

    coins = {f"C{i}": (100, 100 + i) for i in range(18)}
    coins["LATE"] = (100, 300)            # most extended, but only just woke up
    coins["OTHER"] = (100, 250)
    write_panel(panel, coins, now, funding=late)
    _, seen, _ = run(monkeypatch, cfg(), list(coins))
    assert seen and seen[0]["coin"] == "OTHER"
    assert all(s["coin"] != "LATE" for s in seen)


# ── refusals ────────────────────────────────────────────────────────────────

def test_it_refuses_to_rank_a_universe_too_small_to_have_a_decile(panel, monkeypatch):
    """A decile of eight coins is not a decile. Ranking a handful and shorting
    the top one is a coin flip wearing the strategy's name."""
    now = int(time.time() * 1000)
    write_panel(panel, {"A": (100, 150), "B": (100, 90)}, now)
    out, seen, _ = run(monkeypatch, cfg(min_universe=20), ["A", "B"])
    assert out is None and seen == []


def test_a_missing_panel_takes_no_trade(panel, monkeypatch):
    """A cold state directory must mean no entry, never an entry on no data."""
    out, seen, _ = run(monkeypatch, cfg(), ["A", "B", "C"])
    assert out is None and seen == []


def test_a_torn_last_line_does_not_stop_the_book(panel, monkeypatch):
    """data_logger appends while this reads. A half-written final line is
    normal and must not raise into the loop."""
    now = int(time.time() * 1000)
    write_panel(panel, {"A": (100, 150), "B": (100, 90), "C": (100, 100)}, now)
    with open(panel, "a") as fh:
        fh.write('{"ts": 123, "rows": [{"c": "X"')      # truncated mid-write
    out, seen, _ = run(monkeypatch, cfg(), ["A", "B", "C"])
    assert out is not None and seen


def test_disabled_and_shadow_flags_are_both_honoured(panel, monkeypatch):
    """shadow_only is the demote switch autonomous_cycle flips when forward EV
    turns negative. If this book ignored it, a dying strategy would keep
    trading real money with nothing left to stop it."""
    now = int(time.time() * 1000)
    write_panel(panel, {"A": (100, 150), "B": (100, 90), "C": (100, 100)}, now)
    for bad in (cfg(enabled=False), cfg(shadow_only=True)):
        out, seen, _ = run(monkeypatch, bad, ["A", "B", "C"])
        assert out is None and seen == []


def test_it_only_trades_coins_the_live_universe_can_reach(panel, monkeypatch):
    """The panel carries every coin data_logger ever saw. Trading one that is
    not in this cycle's universe is an order into a market we did not price."""
    now = int(time.time() * 1000)
    write_panel(panel, {"GHOST": (100, 300), "A": (100, 130),
                        "B": (100, 100), "C": (100, 95)}, now)
    _, seen, _ = run(monkeypatch, cfg(), ["A", "B", "C"])     # GHOST not listed
    assert seen and all(s["coin"] != "GHOST" for s in seen)


# ── the forward record ──────────────────────────────────────────────────────

def test_every_candidate_is_recorded_even_when_only_one_can_be_funded(panel, monkeypatch):
    """The ledger is the strategy's record, not the account balance's. A book
    that logs only the trades it could afford grades itself on a sample
    selected by margin — which on a $12.94 account is almost all of them."""
    now = int(time.time() * 1000)
    coins = {f"C{i}": (100, 100 + i * 5) for i in range(20)}
    write_panel(panel, coins, now)
    out, seen, recorded = run(monkeypatch, cfg(max_new_per_cycle=1), list(coins))
    assert out["opened"] == 1, "max_new_per_cycle not respected"
    assert len(recorded) >= 2, "only the funded trade reached the ledger"
    assert all(r["side"] == "short" for r in recorded)
    assert all("awake_frac" in r["meta"] and "mom_3d" in r["meta"] for r in recorded)


def test_the_order_carries_the_tested_geometry(panel, monkeypatch):
    """15% stop, 24h flat hold, no trail. W-X1 measured exit overlays across
    four validated families and found they buy win rate by paying EV."""
    now = int(time.time() * 1000)
    write_panel(panel, {"A": (100, 150), "B": (100, 90), "C": (100, 100)}, now)
    _, seen, _ = run(monkeypatch, cfg(), ["A", "B", "C"])
    a = seen[0]
    assert a["backup_sl_pct_override"] == 15.0
    assert a["strategy_book"] == "xs_reversal"
    assert a["strategy_book_notional"] == 11.0
    assert a["tp_scale_fraction_override"] == 0.0      # no partial take-profit
