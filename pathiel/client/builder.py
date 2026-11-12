"""Hyperliquid builder fee: whether an order carries one, decided from a cache.

A public install attaches a builder code ({"b": address, "f": tenths of a bp})
to every order it places, once the user has approved that fee with their master
key (scripts/approve_builder_fee.py). Hyperliquid rejects an order whose fee is
above the user's approval, so attaching blindly would lose orders.

The rule this module exists for (I2 in docs/overhaul/PUBLIC_PLUGIN_2026-11-12.md):
builder state never fails, blocks or delays an order.

  * builder_for_order() is CACHE ONLY. It never calls the network, the rate
    limiter or _fetch on the calling thread, under any state. A missing entry
    means "no builder on this order" and schedules a background read.
  * The read (fetch_max_builder_fee) is one plain requests.post with a (1 s,
    2 s) timeout, no adapter retries and no shared session, run on a daemon
    thread through _spawn. Tests replace _spawn with a recorder.
  * An approved entry is used for up to STALE_OK_S (24 h); a background
    refresh starts once it is older than REFRESH_APPROVED_S (10 min). A failed
    refresh keeps the last good value.
  * When the venue refuses an order that carried the builder,
    exchange._send_order resends it without one and calls note_rejection(),
    which drops the entry and turns the builder off for REJECT_COOLDOWN_S.

The operator's install never attaches: builder_for_order returns None before
it reads anything (reason operator_install), and exchange._order_builder does
not even call it there.

Nothing runs at import. hl_client and requests are imported inside functions.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Optional, Set, Tuple

import contracts.runtime as runtime

logger = logging.getLogger(__name__)

REFRESH_APPROVED_S = 600.0     # approved entry: refresh in the background after this
REFRESH_UNAPPROVED_S = 60.0    # approval below the fee: re-read after this
REFRESH_ERROR_S = 30.0         # last read failed: retry after this
STALE_OK_S = 86400.0           # an entry older than this is treated as missing
REJECT_COOLDOWN_S = 600.0      # after the venue refused a builder order

CONNECT_TIMEOUT_S = 1.0
READ_TIMEOUT_S = 2.0
LIMITER_MAX_WAIT_S = 1.0
HARD_DEADLINE_S = 4.0          # builder_status(refresh=True) waits at most this

REASONS: Dict[str, str] = {
    "operator_install": "operator install: no builder fee",
    "no_builder_address": "no builder address configured: orders carry no builder fee",
    "fee_zero": "builder fee is 0: orders carry no builder fee",
    "no_account": "no trading account configured",
    "self_trade": "the trading account is the builder address: no builder fee",
    "rejected_cooldown": "Hyperliquid refused an order with the builder fee; "
                         "orders go out without it for a few minutes",
    "cache_cold": "approval not read yet (reading it in the background): "
                  "this order carries no builder fee",
    "approval_read_failed": "could not read the approval from Hyperliquid: "
                            "this order carries no builder fee",
    "not_approved": "builder fee not approved (or approved below the fee): orders carry no "
                    "builder fee. Approve it once with scripts/approve_builder_fee.py",
    "approved": "approved: every order carries the builder fee",
}

_Key = Tuple[str, str, str]   # (network, account lowercase, builder lowercase)

_lock = threading.Lock()
# key -> {"value": Optional[int], "fetched_at": Optional[float],
#         "error": Optional[str], "error_at": Optional[float]}
_cache: Dict[_Key, Dict[str, Any]] = {}
_in_flight: Set[_Key] = set()
_cooldown_until: Dict[_Key, float] = {}
_last: Dict[str, Dict[str, Any]] = {}       # account lowercase -> last decision
_counters: Dict[str, int] = {"with_builder": 0, "without_builder": 0, "retried": 0, "refused": 0}
_tls = threading.local()

_clock: Callable[[], float] = time.time     # tests replace it with a fake clock


# ── the read ──────────────────────────────────────────────────────────────────

def fetch_max_builder_fee(user: str, builder: str) -> int:
    """The max builder fee `user` approved for `builder`, in tenths of a bp.

    One rate-limited, plain requests.post with a short timeout. Not the shared
    hl_client session (its adapter retries and wrap_session_retry can stretch a
    read to 18 s). Raises on a limiter refusal, a non-200 reply, or a body that
    is not an int."""
    import requests

    from pathiel.client import hl_client
    from pathiel.client.rate_limit import HL_LIMITER, endpoint_weight

    weight = endpoint_weight("maxBuilderFee")
    if not HL_LIMITER.acquire(weight, max_wait=LIMITER_MAX_WAIT_S, background=True,
                              label="maxBuilderFee<builder"):
        raise RuntimeError("rate budget exhausted for maxBuilderFee")
    resp = requests.post(f"{hl_client.HL_API}/info",
                         json={"type": "maxBuilderFee", "user": user, "builder": builder},
                         timeout=(CONNECT_TIMEOUT_S, READ_TIMEOUT_S))
    if resp.status_code != 200:
        raise RuntimeError(f"maxBuilderFee answered HTTP {resp.status_code}")
    body = resp.json()
    if type(body) is not int:      # bool is an int subclass; it is not an answer here
        raise ValueError(f"maxBuilderFee answered {body!r}, not an integer")
    return body


_fetch: Callable[[str, str], int] = fetch_max_builder_fee   # the refresh job calls this


def _spawn(job: Callable[[], None]) -> None:
    threading.Thread(target=job, daemon=True, name="pathiel-builder-refresh").start()


# ── config ────────────────────────────────────────────────────────────────────

def _config() -> Tuple[bool, str, int, str]:
    """(public, builder address, fee, network) read from the runtime contract now."""
    return (runtime.is_public(), runtime.builder_address(), runtime.builder_fee_tenths_bp(),
            runtime.network())


def _gate(account: Optional[str]) -> Tuple[Optional[str], Optional[_Key], str, int]:
    """(reason_code or None, cache key, builder address, fee). A reason code
    here means no builder and no read, whatever the cache holds."""
    public, address, fee, net = _config()
    acct = (account or "").strip().lower()
    if not public:
        return "operator_install", None, address, fee
    if not address:
        return "no_builder_address", None, address, fee
    if fee <= 0:
        return "fee_zero", None, address, fee
    if not acct:
        return "no_account", None, address, fee
    if acct == address:
        return "self_trade", None, address, fee
    return None, (net, acct, address), address, fee


# ── the cache ─────────────────────────────────────────────────────────────────

def _run_refresh(key: _Key) -> None:
    """Read the approval for `key` and store it. Clears the in-flight flag
    whatever happens. A failed read keeps the last good value."""
    _net, account, address = key
    try:
        try:
            value = _fetch(account, address)
            if type(value) is not int:
                raise ValueError(f"approval read returned {value!r}")
            with _lock:
                _cache[key] = {"value": value, "fetched_at": _clock(), "error": None,
                               "error_at": None}
        except Exception as e:   # noqa: BLE001 - a read failure is data, never a crash
            with _lock:
                entry = dict(_cache.get(key) or {"value": None, "fetched_at": None})
                entry["error"], entry["error_at"] = f"{type(e).__name__}: {e}", _clock()
                _cache[key] = entry
            logger.info(f"[builder] approval read failed for {account}: {type(e).__name__}: {e}")
    finally:
        with _lock:
            _in_flight.discard(key)


def _due(entry: Optional[Dict[str, Any]], fee: int, now: float) -> bool:
    if not entry:
        return True
    last = max(t for t in (entry.get("fetched_at"), entry.get("error_at"), float("-inf"))
               if t is not None)
    if entry.get("error") is not None:
        window = REFRESH_ERROR_S
    elif entry.get("value") is not None and entry["value"] >= fee:
        window = REFRESH_APPROVED_S
    else:
        window = REFRESH_UNAPPROVED_S
    return (now - last) >= window


def _schedule(key: _Key, fee: int, now: float) -> bool:
    """Start a background refresh for `key` when it is due and none is in
    flight. Never blocks, never raises."""
    try:
        with _lock:
            if key in _in_flight:
                return False
            until = _cooldown_until.get(key)
            if until is not None and now < until:
                return False
            if not _due(_cache.get(key), fee, now):
                return False
            _in_flight.add(key)
        try:
            _spawn(lambda: _run_refresh(key))
        except Exception as e:   # noqa: BLE001 - e.g. cannot start a thread at shutdown
            with _lock:
                _in_flight.discard(key)
            logger.info(f"[builder] could not start the approval read: {e}")
            return False
        return True
    except Exception:   # noqa: BLE001 - scheduling must never reach an order
        return False


def _decide(account: Optional[str], now: float) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any], Optional[_Key], int]:
    """Pure cache view: (builder dict or None, decision, key, fee). No I/O."""
    code, key, address, fee = _gate(account)
    decision: Dict[str, Any] = {"attached": False, "reason_code": code or "", "approved_tenths_bp": None,
                                "age_s": None}
    if code is not None or key is None:
        return None, decision, key, fee
    with _lock:
        until = _cooldown_until.get(key)
        if until is not None and now < until:
            decision["reason_code"] = "rejected_cooldown"
            return None, decision, key, fee
        entry = dict(_cache.get(key) or {})
    value, fetched_at = entry.get("value"), entry.get("fetched_at")
    if value is not None and fetched_at is not None and (now - fetched_at) <= STALE_OK_S:
        decision["approved_tenths_bp"] = value
        decision["age_s"] = round(now - fetched_at, 3)
        if value >= fee:
            decision["attached"] = True
            decision["reason_code"] = "approved"
            return {"b": address, "f": int(fee)}, decision, key, fee
        decision["reason_code"] = "not_approved"
        return None, decision, key, fee
    error_at = entry.get("error_at")
    if entry.get("error") is not None and error_at is not None and (now - error_at) <= STALE_OK_S:
        decision["reason_code"] = "approval_read_failed"
        decision["age_s"] = round(now - error_at, 3)
        return None, decision, key, fee
    decision["reason_code"] = "cache_cold"
    return None, decision, key, fee


def warm(account: Optional[str]) -> bool:
    """Schedule a background approval read when the entry is missing or due.
    False (and nothing done) on an operator install, with no builder address,
    a zero fee, no account, or account == builder. Never blocks, never raises."""
    try:
        code, key, _address, fee = _gate(account)
        if code is not None or key is None:
            return False
        return _schedule(key, fee, _clock())
    except Exception:   # noqa: BLE001
        return False


def builder_for_order(account: str, *, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """The builder dict for an order from `account`, or None. CACHE ONLY:
    no network, no limiter, no _fetch on this thread. Never raises.

    Returns a fresh {'b': lowercase address, 'f': int} each call (the SDK
    lowercases builder['b'] in place; key order and the int type are part of
    the signed hash)."""
    try:
        t = _clock() if now is None else float(now)
        result, decision, key, fee = _decide(account, t)
        if key is not None and decision["reason_code"] != "rejected_cooldown":
            _schedule(key, fee, t)
        _record(account, decision)
        if result is not None:
            logger.info(f"[builder] attached f={result['f']} b={result['b']} "
                        f"(approved {decision['approved_tenths_bp']})")
        else:
            logger.info(f"[builder] not attached: {decision['reason_code']}")
        return result
    except Exception as e:   # noqa: BLE001 - builder state never fails an order
        logger.info(f"[builder] not attached: internal error {type(e).__name__}: {e}")
        _record(account, {"attached": False, "reason_code": "approval_read_failed",
                          "approved_tenths_bp": None, "age_s": None})
        return None


def _record(account: Optional[str], decision: Dict[str, Any]) -> None:
    acct = (account or "").strip().lower()
    with _lock:
        _last[acct] = dict(decision)
    _tls.last = {**decision, "account": acct}


def last_decision(account: str) -> Dict[str, Any]:
    """The last builder_for_order decision for `account`."""
    with _lock:
        d = _last.get((account or "").strip().lower())
    if d is None:
        return {"attached": False, "reason_code": "none", "approved_tenths_bp": None, "age_s": None}
    return dict(d)


def thread_decision() -> Optional[Dict[str, Any]]:
    """The last decision made on this thread, with its 'account'. exchange
    reads it right after _order_builder() to build the tool note."""
    d = getattr(_tls, "last", None)
    return dict(d) if d else None


def clear_thread_decision() -> None:
    _tls.last = None


# ── status (for `state`, scripts and tool output) ─────────────────────────────

def builder_status(account: Optional[str], *, refresh: bool = False) -> Dict[str, Any]:
    """What a public user needs to see about the fee.

    refresh=False: a cache view; also calls warm(). Never does I/O here.
    refresh=True: one synchronous read on a worker thread, waiting at most
    HARD_DEADLINE_S. For scripts only, never on an order path."""
    public, address, fee, net = _config()
    code, key, _a, _f = _gate(account)
    timed_out = False
    if refresh and key is not None:
        with _lock:
            _in_flight.add(key)
        worker = threading.Thread(target=_run_refresh, args=(key,), daemon=True,
                                  name="pathiel-builder-status")
        worker.start()
        worker.join(HARD_DEADLINE_S)
        timed_out = worker.is_alive()
    else:
        warm(account)
    _result, decision, _k, _fee = _decide(account, _clock())
    if timed_out:
        decision = {**decision, "attached": False, "reason_code": "approval_read_failed"}
    reason_code = decision["reason_code"]
    with _lock:
        counters = dict(_counters)
    return {
        "enabled": bool(public and address and fee > 0),
        "address": address,
        "fee_tenths_bp": fee,
        "fee_rate": runtime.fee_rate_str(fee),
        "network": net,
        "approved_tenths_bp": decision.get("approved_tenths_bp"),
        "attaches": bool(decision.get("attached")),
        "reason_code": reason_code,
        "reason": REASONS.get(reason_code, reason_code),
        "counters": counters,
    }


# ── venue feedback ────────────────────────────────────────────────────────────

def is_builder_rejection(text: Any) -> bool:
    return "builder" in str(text).lower()


# Hyperliquid's own refusals that have nothing to do with the builder fee. A
# builder order refused with one of these is answered as is: resending it
# without the builder would only repeat the refusal, or, for an IOC that did
# not match, fire a second market order the caller never asked for. Any other
# wording (the venue's builder text could change) still gets the one resend,
# so an order is never lost to builder state. Lowercase substrings.
KNOWN_VENUE_ERRORS: tuple = (
    "could not immediately match",
    "insufficient margin",
    "invalid price",
    "invalid size",
    "minimum value",
    "reduce only order would increase position",
    "post only order would have immediately matched",
    "away from the reference price",
    "too many cumulative requests",
    "open interest",
)


def is_known_venue_error(text: Any) -> bool:
    """True when `text` is one of Hyperliquid's own refusals and does not name
    the builder."""
    low = str(text).lower()
    if "builder" in low:
        return False
    return any(k in low for k in KNOWN_VENUE_ERRORS)


def note_rejection(account: str, text: str) -> None:
    """The venue refused an order that carried the builder: drop the cached
    approval and send orders without a builder for REJECT_COOLDOWN_S."""
    try:
        code, key, _address, _fee = _gate(account)
        if key is None:
            return
        with _lock:
            _cache.pop(key, None)
            _cooldown_until[key] = _clock() + REJECT_COOLDOWN_S
        logger.warning(f"[builder] venue refused an order with the builder fee; off for "
                       f"{REJECT_COOLDOWN_S:g}s: {str(text)[:200]}")
    except Exception:   # noqa: BLE001
        pass


def count(kind: str) -> None:
    with _lock:
        _counters[kind] = _counters.get(kind, 0) + 1


def reset() -> None:
    """Forget everything (tests)."""
    global _tls
    with _lock:
        _cache.clear()
        _in_flight.clear()
        _cooldown_until.clear()
        _last.clear()
        for k in list(_counters):
            _counters[k] = 0
        _counters.update({"with_builder": 0, "without_builder": 0, "retried": 0})
    _tls = threading.local()
