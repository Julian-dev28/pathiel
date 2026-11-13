"""The Claude Code plugin packaging (2026-10-25): this repo is its own plugin
marketplace. `claude plugin marketplace add Julian-dev28/pathiel` then
`claude plugin install pathiel@pathiel` gives a user the MCP server, the
desk-tick skill and /pathiel:setup, with keys and state under ~/.pathiel and
the Python environment under the plugin data dir.

Everything here is offline and deterministic. The one check that needs the
`claude` binary skips when it is not on PATH (CI).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_JSON = ROOT / ".claude-plugin" / "plugin.json"
MARKET_JSON = ROOT / ".claude-plugin" / "marketplace.json"
MCP_WRAPPER = ROOT / "scripts" / "pathiel-mcp"
PY_WRAPPER = ROOT / "scripts" / "pathiel-py"
PLUGIN_ENV = ROOT / "scripts" / "_plugin_env.sh"


def _manifest() -> dict:
    return json.loads(PLUGIN_JSON.read_text())


def _marketplace() -> dict:
    return json.loads(MARKET_JSON.read_text())


# ── manifests ────────────────────────────────────────────────────────────────

def test_the_plugin_and_marketplace_names_agree_and_the_version_is_the_packages():
    """The install id is <entry name>@<marketplace name>; a mismatch between
    the entry and the manifest is the documented way an install fails."""
    from pathiel import __version__
    m, mk = _manifest(), _marketplace()
    assert m["name"] == "pathiel"
    assert mk["name"] == "pathiel"
    entries = mk["plugins"]
    assert len(entries) == 1 and entries[0]["name"] == m["name"]
    assert entries[0]["source"] in ("./", "."), "this repo is the plugin; the source is its own root"
    assert m["version"] == __version__, "bump plugin.json with pathiel/__init__.py"
    assert m["author"]["name"] and mk["owner"]["name"]


def test_the_plugin_manifest_has_no_component_paths_outside_the_defaults():
    """Skills, commands and the MCP wrapper all sit in their default
    locations; the manifest only names the MCP server, so a future field
    cannot quietly replace the default scans."""
    m = _manifest()
    assert set(m) <= {"name", "displayName", "version", "description", "author", "homepage",
                      "repository", "documentationUrl", "privacyPolicyUrl", "supportUrl", "license", "keywords", "mcpServers"}, sorted(m)
    assert (ROOT / "skills" / "desk-tick" / "SKILL.md").is_file()
    assert (ROOT / "skills" / "pathiel-agent" / "SKILL.md").is_file()
    assert (ROOT / "agents" / "desk-tick.md").is_file()
    assert (ROOT / "commands" / "setup.md").is_file()
    assert sorted(p.name for p in (ROOT / ".claude-plugin").iterdir()) == ["icon.png", "marketplace.json", "plugin.json"]


# ── the MCP entry point ──────────────────────────────────────────────────────

def test_the_mcp_server_runs_through_the_plugin_wrapper():
    m = _manifest()
    server = m["mcpServers"]["pathiel"]
    # The directory asks for the interpreter by name (START_FILE_AS_PROGRAM):
    # bash starts the launcher, the launcher is the first argument.
    assert server["command"] == "bash"
    assert server["args"] == ["${CLAUDE_PLUGIN_ROOT}/scripts/pathiel-mcp.sh"]
    for path in (MCP_WRAPPER, PY_WRAPPER):
        assert path.is_file(), path
        assert path.stat().st_mode & stat.S_IXUSR, f"{path.name} is not executable"
    assert PLUGIN_ENV.is_file()


def _without_print_commands(text: str) -> str:
    """The file minus the body of pathiel_print_commands: the one function
    that writes stdout on purpose (`scripts/pathiel-mcp --commands`, which
    exits before the server starts; pinned below and in
    tests/test_public_launcher.py)."""
    out, skipping = [], False
    for line in text.splitlines():
        if line.startswith("pathiel_print_commands() {"):
            skipping = True
        elif skipping and line == "}":
            skipping = False
        elif not skipping:
            out.append(line)
    return "\n".join(out)


def test_the_wrappers_parse_and_keep_stdout_clean_for_the_mcp_transport():
    """`bash -n` on all three, and every echo goes to stderr: one stray byte on
    stdout before the exec breaks the MCP handshake."""
    for path in (PLUGIN_ENV, MCP_WRAPPER, PY_WRAPPER):
        subprocess.run(["bash", "-n", str(path)], check=True)
        for line in _without_print_commands(path.read_text()).splitlines():
            s = line.strip()
            if s.startswith(("echo", "printf")) and "sha256" not in s:
                assert ">&2" in s, f"{path.name}: writes to stdout: {s}"
    # scripts/pathiel-mcp is a shim to the launcher, scripts/pathiel-mcp.sh
    # (2026-11-13: the directory wants the file name to match bash).
    shim = MCP_WRAPPER.read_text()
    assert [ln for ln in shim.splitlines() if ln and not ln.startswith("#")] == \
        ['exec bash "$(cd "$(dirname "$0")" && pwd)/pathiel-mcp.sh" "$@"']
    launcher = MCP_WRAPPER.with_name("pathiel-mcp.sh")
    subprocess.run(["bash", "-n", str(launcher)], check=True)
    text = launcher.read_text()
    assert "exec " in text and "pathiel-mcp-server.py" in text
    assert "--bootstrap" in text
    # The stdout writer runs only behind --commands, which exits before exec.
    prepare = PLUGIN_ENV.read_text().split("pathiel_prepare() {", 1)[1].split("\n}", 1)[0]
    assert "pathiel_print_commands" not in prepare
    assert "pathiel_print_commands" not in PY_WRAPPER.read_text()
    branch = text.split('if [ "${1:-}" = "--commands" ]; then', 1)
    assert len(branch) == 2, "pathiel-mcp has no --commands branch"
    assert text.count("pathiel_print_commands") == 1
    body = branch[1].split("fi", 1)[0]
    assert "pathiel_print_commands" in body and "exit 0" in body
    assert branch[1].index("exit 0") < branch[1].index("exec ")


def test_the_wrappers_put_keys_and_state_outside_the_plugin_root():
    """The plugin root is a cache directory Claude Code replaces on update."""
    env = PLUGIN_ENV.read_text()
    assert 'PATHIEL_HOME="${PATHIEL_HOME:-$HOME/.pathiel}"' in env
    assert 'PATHIEL_DATA="${CLAUDE_PLUGIN_DATA:-$PATHIEL_HOME/data}"' in env
    for var in ("PATHIEL_ENV_FILE", "PATHIEL_STATE_DIR", "PATHIEL_AGENT_CONFIG_FILE"):
        assert f'export {var}="${{{var}:-$PATHIEL_HOME/' in env, var
    assert "requirements.lock" in env and "sha256" in env, "the venv must be rebuilt when the lock changes"


# ── the env file the scripts load ────────────────────────────────────────────

def _load_state_env():
    import importlib.util
    spec = importlib.util.spec_from_file_location("pathiel_state_env_under_test",
                                                  ROOT / "scripts" / "_state_env.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_env_file_precedence_explicit_then_checkout_then_home(tmp_path, monkeypatch):
    se = _load_state_env()
    home = tmp_path / "home"
    (home / ".pathiel").mkdir(parents=True)
    (home / ".pathiel" / ".env.local").write_text("PATHIEL_T_WHO=home\n")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    explicit = tmp_path / "explicit.env"
    explicit.write_text("PATHIEL_T_WHO=explicit\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("PATHIEL_ENV_FILE", raising=False)
    monkeypatch.delenv("PATHIEL_T_WHO", raising=False)

    # nothing in the checkout: the home file is found
    assert se.load_env_local(str(checkout)) == str(home / ".pathiel" / ".env.local")
    assert os.environ["PATHIEL_T_WHO"] == "home"

    # a checkout file outranks home
    monkeypatch.delenv("PATHIEL_T_WHO")
    (checkout / ".env.local").write_text("PATHIEL_T_WHO=checkout\n")
    assert se.load_env_local(str(checkout)) == str(checkout / ".env.local")
    assert os.environ["PATHIEL_T_WHO"] == "checkout"

    # PATHIEL_ENV_FILE outranks both
    monkeypatch.delenv("PATHIEL_T_WHO")
    monkeypatch.setenv("PATHIEL_ENV_FILE", str(explicit))
    assert se.load_env_local(str(checkout)) == str(explicit)
    assert os.environ["PATHIEL_T_WHO"] == "explicit"


def test_env_file_never_overrides_a_real_env_var(tmp_path, monkeypatch):
    se = _load_state_env()
    (tmp_path / ".env.local").write_text("PATHIEL_T_WHO=file\n")
    monkeypatch.setenv("PATHIEL_T_WHO", "process")
    monkeypatch.delenv("PATHIEL_ENV_FILE", raising=False)
    se.load_env_local(str(tmp_path))
    assert os.environ["PATHIEL_T_WHO"] == "process"


def test_the_mcp_server_and_the_web_server_use_the_same_precedence():
    mcp = (ROOT / "scripts" / "pathiel-mcp-server.py").read_text()
    assert "_state_env.load_env_local(" in mcp
    srv = (ROOT / "pathiel" / "server.py").read_text()
    for needle in ('os.environ.get("PATHIEL_ENV_FILE")', '".pathiel", ".env.local"'):
        assert needle in srv, needle


# ── the skill and the command ────────────────────────────────────────────────

def test_the_plugin_desk_tick_skill_and_agent_are_generated_from_the_project_ones():
    """Two copies, one text. Each committed plugin file must equal what the
    generator renders from its .claude/ source."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_plugin_skill as gen
    rendered = dict(gen.render_all())
    assert set(p.relative_to(ROOT).as_posix() for p in rendered) == {
        "skills/desk-tick/SKILL.md", "agents/desk-tick.md"}
    for out, text in rendered.items():
        assert out.read_text() == text, f"rerun scripts/build_plugin_skill.py ({out.name} is stale)"
        assert "python3 scripts/" not in text
        assert "0x2c2e4efF574B3Fd9879AdeF9c5CAC6fD7C916985" not in text
        assert "Julien" not in text and "/Users/" not in text
        assert text.startswith("---\nname: desk-tick\n")
    skill = rendered[ROOT / "skills" / "desk-tick" / "SKILL.md"]
    agent = rendered[ROOT / "agents" / "desk-tick.md"]
    assert '"${CLAUDE_PLUGIN_ROOT}/scripts/pathiel-py" scripts/desk_slot.py' in skill
    assert "`subagent_type` `pathiel:desk-tick`" in skill
    assert '"${CLAUDE_PLUGIN_ROOT}/scripts/pathiel-py" scripts/oil_levels.py --check' in agent


