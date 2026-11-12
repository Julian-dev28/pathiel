"""Gate tests for the public-install test kit in tests/conftest.py.

The kit is what five public-plugin units lean on to prove their claims:
public_env (a subprocess env that cannot load the checkout's live .env.local
or reach the venue), copy_lint (claims and style lint plus a one-hop link
closure) and scorecard (the eval table and JSON). A kit that quietly passed
everything would make every one of those proofs worthless, so each piece is
pinned here against planted cases.

Offline, no network: the one subprocess below only reads a temp env file.

    .venv/bin/python -m pytest -q tests/test_public_kit.py
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

KEY_VARS = ("HYPERLIQUID_PRIVATE_KEY", "HYPERLIQUID_MASTER_PRIVATE_KEY",
            "HYPERLIQUID_WALLET_ADDRESS", "HYPERLIQUID_MASTER_ADDRESS")
PROXY_VARS = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
              "https_proxy", "http_proxy", "all_proxy")

# Claims a public page must never make. Each one is a shape that appeared in
# the operator's research docs or that a copywriter would reach for.
PLANTED_CLAIMS = [
    "+51% per month",
    "ROI",
    "The\nedge is the thesis",
    "18.7% a day",
    "+6.03R",
    "$64 ends $487.66",
    "guaranteed returns",
    "risk-free profit",
    "beats the market",
    "makes money",
    "passive income",
    "profitable",
]

# Lines public copy needs to be able to say: disclaimers, rules, sizing, fee.
CLEAN_LINES = [
    "no guaranteed returns",
    "not risk-free",
    "There is no demonstrated edge and no return is promised",
    "refuses any bracket paying under 2R",
    "Sizing any account, $10 to $100k",
    "a 0.01% fee per fill",
]

# The subprocess prints what scripts/_state_env.py loaded, given a root.
_LOAD_ENV_CODE = textwrap.dedent("""
    import json, os, sys
    sys.path.insert(0, os.path.join(sys.argv[1], "scripts"))
    import _state_env
    loaded = _state_env.load_env_local(sys.argv[2])
    keys = %r
    print(json.dumps({
        "loaded": loaded,
        "empty": {k: os.environ.get(k) == "" for k in keys},
        "decoy": os.environ.get("PATHIEL_KIT_DECOY"),
    }))
