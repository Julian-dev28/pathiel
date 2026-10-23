"""Periodic eval: drive the real MCP server over stdio against live Hyperliquid.

Read-only. Spawns scripts/pathiel-mcp-server.py, performs the handshake, and
calls every tool that does not place, cancel, close or reconfigure anything,
with arguments built from each tool's own schema. Network-dependent, so it is
`online` (deselected by default) and graded on a pass rate, not all-or-nothing:

    pytest -m online tests/test_mcp_online_eval.py -s

Outcome measured: the share of advertised read-only tools that return a
non-error payload end to end. Before the 2026-10-14 hardening it was 78/82
with four more silently returning hard-coded empty data; the threshold below
is the floor that work established. The per-tool table is printed and written
to /tmp/pathiel-mcp-eval/latest.json as the trace.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.online

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "scripts" / "pathiel-mcp-server.py"
PASS_THRESHOLD = 0.97

# Tools that act on the account. Never exercised here.
# desk_submit_call joins this group 2026-10-17: it writes a stored Thesis to
# a desk's journal, a non-idempotent-write reason, not a live-money one.
# place_bracket/bracket_sweep join 2026-10-23, replacing the retired
# scan/research/submit_verdict/execute bot brain path.
MUTATING = {"place_bracket", "bracket_sweep", "close_position", "set_leverage",
            "cancel_order", "config", "desk_submit_call"}

ZERO = "0x0000000000000000000000000000000000000000"
ARGS = {
    "coin": "BTC", "asset": "BTC", "address": ZERO, "trader_id": ZERO,
    "trader_addresses": [ZERO], "oid": 1,
    "cloid": "0x00000000000000000000000000000001",
    # HLP, a vault that has existed since launch.
    "vaultAddress": "0xdfc24b077bc1425ad1dea75bcb6f8158e10df303",
    "leverage": 5, "is_long": True, "size": 0.01,
    # Real desk id: contracts/strategies/fixtures/oil_*.json is built around it.
    "desk": "oil",
}
# A missing record is a correct answer for these probes, not a tool failure.
EXPECTED_ERRORS = {
    "get_liquidation_price": "no open",
    # tests/conftest.py points PATHIEL_STATE_DIR at a temp dir, which the
    # child inherits: no auth DB, so nobody has signed in. Correct answer.
    "wallet_account": "no wallet has signed in",
    # services/strategies (desk-core) is a separate, parallel-owned service.
    # It does not exist in every worktree/deploy yet; a clear "unavailable"
    # error is the correct answer here, not a crash. Once desk-core ships,
    # these start getting real data and this becomes a real end-to-end probe.
    "desk_list": "facade unavailable",
    "desk_brief": "facade unavailable",
    "desk_refresh": "facade unavailable",
    "desk_track_record": "facade unavailable",
}


class Server:
    def __init__(self):
        self.p = subprocess.Popen(
            [sys.executable, str(SERVER)], cwd=ROOT, text=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env={**os.environ, "PATHIEL_MCP_DISABLE_SAMPLING": "1"})
        self.seq = 0

    def call(self, method, params=None):
        self.seq += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.seq,
                                       "method": method, "params": params or {}}) + "\n")
        self.p.stdin.flush()
        line = self.p.stdout.readline()
        assert line, f"server closed stdout during {method}"
        msg = json.loads(line)  # a non-JSON line here means stdout was polluted
        assert msg["id"] == self.seq
        return msg

    def close(self):
        self.p.stdin.close()
        self.p.wait(timeout=10)


@pytest.fixture(scope="module")
def server():
    s = Server()
    init = s.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
    assert init["result"]["protocolVersion"] == "2025-06-18"
    yield s
    s.close()


def test_every_read_only_tool_answers(server):
    assert server.call("ping")["result"] == {}
    tools = server.call("tools/list")["result"]["tools"]
    rows = []
    for t in tools:
        if t["name"] in MUTATING:
            continue
        args = {k: ARGS[k] for k in t["inputSchema"].get("required", []) if k in ARGS}
        t0 = time.time()
        res = server.call("tools/call", {"name": t["name"], "arguments": args})["result"]
        text = res["content"][0]["text"]
        ok = not res["isError"]
        if not ok and EXPECTED_ERRORS.get(t["name"], "\0") in text:
            ok = True
        rows.append({"tool": t["name"], "ok": ok, "secs": round(time.time() - t0, 2),
                     "bytes": len(text), "head": "" if ok else text[:160]})

    passed = sum(r["ok"] for r in rows)
    rate = passed / len(rows)
    out = Path("/tmp/pathiel-mcp-eval")
    out.mkdir(parents=True, exist_ok=True)
    (out / "latest.json").write_text(json.dumps(
        {"at": int(time.time()), "pass_rate": rate, "passed": passed,
         "total": len(rows), "rows": rows}, indent=2))
    print(f"\nMCP online eval: {passed}/{len(rows)} = {rate:.1%} "
          f"(threshold {PASS_THRESHOLD:.0%}); trace /tmp/pathiel-mcp-eval/latest.json")
    for r in rows:
        if not r["ok"]:
            print(f"  FAIL {r['tool']}: {r['head']}")
    assert rate >= PASS_THRESHOLD
