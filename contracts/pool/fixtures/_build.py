"""Regenerate the pool contract fixtures.

    .venv/bin/python -m contracts.pool.fixtures._build

Writes, next to this file:

  join_master.json   a join signed by the account's own key
  join_agent.json    a join signed by another key (an API wallet)
  leave_agent.json   that same API wallet leaving, one day later
  scoreboard.json    a valid scoreboard: a house row and both accounts, all numbers made up

The keys are made here with Account.create(), used for these signatures and
dropped: no key is printed, written or kept, and the accounts hold nothing.
Every run makes new keys, so the addresses and signatures change; the tests
read whatever is on disk and never pin an address.

Regenerate only when STATEMENT_VERSION, FILE_SCHEMA or SCOREBOARD_SCHEMA
changes. No network.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict

from eth_account import Account
from eth_account.messages import encode_defunct

import contracts.pool as pool

HERE = os.path.dirname(os.path.abspath(__file__))
JOINED_AT = "2026-11-12T12:00:00Z"
LEFT_AT = "2026-11-13T12:00:00Z"
AS_OF = "2026-11-20T09:30:00Z"


def _sign(kind: str, address: str, key: Any, at: str) -> Dict[str, Any]:
    signer = Account.from_key(key).address.lower()
    message = pool.statement_text(kind, address, signer, at)
    sig = Account.sign_message(encode_defunct(text=message), key).signature.hex()
    signature = "0x" + sig.removeprefix("0x")
    return pool.build_statement_file(kind, address, signer, at, signature)


def _member_row(address: str, kind: str, role: Any, since: str, closed: int, n: int, wins: int,
                mean_r: Any, lo: Any, hi: Any, p: Any, label: str, luck: Any, luck_n: int) -> Dict[str, Any]:
    wr = round(wins / n, 4) if n else None
    return {"address": address, "short": pool.short_address(address),
            "explorer": pool.explorer_url(address), "kind": kind, "signer_role": role, "since": since,
            "closed_trades": closed, "n": n, "wins": wins, "win_rate": wr,
            "win_lo": 0.2366 if n else None, "win_hi": 0.7634 if n else None,
            "mean_r": mean_r, "r_lo": lo, "r_hi": hi, "p": p, "label": label,
            "luck_median": luck, "luck_n": luck_n}


def _slice(dim: str, value: str, n: int, n_trades: int, members: int, wins: int, mean_r: Any,
           lo: Any, hi: Any, p: Any, label: str) -> Dict[str, Any]:
    return {"key": "all" if dim == "all" else f"{dim}={value}", "dim": dim, "value": value, "n": n,
            "n_trades": n_trades, "members": members, "wins": wins,
            "win_rate": round(wins / n, 4) if n else None,
            "win_lo": 0.2993 if n else None, "win_hi": 0.7007 if n else None,
            "mean_r": mean_r, "r_lo": lo, "r_hi": hi, "p": p, "label": label}


def scoreboard(master_address: str, agent_account: str) -> Dict[str, Any]:
    """Made-up numbers on made-up addresses. The house row uses a throwaway
    address too, never the real one: an invented record must not sit next to
    a real account, even in a fixture."""
    as_of_ms = pool.ms_from_iso(AS_OF)
    doc = pool.empty_scoreboard(as_of_ms)
    house = Account.create().address.lower()
    rows = [
        _member_row(house, "house", None, pool.HOUSE_COUNT_FROM[:10], 19, 19, 9, 0.3105, -0.4412,
                    1.1876, 0.4683, "NOISE", 55.4, 12),
        _member_row(master_address, "member", "master", JOINED_AT[:10], 4, 3, 2, 0.6021, -0.9, 2.1,
                    0.5012, "NOISE", 61.0, 3),
        _member_row(agent_account, "member", "agent", JOINED_AT[:10], 1, 0, 0, None, None, None,
                    None, "NOISE", None, 0),
    ]
    doc["members"] = sorted(rows, key=lambda r: (-r["n"], r["address"]))
    doc["pool"] = [
        _slice("all", "all", 20, 22, 2, 10, 0.3391, -0.3105, 1.0822, 0.3718, "NOISE"),
        _slice("desk", "oil", 14, 16, 2, 7, 0.4127, -0.3999, 1.3101, 0.4203, "NOISE"),
        _slice("desk", "metals", 6, 6, 1, 3, 0.1674, -0.8, 1.2, 0.7811, "NOISE"),
        _slice("side", "long", 11, 12, 2, 6, 0.5, -0.4, 1.5, 0.3, "NOISE"),
        _slice("side", "short", 9, 10, 2, 4, 0.1424, -0.9, 1.1, 0.8, "NOISE"),
    ]
    doc["counts"] = {
        "members": 2, "house": 1, "left": 0,
        "closed_trades": sum(r["closed_trades"] for r in rows),
        "graded_trades": sum(r["n"] for r in rows),
        "independent_trades": 20, "slices": len(doc["pool"]),
        "slices_at_min_n": sum(1 for r in doc["pool"] if r["n"] >= pool.MIN_N_EDGE),
        "edge": 0, "leak": 0,
    }
    return doc


def main() -> int:
    master = Account.create()
    account_with_agent = Account.create()
    agent = Account.create()
    files = {
        "join_master.json": pool.dump_statement_file(
            _sign("join", master.address.lower(), master.key, JOINED_AT)),
        "join_agent.json": pool.dump_statement_file(
            _sign("join", account_with_agent.address.lower(), agent.key, JOINED_AT)),
        "leave_agent.json": pool.dump_statement_file(
            _sign("leave", account_with_agent.address.lower(), agent.key, LEFT_AT)),
    }
    board = scoreboard(master.address.lower(), account_with_agent.address.lower())
    problems = pool.validate_scoreboard(board)
    if problems:
        raise SystemExit("fixture scoreboard is not valid: " + "; ".join(problems))
    files["scoreboard.json"] = pool.dump_scoreboard(board)
    del master, account_with_agent, agent
    for name, text in files.items():
        with open(os.path.join(HERE, name), "w", encoding="ascii") as fh:
            fh.write(text)
        print(f"wrote {name} ({len(text)} bytes)")
    print(json.dumps({"fixtures": sorted(files)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
