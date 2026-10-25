"""Grade every registered account's trades against the thesis that was live
when they placed them. Deterministic: same fills + same thesis window always
produce the same AccountTradeGrade.

WHY A SIBLING FILE, NOT A Thesis FIELD
Storage mirrors the thesis journal but lives next to it, not on it:

    strategies/<desk_id>/trades/<thesis_id>.json

The build brief offered two options: extend contracts.strategies.models.Thesis
with a `trades` field (a CONTRACT_VERSION bump, since every thesis already on
disk would need to keep validating) or a sibling file. This picks the sibling
file because trade grading is a genuinely separate concern with its own
cadence: a thesis is written once and frozen; its trades keep arriving for as
long as the window is open, and recomputing them should never mean
re-reading or re-writing the thesis itself. Keeping them apart also means
this module never has to import-and-widen the Thesis contract to do its job —
one more service, one more sharp boundary, per CLAUDE.md's architecture
section. `AccountTradeGrade`/`ThesisTrades` are additive contract models
(contracts/strategies/models.py): no existing model's shape changed, so no
CONTRACT_VERSION bump was needed either.

WHAT GETS GRADED
`grade_due()` is the idempotent sweep (same contract as grader.grade_all_due):
for every desk, every thesis that is graded-or-expired and has no trades file
yet, for every account in services.accounts.view.list_all_accounts() (every
registered account, every mode, plus the synthetic "env" account), fetch that
account's fills on the call's instrument coin(s) -- both legs for a `pair`
call, the one instrument otherwise -- over
[thesis.created_at, min(thesis.expires_at, next thesis's created_at)], and
compute:

    n_fills, n_opens, n_closes        counted from each fill's own `dir`
                                       ("Open Long"/"Close Short"/...)
    realized_pnl_usd                  sum(closedPnl) - sum(fee) over every
                                       matched fill in the window
    follow_rate                       fraction of OPENING fills whose side
                                       matches the call's direction (long
                                       call / long leg of a pair = "B" opens;
                                       short call / short leg = "A" opens).
                                       None for a `flat` call (no direction)
    first_entry_px, entered_in_zone   px of the first opening fill, and
                                       whether it sat inside call.entry_zone
    max_adverse_fill                  the single worst closedPnl in the
                                       window (most negative; None if no fills)

Grading a thesis with zero registered accounts, or an account with zero
matching fills, still writes a ThesisTrades row (one AccountTradeGrade per
account, all-zero/None fields for a silent account) -- "nobody followed this
call" is itself the finding, not a reason to skip writing anything.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from contracts.strategies.models import (
    AccountTradeGrade, Call, DeskSpec, OpenWindowTrades, ThesisTrades, TrackRecord,
)
from pathiel.agents.atomic_io import write_json_atomic

from services.strategies import desks, grader, journal

FillsFetcher = Callable[[str, int, Optional[int]], Sequence[Dict[str, Any]]]


def default_fills_fetcher(user: str, start_ms: int, end_ms: Optional[int] = None) -> Sequence[Dict[str, Any]]:
    from pathiel.client.hl_client import fetch_user_fills_by_time
    return fetch_user_fills_by_time(user, start_ms, end_ms)


def _fnum(v: Any) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


# ── instrument / direction resolution (mirrors grader._price_series) ───────

def _coins_for_call(call: Call, spec: Optional[DeskSpec]) -> List[str]:
    """The coin(s) a call's fills live on: both legs for a `pair` call (when
    the desk spec resolves the pair label), else the one instrument."""
    if call.side == "pair" and spec is not None:
        pair = next((p for p in spec.pairs if p.label == call.instrument), None)
        if pair is not None:
            return [pair.long, pair.short]
    return [call.instrument]


def _expected_open_side(call: Call, coin: str, spec: Optional[DeskSpec]) -> Optional[str]:
    """"B" (buy) or "A" (sell/ask) -- the opening fill side that follows this
    call on `coin`. None when the call has no direction on that coin (a
    `flat` call, or a coin outside the resolved pair)."""
    if call.side == "long":
        return "B" if coin == call.instrument else None
    if call.side == "short":
        return "A" if coin == call.instrument else None
    if call.side == "pair" and spec is not None:
        pair = next((p for p in spec.pairs if p.label == call.instrument), None)
        if pair is not None:
            if coin == pair.long:
                return "B"
            if coin == pair.short:
                return "A"
    return None


# ── pure grading (no I/O) ────────────────────────────────────────────────

def grade_account_fills(account: Any, call: Call, spec: Optional[DeskSpec],
                        fills: Sequence[Dict[str, Any]],
                        window_start_ms: int, window_end_ms: int) -> AccountTradeGrade:
    """PURE: `fills` may be over-fetched (wider time range, other coins) --
    this filters to the call's coin(s) and the exact window itself, the same
    defensive-clip discipline grader._clip uses for candles. `account` is a
    contracts.accounts.models.Account (duck-typed here so a plain namespace
    with the same attributes works in a test)."""
    coins = set(_coins_for_call(call, spec))
    rows = [f for f in fills if f.get("coin") in coins
           and window_start_ms <= int(f.get("time") or 0) <= window_end_ms]
    rows.sort(key=lambda f: f.get("time") or 0)

    opens = [f for f in rows if "Open" in str(f.get("dir") or "")]
    closes = [f for f in rows if "Close" in str(f.get("dir") or "")]
    realized = sum(_fnum(f.get("closedPnl")) for f in rows) - sum(_fnum(f.get("fee")) for f in rows)

    follow_rate: Optional[float] = None
    if opens:
        counted = 0
        matched = 0
        for f in opens:
            expected = _expected_open_side(call, f.get("coin"), spec)
            if expected is None:
                continue
            counted += 1
            if f.get("side") == expected:
                matched += 1
        follow_rate = (matched / counted) if counted else None

    first_entry_px = _fnum(opens[0].get("px")) if opens else None
    entered_in_zone: Optional[bool] = None
    if first_entry_px is not None and call.entry_zone and len(call.entry_zone) == 2:
        lo, hi = sorted(call.entry_zone)
        entered_in_zone = lo <= first_entry_px <= hi

    max_adverse_fill = min((_fnum(f.get("closedPnl")) for f in rows), default=None)

    return AccountTradeGrade(
        account_id=account.id, label=account.label, main_address=account.main_address,
        mode=account.mode, source=account.source,
        n_fills=len(rows), n_opens=len(opens), n_closes=len(closes),
        realized_pnl_usd=round(realized, 6), follow_rate=follow_rate,
        first_entry_px=first_entry_px, entered_in_zone=entered_in_zone,
        max_adverse_fill=max_adverse_fill,
    )


def grade_thesis_trades(thesis, spec: Optional[DeskSpec], accounts: Sequence[Any],
                        fills_fetcher: FillsFetcher, *,
                        next_created_at: Optional[datetime] = None,
                        now: Optional[datetime] = None) -> ThesisTrades:
    """One ThesisTrades row: every account in `accounts` graded against
    `thesis`'s call over [created_at, min(expires_at, next_created_at, now)].
    `fills_fetcher` may return fills outside that window or coin set --
    grade_account_fills clips to both."""
    now = now or datetime.now(timezone.utc)
    window_end = thesis.expires_at
    if next_created_at is not None:
        window_end = min(window_end, next_created_at)
    window_end = min(window_end, now)
    window_start = thesis.created_at
    start_ms = int(window_start.timestamp() * 1000)
    end_ms = int(window_end.timestamp() * 1000)

    rows: List[AccountTradeGrade] = []
    for account in accounts:
        fills = fills_fetcher(account.main_address, start_ms, end_ms) or []
        rows.append(grade_account_fills(account, thesis.call, spec, fills, start_ms, end_ms))

    return ThesisTrades(thesis_id=thesis.id, desk_id=thesis.desk_id, window_start=window_start,
                        window_end=window_end, computed_at=now, accounts=rows)


# ── storage: sibling files to the thesis journal ────────────────────────────

def trades_dir(desk_id: str) -> str:
    return os.path.join(journal.strategies_dir(), desk_id, "trades")


def trade_grade_path(desk_id: str, thesis_id: str) -> str:
    return os.path.join(trades_dir(desk_id), f"{thesis_id}.json")


def save_thesis_trades(trades: ThesisTrades) -> None:
    write_json_atomic(trade_grade_path(trades.desk_id, trades.thesis_id), trades.model_dump(mode="json"))


def get_thesis_trades(desk_id: str, thesis_id: str) -> Optional[ThesisTrades]:
    path = trade_grade_path(desk_id, thesis_id)
    if not os.path.exists(path):
        return None
    import json
    with open(path) as fh:
        return ThesisTrades.model_validate(json.load(fh))


def list_thesis_trades(desk_id: str) -> List[ThesisTrades]:
    """Every ThesisTrades row for this desk. Missing directory = none yet,
    not an error (same discipline as journal.list_theses)."""
    d = trades_dir(desk_id)
    if not os.path.isdir(d):
        return []
    import json
    out: List[ThesisTrades] = []
    for name in sorted(os.listdir(d)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, name)) as fh:
                out.append(ThesisTrades.model_validate(json.load(fh)))
        except Exception:  # noqa: BLE001 - one corrupt row shouldn't sink the whole desk
            continue
    return out


# ── the idempotent sweep ─────────────────────────────────────────────────────

def grade_due(*, fills_fetcher: Optional[FillsFetcher] = None, now: Optional[datetime] = None,
             desk_id: Optional[str] = None) -> int:
    """Grade trades for every thesis whose WINDOW HAS CLOSED and that has no
    trades file yet, across every desk with a journal on disk (or just
    `desk_id`). A window is closed when the thesis is graded, has expired,
    or has been superseded by a newer thesis (its window ends at that
    thesis's created_at, so nothing more can land in it: 2026-10-25, before
    this a superseded call's fills waited up to a week for its own expiry).
    Idempotent: a thesis with a trades file already saved is skipped, so
    calling this twice in a row grades 0 the second time -- same contract as
    grader.grade_all_due. Call this AFTER grade_due() in the same sweep (the
    server's background job and the desk_grade MCP tool both do) so a thesis
    that just expired is thesis-graded before its trades are computed."""
    fills_fetcher = fills_fetcher or default_fills_fetcher
    now = now or datetime.now(timezone.utc)
    from services.accounts.view import list_all_accounts
    accounts = list_all_accounts()

    graded = 0
    desk_ids = [desk_id] if desk_id else journal.all_desk_ids()
    for d_id in desk_ids:
        theses = sorted(journal.list_theses(d_id), key=lambda t: t.created_at)
        spec = desks.get_spec(d_id)
        for i, thesis in enumerate(theses):
            superseded = i + 1 < len(theses) and theses[i + 1].created_at <= now
            due = thesis.grade is not None or thesis.expires_at <= now or superseded
            if not due or get_thesis_trades(d_id, thesis.id) is not None:
                continue
            next_created_at = theses[i + 1].created_at if i + 1 < len(theses) else None
            trades = grade_thesis_trades(thesis, spec, accounts, fills_fetcher,
                                        next_created_at=next_created_at, now=now)
            save_thesis_trades(trades)
            graded += 1
    return graded


# ── the open window: the latest thesis, summed live ─────────────────────────
#
# A thesis's trades reach disk only once its window closes. Until then the
# fills the account made under it (the stop that fired an hour ago) would be
# invisible on the Desk page for up to a week (operator, 2026-10-25: "seems
# like we were stopped out shouldnt you update"). So the latest thesis is
# summed live at read time, never persisted, and cached per desk for a short
# TTL so the Desk page's poll does not spend the venue budget on it.

OPEN_WINDOW_TTL_S = 60.0
_OPEN_WINDOW_CACHE: Dict[Tuple[str, str], Tuple[float, Optional[OpenWindowTrades]]] = {}


def open_window_trades(thesis, spec: Optional[DeskSpec], accounts: Sequence[Any],
                       fills_fetcher: FillsFetcher, *, now: Optional[datetime] = None) -> OpenWindowTrades:
    """Every account's fills under `thesis` over [created_at, now], summed
    with the same per-account math grade_account_fills uses. Pure apart from
    the fetcher; nothing is written."""
    now = now or datetime.now(timezone.utc)
    start_ms = int(thesis.created_at.timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)
    n_fills = 0
    realized = 0.0
    for account in accounts:
        fills = fills_fetcher(account.main_address, start_ms, end_ms) or []
        g = grade_account_fills(account, thesis.call, spec, fills, start_ms, end_ms)
        n_fills += g.n_fills
        realized += g.realized_pnl_usd
    return OpenWindowTrades(thesis_id=thesis.id, since=thesis.created_at, computed_at=now,
                            n_fills=n_fills, realized_pnl_usd=round(realized, 6))


def open_window_stats(desk_id: str, *, fills_fetcher: Optional[FillsFetcher] = None,
                      now: Optional[datetime] = None, ttl_s: Optional[float] = None,
                      clock: Callable[[], float] = time.monotonic) -> Optional[OpenWindowTrades]:
    """The live roll-up for `desk_id`'s latest thesis, or None when the desk
    has no thesis or the latest one's trades are already on disk (closed
    window). Cached per (state dir, desk) for OPEN_WINDOW_TTL_S seconds (env
    PATHIEL_OPEN_WINDOW_TTL_S). Never raises: a venue error means None this
    round, the same as "nothing yet", and the next round tries again."""
    ttl = ttl_s if ttl_s is not None else float(os.environ.get("PATHIEL_OPEN_WINDOW_TTL_S", OPEN_WINDOW_TTL_S))
    key = (journal.strategies_dir(), desk_id)
    t = clock()
    hit = _OPEN_WINDOW_CACHE.get(key)
    if hit is not None and t - hit[0] < ttl:
        return hit[1]
    result: Optional[OpenWindowTrades] = None
    try:
        thesis = journal.latest_thesis(desk_id)
        if thesis is not None and get_thesis_trades(desk_id, thesis.id) is None:
            from services.accounts.view import list_all_accounts
            result = open_window_trades(thesis, desks.get_spec(desk_id), list_all_accounts(),
                                        fills_fetcher or default_fills_fetcher, now=now)
    except Exception:  # noqa: BLE001 - a read-side roll-up never takes the Desk page down
        result = None
    _OPEN_WINDOW_CACHE[key] = (t, result)
    return result


def clear_open_window_cache() -> None:
    _OPEN_WINDOW_CACHE.clear()


# ── TrackRecord enrichment ──────────────────────────────────────────────────

_UNSET: Any = object()


def _aggregate_trade_stats(desk_id: str, *, open_window: Any = _UNSET) -> Dict[str, Any]:
    """realized_pnl_usd/n_trades flattened across every account's
    AccountTradeGrade for every ThesisTrades row this desk has on disk, plus
    the open window (the latest thesis, summed live; pass `open_window` to
    skip the lookup). n_trades counts fills (not theses): "how many trades
    were placed following these calls." follow_rate is the plain mean of
    every account's own follow_rate across every closed window (unweighted
    by fill count -- one call that twenty fills chased badly should not drown
    out three calls that were each followed cleanly); the open window is left
    out of it because a follow rate needs the whole window."""
    all_accounts = [a for tt in list_thesis_trades(desk_id) for a in tt.accounts]
    ow = open_window_stats(desk_id) if open_window is _UNSET else open_window
    live_fills = ow.n_fills if ow is not None else 0
    live_pnl = ow.realized_pnl_usd if ow is not None else 0.0
    if not all_accounts and not live_fills:
        return {"n_trades": 0, "realized_pnl_usd": None, "follow_rate": None, "open_window": ow}
    follows = [a.follow_rate for a in all_accounts if a.follow_rate is not None]
    return {
        "n_trades": sum(a.n_fills for a in all_accounts) + live_fills,
        "realized_pnl_usd": round(sum(a.realized_pnl_usd for a in all_accounts) + live_pnl, 6),
        "follow_rate": (sum(follows) / len(follows)) if follows else None,
        "open_window": ow,
    }


def enrich_track_record(track: TrackRecord, desk_id: str, *, open_window: Any = _UNSET) -> TrackRecord:
    """A thesis-only TrackRecord (grader.aggregate_track_record /
    grader.track_record_for_desk), plus this desk's trade roll-up: closed
    windows from disk and the open window summed live."""
    return track.model_copy(update=_aggregate_trade_stats(desk_id, open_window=open_window))


def track_record_for_desk(desk_id: str) -> TrackRecord:
    """TrackRecord for one desk, thesis grading AND trade grading together --
    the one function facade.py calls so every caller (list_cards, get_brief,
    track_record, the desk_track_record MCP tool) sees the same combined
    number."""
    return enrich_track_record(grader.track_record_for_desk(desk_id), desk_id)
