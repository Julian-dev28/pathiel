"""Public fills to graded rows: the pool's grading recipe, the per-member
ledger, and the pool-level clusters and slices.

The recipe (docs/overhaul/RESEARCH_POOL_2026-11-12.md, proven on
services/review/tests/fixtures/bundle_2026-11-02: 10 closed trades, 5 wins,
mean R 0.9933, with no thesis and no portfolio read) is `member_records`.
Everything else here reads its output: one member's ledger
(`update_ledger`, `ledger_records`, `purge_ledgers`), one member's public
row (`member_row`), and the pool's independent-trade clusters and slices
(`cluster_records`, `pool_rows`), which call `contracts.pool.cluster_ids`
and never re-implement its window.

No thesis, no equity, no funding: `member_records` passes `[]` for theses
and `{}` for theses by desk, and never reads a portfolio or a ledger of
deposits. Pool data feeds grading only (contracts/pool/tests/
test_thesis_boundary.py)."""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Sequence

import contracts.pool as pool
from services.pool import paths
from services.review import analysis as A
from services.review import ledger as LG
from services.review import stats as S
from services.strategies.evals import loss_audit as LA


def _has_flip(fills: Sequence[Dict[str, Any]], trade: Dict[str, Any]) -> bool:
    """True when some fill of `trade`'s chain (its coin, between its entry
    and exit fills inclusive) carries the position through zero: the audit
    folds that into one trade and its R is wrong (failure mode 12)."""
    coin, t0, t1 = trade["coin"], trade["entry_time"], trade["exit_time"]
    if t1 is None:
        return False
    for f in fills:
        if f.get("coin") != coin:
            continue
        t = int(f["time"])
        if t < t0 or t > t1:
            continue
        start = float(f["startPosition"])
        signed = float(f["sz"]) if f["side"] == "B" else -float(f["sz"])
        after = start + signed
        if abs(start) > 1e-9 and abs(after) > 1e-9 and (start > 0) != (after > 0):
            return True
    return False


def member_records(inputs: Dict[str, List[Dict[str, Any]]], bars5: Dict[str, Any], bars1h: Dict[str, Any],
                   count_from_ms: int, as_of_ms: int) -> List[Dict[str, Any]]:
    """One member's closed, enriched ledger records from their public fills
    and orders, oldest first. Pure on its inputs. `count_from_ms` is the
    member's own join time: fills before it never count, and a position
    already open at the join (its first fill has a non-zero
    `startPosition`) is skipped until the account is flat.

    A trade whose chain holds a fill that crosses through zero gets
    `r_mult = None` and `pool_note = 'flip'`: closed, not graded."""
    fills = [f for f in inputs.get("fills") or [] if f.get("coin") in pool.POOL_COINS
             and int(f["time"]) >= count_from_ms]
    orders = inputs.get("orders") or []
    trades = LA.reconstruct_trades(fills, pool.POOL_COINS, count_from_ms)
    for tr in trades:
        tr["desk"] = pool.POOL_DESK_OF[tr["coin"]]
    LA.attach_funding(trades, [])
    LA.enrich(trades, orders, [], [], bars5, {})
    records = []
    for tr in trades:
        if tr["exit_time"] is None:
            continue
        rec = LG.to_record(tr, [], bars1h.get(tr["coin"], []), bars5.get(tr["coin"], []), as_of_ms)
        if _has_flip(fills, tr):
            rec["r_mult"] = None
            rec["pool_note"] = "flip"
        records.append(rec)
    LG.annotate_pairs(records)
    return records


# ── the per-member ledger ────────────────────────────────────────────────────

def update_ledger(address: str, records: Sequence[Dict[str, Any]], as_of_ms: int,
                  ledger_dir: Optional[str] = None) -> Dict[str, List[str]]:
    """Append what is new or changed to this member's ledger file."""
    path = (os.path.join(ledger_dir, pool.normalize_address(address) + ".jsonl")
            if ledger_dir else paths.ledger_path(address))
    return LG.update_ledger(path, records, LG.iso(as_of_ms))


def ledger_records(address: str, count_from_ms: int, ledger_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """This member's current ledger records entered at or after `count_from_ms`."""
    path = (os.path.join(ledger_dir, pool.normalize_address(address) + ".jsonl")
            if ledger_dir else paths.ledger_path(address))
    return LG.ledger_records(path, count_from_ms)


def purge_ledgers(active_addresses: Sequence[str], ledger_dir: Optional[str] = None) -> List[str]:
    """Delete the ledger file of every address that is not active (a leave's
    local trace is gone too). Returns the addresses removed, sorted."""
    directory = ledger_dir or paths.ledger_dir()
    if not os.path.isdir(directory):
        return []
    active = {pool.normalize_address(a) for a in active_addresses}
    removed = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".jsonl"):
            continue
        address = name[: -len(".jsonl")]
        if address not in active:
            os.remove(os.path.join(directory, name))
            removed.append(address)
    return removed


# ── what the scoreboard may show ────────────────────────────────────────────

def published(records: Sequence[Dict[str, Any]], cutoff: int) -> List[Dict[str, Any]]:
    """Closed before `cutoff` (the 24 hour delay): `contracts.pool.publishable`."""
    return [r for r in records if pool.publishable(r.get("exit_ms"), cutoff)]