""") % (KEY_VARS,)


def _run_load_env(env, root: Path) -> dict:
    out = subprocess.run([sys.executable, "-c", _LOAD_ENV_CODE, str(ROOT), str(root)],
                         env=env, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


# ── public_env ───────────────────────────────────────────────────────────────

def test_public_env_pins_empty_keys_and_an_existing_env_file(public_env, tmp_path):
    env = public_env()
    env_file = public_env.env_file
    assert env_file.is_file()
    assert env["PATHIEL_ENV_FILE"] == str(env_file)
    assert env["PATHIEL_INSTALL"] == "public"
    assert "testnet" in env["PATHIEL_STATE_DIR"]
    assert "PATHIEL_NETWORK" not in env
    for key in KEY_VARS + ("PATHIEL_BUILDER_ADDRESS",):
        assert key in env and env[key] == "", key
    for key in ("PATHIEL_DESK_BACKGROUND_REFRESH", "PATHIEL_DESK_WARMUP",
                "PATHIEL_META_PREWARM", "PATHIEL_GRADE_LOOP",
                "PATHIEL_DASHBOARD_LIVE_EQUITY"):
        assert env[key] == "0", key
    # Every path the env names sits under this test's tmp dir, never HOME or
    # the checkout.
    for key in ("HOME", "PATHIEL_HOME", "PATHIEL_ENV_FILE", "PATHIEL_STATE_DIR",
                "PATHIEL_AGENT_CONFIG_FILE", "PATHIEL_UNIVERSE_CACHE_DIR",
                "PATHIEL_AGENT_MEMORY_FILE", "PATHIEL_DSL_STATE_FILE",
                "SESSION_LOG_PATH"):
        assert Path(env[key]).is_relative_to(tmp_path), key
    # Nothing leaks from the pytest process, which the root conftest filled
    # from the checkout's .env.local.
    assert set(env) - {"PATH"} == set(public_env()) - {"PATH"}
    assert not [k for k in env if k.startswith("PYTHON")]

    # A decoy checkout whose .env.local sets a variable: the fixture's env
    # file wins, so the decoy is never read.
    decoy = tmp_path / "decoy-checkout"
    decoy.mkdir()
    (decoy / ".env.local").write_text(
        "PATHIEL_KIT_DECOY=1\nHYPERLIQUID_PRIVATE_KEY=0xdecoy\n", encoding="utf-8")
    seen = _run_load_env(env, decoy)
    assert seen["loaded"] == str(env_file)
    assert seen["decoy"] is None
    assert seen["empty"] == {k: True for k in KEY_VARS}


def test_public_env_overrides_and_removal(public_env):
    env = public_env(PATHIEL_NETWORK="mainnet", PATHIEL_INSTALL=None,
                     HYPERLIQUID_WALLET_ADDRESS="0xabc", PATHIEL_GRADE_LOOP=1)
    assert env["PATHIEL_NETWORK"] == "mainnet"
    assert "PATHIEL_INSTALL" not in env
    assert env["HYPERLIQUID_WALLET_ADDRESS"] == "0xabc"
    assert env["PATHIEL_GRADE_LOOP"] == "1"
    # Overrides do not stick to the next call.
    again = public_env()
    assert "PATHIEL_NETWORK" not in again
    assert again["PATHIEL_INSTALL"] == "public"
    assert again["HYPERLIQUID_WALLET_ADDRESS"] == ""
    # The env file may be appended to and the next subprocess sees it.
    with public_env.env_file.open("a", encoding="utf-8") as fh:
        fh.write("PATHIEL_KIT_DECOY=from-env-file\n")
    seen = _run_load_env(public_env(), ROOT)
    assert seen["loaded"] == str(public_env.env_file)
    assert seen["decoy"] == "from-env-file"


def test_public_env_has_dead_proxies(public_env, monkeypatch):
    env = public_env()
    for key in PROXY_VARS:
        assert env[key] == "http://127.0.0.1:9", key
    assert env["NO_PROXY"] == "" and env["no_proxy"] == ""
    # What requests would do with that env, resolved without opening a socket.
    import requests.utils
    for key in list(PROXY_VARS) + ["NO_PROXY", "no_proxy"]:
        monkeypatch.setenv(key, env[key])
    for host in ("api.hyperliquid.xyz", "api.hyperliquid-testnet.xyz"):
        proxies = requests.utils.get_environ_proxies(f"https://{host}/info")
        assert proxies.get("https") == "http://127.0.0.1:9", (host, proxies)


# ── copy_lint: claims ────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", PLANTED_CLAIMS)
def test_claims_lint_catches_planted_claims(copy_lint, text):
    assert copy_lint.claim_hits(text), f"claim not caught: {text!r}"


@pytest.mark.parametrize("text", CLEAN_LINES)
def test_claims_lint_allows_disclaimers_and_rules(copy_lint, text):
    assert copy_lint.claim_hits(text) == []


def test_wrapped_lines_are_matched(copy_lint):
    wrapped = "Over the test window the account\n  grew\n18.7%   a\nday, and\nthe\n\tedge is clear."
    hits = copy_lint.claim_hits(wrapped)
    assert len(hits) == 2, hits
    assert all(" :: " in h for h in hits)
    assert copy_lint.normalize("a\n\n b\t c") == "a b c"
    # The negation window looks back 30 characters, no further.
    far = "no " + "x" * 40 + " guaranteed returns"
    assert copy_lint.claim_hits(far)


def test_style_hits_em_dash_and_banned_words(copy_lint):
    assert copy_lint.style_hits("one — two")
    assert copy_lint.style_hits("We Delve into it")
    assert copy_lint.style_hits("a robust\nsetup")
    assert copy_lint.style_hits("plain words, an en dash – and a hyphen - only") == []
    # Whole words only: 'fundamentals' is not 'fundamental'.
    assert copy_lint.style_hits("the fundamentals of oil") == []


def test_banned_words_equal_the_getting_started_list(copy_lint):
    src = ROOT / "tests" / "test_getting_started_doc.py"
    spec = importlib.util.spec_from_file_location("_kit_check_gs_words", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert list(copy_lint.BANNED_WORDS) == list(mod.BANNED_WORDS)
    assert len(copy_lint.BANNED_WORDS) >= 19


# ── copy_lint: links ─────────────────────────────────────────────────────────

@pytest.fixture
def doc_repo(tmp_path, copy_lint):
    """A throwaway repo tree and a lint rooted at it, so the link tests never
    write into the checkout. Returns (lint, repo root)."""
    repo = tmp_path / "repo"
    docs = repo / "docs"
    (docs / "sub").mkdir(parents=True)
    (docs / "seed.md").write_text(textwrap.dedent("""\
        # Seed
        A [markdown link](docs/a.md#section) and a backticked path `docs/b.md`.
        A folder-relative link [here](sub/c.md) and a URL https://example.com/docs/x.md.
        A missing one: [gone](sub/missing.md). Outside the repo: [out](../../outside.md).
        """), encoding="utf-8")
    (docs / "a.md").write_text("# A\n", encoding="utf-8")
    (docs / "b.md").write_text("# B\n", encoding="utf-8")
    (docs / "sub" / "c.md").write_text("# C, links [d](d.md)\n", encoding="utf-8")
    (docs / "sub" / "d.md").write_text("# D\n", encoding="utf-8")
    (docs / "x.md").write_text("# only named inside a URL\n", encoding="utf-8")
    (tmp_path / "outside.md").write_text("# outside the repo\n", encoding="utf-8")
    lint = type(copy_lint)(repo, list(copy_lint.BANNED_WORDS))
    return lint, repo.resolve()


def test_linked_docs_three_link_forms(doc_repo):
    lint, repo = doc_repo
    got = lint.linked_docs(repo / "docs" / "seed.md")
    assert got == {repo / "docs" / "a.md", repo / "docs" / "b.md",
                   repo / "docs" / "sub" / "c.md"}


def test_reach_is_one_hop(doc_repo):
    lint, repo = doc_repo
    seed = repo / "docs" / "seed.md"
    json_seed = repo / "server.json"
    json_seed.write_text('{"readme": "docs/a.md"}', encoding="utf-8")
    got = lint.reach([seed, json_seed, repo / "nope.md"])
    assert got == sorted({seed, json_seed, repo / "docs" / "a.md",
                          repo / "docs" / "b.md", repo / "docs" / "sub" / "c.md"})
    # c.md links d.md; one hop means d.md is not reached from seed.md.
    assert repo / "docs" / "sub" / "d.md" not in got
    # Relative seeds resolve against the repo root.
    assert lint.reach(["docs/seed.md"]) == lint.reach([seed])
    # The real repo: README.md reaches at least itself.
    assert (ROOT / "README.md").resolve() in type(lint)(ROOT, []).reach(["README.md"])


# ── scorecard ────────────────────────────────────────────────────────────────

def test_scorecard_shape_and_json_file(scorecard, tmp_path, monkeypatch, capsys):
    rows = [{"check": "a", "got": 1, "want": 1, "ok": True},
            {"check": "b", "got": Path("/x"), "want": "y", "ok": False}]

    monkeypatch.delenv("PATHIEL_EVAL_OUT", raising=False)
    card = scorecard("kit_probe", rows)
    assert card == {"name": "kit_probe", "passed": 1, "total": 2, "rows": rows}
    assert list(tmp_path.iterdir()) == []
    printed = capsys.readouterr().out
    assert "kit_probe: 1/2" in printed and "PASS" in printed and "FAIL" in printed

    out_dir = tmp_path / "evals"
    monkeypatch.setenv("PATHIEL_EVAL_OUT", str(out_dir))
    scorecard("kit_probe", rows)
    data = json.loads((out_dir / "kit_probe.json").read_text(encoding="utf-8"))
    assert data["passed"] == 1 and data["total"] == 2 and data["name"] == "kit_probe"
    assert data["rows"][1]["got"] == "/x"

    with pytest.raises(ValueError):
        scorecard("kit_probe", [{"check": "no ok key", "got": 1, "want": 1}])
