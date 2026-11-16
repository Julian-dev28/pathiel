"""The SDK clients get a timeout at CONSTRUCTION, not only afterwards.

2026-10-31: during a Hyperliquid 502 storm scripts/thesis_due.py hung 23
minutes inside Info(): the SDK's __init__ fetches meta for every perp dex
with timeout=None, before _set_session_timeout can wrap the session."""
from __future__ import annotations

import pathiel.client.exchange as ex
from pathiel.client.http_session import SDK_TIMEOUT_S


def test_info_is_built_with_a_timeout(monkeypatch):
    seen = {}

    class FakeInfo:
        def __init__(self, **kw):
            seen.update(kw)
            self.session = None

    monkeypatch.setattr(ex, "Info", FakeInfo)
    monkeypatch.setattr(ex, "_info_instance", None)
    monkeypatch.setattr(ex, "_resolve_perp_dexs", lambda: None)
    ex._get_info()
    assert seen["timeout"] == SDK_TIMEOUT_S and 0 < SDK_TIMEOUT_S <= 30


def test_every_sdk_constructor_in_the_client_passes_the_timeout():
    """exchange.py builds Info, the default Exchange and (2026-11-16) the
    sub-account Exchange of _make_target_exchange; hl_client.py builds Info."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "pathiel" / "client"
    src = (root / "exchange.py").read_text()
    assert src.count("timeout=SDK_TIMEOUT_S") == 3
    assert src.count("Exchange(\n") == 2          # both Exchange constructors, each with the timeout
    assert (root / "hl_client.py").read_text().count("timeout=SDK_TIMEOUT_S") == 1
