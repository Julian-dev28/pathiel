"""Order ownership: the desk must never close, cancel, re-bracket, set_stop
or set_target a position or order it did not place itself.

2026-11-17 23:25Z: a desk tick found the operator's own hand-placed
xyz:BRENTOIL long (14.66 @ 90.7165, placed in the Hyperliquid UI), saw
`sweep_brackets` report it "NOT FULLY COVERED", and called `close_position`
on it -- net ~+0.39, but it could as easily have been a loss, and it was
never the desk's trade to close. Until this module the only thing stopping
a repeat was a text note pasted into every tick prompt. This is the
structural version.

Two ways an order traces to the desk, in order of trust:

  1. cloid. Every order pathiel places carries a Hyperliquid client order id
     with a fixed pathiel prefix (`CLOID_PREFIX`), so ownership is readable
     straight off the venue: a fill's own `cloid` field says who placed the
     order behind it, with no local state to lose, survive a redeploy, or
     get out of sync with what the venue actually did.
  2. the journal (JOURNAL_FILE). Some fills never echo a cloid back on the
     venue's own read (every order placed before this module existed, and
     possibly others we have not seen yet) -- the fallback for those is a
     local, append-only record of every entry the desk itself placed,
     written at placement time (`record_desk_order`), matched back by `oid`.

A position is desk-owned only when EVERY fill that built its CURRENTLY open
size traces to one of the two (`is_desk_owned`). One un-tagged,
un-journalled opening fill is enough to call the whole position NOT
desk-owned -- a mixed position (part desk, part operator) is refused like a
pure operator one, because the alternative is guessing which part is ours.
A position this module cannot explain at all (no fills reach back to the
last time it was flat) is also NOT desk-owned: this never assumes a
position is ours for lack of evidence.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from pathiel.agents.atomic_io import append_json_line

# "path" in ASCII hex (p=0x70 a=0x61 t=0x74 h=0x68): a fixed, grep-able,
# human-readable prefix. Hyperliquid's Cloid is exactly 0x + 32 hex chars (16
# bytes); the remaining 24 hex chars (12 bytes) are per-order randomness so
# two orders never collide.
CLOID_PREFIX = "70617468"
CLOID_HEX_LEN = 32
_RANDOM_BYTES = (CLOID_HEX_LEN - len(CLOID_PREFIX)) // 2

JOURNAL_FILE = "ownership_journal.jsonl"
# PATHIEL_STATE_DIR read fresh on every call (not frozen at import) -- the
# same discipline `services/strategies/journal.py` and
# `pathiel.agents.capital_flows` use, so a test can point it at a tmp dir
# with a plain `monkeypatch.setenv` and get real per-test isolation.
# `pathiel.state.state_file()` is frozen to one path for the whole pytest
# session (tests/conftest.py sets it before collection) and is the wrong
# tool for a file tests write to repeatedly.
_FALLBACK_STATE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".state")

# How far back fills are read to explain a CURRENTLY open position. A
# position older than this with no flat in between reports as not
# desk-owned (refuse, never guess) -- raise this only if a desk legitimately
# holds one position longer than this without ever going flat.
DEFAULT_LOOKBACK_DAYS = 45.0


# ── the cloid tag ────────────────────────────────────────────────────────────

def make_cloid() -> str:
    """A fresh pathiel-tagged Hyperliquid client order id: 0x + CLOID_PREFIX
    + random hex, 32 hex chars total (what `hyperliquid.utils.types.Cloid`
    requires). A different value every call -- callers needing the SAME
    cloid across sibling legs must call this once and reuse it."""
    return f"0x{CLOID_PREFIX}{secrets.token_hex(_RANDOM_BYTES)}"


def is_pathiel_cloid(cloid: Optional[Any]) -> bool:
    """True iff `cloid` (a string, a hyperliquid Cloid, or anything with a
    sane str()) carries the pathiel prefix. False for None/empty/anything
    else -- including Hyperliquid's own all-zero "no cloid" sentinel."""
    if not cloid:
        return False
    s = str(cloid).strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    return s.startswith(CLOID_PREFIX.lower()) and len(s) == CLOID_HEX_LEN


# ── the journal (the fallback) ──────────────────────────────────────────────

def _state_dir() -> str:
    return os.environ.get("PATHIEL_STATE_DIR") or _FALLBACK_STATE_DIR


