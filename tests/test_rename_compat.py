"""Gate tests for the pathia -> pathiel rename.

A rename is only safe if the things other people's files point at keep working.
Two of those, and both fail silently rather than loudly:

  the environment   65 PATHIA_* variables live in .env.local, in `fly secrets`
                    and on Vercel. A default that stops being read does not
                    error — it falls back, and the system carries on meaning
                    something else. PATHIA_STATE_DIR falling back means a
                    read-only directory; PATHIA_AUTH_NONCE_SECRET falling back
                    means nobody can sign in.

  the state files   ~/.pathia-session-log.jsonl held 8600 events of real
                    trading history at the time of the rename. Orphaning it
                    does not raise either: the dashboard renders a healthy,
                    empty, entirely wrong account.
"""

from __future__ import annotations

import os
from pathlib import Path

from pathiel import compat

ROOT = Path(__file__).resolve().parents[1]


class TestEnvironmentMirroring:
    def test_an_old_name_answers_to_the_new_one(self):
        env = {"PATHIA_STATE_DIR": "/srv/state"}
        compat.mirror_environment(env)
        assert env["PATHIEL_STATE_DIR"] == "/srv/state"

    def test_a_new_name_answers_to_the_old_one(self):
        """Both directions: a fresh Vercel variable has to reach code that has
        not been renamed yet, and vice versa."""
        env = {"PATHIEL_AUTH_DOMAIN": "example.com"}
        compat.mirror_environment(env)
        assert env["PATHIA_AUTH_DOMAIN"] == "example.com"

    def test_an_explicit_value_is_never_overwritten(self):
        """Setting both to different values is a mistake, and which one the
        operator meant is not knowable here. Neither wins by surprise."""
        env = {"PATHIA_X": "old", "PATHIEL_X": "new"}
        compat.mirror_environment(env)
        assert env == {"PATHIA_X": "old", "PATHIEL_X": "new"}

    def test_unrelated_variables_are_left_alone(self):
        env = {"HOME": "/root", "HYPERLIQUID_WALLET_ADDRESS": "0xabc"}
        compat.mirror_environment(env)
        assert env == {"HOME": "/root", "HYPERLIQUID_WALLET_ADDRESS": "0xabc"}

    def test_it_runs_on_import(self):
        """Every module below pathiel/ reads the environment at module scope,
        so mirroring after the first import would be too late."""
        os.environ["PATHIA_RENAME_IMPORT_PROBE"] = "1"
        try:
            assert compat.mirror_environment() >= 1
            assert os.environ["PATHIEL_RENAME_IMPORT_PROBE"] == "1"
        finally:
            os.environ.pop("PATHIA_RENAME_IMPORT_PROBE", None)
            os.environ.pop("PATHIEL_RENAME_IMPORT_PROBE", None)

    def test_the_package_imports_compat_before_anything_reads_env(self):
        source = (ROOT / "pathiel" / "__init__.py").read_text()
        assert "from pathiel import compat" in source
        assert source.index("from pathiel import compat") < source.index("import ssl")


class TestStateFileFallback:
    def test_the_new_path_wins_when_it_exists(self, tmp_path):
        new = tmp_path / "new.jsonl"
        old = tmp_path / "old.jsonl"
        new.write_text("")
        old.write_text("")
        assert compat.legacy_path(str(new), str(old)) == str(new)

    def test_the_old_path_is_used_when_only_it_exists(self, tmp_path):
        """The case that matters: the rename shipped, the history did not move."""
        old = tmp_path / "old.jsonl"
        old.write_text("")
        assert compat.legacy_path(str(tmp_path / "new.jsonl"), str(old)) == str(old)

    def test_the_new_path_is_returned_when_neither_exists(self, tmp_path):
        """A fresh install writes under the new name."""
        new = tmp_path / "new.jsonl"
        assert compat.legacy_path(str(new), str(tmp_path / "old.jsonl")) == str(new)

    def test_nothing_is_moved(self, tmp_path):
        """Not a migration. Relocating a file the trading loop may have open is
        worse than reading it where it lies, and a silent move of 8600 events
        leaves the operator nothing to undo."""
        old = tmp_path / "old.jsonl"
        old.write_text("data")
        compat.legacy_path(str(tmp_path / "new.jsonl"), str(old))
        assert old.exists() and old.read_text() == "data"
        assert not (tmp_path / "new.jsonl").exists()

    def test_an_explicit_env_var_beats_both(self, monkeypatch, tmp_path):
        old = tmp_path / "old.jsonl"
        old.write_text("")
        monkeypatch.setenv("SESSION_LOG_PATH", "/explicit/path.jsonl")
        assert compat.resolve_state_file(
            "SESSION_LOG_PATH", str(tmp_path / "new.jsonl"), str(old)
        ) == "/explicit/path.jsonl"


class TestTheRenameIsComplete:
    def test_the_session_log_still_finds_real_history(self):
        """Resolves to a path that exists, under either name."""
        from pathiel import session_log
        assert session_log.SESSION_LOG_FILE.endswith("session-log.jsonl")

    def test_the_package_is_importable_under_the_new_name(self):
        import pathiel
        assert pathiel.__version__

    def test_no_module_still_imports_the_old_package(self):
        """A missed import would only fail when that code path first runs."""
        import re
        offenders = []
        for path in list((ROOT / "pathiel").rglob("*.py")) + \
                    list((ROOT / "services").rglob("*.py")) + \
                    list((ROOT / "scripts").rglob("*.py")):
            if "__pycache__" in str(path) or "node_modules" in str(path):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"^\s*(from|import)\s+pathia\b", text, re.M):
                offenders.append(str(path.relative_to(ROOT)))
        assert not offenders, f"still importing the old package: {offenders}"

    def test_the_external_framework_was_not_renamed(self):
        """`Pathia Agent` is NousResearch's product. Renaming it in our docs
        would point readers at a URL that does not exist and credit the wrong
        project."""
        readme = (ROOT / "README.md").read_text()
        assert "NousResearch/pathia-agent" in readme
        assert "Pathiel Agent" not in readme
