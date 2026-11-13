"""The member's own graded report: local, not delayed, publishes nothing.

Same grading and ledger path as the build (`grade.member_records`,
`update_ledger`, `ledger_records`), counted from the account's own
`count_from_ms` (`contracts.pool.local_standing`). No 24 hour delay: this is
the member's own view, and nothing it returns is published. A trade still
inside its settle window counts anyway, marked provisional."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import contracts.pool as pool
from contracts import runtime
from services.pool import fetch as F
from services.pool import grade as G
from services.pool import paths
from services.review import analysis as A
from services.review import report as R
from services.review import stats as S


def _round(x: Any) -> Any:
    return round(x, 4) if isinstance(x, float) else x


def _markdown(account: str, since: str, as_of: str, closed_trades: int, graded_trades: int, provisional: int,
             slices: List[Dict[str, Any]], trades_needed: Optional[int]) -> str:
    lines = [
        f"# pathiel research pool: your report, {account}", "",
        f"Since {since}, as of {as_of}: {closed_trades} closed trades, {graded_trades} graded, "
        f"{provisional} still inside the settle window (provisional).", "",
    ]
    lines += R.slice_table_md(slices)
    lines.append("")
    if trades_needed:
        lines.append(f"At this spread of outcomes, a real +0.4R edge needs about {trades_needed} trades in "
                     "one slice to show. Treat every pattern above as a lead, not a rule.")
    else:
        lines.append("Not enough graded trades yet to estimate how many would show a real edge.")
    lines.append("")
    lines.append("Nothing in this report is published by this call. The scoreboard shows your row at least "
                 "24 hours late.")
    return "\n".join(lines)


def personal(address: str, *, as_of_ms: Optional[int] = None, bundle: Optional[Dict[str, Any]] = None,
            post: Any = None, candle_fetch: Any = None) -> Dict[str, Any]:
    """This account's own graded report. Raises `pool.PoolError('not_member',
    ...)` for any standing outside `pool.GRADED_STATES`, and
    `pool.PoolError('testnet', ...)` for a live (non-bundle) call off
    mainnet."""
    address = pool.normalize_address(address)
    standing = pool.local_standing(paths.repo_pool_root(), paths.outbox_root(), address)
    if standing["state"] not in pool.GRADED_STATES:
        raise pool.PoolError("not_member", "join first: /pathiel:share")
    count_from_ms = standing["count_from_ms"]
    as_of = int(as_of_ms) if as_of_ms is not None else (bundle["as_of_ms"] if bundle else int(time.time() * 1000))

    if bundle is not None:
        one_inputs = (bundle.get("inputs") or {}).get(address) or {"fills": [], "orders": []}
        bars5, bars1h = bundle.get("bars5") or {}, bundle.get("bars1h") or {}
    else:
        if runtime.network() != "mainnet":
            raise pool.PoolError("testnet", "the research pool is mainnet only")
        member = {"address": address, "count_from_ms": count_from_ms}
        one_inputs = F.member_inputs(member, as_of, post=post)
        bars5, bars1h = F.load_candles(paths.work_dir("report-cache"), fetch=candle_fetch)

    fresh = G.member_records(one_inputs, bars5, bars1h, count_from_ms, as_of)
    G.update_ledger(address, fresh, as_of)
    records = G.ledger_records(address, count_from_ms)

    cutoff = as_of + 1   # no delay: everything closed up to and including "now" counts
    recs = [r for r in records if r.get("entry_ms") is not None and int(r["entry_ms"]) >= count_from_ms
            and pool.publishable(r.get("exit_ms"), cutoff)]
    graded = [r for r in recs if r.get("r_mult") is not None]
    provisional = sum(1 for r in recs if not r.get("settled"))

    member = {"address": address, "kind": "house" if standing["state"] == "house" else "member",
             "signer_role": standing["join"]["signer_role"] if standing.get("join") else None,
             "count_from_ms": count_from_ms}
    row = G.member_row(member, records, cutoff, S.SEED)

    slices = []
    for dim in pool.PUBLISHED_DIMENSIONS:
        for value in sorted({v for r in graded for v in A.buckets(r, dim)}):
            s = A.slice_row(graded, dim, value, S.SEED)
            if s["n"] >= 1:
                slices.append({k: _round(v) for k, v in s.items()})

    r_values = [float(r["r_mult"]) for r in graded if r.get("r_mult") is not None]
    needed = R.trades_needed(R.sd(r_values))
    since_iso, as_of_iso = pool.iso_from_ms(count_from_ms), pool.iso_from_ms(as_of)
    markdown = _markdown(pool.short_address(address), since_iso, as_of_iso, len(recs), len(graded), provisional,
                         slices, needed)
    return {
        "ok": True, "account": pool.short_address(address), "state": standing["state"], "since": since_iso,
        "as_of": as_of_iso, "closed_trades": len(recs), "graded_trades": len(graded), "provisional": provisional,
        "row": row, "slices": slices, "trades_needed": needed, "markdown": markdown,
    }
