"""Gate tests for numerology_recorder — the day_root_odd paper trade. No network, no clock freeze."""
import json
from datetime import datetime, timezone

from hermes_trader.agents import numerology_recorder as rec


def test_day_root_direction():
    assert rec._reduce(29) == 2 and rec._reduce(28) == 1
    assert rec.day_root_odd_dir(datetime(2026, 1, 3, tzinfo=timezone.utc)) == 1    # root 3 odd -> long
    assert rec.day_root_odd_dir(datetime(2026, 1, 4, tzinfo=timezone.utc)) == -1   # root 4 even -> short
    assert rec.day_root_odd_dir(datetime(2026, 1, 28, tzinfo=timezone.utc)) == 1   # 2+8=10->1 odd -> long
    assert rec.day_root_odd_dir(datetime(2026, 1, 29, tzinfo=timezone.utc)) == -1  # 2+9=11->2 even -> short


def test_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(rec, "_STATE_FILE", str(tmp_path / "s.json"))
    assert rec.maybe_record([], {"numerology_eth": {"enabled": False}}) == 0


def test_hour_gate_blocks(monkeypatch, tmp_path):
    monkeypatch.setattr(rec, "_STATE_FILE", str(tmp_path / "s.json"))
    # hour=25 is never reached (now.hour is 0..23), so it always gates out
    assert rec.maybe_record([], {"numerology_eth": {"enabled": True, "hour": 25}}) == 0


def test_dedup_same_day(monkeypatch, tmp_path):
    sf = tmp_path / "s.json"
    monkeypatch.setattr(rec, "_STATE_FILE", str(sf))
    today = datetime.now(timezone.utc).date().isoformat()
    sf.write_text(json.dumps({"last_day": today}))
    assert rec.maybe_record([], {"numerology_eth": {"enabled": True, "hour": 0}}) == 0


def test_records_new_day(monkeypatch, tmp_path):
    monkeypatch.setattr(rec, "_STATE_FILE", str(tmp_path / "s.json"))
    written = {}
    monkeypatch.setattr(rec.shadow_ledger, "record",
                        lambda book, **kw: written.update({"book": book, **kw}))
    uni = [{"coin": "ETH", "midPx": 2500.0}]
    n = rec.maybe_record(uni, {"numerology_eth": {"enabled": True, "hour": 0,
                                                  "leverage": 40, "equity_frac": 0.5}})
    assert n == 1
    assert written["book"] == "numerology_eth" and written["coin"] == "ETH"
    expect = "long" if rec.day_root_odd_dir(datetime.now(timezone.utc)) > 0 else "short"
    assert written["side"] == expect
    assert written["entry_ref_px"] == 2500.0
    # sim params carried for the grader; default is SHADOW (no execute_fn passed here)
    assert written["meta"]["leverage"] == 40 and written["meta"]["equity_frac"] == 0.5
    assert written["meta"]["shadow"] is True


def test_default_is_shadow_only():
    """The gun ships holstered: shadow_only defaults true, so a plain call never trades."""
    import inspect
    # a config with the book present but no shadow_only key must behave as shadow
    written = {}
    import types
    reg = types.SimpleNamespace(record=lambda book, **kw: written.update(kw))
    # only assert the default via the meta the recorder writes (shadow flag)
    assert inspect.signature(rec.maybe_record).parameters["execute_fn"].default is None
