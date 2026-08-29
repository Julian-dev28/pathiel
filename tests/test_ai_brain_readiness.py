"""The AI brain must declare whether it can actually run.

Every failure mode here is silent. A missing CLI binary makes _run_cli return ""
(the FileNotFoundError branch), an empty completion fails to parse, and an
unparseable verdict has historically defaulted to PASS. So "the brain is broken"
and "the brain looked and declined" are indistinguishable at runtime — the same
shape as a data outage reading as a quiet market.

It also names the deploy problem: claude_cli and codex_cli shell out to a binary
on the operator's machine. Neither exists in a container, so an image built with
either selected would run with a permanently dead brain.
"""
from __future__ import annotations

import pytest

from hermes_trader.agents.ai_brain import provider_readiness


def test_a_cli_provider_is_never_marked_deployable(monkeypatch):
    for prov in ("claude_cli", "codex_cli"):
        r = provider_readiness(prov)
        assert r["deployable"] is False, (
            f"{prov} shells out to a local binary — an image built with it "
            f"would run with a permanently dead brain")
        assert "container" in r["deploy_note"]


def test_a_missing_cli_binary_is_reported_as_not_ready(monkeypatch):
    monkeypatch.setenv("CLAUDE_CLI_COMMAND", "/nonexistent/definitely-not-here")
    r = provider_readiness("claude_cli")
    assert r["ready"] is False
    assert "PASS" in r["reason"], (
        "the reason must name the silent failure, since that is what makes this "
        "worth checking at all")


def test_openrouter_is_deployable_and_needs_a_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    r = provider_readiness("openrouter")
    assert r["ready"] is True and r["deployable"] is True

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    r2 = provider_readiness("openrouter")
    assert r2["ready"] is False and "OPENROUTER_API_KEY" in r2["reason"]


def test_readiness_never_raises(monkeypatch):
    """Callers are a healthcheck and a preflight. Both want the reason, not a
    traceback."""
    monkeypatch.setenv("CLAUDE_CLI_COMMAND", "")
    assert isinstance(provider_readiness("claude_cli"), dict)


# ── it reaches the surfaces that act on it ───────────────────────────────────

def test_the_deep_healthcheck_reports_the_brain(monkeypatch):
    from fastapi.testclient import TestClient
    from hermes_trader.server import app
    body = TestClient(app).get("/api/health/system").json()
    assert "ai_brain" in body["checks"]


def test_the_deploy_preflight_refuses_a_cli_provider():
    """A deploy must not ship an image whose brain cannot start."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "scripts" / "preflight_secrets.py").read_text()
    assert "ai_brain_deployable" in src
    assert "check_ai_brain_usable" in src


# ── the redaction that made this readable ────────────────────────────────────

def test_redaction_covers_every_declared_secret_regardless_of_length():
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
    import preflight_secrets as pf
    env = {spec.name: "x" * 6 for spec in pf.REGISTRY}
    vals = pf._redaction_values(env)
    assert all(v in vals for v in env.values()), (
        "a declared secret escaped redaction because it was short")


def test_redaction_leaves_short_non_secret_config_readable():
    """Redacting everything made findings useless: '[REDACTED]: shells out to a
    local binary' does not say WHICH provider. No credential is 10 chars long."""
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
    import preflight_secrets as pf
    vals = pf._redaction_values({"AI_BRAIN_PROVIDER": "claude_cli"})
    assert "claude_cli" not in vals


def test_redaction_still_catches_an_undeclared_long_value():
    """Something added to .env.local that nobody remembered to declare here must
    still never print."""
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
    import preflight_secrets as pf
    secret = "s" * pf._UNDECLARED_SECRET_MIN_LEN
    assert secret in pf._redaction_values({"SOME_NEW_THING": secret})
