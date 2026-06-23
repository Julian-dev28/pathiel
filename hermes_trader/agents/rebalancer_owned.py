"""Shared ownership tracker for cross-sectional rebalancers.

Each rebalancer (xs_momentum, vol_dispersion, sortino, amihud) manages its own isolated
set of coins. Without this, a rebalancer's close list would include ALL live account positions
— including those opened by the thought-engine or other rebalancers — and destructively
close foreign positions.

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
"""
from __future__ import annotations

import json
import logging
import os
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


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