def test_the_tick_agent_runs_on_opus_at_medium_effort():
    """Operator, 2026-10-25: "fable is valuable and the tick is eating my
    usage". The session model dispatches; the tick itself runs here."""
    for path in (ROOT / ".claude" / "agents" / "desk-tick.md", ROOT / "agents" / "desk-tick.md"):
        head = path.read_text().split("\n---\n", 1)[0]
        assert "\nmodel: opus\n" in head + "\n", path
        assert "\neffort: medium\n" in head + "\n", path
    skill = (ROOT / ".claude" / "skills" / "desk-tick" / "SKILL.md").read_text()
    assert "`subagent_type` `desk-tick`" in skill
    assert "scripts/desk_slot.py" in skill, "the slot check stays in the dispatcher: one script call"


def test_the_setup_command_names_files_that_exist_and_never_prints_a_key():
    text = (ROOT / "commands" / "setup.md").read_text()
    assert text.startswith("---\ndescription:")
    for rel in ("config/public/env.local.example", "scripts/public_status.py",
                "scripts/pathiel-mcp", "scripts/pathiel-py"):
        assert rel in text and (ROOT / rel).exists(), rel
    # Owned by the builder-approval unit: named here, may land in another commit.
    assert "scripts/approve_builder_fee.py" in text
    for needle in ("--bootstrap", "--commands", "chmod 600", "/mcp", "app.hyperliquid-testnet.xyz",
                   "Never print the value of any key"):
        assert needle in text, needle
    # A fresh install is not "mode OFF" (config.mode gates nothing), and setup
    # never runs the maintainer's checks against the live venue.
    assert "mode OFF" not in text
    assert "preflight_secrets.py" not in text and "mcp_smoke.py" not in text
    # The operator's template carries operator notes; the plugin seeds its own.
    assert "${CLAUDE_PLUGIN_ROOT}/.env.local.example" not in text


