"""Tests for services/risk/evals/replay_benchmark.py.

`replay()` (the pure core — signals in, a report out) gets the bulk of the
coverage directly, with hand-built signal lists: fast, deterministic, no
process boundary. `main()` gets exactly one subprocess smoke test, because
`load_recent_signals` sets PATHIEL_STATE_DIR and imports
pathiel.agents.shadow_ledger, which transitively imports
pathiel.agents.rebalancer_owned — a module that resolves its state
directory AT IMPORT TIME (see that module's own docstring/ruff-exclusion
comment). Calling load_recent_signals twice in the SAME process with two
different state dirs would use whichever directory was frozen in on the
first import, which is a real footgun for an in-process test and exactly
why the real script is designed to run once per process. The subprocess
test exercises the script exactly as Julien will: a fresh interpreter per
run.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from services.risk.evals.replay_benchmark import copy_shadow_ledger, format_report, replay

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "services" / "risk" / "evals" / "replay_benchmark.py"


def _sig(book: str, coin: str = "BTC", stop_pct: float = 15.0, side: str = "long",
        ts=None) -> dict:
    return {"v": 1, "ts": ts if ts is not None else int(time.time() * 1000),
           "book": book, "coin": coin, "side": side, "signal_bar_t": 0,
           "entry_ref_px": 100.0, "horizon_days": 1.0, "stop_pct": stop_pct, "meta": {}}


# ──────────────────────────────────────────────────────── replay() (pure)

def test_empty_signals_passes_trivially():
    report = replay([], [15.0, 1000.0], "auto")
    assert report["passed"] is True
    assert report["signals_total"] == 0
    assert all(row["would_execute"] == 0 for row in report["per_equity"])


def test_a_15_dollar_equity_executes_a_real_signal_from_the_live_bug():
    """The exact scenario this whole wave fixes: a tight-stop signal from a
    real registered book, replayed at $15 equity, should be executable --
    this is the eval's own small proof the $15 fix generalizes beyond the
    one hand-built e2e test."""
    signals = [_sig("news_surge_short", stop_pct=6.0)]
    report = replay(signals, [15.0], "auto")
    assert report["passed"] is True
    assert report["per_equity"][0]["would_execute"] == 1
    assert report["per_equity"][0]["risk_taken_usd"] > 0


def test_unregistered_book_name_is_skipped_not_crashed():
    signals = [_sig("some_retired_hypothesis_book")]
    report = replay(signals, [1000.0], "auto")
    assert report["passed"] is True
    assert report["per_equity"][0]["signals_considered"] == 0


def test_fixed_structural_book_is_skipped_it_has_no_risk_scaled_shape():
    signals = [_sig("drawdown_ladder", stop_pct=0.0)]
    report = replay(signals, [1000.0], "auto")
    assert report["per_equity"][0]["signals_considered"] == 0


def test_zero_or_missing_stop_pct_is_skipped_not_treated_as_free_risk():
    signals = [_sig("xs_reversal", stop_pct=0.0)]
    report = replay(signals, [1000.0], "auto")
    assert report["per_equity"][0]["signals_considered"] == 0


def test_never_violates_its_own_pass_rule_across_a_wide_equity_sweep():
    """The property that IS the eval: across every registered risk-scaled
    book and a wide equity sweep, no executed trade's risk_pct ever
    exceeds its cap (the base cap, or the bump cap when bumped)."""
    import services.risk.books as risk_books
    signals = [_sig(b, coin="BTC", stop_pct=p)
              for b in risk_books.RISK_SCALED_BOOK_NAMES
              for p in (3.0, 6.0, 12.0, 21.0)]
    report = replay(signals, [5, 15, 50, 500, 5000, 100000], "auto")
    assert report["passed"] is True, report["violations"]


def test_more_equity_never_decreases_would_execute_for_a_fixed_signal_set():
    """Direct consequence of derive_limits' own feasible-set monotonicity
    (services/risk/tests/test_derive.py) -- re-asserted here against the
    REPLAY path specifically, since that is the code the eval script
    actually exercises."""
    import services.risk.books as risk_books
    signals = [_sig(b, stop_pct=15.0) for b in risk_books.RISK_SCALED_BOOK_NAMES]
    report = replay(signals, [5, 10, 15, 100, 10000], "auto")
    counts = [row["would_execute"] for row in report["per_equity"]]
    assert counts == sorted(counts)


def test_format_report_names_the_pass_rule_and_is_human_readable():
    report = replay([_sig("xs_reversal")], [15.0], "auto")
    text = format_report(report)
    assert "no trade exceeds its limit" in text
    assert "PASSED" in text or "FAILED" in text


# ───────────────────────────────────────────────────── copy_shadow_ledger

def test_copy_shadow_ledger_copies_only_that_subdirectory(tmp_path):
    src = tmp_path / "fake_state"
    (src / "shadow_ledger").mkdir(parents=True)
    (src / "shadow_ledger" / "xs_reversal.jsonl").write_text(json.dumps(_sig("xs_reversal")) + "\n")
    (src / "some_other_live_file.json").write_text("{}")   # must NOT be copied

    dst = copy_shadow_ledger(str(src))
    assert (dst / "shadow_ledger" / "xs_reversal.jsonl").is_file()
    assert not (dst / "some_other_live_file.json").exists()


def test_copy_shadow_ledger_tolerates_a_missing_source_directory(tmp_path):
    dst = copy_shadow_ledger(str(tmp_path / "does_not_exist"))
    assert (dst / "shadow_ledger").is_dir()   # created empty, not an error


# ─────────────────────────────────────────────────────── main() (subprocess)

def test_script_runs_end_to_end_against_a_synthetic_state_dir(tmp_path):
    """Exactly how Julien will run it: a fresh process, a real --state-dir.
    Fabricates a tiny synthetic shadow_ledger (never touches any real
    state) to prove the full CLI path -- copy, load, replay, report,
    exit code -- works end to end."""
    state_dir = tmp_path / "synthetic_state"
    ledger_dir = state_dir / "shadow_ledger"
    ledger_dir.mkdir(parents=True)
    rows = [_sig("news_surge_short", coin="BTC", stop_pct=6.0),
           _sig("xs_reversal", coin="ETH", stop_pct=15.0)]
    (ledger_dir / "news_surge_short.jsonl").write_text(json.dumps(rows[0]) + "\n")
    (ledger_dir / "xs_reversal.jsonl").write_text(json.dumps(rows[1]) + "\n")

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--state-dir", str(state_dir),
        "--equities", "15,1000", "--json"],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads(proc.stdout)
    assert report["signals_total"] == 2
    assert report["passed"] is True
    assert len(report["per_equity"]) == 2


def test_script_exits_2_with_no_state_dir_and_no_env_var(monkeypatch):
    env = {k: v for k, v in __import__("os").environ.items() if k != "PATHIEL_STATE_DIR"}
    proc = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True,
                          timeout=30, env=env)
    assert proc.returncode == 2