def journal_path() -> str:
    return os.path.join(_state_dir(), JOURNAL_FILE)


def record_desk_order(coin: str, cloid: str, kind: str, *, oid: Optional[int] = None,
                      side: Optional[str] = None, size: Optional[float] = None,
                      px: Optional[float] = None, ts: Optional[str] = None) -> None:
    """Append one desk-placed order to the local journal. `kind` is "entry",
    "topup", "sl" or "tp"; only entries and topups matter to
    `is_desk_owned` (they are what opens a position), but every kind is
    recorded so the journal is a complete, auditable order log. Durable
    (fsync'd) by `atomic_io.append_json_line`: the caller's next act is
    usually to trade against what it just recorded."""
    row = {"ts": ts or datetime.now(timezone.utc).isoformat(), "coin": coin, "cloid": cloid,
           "kind": kind, "oid": int(oid) if oid is not None else None,
           "side": side, "size": size, "px": px}
    append_json_line(journal_path(), row)


def load_journal(path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every journal row, file order. A torn last line (a crash mid-write)
    is skipped, not fatal -- same discipline as services/review/ledger.py's
    read_ledger."""
    p = path or journal_path()
    if not os.path.exists(p):
        return []
    out: List[Dict[str, Any]] = []
    with open(p) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                out.append(row)
    return out


def journal_oids(journal: Optional[Sequence[Dict[str, Any]]] = None) -> set:
    """Every oid the journal has recorded, as ints. Reads the journal file
    when `journal` is not given."""
    rows = journal if journal is not None else load_journal()
    out: set = set()
    for row in rows:
        oid = row.get("oid")
        if oid is None:
            continue
        try:
            out.add(int(oid))
        except (TypeError, ValueError):
            continue
    return out


# ── pure: which fills built a position's current open size ─────────────────

def _signed(f: Dict[str, Any]) -> Optional[float]:
    try:
        sz = float(f.get("sz") or 0)
    except (TypeError, ValueError):
        return None
    return sz if f.get("side") == "B" else -sz


def opening_fills(position: Dict[str, Any], fills: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The fills, oldest first, that built `position`'s CURRENTLY open size:
    every fill on `position["coin"]` since the last time that coin's
    position was exactly flat, replayed in fill order. Pure, and
    conservative by construction: `fills` must already reach back to the
    last flat point, or this returns a chain that does not cover the whole
    position -- `is_desk_owned` below treats that (and an empty chain) as
    NOT desk-owned, never as "probably ours". Fill side "B" is a buy, any
    other value a sell (matches Hyperliquid's userFills shape)."""
    coin = position.get("coin")
    if not coin:
        return []
    rows = sorted(
        (f for f in fills if f.get("coin") == coin),
        key=lambda f: (int(f.get("time") or 0), str(f.get("oid") or ""), str(f.get("tid") or "")),
    )
    pos = 0.0
    chain: List[Dict[str, Any]] = []
    for f in rows:
        signed = _signed(f)
        if signed is None:
            continue
        before, after = pos, pos + signed
        if abs(after) > abs(before) + 1e-9:
            chain.append(f)
        pos = after
        if abs(pos) < 1e-9:
            chain = []
    return chain


def _position_size(position: Dict[str, Any]) -> Optional[float]:
    for key in ("szi", "size"):
        if key in position and position[key] is not None:
            try:
                return float(position[key])
            except (TypeError, ValueError):
                continue
    return None


def is_desk_owned(position: Dict[str, Any], fills: Sequence[Dict[str, Any]],
                  journal: Optional[Sequence[Dict[str, Any]]] = None) -> bool:
    """True only when EVERY fill that opened `position`'s current size
    traces to a pathiel cloid (`is_pathiel_cloid`) or the journal (by oid).
    Pure given `fills` and `journal` (`load_journal()` is the live default
    everywhere else in this module calls it, not here).

    False when:
      - the position is flat (nothing to own),
      - no opening fill is found at all (an empty or too-short `fills`),
      - the opening fills found do not add up to the position's own size
        (`position["szi"]`/`["size"]`, when given) -- the chain is
        incomplete, so this refuses to vouch for it,
      - at least one opening fill carries neither a pathiel cloid nor a
        journalled oid (a mixed position, or a pure operator one).
    """
    size = _position_size(position)
    if size is not None and abs(size) < 1e-9:
        return False
    chain = opening_fills(position, fills)
    if not chain:
        return False
    if size is not None:
        net = sum(s for s in (_signed(f) for f in chain) if s is not None)
        if abs(abs(net) - abs(size)) > max(1e-6, abs(size) * 1e-4):
            return False
    oids = journal_oids(journal)
    for f in chain:
        if is_pathiel_cloid(f.get("cloid")):
            continue
        oid = f.get("oid")
        try:
            if oid is not None and int(oid) in oids:
                continue
        except (TypeError, ValueError):
            pass
        return False
    return True


# ── impure: the live check (one position read, one fills read) ─────────────

def _coin_position(user: str, coin: str, fetch_state: Any = None) -> Optional[Dict[str, Any]]:
    """{"coin", "szi", "entry_px", "leverage"} for the open position on
    `coin` (entry_px/leverage None when flat), or None when the read
    failed. Fresh, never the cached account_snapshot -- a trading decision
    never reads a stale snapshot (same discipline as
    pathiel.agents.bracket.close_position)."""
    from pathiel.client.hl_client import fetch_account_state
    fetch_state = fetch_state or fetch_account_state
    try:
        state = fetch_state(user, include_hip3=True)
    except Exception:
        return None
    if not isinstance(state, dict):
        return None
    for row in state.get("asset_positions") or []:
        p = (row or {}).get("position") or {}
        if p.get("coin") != coin:
            continue
        try:
            szi = float(p.get("szi") or 0)
        except (TypeError, ValueError):
            return None
        try:
            entry_px = float(p.get("entryPx") or 0) or None
        except (TypeError, ValueError):
            entry_px = None
        lev = p.get("leverage")
        lev_val = lev.get("value") if isinstance(lev, dict) else lev
        try:
            leverage = int(lev_val) if lev_val else None
        except (TypeError, ValueError):
            leverage = None
        return {"coin": coin, "szi": szi, "entry_px": entry_px, "leverage": leverage}
    return {"coin": coin, "szi": 0.0, "entry_px": None, "leverage": None}


def check_position_ownership(coin: str, *, user: Optional[str] = None,
                             lookback_days: float = DEFAULT_LOOKBACK_DAYS,
                             fetch_state: Any = None, fetch_fills: Any = None,
                             journal: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Live ownership check for the account's open position on `coin`.

    Returns {"coin", "flat", "desk_owned", "size", "entry_px", "leverage"}
    and, on a read failure, "read_failed": True. `flat` True means there is
    nothing to protect or refuse on -- every call site in this module's
    callers treats a flat coin as "proceed", never as "refuse".

    Network: one clearinghouseState read and one paged userFillsByTime read,
    both public, no key -- the same reads `services/strategies/evals/
    loss_audit.py` already makes to grade closed trades. Never raises: a
    read failure answers `desk_owned: False` ("read_failed": True) --
    refuse, don't guess.
    """
    from pathiel.client.hl_client import resolve_user_address
    user = user or resolve_user_address()
    if not user:
        return {"coin": coin, "flat": True, "desk_owned": True, "size": 0.0,
                "entry_px": None, "leverage": None, "read_failed": True}
    pos = _coin_position(user, coin, fetch_state=fetch_state)
    if pos is None:
        return {"coin": coin, "flat": False, "desk_owned": False, "size": None,
                "entry_px": None, "leverage": None, "read_failed": True}
    if abs(pos["szi"]) < 1e-9:
        return {"coin": coin, "flat": True, "desk_owned": True, "size": 0.0,
                "entry_px": None, "leverage": None}
    try:
        from pathiel.client.hl_client import fetch_user_fills_by_time
        fetch_fills = fetch_fills or fetch_user_fills_by_time
        start_ms = int((time.time() - lookback_days * 86400.0) * 1000)
        fills = fetch_fills(user, start_ms)
    except Exception:
        fills = []
    owned = is_desk_owned({"coin": coin, "szi": pos["szi"]}, fills,
                          journal if journal is not None else load_journal())
    return {"coin": coin, "flat": False, "desk_owned": owned, "size": pos["szi"],
            "entry_px": pos["entry_px"], "leverage": pos["leverage"]}
