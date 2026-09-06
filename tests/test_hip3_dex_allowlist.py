"""The dex allowlist must bind where the COST is and where the RISK is.

WHAT WENT WRONG (2026-09-06, live)
`.agent-config.json` read `"hip3_dex_allowlist": ["xyz"]` and the loop still
produced:

    21:43:53 REFUSED SHORT io:SNDK - io dex unfunded ($0.00)

The allowlist existed only in `perception.py`'s scan filter. Every other caller
of `get_universe(include_hip3=True)` - hyperfeed, the executor's own size
lookup, the trend engine, the dashboard - saw all 280 HIP-3 markets across all
eleven dexes. `io:SNDK` was ranked, analysed and sent to the executor, and the
ONLY thing that stopped the order was io happening to hold $0.00. Fund io and
the same path opens a real position on a venue the operator switched off.

So the allowlist is tested at both layers, because they need different
guarantees. In `get_universe` it is a cost control: a muted dex must not even be
fetched. In `maybe_execute` it is a safety control: a muted dex must be refused
whatever produced the candidate, and refused for the RIGHT reason rather than
incidentally by a balance check that a deposit would silence.
"""
from __future__ import annotations

import pytest

import pathia.client.universe as U


@pytest.fixture
def _dexes(monkeypatch):
    monkeypatch.setattr(U, "list_hip3_dexes", lambda force_refresh=False:
                        ["xyz", "io", "para", "mkts", "km"])


def _cfg(monkeypatch, **over):
    import pathia.agents.config_store as CS
    base = {"hip3_dex_allowlist": [], "hip3_dex_blocklist": []}
    base.update(over)
    monkeypatch.setattr(CS, "read_agent_config", lambda: base)
    return base


def test_allowlist_drops_io_para_and_mkts(_dexes, monkeypatch):
    """The exact live config: only xyz survives."""
    _cfg(monkeypatch, hip3_dex_allowlist=["xyz"])
    assert U._allowed_hip3_dexes() == ["xyz"]


def test_muted_dex_is_never_even_fetched(_dexes, monkeypatch):
    """Cost, not just correctness: a muted dex must cost no /info round trip.

    Filtering the RESULT would still pay to fetch every dex, which is the thing
    the allowlist exists to avoid.
    """
    _cfg(monkeypatch, hip3_dex_allowlist=["xyz"])
    fetched: list[str] = []

    def _spy(dex, force_refresh=False):
        fetched.append(dex)
        return ({}, {})

    monkeypatch.setattr(U, "_fetch_hip3_meta", _spy)
    monkeypatch.setattr(U, "_fetch_perp_meta", lambda f: ({}, {}))
    monkeypatch.setattr(U, "_fetch_spot_meta", lambda f: ({}, {}))
    U.get_universe(include_hip3=True, include_crypto=False)
    assert fetched == ["xyz"], f"fetched muted dexes: {fetched}"


def test_blocklist_removes_named_dexes(_dexes, monkeypatch):
    _cfg(monkeypatch, hip3_dex_blocklist=["io", "para", "mkts"])
    assert U._allowed_hip3_dexes() == ["xyz", "km"]


def test_empty_allowlist_means_unrestricted(_dexes, monkeypatch):
    """Empty list keeps its historical meaning: no restriction, not 'none'."""
    _cfg(monkeypatch)
    assert U._allowed_hip3_dexes() == ["xyz", "io", "para", "mkts", "km"]


def test_unreadable_config_does_not_silently_widen_or_empty(_dexes, monkeypatch):
    """A config read failure must not turn into 'trade every dex' silently.

    It returns the registered list unchanged - the same as no allowlist - and
    the executor gate below still refuses. The failure mode that MUST NOT
    happen is an exception escaping into the scan loop.
    """
    import pathia.agents.config_store as CS

    def _boom():
        raise RuntimeError("config unreadable")

    monkeypatch.setattr(CS, "read_agent_config", _boom)
    assert U._allowed_hip3_dexes() == ["xyz", "io", "para", "mkts", "km"]


def test_executor_refuses_muted_dex_before_any_balance_lookup(monkeypatch):
    """The live bug, at the layer that actually places the order.

    Asserts the refusal reason names the ALLOWLIST. If it came back
    'hip3_dex_underfunded' the order was stopped by io's empty balance, which
    is luck, not a control - a deposit would remove it.
    """
    import pathia.agents.executor as E

    monkeypatch.setattr(E, "read_agent_config",
                        lambda: {"hip3_dex_allowlist": ["xyz"], "enable_hip3": True,
                                 "enable_crypto": False, "mode": "LIVE"})
    monkeypatch.setattr(E, "resolve_user_address", lambda: "0xabc")

    def _no_network(*a, **k):
        raise AssertionError("balance lookup ran for a muted dex")

    import pathia.client.hl_client as HL
    monkeypatch.setattr(HL, "_http_post", _no_network)

    res = E.maybe_execute({"id": "t1", "coin": "io:SNDK", "side": "short"})
    assert res["executed"] is False
    assert "not_allowlisted" in res["reason"], res["reason"]
    assert "io" in res["reason"]
