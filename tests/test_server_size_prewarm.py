"""The server warms "Your size" metadata at startup, off the request path.

2026-10-19: the first POST /api/risk/size after a server restart took 2.8 s
(scripts/site_latency_eval.py, twice): it built the SDK client, which reads
every dex's meta, inside the request for the first desk a user opened. The
trading loop has always prewarmed this; the server never did.
"""
from __future__ import annotations

import logging
import threading

from fastapi.testclient import TestClient


def test_startup_warms_every_desk_coin_in_the_background(monkeypatch):
    import pathiel.server as srv
    from services.risk import api as risk_api
    seen = {}
    done = threading.Event()

    def warm(coins):
        from pathiel.client.rate_limit import in_background
        seen.update(coins=list(coins), background=in_background(),
                    thread=threading.current_thread().name)
        done.set()
        return len(seen["coins"])
    monkeypatch.setenv("PATHIEL_META_PREWARM", "1")
    monkeypatch.setattr(risk_api, "prewarm_coin_specs", warm)
    with TestClient(srv.app):
        assert done.wait(5), "startup never warmed the size metadata"
    assert {"BTC", "xyz:BRENTOIL", "xyz:CL"} <= set(seen["coins"])
    assert seen["background"] is True and seen["thread"] == "size-prewarm"


def test_the_switch_turns_it_off(monkeypatch):
    import pathiel.server as srv
    from services.risk import api as risk_api
    calls = []
    monkeypatch.setenv("PATHIEL_META_PREWARM", "0")
    monkeypatch.setattr(risk_api, "prewarm_coin_specs", lambda coins: calls.append(coins))
    with TestClient(srv.app):
        pass
    assert calls == []


def test_a_failed_warm_is_a_warning_not_a_crash(monkeypatch, caplog):
    import pathiel.server as srv
    from services.risk import api as risk_api

    def down(coins):
        raise RuntimeError("venue down")
    monkeypatch.setattr(risk_api, "prewarm_coin_specs", down)
    caplog.set_level(logging.WARNING, logger=srv.logger.name)
    srv._prewarm_size_specs()
    assert "size metadata prewarm failed (venue down)" in caplog.text
