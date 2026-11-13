"""Build and write `docs/scoreboard/scoreboard.json`: pure assembly of the
member and pool rows `grade.py` computes, validated against the contract
before anything is written."""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import contracts.pool as pool
from services.pool import grade as G
from services.pool import paths
from services.review import stats as S


def build(members: List["pool.ActiveMember"], records_by_address: Dict[str, List[Dict[str, Any]]],
         as_of_ms: int, *, left: int = 0, seed: int = S.SEED) -> Dict[str, Any]:
    """The scoreboard doc for this build. Pure: no file is touched here.
    Raises `pool.PoolError('invalid_scoreboard', ...)` if the result does
    not pass `pool.validate_scoreboard`, which must never happen on correct
    inputs and protects every promise the published file makes."""
    cutoff = pool.cutoff_ms(as_of_ms)
    member_rows: List[Dict[str, Any]] = []
    graded_by_address: Dict[str, List[Dict[str, Any]]] = {}
    for member in members:
        address = member["address"]
        records = records_by_address.get(address) or []
        member_rows.append(G.member_row(member, records, cutoff, seed))
        _, graded = G._published_graded(member, records, cutoff)
        graded_by_address[address] = graded
    member_rows.sort(key=lambda r: (-r["n"], r["address"]))

    clusters = G.cluster_records(graded_by_address)
    rows = G.pool_rows(clusters, seed)

    house_count = sum(1 for r in member_rows if r["kind"] == "house")
    counts = {
        "members": len(member_rows) - house_count,
        "house": house_count,
        "left": int(left),
        "closed_trades": sum(r["closed_trades"] for r in member_rows),
        "graded_trades": sum(r["n"] for r in member_rows),
        "independent_trades": rows[0]["n"] if rows else 0,
        "slices": len(rows),
        "slices_at_min_n": sum(1 for r in rows if r["n"] >= pool.MIN_N_EDGE),
        "edge": sum(1 for r in rows if r["label"] == "EDGE"),
        "leak": sum(1 for r in rows if r["label"] == "LEAK"),
    }
    doc = {
        "schema": pool.SCOREBOARD_SCHEMA, "network": pool.NETWORK, "day": pool.day_of(as_of_ms),
        "cutoff": pool.iso_from_ms(cutoff), "delay_hours": pool.DELAY_MS // pool.HOUR_MS,
        "min_n_edge": pool.MIN_N_EDGE, "counts": counts, "pool": rows, "members": member_rows,
        "notes": list(pool.NOTES),
    }
    problems = pool.validate_scoreboard(doc)
    if problems:
        raise pool.PoolError("invalid_scoreboard", "; ".join(problems))
    return doc


def write(doc: Dict[str, Any], out_dir: Optional[str] = None) -> bool:
    """`pool.dump_scoreboard(doc)` to `<out_dir or paths.scoreboard_dir()>/
    scoreboard.json`, atomically, only when the bytes differ. Returns
    whether the file changed."""
    directory = out_dir or paths.scoreboard_dir()
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, pool.SCOREBOARD_JSON)
    data = pool.dump_scoreboard(doc)
    if os.path.exists(path):
        with open(path, "r", encoding="ascii") as fh:
            if fh.read() == data:
                return False
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="ascii") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return True


def summary_line(doc: Dict[str, Any], changed: Any, out_dir: Optional[str] = None) -> str:
    """'POOL <day> cutoff=<cutoff day> members=N house=N left=N closed=N
    graded=N independent=N slices_at_10=K/M edge=N leak=N
    changed=yes|no|dry out=<path>'. `changed` is `True`, `False` or the
    string 'dry' (`--dry-run`: no file was ever written)."""
    c = doc["counts"]
    changed_str = "dry" if changed == "dry" else ("yes" if changed else "no")
    out_path = os.path.join(out_dir or paths.scoreboard_dir(), pool.SCOREBOARD_JSON)
    return (f"POOL {doc['day']} cutoff={doc['cutoff'][:10]} members={c['members']} house={c['house']} "
            f"left={c['left']} closed={c['closed_trades']} graded={c['graded_trades']} "
            f"independent={c['independent_trades']} slices_at_10={c['slices_at_min_n']}/{c['slices']} "
            f"edge={c['edge']} leak={c['leak']} changed={changed_str} out={out_path}")


def new_rows(previous: Optional[Dict[str, Any]], doc: Dict[str, Any]) -> List[str]:
    """'POOL-NEW <short address> since=<since>' for every member row of
    `doc` whose address had no row in `previous`. `[]` when `previous` is
    `None` or not a valid scoreboard. Ordered by address."""
    if previous is None or pool.validate_scoreboard(previous):
        return []
    had = {r["address"] for r in previous.get("members") or [] if isinstance(r, dict) and "address" in r}
    gained = sorted((r for r in doc.get("members") or [] if r["address"] not in had), key=lambda r: r["address"])
    return [f"POOL-NEW {pool.short_address(r['address'])} since={r['since']}" for r in gained]
