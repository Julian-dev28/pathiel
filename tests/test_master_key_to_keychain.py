"""scripts/master_key_to_keychain.py: move the withdrawal key from the env file
into the macOS Keychain (2026-11-16). A fake Keychain keeps this offline and
cross-platform; the real `security` round trip was run by hand on macOS 15
with a dummy item and a fake key."""
from __future__ import annotations

import importlib.util
import os
import stat
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("master_key_to_keychain", ROOT / "scripts" / "master_key_to_keychain.py")
K = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(K)

FAKE = "0x" + "ab" * 32
ENV = f"HYPERLIQUID_PRIVATE_KEY=0xagent\n# keep me\n\nHYPERLIQUID_MASTER_PRIVATE_KEY={FAKE}\nPATHIEL_STATE_DIR=/x\n"
REST = "HYPERLIQUID_PRIVATE_KEY=0xagent\n# keep me\n\nPATHIEL_STATE_DIR=/x\n"


class FakeKeychain:
    def __init__(self, broken: bool = False):
        self.items, self.broken = {}, broken

    def store(self, account, service, secret):
        self.items[(account, service)] = "corrupted" if self.broken else secret

    def read(self, account, service):
        return self.items.get((account, service))


@pytest.fixture
def env_file(tmp_path):
    p = tmp_path / ".env.local"
    p.write_text(ENV)
    p.chmod(0o600)
    return p


def _move(env_file, kc, **kw):
    return K.move(str(env_file), "me", "svc", store=kc.store, read=kc.read, **kw)


def test_split_env_takes_the_key_out_and_keeps_every_other_line():
    assert K.split_env(ENV) == (FAKE, REST)
    assert K.split_env("HYPERLIQUID_MASTER_PRIVATE_KEY=\nA=1\n") == (None, "A=1\n")
    assert K.split_env(f'HYPERLIQUID_MASTER_PRIVATE_KEY="{FAKE}"\n') == (FAKE, "")
    assert K.split_env("# HYPERLIQUID_MASTER_PRIVATE_KEY=x\n") == (None, "# HYPERLIQUID_MASTER_PRIVATE_KEY=x\n")


def test_move_stores_verifies_then_removes_and_keeps_the_mode(env_file):
    kc = FakeKeychain()
    code, report = _move(env_file, kc)
    assert code == 0 and kc.items[("me", "svc")] == FAKE
    assert env_file.read_text() == REST
    assert stat.S_IMODE(os.stat(env_file).st_mode) == 0o600
    assert FAKE not in report and "ab" * 32 not in report


def test_a_keychain_copy_that_does_not_read_back_leaves_the_file_alone(env_file):
    code, report = _move(env_file, FakeKeychain(broken=True))
    assert code == 1 and env_file.read_text() == ENV and FAKE not in report


def test_dry_run_changes_nothing(env_file):
    kc = FakeKeychain()
    code, _ = _move(env_file, kc, dry_run=True)
    assert code == 0 and kc.items == {} and env_file.read_text() == ENV


def test_a_value_that_is_not_a_key_is_refused_and_not_echoed(tmp_path):
    p = tmp_path / ".env.local"
    p.write_text("HYPERLIQUID_MASTER_PRIVATE_KEY=oops-not-a-key\n")
    kc = FakeKeychain()
    code, report = _move(p, kc)
    assert code == 2 and kc.items == {} and "oops" not in report
    assert p.read_text() == "HYPERLIQUID_MASTER_PRIVATE_KEY=oops-not-a-key\n"


def test_a_second_run_has_nothing_to_move(env_file):
    kc = FakeKeychain()
    _move(env_file, kc)
    code, report = _move(env_file, kc)
    assert code == 0 and "nothing to move" in report and "The Keychain holds svc" in report


def test_the_secret_goes_to_security_on_stdin_never_in_argv(monkeypatch):
    seen = {}

    def run(args, **kw):
        seen["args"], seen["input"] = args, kw.get("input")
        class P:
            returncode, stderr = 0, ""
        return P()

    monkeypatch.setattr(K.subprocess, "run", run)
    K.keychain_store("me", "svc", FAKE)
    assert seen["args"] == ["security", "-i"]
    assert FAKE not in " ".join(seen["args"]) and FAKE in seen["input"]
    assert seen["input"].startswith('add-generic-password -U -a "me" -s "svc" -w "')


def test_the_printed_example_is_a_command_treasury_accepts(env_file):
    """The 2026-11-16 report printed `scripts/treasury.py` with no command, which
    exits with a usage error. Every example must name a real subcommand."""
    import re
    subs = set(re.findall(r'add_parser\("([a-z-]+)"', (ROOT / "scripts" / "treasury.py").read_text()))
    _, report = _move(env_file, FakeKeychain())
    docs = [report, K.__doc__, (ROOT / "README.md").read_text(), (ROOT / "docs" / "SECRETS.md").read_text()]
    for text in docs:
        calls = re.findall(r"scripts/treasury\.py(?: ([a-z-]+))?", text.split("Keychain", 1)[-1])
        assert calls and all(c in subs for c in calls), calls
