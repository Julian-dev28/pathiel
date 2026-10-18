#!/usr/bin/env python3
"""Live smoke eval for scripts/pathiel-mcp-server.py over its real stdio transport.

    .venv/bin/python scripts/mcp_smoke.py              # every read-only tool, live HL
    .venv/bin/python scripts/mcp_smoke.py --only desk_brief,state
    .venv/bin/python scripts/mcp_smoke.py --json /tmp/pathiel-mcp-smoke/report.json

What it proves, per tool: the call answers (no hang), isError matches what the
case expects, the latency is inside the budget, and the payload never contains
a configured private key (checked by value against env + .env.local, never
printed). It also fails when tools/list advertises a tool that has no case and
no skip reason here, so a new tool cannot ship without being smoked.

Never calls a tool that trades, writes the journal, or spends LLM tokens: those
are listed in SKIPPED with the reason. Read-only Hyperliquid /info calls only.

Exit 0 = all green, 1 = at least one failure (table says which).
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parent.parent
SERVER = REPO / "scripts" / "pathiel-mcp-server.py"
OIL = "xyz:CL"
HLP_VAULT = "0xdfc24b077bc1425ad1dea75bcb6f8158e10df303"

# Never called by the smoke: live orders, journal writes, billable LLM calls.
SKIPPED: Dict[str, str] = {
    "execute": "places a live order",
    "close_position": "closes a live position",
    "set_leverage": "changes live leverage",
    "cancel_order": "cancels a live order",
    "submit_verdict": "writes an analysis the execute tool can act on",
    "research": "spends LLM tokens",
    "desk_refresh": "spends LLM tokens (run it on purpose, not in a smoke)",
    "desk_submit_call": "writes to the desk journal",
    "risk_set_profile": "changes live position sizing",
}


@dataclass
class Case:
    tool: str
    args: Dict[str, Any] = field(default_factory=dict)
    budget_s: float = 15.0
    expect_error: bool = False


def cases(user: str) -> List[Case]:
    now_ms = int(time.time() * 1000)
    week_ms = 7 * 86_400_000
    return [
        Case("state"), Case("connected_wallets"), Case("wallet_account"), Case("config"),
        Case("scan", {"maxMarkets": 5}, budget_s=90),
        Case("leaderboard_get_markets", {"limit": 5}),
        Case("leaderboard_get_top_traders", {"limit": 3}),
        Case("leaderboard_get_trader_positions", {"trader_id": user}),
        Case("discovery_get_top_traders", {"limit": 3}),
        Case("discovery_get_trader_state", {"trader_addresses": [user]}),
        Case("market_get_asset_data", {"asset": OIL}),
        Case("market_get_funding_regime"), Case("market_list_instruments"),
        Case("get_price", {"coin": OIL}),
        Case("get_candles", {"coin": OIL, "interval": "1h", "count": 24}),
        Case("get_open_orders"), Case("get_spot_balances"), Case("get_user_fees"),
        Case("get_referral"), Case("get_trade_history", {"limit": 5}),
        Case("get_funding_history", {"coin": OIL, "limit": 5}),
        Case("get_l2_book", {"coin": OIL}), Case("get_user_state"), Case("get_user_twist"),
        Case("get_withdrawals", {"limit": 5}), Case("get_predicted_funding", {"coin": OIL}),
        Case("get_asset_context", {"coin": OIL}), Case("get_user_defined_types"),
        Case("get_price_history", {"coin": OIL}), Case("get_assets"),
        Case("get_deposits", {"limit": 5}), Case("get_transfers", {"limit": 5}),
        Case("get_rewards"), Case("get_staking_info"), Case("get_user_roles"),
        Case("get_max_trade_size", {"coin": OIL}), Case("get_portfolio_status"),
        Case("get_coin_info", {"coin": OIL}), Case("get_all_mids"),
        Case("get_order_by_oid", {"oid": 1}),          # answers status "unknownOid"
        Case("get_trading_permissions"), Case("get_account_summary"),
        Case("get_asset_positions"), Case("get_24h_stats", {"coin": OIL}),
        Case("get_funding_rate", {"coin": OIL}), Case("get_portfolio_pnl"),
        Case("get_risk_metrics"), Case("get_spot_markets"),
        Case("get_historical_funding", {"coin": OIL, "limit": 5}),
        Case("get_open_interest", {"coin": OIL}),
        Case("get_vault_details", {"vaultAddress": HLP_VAULT}),
        Case("get_api_rate_limits"), Case("get_server_time"),
        Case("get_user_orders_history", {"limit": 5}),
        Case("get_price_impact", {"coin": OIL, "notionalUsd": 100}),
        Case("get_withdrawal_status"), Case("get_validator_info"), Case("get_network_stats"),
        Case("get_liquidation_price", {"coin": OIL, "leverage": 10, "is_long": True}),
        Case("get_max_leverage", {"coin": OIL}),
        Case("get_user_fills_by_time", {"start_time": now_ms - week_ms}),
        Case("get_historical_orders"),
        Case("query_order_by_cloid", {"cloid": "0x" + "0" * 31 + "1"}),
        Case("desk_list"), Case("desk_brief", {"desk": "oil"}),
        Case("desk_brief", {"desk": "oil", "mode": "full"}),
        Case("desk_track_record", {"desk": "oil"}),
        Case("account_overview"), Case("accounts"), Case("risk_limits"),
        Case("risk_limits", {"equity_usd": 15, "preset": "starter"}),
        Case("risk_size_trade", {"coin": OIL, "entry_px": 98.87, "stop_px": 88.22,
                                 "leverage": 5, "targets": [101.19, 105.55]}),
        Case("get_user_state", {"dex": "xyz"}),
        Case("market_list_instruments", {"query": "xyz:", "limit": 5}),
    ]


_SECRET_NAME_PARTS = ("PRIVATE_KEY", "SECRET", "TOKEN", "PASSWORD", "API_KEY")


def _secret_values() -> List[str]:
    """Secret VALUES (private keys, API keys, tokens, passwords) from env and
    .env.local, lowercased, 0x stripped. Used only for substring checks;
    never printed or written anywhere."""
    vals: List[str] = []

    def is_secret(name: str) -> bool:
        return any(part in name.upper() for part in _SECRET_NAME_PARTS)

    for k, v in os.environ.items():
        if is_secret(k) and v.strip():
            vals.append(v.strip())
    env_file = REPO / ".env.local"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            k, sep, v = line.partition("=")
            if sep and is_secret(k) and v.strip():
                vals.append(v.strip().strip('"').strip("'"))
    out = []
    for v in vals:
        v = v.lower()
        v = v[2:] if v.startswith("0x") else v
        if len(v) >= 16:
            out.append(v)
    return out


def _server_env() -> Dict[str, str]:
    """The environment Claude Code gives the server: this one, minus any
    *PRIVATE_KEY* variable pinned to "" (the repo's test session does that so
    no test process ever holds a key). Dropping the empty pin lets the server
    read its own .env.local, exactly as when Claude Code launches it. Values
    are never read here; outputs are still leak-checked by _secret_values."""
    return {k: v for k, v in os.environ.items()
            if not ("PRIVATE_KEY" in k.upper() and v == "")}


class Client:
    """Minimal JSON-RPC-over-stdio MCP client: one reader thread, one future
    per request id, so concurrent calls are possible."""

    def __init__(self) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, str(SERVER)], cwd=str(REPO), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
            env=_server_env())
        self._waiters: Dict[Any, "queue.Queue[Dict[str, Any]]"] = {}
        self._lock = threading.Lock()
        self._seq = 0
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _drain_stderr(self) -> None:
        for _ in self.proc.stderr:  # keep the pipe from filling; content not needed
            pass

    def _read(self) -> None:
        for line in self.proc.stdout:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            with self._lock:
                q = self._waiters.get(msg.get("id"))
            if q is not None:
                q.put(msg)

    def request(self, method: str, params: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        with self._lock:
            self._seq += 1
            rid = self._seq
            q: "queue.Queue[Dict[str, Any]]" = queue.Queue()
            self._waiters[rid] = q
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method,
                                          "params": params}) + "\n")
        self.proc.stdin.flush()
        try:
            return q.get(timeout=timeout)
        finally:
            with self._lock:
                self._waiters.pop(rid, None)

    def notify(self, method: str) -> None:
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self.proc.stdin.flush()

    def close(self) -> None:
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


def _parallel_check(client: Client, rows: List[Dict[str, Any]]) -> int:
    """The transport must not serialize calls: a fast read sent right after a
    slow desk_brief comes back first. Returns 1 on failure, 0 on success."""
    order: List[str] = []
    lock = threading.Lock()

    def call(name: str, args: Dict[str, Any]) -> None:
        client.request("tools/call", {"name": name, "arguments": args}, timeout=300)
        with lock:
            order.append(name)

    slow = threading.Thread(target=call, args=("desk_brief", {"desk": "ai", "mode": "full"}))
    fast = threading.Thread(target=call, args=("get_server_time", {}))
    t0 = time.monotonic()
    slow.start()
    time.sleep(0.05)
    fast.start()
    slow.join(300)
    fast.join(300)
    ok = order[:1] == ["get_server_time"]
    rows.append({"tool": "(parallel)", "ok": ok, "s": round(time.monotonic() - t0, 3),
                 "preview": f"answer order: {order}"})
    print(f"{'ok  ' if ok else 'FAIL'} {'(parallel) slow+fast':32} {time.monotonic() - t0:6.2f}s "
          f"answer order: {order}")
    return 0 if ok else 1


def run(only: Optional[List[str]], report_path: Path) -> int:
    secrets = _secret_values()
    client = Client()
    rows: List[Dict[str, Any]] = []
    failures = 0
    try:
        t0 = time.monotonic()
        init = client.request("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "mcp_smoke", "version": "1"}}, timeout=30)
        init_s = time.monotonic() - t0
        client.notify("notifications/initialized")
        tools = client.request("tools/list", {}, timeout=30)["result"]["tools"]
        advertised = {t["name"] for t in tools}
        user = ""
        try:
            sys.path.insert(0, str(REPO))
            from pathiel.client.hl_client import resolve_user_address  # noqa: E402
            user = resolve_user_address()
        except Exception:
            pass
        all_cases = cases(user)
        covered = {c.tool for c in all_cases} | set(SKIPPED)
        uncovered = sorted(advertised - covered)
        stale = sorted({c.tool for c in all_cases} - advertised)
        print(f"initialize {init_s:.2f}s  tools={len(tools)}  "
              f"instructions={'yes' if init['result'].get('instructions') else 'NO'}")
        if uncovered:
            failures += 1
            print(f"FAIL uncovered tools (add a Case or a SKIPPED reason): {uncovered}")
        if stale:
            print(f"note: cases for tools the server does not advertise: {stale}")
        for case in all_cases:
            if case.tool not in advertised or (only and case.tool not in only):
                continue
            t = time.monotonic()
            try:
                resp = client.request("tools/call", {"name": case.tool, "arguments": case.args},
                                      timeout=case.budget_s * 4)
                dt = time.monotonic() - t
            except queue.Empty:
                dt = time.monotonic() - t
                rows.append({"tool": case.tool, "ok": False, "why": "no answer (hang)", "s": dt})
                failures += 1
                print(f"FAIL {case.tool:32} {dt:6.2f}s  no answer")
                continue
            result = resp.get("result") or {}
            text = "".join(c.get("text", "") for c in result.get("content") or [])
            is_err = bool(result.get("isError")) or "error" in resp
            leak = any(s in text.lower() for s in secrets)
            problems = []
            if leak:
                problems.append("PRIVATE KEY IN OUTPUT")
            if is_err != case.expect_error:
                problems.append("isError" if is_err else "expected an error")
            if dt > case.budget_s:
                problems.append(f"slow (> {case.budget_s:g}s)")
            ok = not problems
            failures += 0 if ok else 1
            preview = " ".join(text.split())[:150] if not leak else "<redacted: leak>"
            rows.append({"tool": case.tool, "args": case.args, "ok": ok, "s": round(dt, 3),
                         "bytes": len(text), "is_error": is_err, "problems": problems,
                         "preview": preview})
            print(f"{'ok  ' if ok else 'FAIL'} {case.tool:32} {dt:6.2f}s {len(text):7d}B "
                  f"{'; '.join(problems)}  {preview[:90]}")
        if not only:
            failures += _parallel_check(client, rows)
    finally:
        client.close()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps({"failures": failures, "rows": rows}, indent=1))
    print(f"\n{len(rows)} calls, {failures} failure(s). Report: {report_path}")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", help="comma-separated tool names")
    ap.add_argument("--json", default="/tmp/pathiel-mcp-smoke/report.json")
    a = ap.parse_args()
    only = [s.strip() for s in a.only.split(",")] if a.only else None
    return run(only, Path(a.json))


if __name__ == "__main__":
    raise SystemExit(main())
