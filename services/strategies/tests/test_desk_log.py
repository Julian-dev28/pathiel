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
    p.write_text("DESK-TICK oil 2026-10-23T00:00 mid=107.5 pos=flat action=NONE no setup\n"
                "DESK-TICK oil 2026-10-23T00:15 mid=107.6 pos=flat action=NONE no setup\n")
    assert DL.read_desk_log("oil", n=1, path=str(p)) == [
        "DESK-TICK oil 2026-10-23T00:15 mid=107.6 pos=flat action=NONE no setup"]


def test_read_desk_log_missing_file_is_empty_not_an_error(tmp_path):
    assert DL.read_desk_log("oil", n=30, path=str(tmp_path / "does-not-exist.log")) == []


def test_log_path_honors_the_env_var(monkeypatch):
    monkeypatch.delenv("PATHIEL_OIL_LOG", raising=False)
    assert DL.log_path("oil") == DL.DEFAULT_LOG_PATH
    monkeypatch.setenv("PATHIEL_OIL_LOG", "/tmp/somewhere/progress.log")
    assert DL.log_path("oil") == "/tmp/somewhere/progress.log"


def test_log_path_generalizes_to_any_desk_id(monkeypatch):
    """Every desk's tick appends to the one shared file (desk-tick.md step 6),
    so it is every desk's default; 2026-10-31 the silver card read an empty
    /tmp/metals-loop file while its ticks were logged in the shared one."""
    monkeypatch.delenv("PATHIEL_BTC_LOG", raising=False)
    monkeypatch.delenv("PATHIEL_METALS_LOG", raising=False)
    assert DL.log_path("btc") == DL.DEFAULT_LOG_PATH
    assert DL.log_path("metals") == DL.DEFAULT_LOG_PATH
    # The production default is the one shared file. Tests read it off the
    # source: conftest's _desk_log_isolation repoints the constant per test so
    # no test can write the live log (2026-11-20).
    import inspect
    assert 'DEFAULT_LOG_PATH = "/tmp/oil-loop/progress.log"' in inspect.getsource(DL)
    monkeypatch.setenv("PATHIEL_BTC_LOG", "/tmp/custom-btc.log")
    assert DL.log_path("btc") == "/tmp/custom-btc.log"


def test_read_desk_log_resolves_the_desk_specific_env_path(tmp_path, monkeypatch):
    p = tmp_path / "btc-progress.log"
    p.write_text("DESK-TICK btc line one\n")
    monkeypatch.setenv("PATHIEL_BTC_LOG", str(p))
    assert DL.read_desk_log("btc", n=5) == ["DESK-TICK btc line one"]


# ── 2026-10-31: silver's loop shares oil's progress.log ─────────────────────

def test_filter_desk_lines_keeps_only_this_desks_tick_lines():
    lines = [
        "DESK-TICK oil 2026-10-31T00:00Z coin=xyz:BRENTOIL mid=112.5 pos=flat action=NONE no setup",
        "DESK-TICK metals 2026-10-31T00:01Z coin=xyz:SILVER mid=56.9 pos=flat action=NONE no setup",
        "DESK-TICK oil 2026-10-31T00:15Z coin=xyz:BRENTOIL mid=112.6 pos=flat action=NONE no setup",
    ]
    assert DL.filter_desk_lines(lines, "oil") == [lines[0], lines[2]]
    assert DL.filter_desk_lines(lines, "metals") == [lines[1]]


def test_filter_desk_lines_is_case_insensitive_on_the_desk_id():
    lines = ["DESK-TICK OIL 2026-10-31T00:00Z action=NONE"]
    assert DL.filter_desk_lines(lines, "oil") == lines


def test_filter_desk_lines_drops_lines_with_no_known_desk_addressed_prefix():
    """2026-11-20 bug fix: until this date a line with neither `DESK-TICK
    <desk>` nor `WATCH <desk>` passed through for EVERY desk ("nothing says
    it belongs to another desk"), on the assumption nothing else ever wrote
    into this shared file. The oil-only paper-breakout sim's own
    `PAPER-A6 ...` lines (scripts/paper_breakout.py, written into this same
    `/tmp/oil-loop/progress.log`) broke that: the sp500 and hype desks'
    "Last tick" picked up a PAPER-A6 line that was never theirs. Default
    filtering now drops any unaddressed line for every desk; `include_all`
    is the way to still see it (test below)."""
    lines = ["PAPER-A6 2026-11-20T00:00Z mid=90.05 pos=long 0.42@88.50 stop=86.10 tp=91.66 action=HOLD"]
    assert DL.filter_desk_lines(lines, "oil") == []
    assert DL.filter_desk_lines(lines, "metals") == []