def test_setup_shows_the_disclosure_before_any_key_and_never_runs_the_sign_line():
    # One line of prose: line breaks and blockquote markers folded to spaces.
    text = re.sub(r"\s*\n\s*(>\s*)?", " ", (ROOT / "commands" / "setup.md").read_text())
    first_key = text.index("HYPERLIQUID_PRIVATE_KEY")
    import contracts.runtime as runtime
    fee_needle = "Fee: **none**" if runtime.BUILDER_FEE_WAIVED else _per_fill()
    for needle in ("testnet", fee_needle, "not financial advice", "US persons", "approve"):
        assert needle in text and text.index(needle) < first_key, needle
    if runtime.BUILDER_FEE_WAIVED:
        assert "nothing to approve and no master key to ask for" in text
        assert "approve-sign" not in text          # setup never hands out a signing line
    else:
        assert "Never run the `approve-sign` command yourself" in text
    assert "never ask for" in text.lower()


def _per_fill() -> str:
    """The disclosure needle, from the one config point (contracts.runtime)."""
    import contracts.runtime as runtime
    return f"{runtime.fee_rate_str(runtime.DEFAULT_BUILDER_FEE_TENTHS_BP)} of notional per fill"


def test_setup_fee_figures_follow_the_default_fee():
    """Every fee figure in setup.md comes from DEFAULT_BUILDER_FEE_TENTHS_BP:
    a change to the default with stale copy fails here, not in front of a
    user who is about to approve."""
    import contracts.runtime as runtime
    fee = runtime.DEFAULT_BUILDER_FEE_TENTHS_BP
    text = re.sub(r"\s*\n\s*(>\s*)?", " ", (ROOT / "commands" / "setup.md").read_text())
    if runtime.BUILDER_FEE_WAIVED:
        # Waived: setup names no rate except the past one of pathiel 0.4.0.
        rates = re.findall(r"([0-9.]+%) (?:of notional per fill|for a round trip)", text)
        assert rates == [], rates
        # Left: the 1% risk figure and the 0% revocation rate.
        others = set(re.findall(r"([0-9.]+%)", text.replace("0.01% fee of pathiel 0.4.0", "")))
        assert others <= {"1%", "0%"}, others
        return
    per_fill = re.findall(r"([0-9.]+%) of notional per fill", text)
    assert per_fill and set(per_fill) == {runtime.fee_rate_str(fee)}, per_fill
    round_trip = re.findall(r"([0-9.]+%) for a round trip", text)
    assert round_trip == [runtime.fee_rate_str(2 * fee)], round_trip
    bps = re.findall(r"\(([0-9.]+) basis points?", text)
    assert bps and all(float(b) == fee / 10 for b in bps), bps


