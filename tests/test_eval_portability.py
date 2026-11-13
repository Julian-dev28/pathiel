"""Eval: is what a public plugin user gets free of the operator's machine,
wallet, name, profile and return figures, and does every venue read follow
the resolved network?

Offline scorecard. Each row is one measured count against its target:

  machine-bound matches in SHIPPED                       0
  hardcoded venue URLs outside ALLOWED                   0
  operator-number matches in generated tick files        0
  research-doc references in generated tick files        0
  operator persona hits in the public thesis prompt      0
  claim hits in the shipped reach set                    0
  treasury refusals off the operator mainnet path        2/2  (real subprocess runs)
  resolved-URL agreement, venue.py + oil_levels.py       4/4  (mainnet, testnet each)

The two treasury runs are `scripts/treasury.py status` in a subprocess with
the pinned public env (empty keys, dead proxies): a public install, and an
operator install with PATHIEL_NETWORK=testnet. Both must exit 2 and print the
refusal. A guard that stopped working would reach for the venue and hit the
dead proxy, so nothing leaves the box either way.

Run: .venv/bin/python -m pytest -q -s tests/test_eval_portability.py
Threshold: every row passes (all counts are deterministic).
"""
from __future__ import annotations

import importlib
import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
REFUSAL = "treasury.py is a maintainer tool for mainnet only"
URLS = {"mainnet": "https://api.hyperliquid.xyz/info",
        "testnet": "https://api.hyperliquid-testnet.xyz/info"}


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _treasury_refusals(public_env) -> int:
    cases = (
        public_env(PATHIEL_INSTALL="public"),
        public_env(PATHIEL_INSTALL=None, PATHIEL_NETWORK="testnet"),
    )
    refused = 0
    for env in cases:
        proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "treasury.py"), "status"],
                              cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
        if proc.returncode == 2 and REFUSAL in proc.stdout and "nothing was sent" in proc.stdout:
            refused += 1
    return refused


def _venue_url(monkeypatch, network: str) -> str:
    from services.intel import http_client
    from services.intel.sources import venue
    seen = []
    monkeypatch.setattr(http_client, "fetch_json_post",
                        lambda url, body, **kw: (seen.append(url), ([{"universe": []}, []], None))[1])
    monkeypatch.setenv("PATHIEL_NETWORK", network)
    try:
        mod = importlib.reload(venue)
        mod.fetch_meta_and_ctxs("xyz")
    finally:
        monkeypatch.delenv("PATHIEL_NETWORK", raising=False)
        importlib.reload(venue)
    return seen[0] if len(seen) == 1 else ""


def _oil_levels_url(monkeypatch, network: str) -> str:
    monkeypatch.setenv("PATHIEL_NETWORK", network)
    try:
        mod = _load(ROOT / "scripts" / "oil_levels.py", "oil_levels_eval_probe")
    finally:
        monkeypatch.delenv("PATHIEL_NETWORK", raising=False)
    seen = []

    class _Resp:
        def read(self):
            return b"[]"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(mod.urllib.request, "urlopen",
                        lambda req, **kw: (seen.append(req.full_url), _Resp())[1])
    mod._post({"type": "allMids"})
    return seen[0] if len(seen) == 1 else ""


def run(monkeypatch, public_env, copy_lint) -> list:
    port = _load(HERE / "test_public_portability.py", "public_portability_eval_probe")
    urls = _load(HERE / "test_network_url_allowlist.py", "network_url_allowlist_eval_probe")
    for key in ("PATHIEL_INSTALL", "PATHIEL_NETWORK"):
        monkeypatch.delenv(key, raising=False)

    machine = len(port.machine_bound_hits(port.shipped_files()))
    venue_urls = len(urls.offenders(urls.candidate_files()))
    numbers = len(port.pattern_hits(port.GENERATED, port.OPERATOR_NUMBERS))
    research = len(port.pattern_hits(port.GENERATED, port.RESEARCH_DOCS))
    persona = len(port.persona_hits(port.public_thesis_prompt(monkeypatch)))
    monkeypatch.delenv("PATHIEL_INSTALL", raising=False)
    claims_by_file, _reach = port.reach_claim_hits(copy_lint)
    claims = sum(len(v) for v in claims_by_file.values())
    refusals = _treasury_refusals(public_env)
    agree = sum(1 for net in ("mainnet", "testnet")
                for got in (_venue_url(monkeypatch, net), _oil_levels_url(monkeypatch, net))
                if got == URLS[net])

    def row(check, got, want):
        return {"check": check, "got": got, "want": want, "ok": got == want}

    return [
        row("machine-bound matches in SHIPPED", machine, 0),
        row("hardcoded venue URLs outside ALLOWED", venue_urls, 0),
        row("operator numbers in generated tick files", numbers, 0),
        row("research-doc refs in generated tick files", research, 0),
        row("operator persona hits, public thesis prompt", persona, 0),
        row("claim hits in shipped reach set", claims, 0),
        row("treasury refusals off operator mainnet", f"{refusals}/2", "2/2"),
        row("resolved-URL agreement venue+oil_levels", f"{agree}/4", "4/4"),
    ]


def test_eval_portability(monkeypatch, public_env, copy_lint, scorecard):
    card = scorecard("portability", run(monkeypatch, public_env, copy_lint))
    failed = [r for r in card["rows"] if not r["ok"]]
    assert not failed, failed
    assert card["passed"] == card["total"] == 8
