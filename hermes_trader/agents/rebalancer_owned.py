"""Shared ownership tracker for cross-sectional rebalancers.

Each strategy book manages its own isolated set of coins. Without this, a book's close list
would include ALL live account positions — including those opened by the thought-engine or
other books — and destructively close foreign positions.

Usage pattern in each rebalancer's maybe_rebalance():
    owned = _get_owned()                        # module-level singleton, loaded from disk
    owned.prune(live_coin_set)                  # drop coins closed externally / stopped out
    cur_long, cur_short = owned.current_book()  # ONLY this rebalancer's positions
    plan = rebalance_plan(book, cur_long, cur_short)
    # execute plan ...
    for coin in plan["close_long"] + plan["close_short"]:
        close_fn(coin)
        owned.remove(coin)
    for coin in plan["open_long"]:
        execute_fn(analysis(coin, "long", ...))
        owned.add(coin, "long")
    for coin in plan["open_short"]:
        execute_fn(analysis(coin, "short", ...))
        owned.add(coin, "short")
    owned.save()

Invariant: close_long/close_short can ONLY contain coins in owned.longs/owned.shorts,
because cur_long/cur_short are derived exclusively from the owned set (intersected against
live positions). Foreign positions (opened by other strategies) are NEVER in cur_long/cur_short
and therefore never in close_long/close_short.

Cross-book claims registry (ClaimsRegistry)
==========================================
Each live strategy book can target ANY coin in the liquid universe. Because the exchange holds ONE
net position per coin, two books simultaneously holding the same coin either (a) net/cancel on
the exchange, or (b) when one book closes via close_position_market(coin), it closes the ENTIRE
net position — stomping on the other book's leg.

ClaimsRegistry is a file-backed map {coin -> owning_book_name} shared across all live strategy
modules. A book must CLAIM a coin before opening it; a coin can be claimed by AT MOST ONE book
at a time. Any book that is not the owner gets that coin filtered OUT of its candidate universe
before even ranking/planning. On close/prune the owning book releases its claim.

The claim file (.rebalancer_claims.json) uses best-effort reads/writes (same pattern as
other state files). The single-threaded trading loop means concurrent writes within one cycle
are not a risk, but we load-merge-save defensively to guard against mid-cycle restarts.

The claim registry is intentionally shared by the live EV+ books so one strategy cannot
silently net out or close another strategy's coin.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# State-file base dir: HERMES_STATE_DIR (tests/conftest.py points it at a temp dir so the suite
# never touches live state) else the project root. ALL rebalancer state files (timers, owned-position
# sets, the claims registry, vol-managed history) route through state_file() so they redirect together.
_STATE_DIR = os.environ.get("HERMES_STATE_DIR") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def state_file(name: str) -> str:
    """Absolute path for a rebalancer state file, under HERMES_STATE_DIR (tests) or the project root."""
    return os.path.join(_STATE_DIR, name)


# Cross-book claims registry path.
_CLAIMS_FILE = state_file(".rebalancer_claims.json")

# Only books that currently use ClaimsRegistry may persist claims in live mode.
# This prevents claims left behind by deleted strategy modules from blocking
# active EV+ books after a refactor or cleanup.
_ACTIVE_CLAIM_BOOKS = frozenset({"xs_momentum", "rally_exhaustion", "hail_mary_short"})


def active_claim_books() -> Set[str]:
    """Return the live strategy books allowed to own cross-book claims."""
    return set(_ACTIVE_CLAIM_BOOKS)


class ClaimsRegistry:
    """Cross-book claim registry: coin -> owning_book_name.

    Guarantees at most one factor book holds any given coin.  A book must call
    claim(coin, book) before opening a position and release(coin, book) when it
    closes or prunes that coin.  claimed_by_others(book) returns the set of coins
    locked by a *different* book — callers exclude these from their candidate
    universe before ranking.

    File format: {"claims": {"BTC": "xs_momentum", "ETH": "rally_exhaustion", ...}}

    All mutations are in-memory until save() is called.  save() and load() are
    best-effort: a failure is logged but never raises (same contract as
    OwnedPositions).  The trading loop is single-threaded so concurrent writes
    within one cycle are impossible; the load-merge-save pattern guards against
    a mid-cycle restart leaving a stale file.
    """

    def __init__(
        self,
        path: str = _CLAIMS_FILE,
        active_books: Optional[Set[str]] = None,
    ) -> None:
        self._path = path
        self._active_books = frozenset(active_books) if active_books is not None else None
        # coin -> owning book name
        self._claims: Dict[str, str] = {}
        self._loaded = False

    # ── Load / save ────────────────────────────────────────────────────────────

    def load(self) -> "ClaimsRegistry":
        """Load from disk; silently starts empty if file missing or corrupt."""
        if self._loaded:
            return self
        try:
            with open(self._path) as fh:
                data = json.load(fh)
            raw = data.get("claims") or {}
            self._claims = {str(k): str(v) for k, v in raw.items()}
            self._enforce_active_books(persist=True)
        except Exception:
            self._claims = {}
        self._loaded = True
        return self

    def save(self) -> None:
        """Persist current claims to disk (best-effort)."""
        try:
            with open(self._path, "w") as fh:
                json.dump({"claims": self._claims}, fh, sort_keys=True)
        except Exception as exc:
            logger.warning(f"[rebalancer_claims] could not save to {self._path}: {exc}")

    # ── Mutations ──────────────────────────────────────────────────────────────

    def claim(self, coin: str, book: str) -> bool:
        """Claim `coin` for `book`.

        Returns True if the claim was granted (coin was unclaimed or already owned
        by this book).  Returns False if the coin is claimed by a DIFFERENT book —
        the caller should skip this coin rather than open a conflicting position.
        """
        self._enforce_active_books(persist=True)
        if self._active_books is not None and book not in self._active_books:
            logger.warning(f"[rebalancer_claims] claim({coin}, {book}) denied: inactive book")
            return False
        existing = self._claims.get(coin)
        if existing is None or existing == book:
            self._claims[coin] = book
            return True
        logger.debug(
            f"[rebalancer_claims] claim({coin}, {book}) DENIED — already held by {existing}"
        )
        return False

    def release(self, coin: str, book: str) -> None:
        """Release the claim on `coin` (only if `book` currently owns it)."""
        self._enforce_active_books(persist=True)
        if self._claims.get(coin) == book:
            del self._claims[coin]

    def release_all(self, book: str) -> None:
        """Release every coin claimed by `book` (used on full re-plan / teardown)."""
        self._enforce_active_books(persist=True)
        to_drop = [c for c, b in self._claims.items() if b == book]
        for c in to_drop:
            del self._claims[c]

    def _enforce_active_books(self, persist: bool = False) -> Dict[str, str]:
        """Self-heal stale owners whenever the registry is used."""
        if self._active_books is None:
            return {}
        dropped = self.scrub_stale_owners(set(self._active_books))
        if dropped:
            logger.warning(
                "[rebalancer_claims] scrubbed stale owners: "
                + ", ".join(f"{coin}={owner}" for coin, owner in sorted(dropped.items()))
            )
            if persist:
                self.save()
        return dropped

    def scrub_stale_owners(self, active_books: Set[str]) -> Dict[str, str]:
        """Drop claims owned by books that are no longer active.

        Returns the removed {coin: owner} map so callers/tests can audit what was
        scrubbed. The mutation is in-memory until save() is called.
        """
        active = {str(b) for b in active_books}
        dropped = {c: b for c, b in self._claims.items() if b not in active}
        for coin in dropped:
            del self._claims[coin]
        return dropped

    def prune_to(self, live_coins: Set[str], book: str) -> None:
        """Drop this book's claims for any coin no longer in live positions.

        Mirrors OwnedPositions.prune() so the two stay consistent: when a coin is
        stopped out or externally closed, both the ownership tracker AND the claim
        registry are cleaned up together.
        """
        self._enforce_active_books(persist=True)
        to_drop = [c for c, b in self._claims.items() if b == book and c not in live_coins]
        for c in to_drop:
            del self._claims[c]

    # ── Queries ────────────────────────────────────────────────────────────────

    def claimed_by_others(self, book: str) -> Set[str]:
        """Return the set of coins claimed by any book OTHER than `book`.

        Callers subtract this from their candidate universe before ranking so two
        books can never target the same coin simultaneously.
        """
        self._enforce_active_books(persist=True)
        return {c for c, b in self._claims.items() if b != book}

    def owner_of(self, coin: str) -> Optional[str]:
        """Return the book that owns `coin`, or None if unclaimed."""
        self._enforce_active_books(persist=True)
        return self._claims.get(coin)

    def claims(self) -> Dict[str, str]:
        """Return a copy of the current claim map after self-healing stale owners."""
        self._enforce_active_books(persist=True)
        return dict(self._claims)


# Module-level singleton — shared across all factor live modules that import from here.
_claims_registry: Optional[ClaimsRegistry] = None


def get_claims_registry(path: str = _CLAIMS_FILE) -> ClaimsRegistry:
    """Return (and lazily load) the shared cross-book claims registry singleton."""
    global _claims_registry
    if _claims_registry is None:
        _claims_registry = ClaimsRegistry(path, active_books=active_claim_books())
    return _claims_registry.load()


class OwnedPositions:
    """Persist + query the set of coins this rebalancer currently holds.

    State file format:
        {"longs": ["BTC", "ETH"], "shorts": ["SOL", "MATIC"]}

    All mutations are in-memory until save() is called. save() is a best-effort
    write: a failure is logged but never raises (same pattern as the timer files).
    """

    def __init__(self, state_file: str) -> None:
        self._path = state_file
        self._longs: Set[str] = set()
        self._shorts: Set[str] = set()
        self._loaded = False

    # ── Load / save ────────────────────────────────────────────────────────────

    def load(self) -> "OwnedPositions":
        """Load from disk; silently starts empty if file missing or corrupt."""
        if self._loaded:
            return self
        try:
            with open(self._path) as fh:
                data = json.load(fh)
            self._longs = set(data.get("longs") or [])
            self._shorts = set(data.get("shorts") or [])
        except Exception:
            self._longs = set()
            self._shorts = set()
        self._loaded = True
        return self

    def save(self) -> None:
        """Persist current owned set to disk (best-effort)."""
        try:
            with open(self._path, "w") as fh:
                json.dump({"longs": sorted(self._longs), "shorts": sorted(self._shorts)}, fh)
        except Exception as exc:
            logger.warning(f"[rebalancer_owned] could not save state to {self._path}: {exc}")

    # ── Mutations ──────────────────────────────────────────────────────────────

    def add(self, coin: str, side: str) -> None:
        """Record that we opened `coin` on `side` ('long' or 'short')."""
        if side == "long":
            self._longs.add(coin)
            self._shorts.discard(coin)  # can't be both
        else:
            self._shorts.add(coin)
            self._longs.discard(coin)

    def remove(self, coin: str) -> None:
        """Record that we closed `coin` (remove from whichever side it was on)."""
        self._longs.discard(coin)
        self._shorts.discard(coin)

    def prune(self, live_coins: Set[str]) -> None:
        """Remove coins that are no longer open in the live account.

        This handles stops/liquidations/external closes: if a coin we thought we
        owned is no longer in the live positions, drop it from our tracked set so
        we don't phantom-close it next rebalance.
        """
        dropped_longs = self._longs - live_coins
        dropped_shorts = self._shorts - live_coins
        if dropped_longs or dropped_shorts:
            logger.info(
                f"[rebalancer_owned] {self._path}: pruning vanished coins "
                f"longs={sorted(dropped_longs)} shorts={sorted(dropped_shorts)}"
            )
        self._longs -= live_coins.__class__(dropped_longs)  # equivalent to -= dropped_longs
        self._shorts -= live_coins.__class__(dropped_shorts)

    # ── Queries ────────────────────────────────────────────────────────────────

    def current_book(self) -> Tuple[List[str], List[str]]:
        """Return (owned_longs, owned_shorts) — coins we opened that we track."""
        return sorted(self._longs), sorted(self._shorts)

    def filter_to_owned(self, positions) -> Tuple[List[str], List[str]]:
        """Derive cur_long/cur_short by intersecting live positions with our owned set.

        A coin counts as 'ours' only if:
        1. We have a record of opening it (it's in _longs or _shorts), AND
        2. It's still open in the live account (present in positions with szi != 0).

        This means even if the rebalancer's state file is stale (e.g. a coin was
        force-closed by the engine), we won't try to close it again.
        """
        live_longs: Set[str] = set()
        live_shorts: Set[str] = set()
        for p in positions or []:
            pos = p.get("position", p) if isinstance(p, dict) else {}
            coin = pos.get("coin")
            try:
                szi = float(pos.get("szi", 0) or 0)
            except (TypeError, ValueError):
                szi = 0.0
            if not coin or szi == 0:
                continue
            if szi > 0:
                live_longs.add(coin)
            else:
                live_shorts.add(coin)

        cur_long = sorted(self._longs & live_longs)
        cur_short = sorted(self._shorts & live_shorts)
        return cur_long, cur_short

    @property
    def longs(self) -> Set[str]:
        return frozenset(self._longs)

    @property
    def shorts(self) -> Set[str]:
        return frozenset(self._shorts)


def _live_coin_set(positions) -> Set[str]:
    """Extract the set of coins with nonzero size from a live positions list."""
    coins: Set[str] = set()
    for p in positions or []:
        pos = p.get("position", p) if isinstance(p, dict) else {}
        coin = pos.get("coin")
        try:
            szi = float(pos.get("szi", 0) or 0)
        except (TypeError, ValueError):
            szi = 0.0
        if coin and szi != 0:
            coins.add(coin)
    return coins


def prune_claims_to_live(positions, books: Optional[Set[str]] = None) -> Dict[str, str]:
    """Release claims whose coins are no longer open in the live account.

    Strategy modules also prune their own claims, but some are cadence-gated
    (for example xs_momentum rebalances every N days). This cycle-level scrub
    prevents vanished/stopped coins from blocking other live books until the
    owning strategy's next scheduled run.
    """
    live = _live_coin_set(positions)
    claims = get_claims_registry()
    before = claims.claims()
    target_books = {str(b) for b in (books if books is not None else active_claim_books())}
    for book in target_books:
        claims.prune_to(live, book)
    after = claims.claims()
    dropped = {coin: owner for coin, owner in before.items() if after.get(coin) != owner}
    if dropped:
        logger.info(
            "[rebalancer_claims] pruned non-live claims: "
            + ", ".join(f"{coin}={owner}" for coin, owner in sorted(dropped.items()))
        )
        claims.save()
    return dropped