def test_setup_says_to_reconnect_after_the_env_file_changes():
    """The MCP server reads keys and the network once at start, before setup
    writes the env file. Setup must send the user to /mcp to reconnect, and
    must check `state` against the status lines before calling it done."""
    text = re.sub(r"\s+", " ", (ROOT / "commands" / "setup.md").read_text())
    step6 = text[text.index("6. **Connect the tools.**"):text.index("7. **Prove it.**")]
    assert "reconnect pathiel in `/mcp`" in step6 and "restart Claude Code" in step6
    assert "~/.pathiel/.env.local" in step6
    assert step6.index("reconnect") < step6.index("65 tools")
    step7 = text[text.index("7. **Prove it.**"):text.index("8. **Say what is next.**")]
    assert "match the `network` and `trading` lines" in step7
    assert "reconnect pathiel in `/mcp`" in step7


def test_setup_says_the_fee_approval_is_per_network():
    """An approval signed on the testnet default does not cover mainnet;
    without this line a user who switches later pays no fee and never knows."""
    text = re.sub(r"\s+", " ", (ROOT / "commands" / "setup.md").read_text())
    step2 = text[text.index("2. **Choose a mode"):text.index("3. **The env file.**")]
    import contracts.runtime as runtime
    if runtime.BUILDER_FEE_WAIVED:          # nothing to approve on any network
        assert "approve-sign" not in step2
        return
    step5 = text[text.index("5. **Builder fee approval"):text.index("6. **Connect the tools.**")]
    assert "does not carry over" in step2 and "`approve-sign`" in step2
    assert "The approval is per network" in step5
    assert "testnet does not cover mainnet" in step5


