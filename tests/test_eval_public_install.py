"""Eval: what a fresh public install trades with, and that the operator's does
not move.

Outcome measured: a stranger who installs the plugin lands on mainnet (real
money, the 2026-11-12 decision; testnet is one line away and gets its own
state dir) with the shipped builder address and a 3x, 1% a trade,
one-position profile seeded from the tracked template; every
trade that profile sizes stays inside its caps across a grid of account sizes,
stop widths and venue size steps; the operator's 20x config resolves as
before and its file is untouched; and the builder fee approval command
/pathiel:setup hands the user runs from a plain terminal on the same venv.

Four parts, one scorecard (threshold: every row passes):

  1. fresh HOME through the launcher functions plus seed-public, then the
     same HOME with the PATHIEL_NETWORK=testnet line
  2. size_trade grid: equity {25 .. 100k} x stop {0.5, 1, 2, 5}% x size step
     {none, 5, 2, 0 decimals} x requested leverage {none, 20x}
  3. operator scenario
  4. the printed approve-dry command with the plugin variables and
     PATHIEL_PYTHON unset and a too-old python3 first on PATH

Offline. Every subprocess gets env=public_env(...). The launcher never builds
a venv here: the fake data dir's venv/bin/python is a stub (part 4) or a
two-line shell that execs this interpreter (part 1).

    .venv/bin/python -m pytest -q -s tests/test_eval_public_install.py

Writes $PATHIEL_EVAL_OUT/public_install.json when that variable is set.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List

NAME = "public_install"
SHIPPED_BUILDER = "0x2c2e4efF574B3Fd9879AdeF9c5CAC6fD7C916985"   # contracts/runtime default
ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ENV = ROOT / "scripts" / "_plugin_env.sh"
MCP_WRAPPER = ROOT / "scripts" / "pathiel-mcp"
TEMPLATE = ROOT / "config" / "public" / "agent-config.json"
REPO_CONFIG = ROOT / ".agent-config.json"
LAUNCHER_VARS = ("PATHIEL_INSTALL", "PATHIEL_HOME", "PATHIEL_ENV_FILE", "PATHIEL_STATE_DIR",
                 "PATHIEL_AGENT_CONFIG_FILE", "PATHIEL_UNIVERSE_CACHE_DIR", "PATHIEL_NETWORK",
                 "CLAUDE_PLUGIN_DATA", "CLAUDE_PLUGIN_ROOT")

EQUITIES = [25, 50, 100, 500, 1_000, 10_000, 100_000]
STOPS_PCT = [0.5, 1.0, 2.0, 5.0]
# (sz_decimals, entry price): a realistic coin for each size step.
SIZE_STEPS = [(None, 100.0), (5, 90_000.0), (2, 65.0), (0, 0.15)]
REQUESTED_LEVERAGE = [None, 20.0]
COIN_MAX_LEVERAGE = 20.0

OPERATOR_PROFILE = {"preset": "auto", "risk_per_trade_pct": 100, "daily_loss_limit_pct": 100,
                    "max_leverage": 20, "max_open_positions": 1, "allow_min_order_bump": True,
                    "max_bumped_risk_pct": 100}


def _row(check: str, got: Any, want: Any) -> Dict[str, Any]:
    return {"check": check, "got": got, "want": want, "ok": got == want}


def _fresh(make: Callable[..., Dict[str, str]], **overrides) -> Dict[str, str]:
    unset: Dict[str, Any] = {k: None for k in LAUNCHER_VARS}
    unset.update(overrides)
    return make(**unset)


def _venv(base: Path, body: str) -> Path:
    venv = base / "venv"
    (venv / "bin").mkdir(parents=True)
    py = venv / "bin" / "python"
    py.write_text("#!/bin/sh\n" + body)
    py.chmod(py.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    want = hashlib.sha256((ROOT / "requirements.lock").read_bytes()).hexdigest()
    (venv / ".requirements.sha256").write_text(want + "\n")
    return base


# ── 1. fresh install ──────────────────────────────────────────────────────────

def part_fresh_install(make, tmp: Path) -> List[Dict[str, Any]]:
    from services.risk.presets import PUBLIC_DEFAULT_PROFILE

    data = _venv(tmp / "data1", f'exec {shlex.quote(sys.executable)} "$@"\n')
    home = tmp / "fresh-home"
    env = _fresh(make, HOME=str(home), CLAUDE_PLUGIN_DATA=str(data))
    out = tmp / "fresh.json"
    code = ("import json, sys; from pathiel.agents.config_store import CONFIG_PATH, read_risk_profile; "
            "import contracts.runtime as r; from services.strategies import desks; "
            "json.dump({'config_path': CONFIG_PATH, 'network': r.network(), 'install': r.install_kind(), "
            "'network_explicit': r.network_is_explicit(), 'builder': r.builder_address(), "
            "'profile': read_risk_profile().model_dump(), "
            "'ladders': {s.id: list(s.leverage_ladder) for s in desks.list_specs()}}, "
            "open(sys.argv[1], 'w'))")
    script = (f"set -euo pipefail\n. {shlex.quote(str(PLUGIN_ENV))}\n"
              "pathiel_paths\npathiel_resolve_network\npathiel_state_paths\npathiel_seed_config\n"
              f"cd {shlex.quote(str(ROOT))}\n"
              f"{shlex.quote(sys.executable)} -c {shlex.quote(code)} {shlex.quote(str(out))}\n"
              f"printf '%s' \"$PATHIEL_STATE_DIR\" > {shlex.quote(str(tmp / 'fresh.state'))}\n")
    proc = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        return [_row("fresh: launcher functions ran", proc.stderr[-300:], "exit 0")]
    got = json.loads(out.read_text())
    config = home / ".pathiel" / ".agent-config.json"
    fresh_state = Path((tmp / "fresh.state").read_text()).name

    # The same HOME after the user adds the one testnet line.
    (home / ".pathiel" / ".env.local").write_text("PATHIEL_NETWORK=testnet\n")
    proc_t = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=60)
    got_t = json.loads(out.read_text()) if proc_t.returncode == 0 else {}
    testnet_state = Path((tmp / "fresh.state").read_text()).name if proc_t.returncode == 0 else ""
    return [
        _row("fresh: launcher functions ran", "exit 0", "exit 0"),
        _row("fresh: stdout clean", proc.stdout, ""),
        _row("fresh: install", got["install"], "public"),
        _row("fresh: network (default, real money)", got["network"], "mainnet"),
        _row("fresh: network not marked as chosen", got["network_explicit"], False),
        _row("fresh: state dir", fresh_state, "state"),
        _row("fresh: builder is the shipped address", got["builder"], SHIPPED_BUILDER.lower()),
        _row("testnet line: network", got_t.get("network"), "testnet"),
        _row("testnet line: network marked as chosen", got_t.get("network_explicit"), True),
        _row("testnet line: state dir", testnet_state, "state-testnet"),
        _row("fresh: config path is ~/.pathiel/.agent-config.json", got["config_path"] == str(config), True),
        _row("fresh: config equals template",
             json.loads(config.read_text()) == json.loads(TEMPLATE.read_text()), True),
        _row("fresh: config mode 0600", oct(stat.S_IMODE(config.stat().st_mode)), "0o600"),
        _row("fresh: profile", got["profile"] == PUBLIC_DEFAULT_PROFILE.model_dump(), True),
        # final review: the thesis writer and the brief offer no rung the profile refuses
        _row("fresh: desk ladders within the profile cap",
             sorted(d for d, lad in got["ladders"].items() if max(lad) > PUBLIC_DEFAULT_PROFILE.max_leverage),
             []),
        _row("fresh: oil ladder", got["ladders"].get("oil"), [2.0, 3.0]),
    ]


# ── 2. sizing grid ────────────────────────────────────────────────────────────

def part_grid() -> List[Dict[str, Any]]:
    from pathiel.client.exchange import MIN_ORDER_USD
    from services.risk.books import all_book_specs
    from services.risk.derive import VenueRules
    from services.risk.presets import public_default_profile
    from services.risk.size import size_trade

    template = json.loads(TEMPLATE.read_text())
    books = all_book_specs(template)
    venue = VenueRules(min_order_notional_usd=MIN_ORDER_USD)
    total = traded = refused_with_reason = within = bumped = 0
    worst: List[str] = []
    for equity in EQUITIES:
        for stop_pct in STOPS_PCT:
            for decimals, entry in SIZE_STEPS:
                for lev in REQUESTED_LEVERAGE:
                    total += 1
                    stop = entry * (1 - stop_pct / 100.0)
                    t = size_trade(equity, public_default_profile(), books, entry_px=entry,
                                   stop_px=stop, leverage=lev, coin_max_leverage=COIN_MAX_LEVERAGE,
                                   venue=venue, sz_decimals=decimals)
                    if not t.can_trade:
                        refused_with_reason += bool(t.reason)
                        within += bool(t.reason)
                        continue
                    traded += 1
                    bumped += bool(t.bumped_to_min)
                    risk_pct = t.size * entry * (stop_pct / 100.0) / equity * 100.0
                    cap = 2.0 if t.bumped_to_min else 1.0
                    tol = (10.0 ** -decimals) * entry * (stop_pct / 100.0) / equity * 100.0 \
                        if decimals is not None else 0.0
                    ok = (t.leverage is not None and t.leverage <= 3.0 + 1e-9
                          and risk_pct <= cap + tol + 1e-9)
                    within += ok
                    if not ok:
                        worst.append(f"eq={equity} stop={stop_pct}% dec={decimals} lev={lev}: "
                                     f"{t.leverage}x risk {risk_pct:.3f}% cap {cap}+{tol:.3f}")
    return [
        _row("grid: rows within caps (lev <= 3x, risk <= cap)", f"{within}/{total}", f"{total}/{total}"),
        _row("grid: refusals carry a reason", refused_with_reason, total - traded),
        _row("grid: some rows trade", traded > 0, True),
        _row("grid: the 2% bump branch is exercised", bumped > 0, True),
        _row("grid: violations", worst[:3], []),
    ]


# ── 3. operator scenario ──────────────────────────────────────────────────────

def part_operator(make, tmp: Path) -> List[Dict[str, Any]]:
    from contracts.risk.models import RiskProfile
    from pathiel.agents import config_store as cs
    from services.risk.derive import VenueRules
    from services.risk.size import size_trade

    saved = os.environ.pop("PATHIEL_INSTALL", None)
    try:
        operator_profile = cs.read_risk_profile({"risk_profile": OPERATOR_PROFILE})
        tracked = json.loads(REPO_CONFIG.read_text())
        tracked_lev = cs.read_risk_profile(tracked).max_leverage
        bare = cs.read_risk_profile({})
    finally:
        if saved is not None:
            os.environ["PATHIEL_INSTALL"] = saved
    sized = size_trade(1_000, operator_profile, [], entry_px=100.0, stop_px=99.0, leverage=20.0,
                       coin_max_leverage=20.0, venue=VenueRules(min_order_notional_usd=10.5))

    probe = ("from pathiel.agents.config_store import CONFIG_PATH; print(CONFIG_PATH)")
    op_env = make(PATHIEL_INSTALL=None, PATHIEL_AGENT_CONFIG_FILE=None)
    proc = subprocess.run([sys.executable, "-c", probe], env=op_env, cwd=str(ROOT),
                          capture_output=True, text=True, timeout=60)
    return [
        _row("operator: operator-shaped profile max leverage", operator_profile.max_leverage, 20.0),
        _row("operator: tracked config max leverage", tracked_lev,
             float(tracked["risk_profile"]["max_leverage"])),
        _row("operator: no profile stays auto", bare == RiskProfile(), True),
        _row("operator: sizes past 3x when asked", (sized.leverage or 0) > 3.0, True),
        _row("operator: config path is the repo file", proc.stdout.strip() == str(REPO_CONFIG), True),
    ]


# ── 4. the printed approval command ───────────────────────────────────────────

def part_approve_command(make, tmp: Path) -> List[Dict[str, Any]]:
    log = tmp / "calls.log"
    # `-c` calls are pathiel_build_venv's version check and lock hash: the
    # printed line pins PATHIEL_PYTHON to this stub, so they run on the real
    # interpreter, unlogged.
    data = _venv(tmp / "plugin data",
                 f'if [ "${{1:-}}" = "-c" ]; then exec {shlex.quote(sys.executable)} "$@"; fi\n'
                 "{ printf 'CALL VENV=%s\\n' \"$PATHIEL_VENV\"; "
                 "for a in \"$@\"; do printf 'ARG=%s\\n' \"$a\"; done; } >> "
                 + shlex.quote(str(log)) + "\n")
    env = _fresh(make, HOME=str(tmp / "a home"), CLAUDE_PLUGIN_DATA=str(data),
                 CLAUDE_PLUGIN_ROOT=str(ROOT), PATHIEL_PYTHON=sys.executable)
    proc = subprocess.run([str(MCP_WRAPPER), "--commands"], env=env, capture_output=True,
                          text=True, timeout=60)
    lines = dict(line.split("\t", 1) for line in proc.stdout.splitlines() if "\t" in line)
    dry = lines.get("approve-dry", "")
    # A plain terminal: no plugin variables, no PATHIEL_PYTHON, and a python3
    # first on PATH that is too old (macOS /usr/bin/python3 is 3.9).
    old_bin = tmp / "old-python-bin"
    old_bin.mkdir()
    old_py = old_bin / "python3"
    old_py.write_text('#!/bin/sh\n[ "${1:-}" = "--version" ] && { echo "Python 3.9.6"; exit 0; }\nexit 1\n')
    old_py.chmod(old_py.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    plain = _fresh(make, HOME=str(tmp / "a home"), PATHIEL_PYTHON=None,
                   PATH=f"{old_bin}:{make()['PATH']}")
    log.write_text("")
    ran = subprocess.run(["bash", "-c", dry], env=plain, capture_output=True, text=True,
                         timeout=60, cwd=str(tmp)) if dry else None
    calls = log.read_text().split("CALL ")[1:]
    approve = [c for c in calls if "approve_builder_fee.py" in c]
    return [
        _row("approve: --commands exit", proc.returncode, 0),
        _row("approve: three lines", sorted(lines), ["approve-dry", "approve-sign", "status"]),
        _row("approve: sign line is dry plus --yes", bool(dry) and lines.get("approve-sign") == dry + " --yes",
             True),
        _row("approve: dry line pins the venv python",
             f"PATHIEL_PYTHON={data / 'venv' / 'bin' / 'python'}" in shlex.split(dry), True),
        _row("approve: dry line runs without plugin vars, old python3 on PATH",
             ran.returncode if ran else None, 0),
        _row("approve: runs approve_builder_fee.py once", len(approve), 1),
        _row("approve: same venv",
             bool(approve) and approve[0].splitlines()[0] == f"VENV={data / 'venv'}", True),
        _row("approve: no second venv", (tmp / "a home" / ".pathiel" / "data").exists(), False),
    ]


def run(make: Callable[..., Dict[str, str]], tmp: Path) -> List[Dict[str, Any]]:
    before = (REPO_CONFIG.read_bytes(), REPO_CONFIG.stat().st_mtime_ns)
    rows = (part_fresh_install(make, tmp) + part_grid() + part_operator(make, tmp)
            + part_approve_command(make, tmp))
    after = (REPO_CONFIG.read_bytes(), REPO_CONFIG.stat().st_mtime_ns)
    rows.append(_row("operator: repo .agent-config.json unchanged", after == before, True))
    return rows


def test_public_install_eval(public_env, tmp_path, scorecard):
    card = scorecard(NAME, run(public_env, tmp_path))
    assert card["total"] == 16 + 5 + 5 + 8 + 1
    assert card["passed"] == card["total"], [r for r in card["rows"] if not r["ok"]]