def test_filter_desk_lines_keeps_watch_lines_naming_this_desk():
    lines = [
        "WATCH oil 2026-11-20T00:00Z coin=xyz:BRENTOIL mid=112.0 action=WAKE entry rule passed, 2.1R",
        "WATCH hype 2026-11-20T00:01Z coin=HYPE mid=29.4 action=WAKE entry rule passed, 2.3R",
        "DESK-TICK oil 2026-11-20T00:02Z coin=xyz:BRENTOIL mid=112.0 action=PLACED 2.1R",
    ]
    assert DL.filter_desk_lines(lines, "oil") == [lines[0], lines[2]]
    assert DL.filter_desk_lines(lines, "hype") == [lines[1]]
    assert DL.filter_desk_lines(lines, "sp500") == []


def test_filter_desk_lines_is_case_insensitive_on_watch_lines_too():
    lines = ["WATCH HYPE 2026-11-20T00:00Z action=WAKE entry rule passed"]
    assert DL.filter_desk_lines(lines, "hype") == lines


def test_read_desk_log_filters_a_shared_file_to_this_desk_before_tailing(tmp_path):
    p = tmp_path / "progress.log"
    lines = []
    for i in range(5):
        lines.append(f"DESK-TICK metals 2026-10-31T00:{i:02d}Z coin=xyz:SILVER mid=56.9 action=NONE tick {i}")
        lines.append(f"DESK-TICK oil 2026-10-31T00:{i:02d}Z coin=xyz:BRENTOIL mid=112.5 action=NONE tick {i}")
    p.write_text("\n".join(lines) + "\n")
    # Lines interleave metals/oil every round; each desk must still get its
    # OWN last 2 lines, not a mix of the two or the other desk's lines.
    assert DL.read_desk_log("oil", n=2, path=str(p)) == [
        "DESK-TICK oil 2026-10-31T00:03Z coin=xyz:BRENTOIL mid=112.5 action=NONE tick 3",
        "DESK-TICK oil 2026-10-31T00:04Z coin=xyz:BRENTOIL mid=112.5 action=NONE tick 4",
    ]
    assert DL.read_desk_log("metals", n=2, path=str(p)) == [
        "DESK-TICK metals 2026-10-31T00:03Z coin=xyz:SILVER mid=56.9 action=NONE tick 3",
        "DESK-TICK metals 2026-10-31T00:04Z coin=xyz:SILVER mid=56.9 action=NONE tick 4",
    ]


# ── 2026-11-20: the "Last tick" bug (a PAPER-A6 line, not this desk's own) ──

def test_read_desk_log_never_lets_an_unaddressed_sim_line_pose_as_this_desks_last_tick(tmp_path):
    """The exact bug report: the oil-only paper_breakout.py sim writes
    PAPER-A6 lines into the SAME shared file the sp500/hype desks read their
    loop log from. Neither desk has ticked yet in this file; before the fix
    the sim line (no DESK-TICK/WATCH prefix) still came back as their "last
    line". After the fix it must never appear for a desk that never wrote
    it, with or without other desks' real ticks present."""
    p = tmp_path / "progress.log"
    p.write_text(
        "DESK-TICK oil 2026-11-20T00:00Z coin=xyz:BRENTOIL mid=61.2 action=NONE no setup\n"
        "PAPER-A6 2026-11-20T00:00Z mid=90.0475 pos=long 0.4210@88.503 stop=86.100 tp=91.664 action=HOLD\n"
        "DESK-TICK oil 2026-11-20T00:15Z coin=xyz:BRENTOIL mid=61.3 action=NONE no setup\n"
    )
    assert DL.read_desk_log("sp500", n=30, path=str(p)) == []
    assert DL.read_desk_log("hype", n=30, path=str(p)) == []
    oil_lines = DL.read_desk_log("oil", n=30, path=str(p))
    assert oil_lines and oil_lines[-1].startswith("DESK-TICK oil")
    assert "PAPER-A6" not in oil_lines[-1]


def test_read_desk_log_include_all_returns_the_raw_tail_unfiltered(tmp_path):
    p = tmp_path / "progress.log"
    p.write_text(
        "DESK-TICK oil 2026-11-20T00:00Z action=NONE no setup\n"
        "PAPER-A6 2026-11-20T00:00Z mid=90.05 action=HOLD\n"
        "DESK-TICK hype 2026-11-20T00:01Z action=NONE no setup\n"
    )
    assert DL.read_desk_log("sp500", n=30, path=str(p), include_all=True) == [
        "DESK-TICK oil 2026-11-20T00:00Z action=NONE no setup",
        "PAPER-A6 2026-11-20T00:00Z mid=90.05 action=HOLD",
        "DESK-TICK hype 2026-11-20T00:01Z action=NONE no setup",
    ]
    assert DL.read_desk_log("sp500", n=30, path=str(p), include_all=False) == []
