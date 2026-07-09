"""funding_spike_short (W-F2A validated spec): z>=2 entry, z<1 episode reset,
validated 15%/5d no-trail structure, shadow recording in both modes."""
import os

import pytest

from hermes_trader.agents import funding_spike_short_live as fs
from hermes_trader.agents import rebalancer_owned as ro

DAY = 86_400_000
NOW_MS = 1_760_000_000_000
NOW_S = NOW_MS / 1000.0


def _rows(daily_sums, last24):
    """Hourly funding rows: 30 daily buckets with the given sums + a trailing-24h block."""
    rows = []
    start = NOW_MS - (len(daily_sums) + 1) * DAY
    for i, s in enumerate(daily_sums):
        day0 = start + i * DAY
        rows += [{"time": day0 + h * 3_600_000, "fundingRate": s / 24.0} for h in range(24)]
    rows += [{"time": NOW_MS - DAY + (h + 1) * 3_600_000 - 1, "fundingRate": last24 / 24.0}
             for h in range(24)]
    return rows


def _uni(coins=("ALT",), dvol=30_000_000, mid=10.0):
    return [{"coin": c, "type": "perp", "dayNtlVlm": dvol, "midPx": mid} for c in coins]


def _cfg(**ov):
    cfg = {"enabled": True, "shadow_only": False, "scan_interval_hours": 0,
           "entry_z": 2.0, "exit_z": 1.0, "lookback_days": 30,
           "min_volume_usd": 20_000_000, "executor_short_volume_floor_usd": 20_000_000,
           "max_scan_coins": 40, "hold_days": 5.0, "stop_pct": 15.0,
           "notional_usd": 20.0, "leverage": 1, "max_new_per_cycle": 1}
    cfg.update(ov)
    return {"funding_spike_short": cfg}


def _setup(monkeypatch, z=3.0):
    ro._claims_registry = None
    captured, events = [], []
    monkeypatch.setattr(fs.shadow_ledger, "record_many",
                        lambda book, rows: captured.append((book, list(rows))) or len(rows))
    for path in (fs._SEEN_FILE, fs._TS_FILE):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    monkeypatch.setattr(fs, "log_event", lambda e: events.append(e))
    monkeypatch.setattr(fs, "_last_ts", lambda: 0.0)
    monkeypatch.setattr(fs, "_save_ts", lambda t: None)
    monkeypatch.setattr(fs.time, "time", lambda: NOW_S)
    monkeypatch.setattr(fs, "active_position_coins", lambda: {})
    monkeypatch.setattr(fs, "_coin_funding_z", lambda coin, now_ms, lb: z)
    return captured, events


# --------------------------------------------------------------------- z math
def test_funding_z_spike_detected():
    # 30 flat days at +0.1%/d, trailing 24h at +0.5%/d -> big positive z
    rows = _rows([0.001] * 30, 0.005)
    z = fs.funding_z(rows, NOW_MS)
    assert z is not None and z > 2.0


def test_funding_z_normal_day_no_spike():
    rows = _rows([0.001] * 30, 0.001)
    z = fs.funding_z(rows, NOW_MS)
    assert z is not None and abs(z) < 1.0


def test_funding_z_settled_rows_only():
    """Rows timestamped in the future (unsettled) must be excluded."""
    rows = _rows([0.001] * 30, 0.001)
    rows.append({"time": NOW_MS + 3_600_000, "fundingRate": 5.0})
    z = fs.funding_z(rows, NOW_MS)
    assert z is not None and abs(z) < 1.0


def test_funding_z_thin_history_none():
    assert fs.funding_z(_rows([0.001] * 4, 0.005), NOW_MS) is None


# --------------------------------------------------------------------- book behavior
def test_live_opens_short_with_validated_structure(monkeypatch):
    captured, events = _setup(monkeypatch, z=2.5)
    calls = []
    rec = fs.maybe_run(_cfg(), _uni(), [], None, lambda a: calls.append(a) or {"executed": True})
    assert rec["shadow"] is False and rec["opened"] == 1
    a = calls[0]
    assert a["side"] == "short" and a["strategy_book"] == "funding_spike_short"
    assert a["backup_sl_pct_override"] == 15.0 and a["leverage_override"] == 1
    dsl = a["dsl_exit_override"]
    assert dsl["max_loss_pct"] == 15.0
    assert dsl["protect_pct"] == 9999.0          # no trail: stop-or-horizon
    assert dsl["hard_timeout_minutes"] == 5.0 * 1440
    assert a["min_short_volume_usd_override"] == 20_000_000.0
    opens = [e for e in events if e.get("event") == "book_open"]
    assert len(opens) == 1 and opens[0]["book"] == "funding_spike_short"
    # ledger recorded with the z in meta
    book, rows = captured[0]
    assert rows[0]["meta"]["funding_z"] == 2.5 and rows[0]["stop_pct"] == 15.0


def test_shadow_records_and_arms_episode_dedup(monkeypatch):
    captured, _ = _setup(monkeypatch, z=2.5)
    seen = {}
    monkeypatch.setattr(fs, "_load_seen", lambda: dict(seen))
    monkeypatch.setattr(fs, "_save_seen", lambda s: (seen.clear(), seen.update(s)))
    calls = []
    rec = fs.maybe_run(_cfg(shadow_only=True), _uni(), [], None,
                       lambda a: calls.append(a) or {"executed": True})
    assert rec["shadow"] is True and rec["signals"] == 1 and calls == []
    assert "ALT" in seen                          # episode armed even in shadow
    # second scan, z still elevated -> NO new record (episode dedup)
    rec2 = fs.maybe_run(_cfg(shadow_only=True), _uni(), [], None, lambda a: None)
    assert rec2["signals"] == 0


def test_episode_resets_when_z_falls_below_exit(monkeypatch):
    _setup(monkeypatch, z=0.5)
    seen = {"ALT": NOW_MS - 3 * DAY}
    monkeypatch.setattr(fs, "_load_seen", lambda: dict(seen))
    saved = {}
    monkeypatch.setattr(fs, "_save_seen", lambda s: saved.update(s) or saved)
    rec = fs.maybe_run(_cfg(shadow_only=True), _uni(), [], None, lambda a: None)
    assert rec["signals"] == 0
    assert "ALT" not in saved                     # z<1 cleared the episode


def test_no_spike_no_signal(monkeypatch):
    _setup(monkeypatch, z=1.2)
    rec = fs.maybe_run(_cfg(), _uni(), [], None, lambda a: {"executed": True})
    assert rec["signals"] == 0 and rec["opened"] == 0


def test_volume_floor_bounds_scan(monkeypatch):
    _setup(monkeypatch, z=3.0)
    rec = fs.maybe_run(_cfg(), _uni(dvol=5_000_000), [], None, lambda a: {"executed": True})
    assert rec["scanned"] == 0 and rec["signals"] == 0


def test_disabled_is_noop():
    assert fs.maybe_run(_cfg(enabled=False), _uni(), [], None, lambda a: None) is None
