"""services/strategies/oil_log.py: tailing the oil-tick loop's plain-text
progress log."""
from __future__ import annotations

from services.strategies import oil_log as OL


def test_tail_lines_keeps_only_the_last_n_non_empty_lines():
    text = "\n".join([f"line{i}" for i in range(5)]) + "\n\n   \n"
    assert OL.tail_lines(text, 2) == ["line3", "line4"]
    assert OL.tail_lines(text, 100) == [f"line{i}" for i in range(5)]
    assert OL.tail_lines(text, 0) == []


def test_read_oil_log_reads_the_real_file(tmp_path):
    p = tmp_path / "progress.log"
    p.write_text("OIL-TICK 2026-10-23T00:00 mid=107.5 pos=flat action=NONE no setup\n"
                "OIL-TICK 2026-10-23T00:15 mid=107.6 pos=flat action=NONE no setup\n")
    assert OL.read_oil_log(n=1, path=str(p)) == [
        "OIL-TICK 2026-10-23T00:15 mid=107.6 pos=flat action=NONE no setup"]


def test_read_oil_log_missing_file_is_empty_not_an_error(tmp_path):
    assert OL.read_oil_log(n=30, path=str(tmp_path / "does-not-exist.log")) == []


def test_log_path_honors_the_env_var(monkeypatch):
    monkeypatch.delenv("PATHIEL_OIL_LOG", raising=False)
    assert OL.log_path() == OL.DEFAULT_LOG_PATH
    monkeypatch.setenv("PATHIEL_OIL_LOG", "/tmp/somewhere/progress.log")
    assert OL.log_path() == "/tmp/somewhere/progress.log"
