"""The oil desk brief's "full size" is stable regardless of the desk's own
open position (2026-11-17 fix).

Live, 2026-11-16/17: desk_brief's "Your size" for xyz:BRENTOIL (the number
the desk-tick agent uses to decide a same-side top-up, "add to full size")
read 13.59, then 19.62, then 7.71 across ticks with the thesis's entry and
stop unchanged. Root cause: `_thesis_sizing` (scripts/pathiel-mcp-server.py)
sized off the account's TOTAL equity, which includes the open position's
own unrealized P&L -- so the position's own mark-to-market swings fed
straight back into its own sizing target.

Fix: `_stable_equity_for_sizing` / `_coin_position` back that position's
own uPnL out of equity first (services.risk.size.
equity_excluding_position_pnl), so full size reads the same whether the
desk is flat or already holds the position, and the brief now reports
`held_size` / `topup_room` explicitly (full size minus held, never
negative) for the desk-tick agent to read directly instead of inferring it.

Deterministic, offline: every venue read here is monkeypatched (the root
conftest.py's network guard fails the test if one leaks through).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.risk.models import RiskProfile
from services.risk.derive import VenueRules

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "scripts" / "pathiel-mcp-server.py"
COIN = "xyz:BRENTOIL"


def _mcp():
    spec = importlib.util.spec_from_file_location("pathiel_mcp_full_size_test", SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _brief(*, side="long", entry=89.4062, stop=88.64, instrument=COIN, targets=None,
          account_equity=None):
    """The slice of a DeskBrief _thesis_sizing reads: thesis.call and, when
    the brief has cached analytics, analytics.account."""
    call = SimpleNamespace(side=side, entry_px=entry, stop_px=stop, instrument=instrument,
                           targets=targets or [])
    thesis = SimpleNamespace(call=call)
    account = (SimpleNamespace(equity_usd=account_equity, source="venue")
              if account_equity is not None else None)
    analytics = SimpleNamespace(account=account) if account_equity is not None else None
    return SimpleNamespace(thesis=thesis, analytics=analytics)


@pytest.fixture
def profile_and_venue():
    """20x, no risk-pct cap (contracts/risk/models.py CONTRACT_VERSION note:
    "operator opts out of risk caps") -- the leverage cap is what actually
    binds, isolating the equity-stability question from stop-distance math."""
    return (RiskProfile(preset="custom", risk_per_trade_pct=100.0, daily_loss_limit_pct=100.0,
                        max_leverage=20.0, allow_min_order_bump=True, max_bumped_risk_pct=100.0),
           VenueRules(min_order_notional_usd=10.5))


def _wire(monkeypatch, mcp, profile_and_venue, *, account_state):
    profile, venue = profile_and_venue
    monkeypatch.setattr(mcp, "_risk_inputs", lambda: (profile, [], venue))
    monkeypatch.setattr(mcp, "_coin_specs", lambda c: (20.0, 2))
    monkeypatch.setattr(mcp, "resolve_user_address", lambda: "0xu")
    import pathiel.client.account_snapshot as snap
    monkeypatch.setattr(snap, "account_state",
                        lambda user, include_hip3=True, **kw: account_state)


class TestThesisSizingStability:
    def test_full_size_is_identical_flat_or_holding_the_position_with_upnl(
            self, monkeypatch, profile_and_venue):
        mcp = _mcp()
        _wire(monkeypatch, mcp, profile_and_venue,
             account_state={"equity": 70.0, "asset_positions": []})
        flat = mcp._thesis_sizing(_brief(account_equity=70.0))
        assert flat["can_trade"] is True and "held_size" not in flat

        # the SAME desk, now holding a 14.79 long with +$24 unrealized P&L:
        # total account equity reads $94, not $70.
        _wire(monkeypatch, mcp, profile_and_venue, account_state={
            "equity": 94.0,
            "asset_positions": [{"position": {"coin": COIN, "szi": "14.79", "unrealizedPnl": "24.0"}}],
        })
        held = mcp._thesis_sizing(_brief(account_equity=94.0))
        assert held["size"] == flat["size"], "full size must not move with the position's own uPnL"
        assert held["held_size"] == 14.79
        assert held["topup_room"] == round(max(0.0, held["size"] - 14.79), 8)

    def test_held_size_and_topup_room_absent_when_flat(self, monkeypatch, profile_and_venue):
        mcp = _mcp()
        _wire(monkeypatch, mcp, profile_and_venue,
             account_state={"equity": 70.0, "asset_positions": []})
        out = mcp._thesis_sizing(_brief(account_equity=70.0))
        assert "held_size" not in out and "topup_room" not in out

    def test_topup_room_is_zero_not_negative_when_already_past_full_size(
            self, monkeypatch, profile_and_venue):
        """A position bigger than full size (e.g. a stale thesis after a
        thesis rewrite shrank it) must report zero room, never a negative
        number a caller might mistake for a reduce."""
        mcp = _mcp()
        _wire(monkeypatch, mcp, profile_and_venue, account_state={
            "equity": 70.0,
            "asset_positions": [{"position": {"coin": COIN, "szi": "999", "unrealizedPnl": "0"}}],
        })
        out = mcp._thesis_sizing(_brief(account_equity=70.0))
        assert out["topup_room"] == 0.0

    def test_a_short_positions_held_size_is_reported_unsigned(self, monkeypatch, profile_and_venue):
        mcp = _mcp()
        _wire(monkeypatch, mcp, profile_and_venue, account_state={
            "equity": 70.0,
            "asset_positions": [{"position": {"coin": COIN, "szi": "-5.5", "unrealizedPnl": "3.0"}}],
        })
        out = mcp._thesis_sizing(_brief(side="short", entry=89.0, stop=90.0, account_equity=70.0))
        assert out["held_size"] == 5.5

    def test_a_different_coins_position_is_not_mistaken_for_this_ones(
            self, monkeypatch, profile_and_venue):
        """desk_share's own validation would refuse an unconfigured coin
        long before this matters live, but the uPnL lookup itself must key
        on the exact coin: another desk's open position must never be read
        as this one's."""
        mcp = _mcp()
        _wire(monkeypatch, mcp, profile_and_venue, account_state={
            "equity": 94.0,
            "asset_positions": [{"position": {"coin": "xyz:SILVER", "szi": "40.0", "unrealizedPnl": "24.0"}}],
        })
        out = mcp._thesis_sizing(_brief(account_equity=94.0))
        assert "held_size" not in out   # xyz:BRENTOIL itself is flat
        # and the $24 on xyz:SILVER was NOT backed out of xyz:BRENTOIL's sizing
        # (a flat $70 book sizes smaller than a flat $94 one -- the $24 stayed in)
        _wire(monkeypatch, mcp, profile_and_venue, account_state={"equity": 70.0, "asset_positions": []})
        bare = mcp._thesis_sizing(_brief(account_equity=70.0))
        assert out["size"] != bare["size"]


class TestNoNetworkLeak:
    """A user/read failure degrades to the raw equity, never raises and
    never blocks a read-only brief -- and a flat (no credentials) account
    never reaches the network at all (see _coin_position's `if not user`
    guard)."""

    def test_an_unresolvable_user_still_sizes(self, monkeypatch, profile_and_venue):
        mcp = _mcp()
        profile, venue = profile_and_venue
        monkeypatch.setattr(mcp, "_risk_inputs", lambda: (profile, [], venue))
        monkeypatch.setattr(mcp, "_coin_specs", lambda c: (20.0, 2))
        monkeypatch.setattr(mcp, "resolve_user_address", lambda: "")

        def _boom(*a, **k):
            raise AssertionError("must not read the account when there is no user")

        import pathiel.client.account_snapshot as snap
        monkeypatch.setattr(snap, "account_state", _boom)
        out = mcp._thesis_sizing(_brief(account_equity=70.0))
        assert out["can_trade"] is True
