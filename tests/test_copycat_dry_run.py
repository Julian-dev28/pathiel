"""copycat_dry_run: the harness must never reach the real exchange.

DryRunVenue (scripts/copycat_dry_run.py) subclasses copycat_live.LiveVenue
and overrides set_leverage / market / close to record intent instead of
calling the exchange, and overrides leader_state ONLY to observe (call the
real inherited read via super(), record ok/fail per leader, pass the result
through unchanged). This pins that contract directly -- call the three
write-override methods with the real write functions monkeypatched to
raise -- and through a full maybe_run() cycle end to end, with fake reads
(no network) and a permissive fake claims registry.

"copycat" is registered in rebalancer_owned's `_ACTIVE_CLAIM_BOOKS`
(pathiel/agents/rebalancer_owned.py), so `claims.claim(coin, "copycat")`
is live. This test still patches copycat_live's own `get_claims_registry`
with a permissive in-memory fake rather than the real registry -- that
registry is a process-wide singleton backed by a real file
(`.rebalancer_claims.json`), and reusing it here would mean this test's
claims persist in that singleton for the rest of the pytest session
(contaminating whatever test runs next in the same process) and would add
real file I/O to what the task requires stay a <2s, no-network gate test.
The fake keeps this test fully isolated and fast while still exercising
the exact claim/release/owner_of calls maybe_run makes.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any, Dict

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_dry_run_module():
    """Exec scripts/copycat_dry_run.py fresh. Its own top-level code
    redirects PATHIEL_STATE_DIR to a throwaway temp dir (see the script's
    own docstring for why) -- snapshot/restore the env var so this test
    cannot change where any OTHER test's state lives."""
    prev = os.environ.get("PATHIEL_STATE_DIR")
    spec = importlib.util.spec_from_file_location(
        "copycat_dry_run_test_mod", ROOT / "scripts" / "copycat_dry_run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if prev is None:
        os.environ.pop("PATHIEL_STATE_DIR", None)
    else:
        os.environ["PATHIEL_STATE_DIR"] = prev
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load_dry_run_module()


def _raiser(name: str):
    def _r(*a: Any, **kw: Any) -> Any:
        raise AssertionError(f"{name} was called -- a dry run must never reach the real exchange")
    return _r


class _FakeClaims:
    """Permissive stand-in for the real cross-book ClaimsRegistry. "copycat"
    IS in `_ACTIVE_CLAIM_BOOKS` now, so the real registry would grant these
    claims too -- this fake exists so the test doesn't touch the real
    registry's process-wide singleton or its backing file (see the module
    docstring above), not to work around a gate that would otherwise deny
    the claim."""

    def __init__(self) -> None:
        self._owned: Dict[str, str] = {}

    def claim(self, coin: str, book: str) -> bool:
        owner = self._owned.get(coin)
        if owner is None or owner == book:
            self._owned[coin] = book
            return True
        return False

    def release(self, coin: str, book: str) -> None:
        if self._owned.get(coin) == book:
            del self._owned[coin]

    def owner_of(self, coin: str) -> Any:
        return self._owned.get(coin)

    def save(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _fake_claims(monkeypatch):
    import pathiel.agents.copycat_live as CC
    monkeypatch.setattr(CC, "get_claims_registry", lambda: _FakeClaims())


@pytest.fixture(autouse=True)
def _raise_on_real_exchange_calls(monkeypatch):
    """The gate: if DryRunVenue (or copycat_live around it) ever calls one
    of these three for real, this test fails loudly instead of placing a
    real order."""
    import pathiel.client.exchange as exch
    import pathiel.agents.executor as execu
    monkeypatch.setattr(exch, "place_hl_order", _raiser("exchange.place_hl_order"))
    monkeypatch.setattr(exch, "set_leverage", _raiser("exchange.set_leverage"))
    monkeypatch.setattr(execu, "close_position_market", _raiser("executor.close_position_market"))


def test_dry_run_venue_write_methods_never_touch_the_exchange(mod):
    """Direct contract test: DryRunVenue's own override methods must
    succeed and record intent WITHOUT the real write functions (monkey-
    patched above to raise) ever running."""
    venue = mod.DryRunVenue()

    lev = venue.set_leverage("BTC", 5, True)
    assert lev == {"ok": True, "is_cross": True, "dry_run": True}

    fill = venue.market("BTC", True, 0.05, 100.0)
    assert fill["ok"] is True
    assert fill["avg_px"] == 100.0
    assert fill["total_sz"] == 0.05

    reduce_fill = venue.market("BTC", False, 0.02, 100.0, reduce_only=True)
    assert reduce_fill["ok"] is True

    closed = venue.close("BTC")
    assert closed is True

    calls = [i["call"] for i in venue.intents]
    assert calls == ["set_leverage", "market", "market", "close"]
    assert venue.intents[0] == {"call": "set_leverage", "coin": "BTC", "leverage": 5,
                                "is_cross_requested": True}
    assert venue.intents[1]["notional_usd"] == pytest.approx(5.0)
    assert venue.intents[2]["reduce_only"] is True


def test_dry_run_venue_reads_other_than_leader_state_are_inherited_unchanged(mod):
    """mid / max_leverage / size_for / leader_extras must be the exact same
    method objects as the real LiveVenue -- DryRunVenue does not touch
    them at all."""
    import pathiel.agents.copycat_live as CC
    for name in ("leader_extras", "mid", "max_leverage", "size_for"):
        assert getattr(mod.DryRunVenue, name) is getattr(CC.LiveVenue, name), (
            f"DryRunVenue.{name} must be the inherited read method, not an override")


def test_dry_run_venue_leader_state_observes_but_never_alters_the_real_read(mod, monkeypatch):
    """leader_state IS overridden (to record per-leader read health for the
    harness's own pass/fail bar), but it must call straight through to the
    real LiveVenue.leader_state via super() and return its result
    untouched -- this patches the PARENT class's method and checks the
    child's override still produces the identical object."""
    import pathiel.agents.copycat_live as CC

    sentinel = {"equity_perp_usdc": 42.0, "spot_usdc": 1.0, "positions": {}}
    monkeypatch.setattr(CC.LiveVenue, "leader_state", lambda self, user: sentinel)

    venue = mod.DryRunVenue()
    result = venue.leader_state("0xsomeleader")
    assert result is sentinel
    assert venue.leader_reads == {"0xsomeleader": True}

    monkeypatch.setattr(CC.LiveVenue, "leader_state", lambda self, user: None)
    venue2 = mod.DryRunVenue()
    assert venue2.leader_state("0xdead") is None
    assert venue2.leader_reads == {"0xdead": False}


def test_full_cycle_through_maybe_run_opens_without_touching_the_exchange(mod, tmp_path):
    """End-to-end: drive copycat_live.maybe_run for two cycles (the
    leader's book starts empty, then he opens a position) through
    DryRunVenue with fake reads. Zero exceptions, the real write functions
    (monkeypatched to raise above) are never called, and the resulting
    intents show a real set_leverage + market call -- if maybe_run's open
    path ever reached the exchange module directly instead of going
    through venue.set_leverage/market, this test fails on the
    AssertionError from `_raiser`, not a silent pass."""
    import pathiel.agents.copycat_live as CC

    leader = "0x" + "a" * 40
    lead_positions: Dict[str, Dict[str, float]] = {}

    venue = mod.DryRunVenue()
    venue.leader_state = lambda user: {"equity_perp_usdc": 1000.0, "spot_usdc": 0.0,
                                       "positions": dict(lead_positions)}
    venue.leader_extras = lambda user: {"spot_usdc": 0.0, "spot_other_usd": 0.0,
                                        "staked_hype_usd": 0.0}
    venue.mid = lambda coin: 100.0
    venue.max_leverage = lambda coin: 50
    # size_for is real math over a real coin-precision lookup (network on a
    # cold cache) -- fake it for this no-network gate test; the real dry run
    # script leaves it as the true inherited method.
    venue.size_for = lambda coin, notional, mid: notional / mid

    # sizing_mode left unset -> default "fixed_margin" (the book's real
    # default as of 2026-10-02): a fresh open is fixed_margin_usd x leverage,
    # independent of the leader's size -- 0.25 x 50 (max_leverage capped by
    # max_position_leverage 50) = $12.50, clearing the $10.50 floor.
    cfg = {"copycat": {"enabled": True, "shadow_only": False, "leaders": [leader],
                       "sleeve_frac": 0.25, "max_position_leverage": 50,
                       "leverage_mode": "max", "margin_mode": "isolated",
                       "include_spot_staked": False, "only_new_positions": False,
                       "max_worse_entry_pct": 10.0, "min_order_bump": True}}
    state_path = str(tmp_path / ".copycat.json")
    equity_log_path = str(tmp_path / ".copycat_equity.jsonl")

    # Cycle 0: leader flat. only_new_positions=False means there is no
    # quiet baseline -- an empty book just produces an empty diff.
    out0 = CC.maybe_run(cfg, [], 1000.0, {"": 1000.0}, 0.0, -100.0, venue=venue,
                        now_ms=0, state_path=state_path, equity_log_path=equity_log_path)
    assert out0["opened"] == []

    # Cycle 1: leader opens BTC long. fixed_margin sizing doesn't care how
    # big his add is, only that an open happened.
    lead_positions["BTC"] = {"szi": 10.0, "entry_px": 100.0, "lev": 5.0}
    out1 = CC.maybe_run(cfg, [], 1000.0, {"": 1000.0}, 0.0, -100.0, venue=venue,
                        now_ms=60_000, state_path=state_path, equity_log_path=equity_log_path)
    assert out1["opened"] == ["BTC"], out1
    assert out1["skipped"] == {}

    set_lev = [i for i in venue.intents if i["call"] == "set_leverage" and i["coin"] == "BTC"]
    market = [i for i in venue.intents if i["call"] == "market" and i["coin"] == "BTC"]
    assert len(set_lev) == 1 and set_lev[0]["leverage"] == 50   # max_leverage(50), mode "max"
    assert len(market) == 1
    assert market[0]["notional_usd"] == pytest.approx(12.50)   # fixed_margin: $0.25 x 50x
    # leader_state was replaced with a plain fake above (for simplicity, same
    # as mid/max_leverage/size_for) so it doesn't exercise the observer
    # wrapper here -- that contract has its own dedicated test above.
