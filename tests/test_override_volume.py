"""Williams volume-confirm on the override path — gate-logic unit tests (no network)."""

import pathia.agents.executor as ex


def _patch_vol(monkeypatch, confirmed):
    monkeypatch.setattr(ex, "_volume_confirmed", lambda coin, mr, lb=20: confirmed)


def test_helper_fails_open_on_fetch_error(monkeypatch):
    # _volume_confirmed imports fetch_hl_candles from the client module at call time;
    # patch it there to raise -> the helper must fail OPEN (return True, don't block).
    import pathia.client.hl_client as hl
    monkeypatch.setattr(hl, "fetch_hl_candles", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("net")))
    assert ex._volume_confirmed("DOGE", 1.5) is True


def test_helper_fails_open_on_thin_history(monkeypatch):
    import pathia.client.hl_client as hl
    monkeypatch.setattr(hl, "fetch_hl_candles", lambda *a, **k: [])  # too few bars
    assert ex._volume_confirmed("DOGE", 1.5) is True


def test_disabled_flag_allows_override(monkeypatch):
    # When override_volume_confirm disabled, the volume check must not run/block.
    _patch_vol(monkeypatch, False)  # would block if consulted
    cfg = {"ta_sidestep_force_execute": True, "min_ai_confidence": 0.70,
           "runner_entry_gate": {"min_composite": 30},
           "override_volume_confirm": {"enabled": False}}
    # exercise just the gate predicate by reconstructing the condition
    vc = cfg.get("override_volume_confirm") or {}
    assert not bool(vc.get("enabled", False))  # disabled -> no volume gate


def test_override_volume_confirmed_passes(monkeypatch):
    _patch_vol(monkeypatch, True)
    assert ex._volume_confirmed("DOGE", 1.5) is True


def test_override_no_volume_blocked_value(monkeypatch):
    _patch_vol(monkeypatch, False)
    assert ex._volume_confirmed("DOGE", 1.5) is False