def _published_graded(member: "pool.ActiveMember", records: Sequence[Dict[str, Any]], cutoff: int
                      ) -> Any:
    """(published, graded) for one member: records from their own
    `count_from_ms`, closed before `cutoff`, and of those the ones with an
    R. Shared by `member_row` and the pool's clustering so the two always
    agree on which trades count."""
    count_from_ms = member["count_from_ms"]
    recs = published([r for r in records if r.get("entry_ms") is not None and int(r["entry_ms"]) >= count_from_ms],
                     cutoff)
    graded = [r for r in recs if r.get("r_mult") is not None]
    return recs, graded


def member_row(member: "pool.ActiveMember", records: Sequence[Dict[str, Any]], cutoff: int,
               seed: int = S.SEED) -> "pool.MemberRow":
    """One member's public row (module doc; `n == 0` gives nulls and NOISE
    through the generic stats, with no special case). Over the member's own
    trades, not clusters."""
    address = member["address"]
    recs, graded = _published_graded(member, records, cutoff)
    row = A.slice_row(graded, "all", "all", S.sub_seed(seed, address))
    luck_vals = sorted(r["luck_pct"] for r in graded if r.get("luck_pct") is not None)
    luck_median = round(S.quantile(luck_vals, 0.5), 1) if luck_vals else None

    def r4(x: Any) -> Any:
        return round(x, 4) if isinstance(x, float) else x

    return {
        "address": address, "short": pool.short_address(address), "explorer": pool.explorer_url(address),
        "kind": member["kind"], "signer_role": member.get("signer_role"), "since": pool.day_of(member["count_from_ms"]),
        "closed_trades": len(recs), "n": len(graded), "wins": row["wins"],
        "win_rate": r4(row["win_rate"]), "win_lo": r4(row["win_lo"]), "win_hi": r4(row["win_hi"]),
        "mean_r": r4(row["mean_r"]), "r_lo": r4(row["r_lo"]), "r_hi": r4(row["r_hi"]), "p": r4(row["p"]),
        "label": row["label"], "luck_median": luck_median, "luck_n": len(luck_vals),
    }


# ── independent trades ──────────────────────────────────────────────────────

_CLUSTER_DIM_FIELDS = ("desk", "side", "weekend", "hour_utc", "planned_r", "range_pos", "trend_20d_pct")


def cluster_records(graded_by_address: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """One record per independent trade (cluster), over every graded,
    published record of every member row. Clusters come from
    `contracts.pool.cluster_ids`: same coin and side, entered within
    `pool.CLUSTER_SPAN_MS` of the cluster's first entry. A cluster's record
    carries the dimension fields of its earliest trade (tie on entry: the
    lowest address), `r_mult` and `net_usd` both set to the mean R of its
    trades (so `services.review.analysis.slice_row` can count wins by sign;
    no dollar amount leaves this function), `n_trades` (member trades
    behind it) and `addresses` (the distinct members behind it)."""
    flat: List[Any] = []
    for address in sorted(graded_by_address):
        for r in graded_by_address[address]:
            flat.append((address, r))
    rows = [(r["coin"], r["side"], int(r["entry_ms"])) for _, r in flat]
    ids = pool.cluster_ids(rows)
    by_cluster: Dict[int, List[Any]] = {}
    for pair, cid in zip(flat, ids):
        by_cluster.setdefault(cid, []).append(pair)
    out = []
    for cid in sorted(by_cluster):
        members = by_cluster[cid]
        _, earliest = min(members, key=lambda pair: (int(pair[1]["entry_ms"]), pair[0]))
        rs = [float(r["r_mult"]) for _, r in members]
        mean_r = sum(rs) / len(rs)
        rec: Dict[str, Any] = {k: earliest.get(k) for k in _CLUSTER_DIM_FIELDS}
        rec.update({"r_mult": mean_r, "net_usd": mean_r, "n_trades": len(members),
                   "addresses": {a for a, _ in members}})
        out.append(rec)
    return out


def pool_rows(clusters: Sequence[Dict[str, Any]], seed: int = S.SEED) -> List["pool.SliceRow"]:
    """One row per non-trivial slice of `pool.PUBLISHED_DIMENSIONS`, "all"
    first, then dimension order, then n descending, then value. A dimension
    whose only present bucket holds every cluster is skipped (it adds
    nothing over "all"), except "all" itself, which is always present, even
    with zero clusters (the shape of `pool.empty_scoreboard`'s row)."""
    out: List[Dict[str, Any]] = []
    for dim in pool.PUBLISHED_DIMENSIONS:
        values: Dict[str, int] = {}
        for c in clusters:
            for v in A.buckets(c, dim):
                values[v] = values.get(v, 0) + 1
        if dim == "all":
            value_list = ["all"]
        else:
            if not values or (len(values) == 1 and next(iter(values.values())) == len(clusters)):
                continue
            value_list = sorted(values, key=lambda v: (-values[v], v))
        for value in value_list:
            row = A.slice_row(clusters, dim, value, seed)
            inside = [c for c in clusters if value in A.buckets(c, dim)]
            n_trades = sum(c["n_trades"] for c in inside)
            members = len({a for c in inside for a in c["addresses"]})

            def r4(x: Any) -> Any:
                return round(x, 4) if isinstance(x, float) else x

            out.append({
                "key": row["key"], "dim": dim, "value": value, "n": row["n"], "n_trades": n_trades,
                "members": members, "wins": row["wins"], "win_rate": r4(row["win_rate"]),
                "win_lo": r4(row["win_lo"]), "win_hi": r4(row["win_hi"]), "mean_r": r4(row["mean_r"]),
                "r_lo": r4(row["r_lo"]), "r_hi": r4(row["r_hi"]), "p": r4(row["p"]), "label": row["label"],
            })
    return out