def test_setup_copy_has_no_claims_or_banned_style(copy_lint):
    text = (ROOT / "commands" / "setup.md").read_text()
    assert copy_lint.claim_hits(text) == []
    assert copy_lint.style_hits(text) == []


def test_the_public_env_template_carries_no_real_value():
    """/pathiel:setup copies config/public/env.local.example to
    ~/.pathiel/.env.local. Every assignment is empty, mainnet is a comment,
    and the master private key has no line at all."""
    text = (ROOT / "config" / "public" / "env.local.example").read_text()
    assignments = [line for line in text.splitlines() if line and not line.startswith("#")]
    assert assignments == ["HYPERLIQUID_WALLET_ADDRESS=", "HYPERLIQUID_PRIVATE_KEY=",
                           "HYPERLIQUID_MASTER_ADDRESS="]
    assert "HYPERLIQUID_MASTER_PRIVATE_KEY" not in text
    assert "# PATHIEL_NETWORK=testnet" in text and "REAL MONEY" in text and "MAINNET" in text
    assert "Never add your main account's private key" in text
    assert "different API wallets" in text and "app.hyperliquid-testnet.xyz" in text
    assert not re.search(r"0x[0-9a-fA-F]{40}", text), "no address of any kind"
    for line in text.splitlines():
        if "=" in line and not line.lstrip("# ").startswith(("PATHIEL_NETWORK=",)):
            name, _, value = line.lstrip("# ").partition("=")
            if re.fullmatch(r"[A-Z_]+", name.strip()):
                assert value.strip() == "", line


