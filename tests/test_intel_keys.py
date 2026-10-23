"""Unit tests for scripts/intel_keys.py: reports which services/intel API
keys are set, from a plain dict rather than the real process environment
or a real .env.local, so these never depend on (or mutate) anything
outside the test."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "intel_keys.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("intel_keys_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


intel_keys = _load_module()


def test_key_status_all_missing_from_empty_environ(monkeypatch):
    monkeypatch.setattr(intel_keys, "ENV_LOCAL", Path("/nonexistent/.env.local"))
    status = intel_keys.key_status({})
    assert set(status) == {"EIA_API_KEY", "FRED_API_KEY", "CONGRESS_API_KEY"}
    assert all(v is False for v in status.values())


def test_key_status_reflects_a_set_key(monkeypatch):
    monkeypatch.setattr(intel_keys, "ENV_LOCAL", Path("/nonexistent/.env.local"))
    status = intel_keys.key_status({"EIA_API_KEY": "real-key-value"})
    assert status["EIA_API_KEY"] is True
    assert status["FRED_API_KEY"] is False


def test_key_status_reads_env_local_but_never_overrides_real_environ(tmp_path, monkeypatch):
    env_local = tmp_path / ".env.local"
    env_local.write_text("EIA_API_KEY=from_dotenv\nFRED_API_KEY=also_from_dotenv\n")
    monkeypatch.setattr(intel_keys, "ENV_LOCAL", env_local)

    status = intel_keys.key_status({"EIA_API_KEY": "from_real_shell"})
    assert status["EIA_API_KEY"] is True   # present either way
    assert status["FRED_API_KEY"] is True  # filled from .env.local
    assert status["CONGRESS_API_KEY"] is False


def test_key_status_ignores_comments_and_blank_lines(tmp_path, monkeypatch):
    env_local = tmp_path / ".env.local"
    env_local.write_text("# a comment\n\nEIA_API_KEY=value\n")
    monkeypatch.setattr(intel_keys, "ENV_LOCAL", env_local)
    status = intel_keys.key_status({})
    assert status["EIA_API_KEY"] is True


def test_render_report_lists_missing_keys_with_register_urls():
    status = {"EIA_API_KEY": False, "FRED_API_KEY": True, "CONGRESS_API_KEY": False}
    report = intel_keys.render_report(status)
    assert "MISSING" in report and "SET" in report
    assert "eia.gov/opendata/register.php" in report
    assert "fred.stlouisfed.org" not in report  # FRED is SET -> no register URL printed
    assert "congress.gov/sign-up" in report
    assert "2 of 3 key(s) missing" in report
    assert "No key needed" in report
    assert "sources/venue.py" in report


def test_render_report_all_set_says_so():
    status = {"EIA_API_KEY": True, "FRED_API_KEY": True, "CONGRESS_API_KEY": True}
    report = intel_keys.render_report(status)
    assert "All 3 keys are set." in report
    assert "MISSING" not in report


def test_main_check_flag_exit_code(monkeypatch, capsys):
    monkeypatch.setattr(intel_keys, "ENV_LOCAL", Path("/nonexistent/.env.local"))
    monkeypatch.setattr(intel_keys.sys, "argv", ["intel_keys.py", "--check"])
    monkeypatch.setattr(intel_keys.os, "environ", {})
    assert intel_keys.main() == 1

    monkeypatch.setattr(intel_keys.os, "environ",
                        {"EIA_API_KEY": "x", "FRED_API_KEY": "x", "CONGRESS_API_KEY": "x"})
    assert intel_keys.main() == 0
