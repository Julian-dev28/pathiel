"""services/strategies/desk_log.py: tailing a desk's plain-text progress log,
generalized per desk id."""
from __future__ import annotations

from services.strategies import desk_log as DL


def test_tail_lines_keeps_only_the_last_n_non_empty_lines():
    text = "\n".join([f"line{i}" for i in range(5)]) + "\n\n   \n"
    assert DL.tail_lines(text, 2) == ["line3", "line4"]
    assert DL.tail_lines(text, 100) == [f"line{i}" for i in range(5)]
    assert DL.tail_lines(text, 0) == []


def test_read_desk_log_reads_the_real_file(tmp_path):
    p = tmp_path / "progress.log"
    p.write_text("OIL-TICK 2026-10-23T00:00 mid=107.5 pos=flat action=NONE no setup\n"
                "OIL-TICK 2026-10-23T00:15 mid=107.6 pos=flat action=NONE no setup\n")
    assert DL.read_desk_log("oil", n=1, path=str(p)) == [
        "OIL-TICK 2026-10-23T00:15 mid=107.6 pos=flat action=NONE no setup"]


def test_read_desk_log_missing_file_is_empty_not_an_error(tmp_path):
    assert DL.read_desk_log("oil", n=30, path=str(tmp_path / "does-not-exist.log")) == []


def test_log_path_honors_the_env_var(monkeypatch):
    monkeypatch.delenv("PATHIEL_OIL_LOG", raising=False)
    assert DL.log_path("oil") == DL.DEFAULT_LOG_PATH
    monkeypatch.setenv("PATHIEL_OIL_LOG", "/tmp/somewhere/progress.log")
    assert DL.log_path("oil") == "/tmp/somewhere/progress.log"


def test_log_path_generalizes_to_any_desk_id(monkeypatch):
    monkeypatch.delenv("PATHIEL_BTC_LOG", raising=False)
    assert DL.log_path("btc") == "/tmp/btc-loop/progress.log"
    monkeypatch.setenv("PATHIEL_BTC_LOG", "/tmp/custom-btc.log")
    assert DL.log_path("btc") == "/tmp/custom-btc.log"


def test_read_desk_log_resolves_the_desk_specific_env_path(tmp_path, monkeypatch):
    p = tmp_path / "btc-progress.log"
    p.write_text("BTC-TICK line one\n")
    monkeypatch.setenv("PATHIEL_BTC_LOG", str(p))
    assert DL.read_desk_log("btc", n=5) == ["BTC-TICK line one"]