def test_dockerfile_mcp_is_static_sound():
    """Static only: Docker cannot build in the gate. Same pinned base as
    Dockerfile, the public install kind, the launcher as entrypoint, every
    copied source present and inside the build context, no key or operator
    config in it, a non-root user, and every package the server imports."""
    text = (ROOT / "Dockerfile.mcp").read_text()
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
    base = next(line for line in (ROOT / "Dockerfile").read_text().splitlines() if line.startswith("FROM "))
    base_image = base.split()[1]
    assert "@sha256:" in base_image
    assert [line for line in lines if line.startswith("FROM ")] == [f"FROM {base_image}"]

    env_text = " ".join(line for line in lines)
    assert "PATHIEL_INSTALL=public" in env_text and "CLAUDE_PLUGIN_DATA=/opt/pathiel-data" in env_text
    assert "PATHIEL_HOME=/data" in env_text
    assert lines[-1] == 'ENTRYPOINT ["/app/scripts/pathiel-mcp"]'
    assert 'LABEL io.modelcontextprotocol.server.name="io.github.Julian-dev28/pathiel"' in lines
    assert "VOLUME /data" in lines
    users = [line for line in lines if line.startswith("USER ")]
    assert users and users[-1] != "USER root" and users[-1] != "USER 0"
    assert "PATHIEL_HOME=/tmp/pathiel-build /app/scripts/pathiel-mcp --bootstrap" in text
    assert "chmod -R a+rX /opt/pathiel-data /app" in text
    assert "PATHIEL_NETWORK" not in env_text, \
        "the image must not pick a network; testnet is the launcher's default"

    copies = [line.split()[1:-1] for line in lines if line.startswith("COPY ")]
    sources = [src for group in copies for src in group]
    ignored = (ROOT / ".dockerignore").read_text().splitlines()
    for src in sources:
        assert (ROOT / src).exists(), src
        assert not src.startswith((".env", ".agent-config")), src
        assert src.rstrip("/") not in ignored and src not in ignored, f"{src} is in .dockerignore"
    for group in copies:
        dirs = [s for s in group if s.endswith("/")]
        assert len(dirs) <= 1, f"COPY {' '.join(group)} would flatten directories"
    for needed in ("requirements.lock", "pyproject.toml", "pathiel/", "contracts/", "services/",
                   "scripts/", "config/"):
        assert needed in sources, needed
    assert not any(s in (".", "./") for s in sources), "never COPY the whole context"

    # Every top-level package the MCP server imports is in the image.
    server = (ROOT / "scripts" / "pathiel-mcp-server.py").read_text()
    for pkg in set(re.findall(r"^\s*(?:from|import) (pathiel|services|contracts)\b", server, re.M)):
        assert f"{pkg}/" in sources, pkg
    # The keys and the operator's config stay out of the build context.
    for pattern in (".env.local", ".agent-config.json"):
        assert pattern in ignored, pattern


def test_the_env_template_carries_no_real_value():
    """/pathiel:setup copies .env.local.example to ~/.pathiel/.env.local."""
    text = (ROOT / ".env.local.example").read_text()
    for line in text.splitlines():
        if line.startswith(("HYPERLIQUID_PRIVATE_KEY=", "HYPERLIQUID_MASTER_PRIVATE_KEY=")):
            value = line.split("=", 1)[1]
            assert "fake" in value.lower() or value.strip("0x") == "" , line


# ── the real validator, when it is on this machine ───────────────────────────

@pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not on PATH")
def test_claude_plugin_validate_passes():
    proc = subprocess.run(["claude", "plugin", "validate", str(ROOT)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "passed" in (proc.stdout + proc.stderr).lower()


def test_a_due_rewrite_is_forced_through_desk_refresh():
    """2026-11-01: thesis_due said "daily: thesis is 24h+ old" on every silver
    tick for 16 hours, but desk_refresh (without force) only rewrites on
    expiry, a big move or a headline, so it answered no_trigger each time and
    silver traded a 40h-old call. thesis_due is the one gate for the
    operator's rewrite rule; the tick must pass force: true when it says due."""
    for path in (ROOT / ".claude" / "agents" / "desk-tick.md", ROOT / "agents" / "desk-tick.md"):
        text = path.read_text()
        assert "scripts/thesis_due.py --desk DESK" in text, path
        assert "`desk_refresh {desk: DESK, force: true}` only when it says `\"due\": true`" in text, path
        assert "desk_refresh {desk: DESK}`" not in text, f"{path}: an unforced refresh would skip the daily rewrite"


def test_routine_ticks_stay_quiet_in_the_chat():
    """Operator, 2026-11-02: HOLD / SKIP / repeat-NONE ticks crowd the chat.
    Both skill copies tell the session to say nothing for those."""
    for p in (ROOT / ".claude" / "skills" / "desk-tick" / "SKILL.md",
              ROOT / "skills" / "desk-tick" / "SKILL.md"):
        text = p.read_text()
        assert "Say NOTHING in the chat for a HOLD, a\n   SKIP, or a NONE" in text, p
        assert "A HOLD tick is\n   the log line only." not in text, p


def test_desk_loops_window_keeps_the_live_cron_offsets_and_one_watcher():
    """The ticks moved to their own window on 2026-11-02. The loop skill must
    keep the offsets the session used (oil :03, metals :10, 15 min apart) and
    never start a second price watcher."""
    text = (ROOT / ".claude" / "skills" / "desk-loops" / "SKILL.md").read_text()
    assert "`3,18,33,48 * * * *`, prompt `/desk-tick oil`" in text
    assert "`10,25,40,55 * * * *`, prompt `/desk-tick metals`" in text
    assert "pgrep -f scripts/trigger_watch.py" in text
    assert "--model opus" in text  # protect the Fable meter


def test_setup_says_mainnet_is_the_real_money_default_and_offers_testnet():
    """Decision 2026-11-12: a fresh install trades real money by default.
    Setup says so before any key, offers the one testnet line for practice,
    and names the WTI stand-in for Brent there. It never tells the user to
    add a mainnet line (there is none to add)."""
    text = re.sub(r"\s*\n\s*(>\s*)?", " ", (ROOT / "commands" / "setup.md").read_text())
    first_key = text.index("HYPERLIQUID_PRIVATE_KEY")
    for needle in ("**mainnet** by default", "**real money**", "`PATHIEL_NETWORK=testnet`",
                   "WTI (`xyz:CL`)"):
        assert needle in text and text.index(needle) < first_key, needle
    assert "PATHIEL_NETWORK=mainnet" not in text
    assert "starts on the Hyperliquid **testnet**" not in text
    step2 = text[text.index("2. **Choose a mode"):text.index("3. **The env file.**")]
    assert "**Mainnet trading** (the default network)" in step2
    assert "**Testnet trading** (practice)" in step2


def test_the_env_template_offers_the_testnet_line_commented():
    text = (ROOT / "config" / "public" / "env.local.example").read_text()
    assert "# PATHIEL_NETWORK=testnet" in text
    assert "PATHIEL_NETWORK=mainnet" not in text
    assert "MAINNET unless this file says otherwise" in text and "WTI (xyz:CL)" in text


def test_setup_offers_connect_your_wallet_first_and_paste_as_the_fallback():
    """Step 3 (2026-11-13): wallet_connect first, the manual API wallet
    paste second; the script twin ships in the bundle; a replace needs the
    user's yes; testnet reconnects before connecting."""
    text = re.sub(r"\s+", " ", (ROOT / "commands" / "setup.md").read_text())
    step3 = text[text.index("3. **The env file.**"):text.index("4. **Check the install**")]
    connect = step3.index("**Connect your wallet (recommended).**")
    paste = step3.index("**Paste an API wallet (fallback),**")
    assert connect < paste
    assert "`wallet_connect`" in step3 and '{"status": true}' in step3
    assert "scripts/connect_wallet.py" in step3 and "in the background" in step3
    assert "`key_exists`" in step3 and "ask the user before" in step3 and '{"replace": true}' in step3
    assert step3.index("`PATHIEL_NETWORK=testnet`") < connect and "reconnects pathiel in `/mcp`" in step3
    assert "never withdraw" in step3 and "MAINNET in red" in step3
    from importlib.util import module_from_spec, spec_from_file_location
    spec = spec_from_file_location("_bpb_setup", ROOT / "scripts" / "build_plugin_bundle.py")
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert "scripts/connect_wallet.py" in mod.ENTRY_SCRIPTS
