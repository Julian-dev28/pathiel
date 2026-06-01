"""DSL (Dynamic Stop-Loss) exit engine.

Manages exit logic for open positions with a two-phase design:

  Phase 1 — Loss protection from entry until price moves up `protect_pct`.
  Phase 2 — Profit locking with tiered retrace thresholds once PnL is positive.

Unlike a plain SL order, DSL trails upward as price rises and only exits
when the mark price breaches the computed floor.

Phase 1 (Loss protection):
  - max_loss_pct below entry → hard stop
  - protect_pct above entry → transition to Phase 2
  - min(profit_floor, entry - max_loss)

Phase 2 (Profit locking):
  - trailing floor at entry + (peak - entry) * (1 - retrace_pct)
  - retrace_pct increases with unrealized profit (tiers)
  - hard_timeout after entry → emergency exit

Usage:
    dsl = ExitPolicy(max_loss_pct=3.0, protect_pct=1.5, ...)
    verdict = dsl.check(position_entry_price, current_mark_price, entry_time)
    if verdict.exit:
        close_position(reason=verdict.reason)
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

# Persist tracker state so a daemon restart doesn't lose peak/floor ratchets.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DSL_STATE_FILE = os.environ.get(
    "HERMES_DSL_STATE_FILE",
    os.path.join(_REPO_ROOT, ".dsl-state.json"),
)
_STATE_VERSION = 1


@dataclass
class RetraceTier:
    """A profit tier with its own retrace threshold.

    Example: when price is 10% above entry, retrace threshold is 30% —
    so the floor trails at entry + (peak - entry) * (1 - 0.30).
    """
    pct_above_entry: float  # Min profit % above entry to activate this tier
    retrace_threshold: float  # Fraction of peak profit to give back (0-1)


@dataclass
class ExitVerdict:
    """Result of a DSL floor check."""
    exit: bool = False
    reason: str = ""
    floor_price: Optional[float] = None
    peak_price: Optional[float] = None
    phase: str = ""  # "phase1" or "phase2"
    unrealized_pct: float = 0.0  # spot price-move %, not leveraged
    coin: str = ""
    position_side: str = ""  # "long" or "short" (distinct from `phase`)
    leverage: int = 1


@dataclass
class ExitPolicy:
    """DSL exit policy configuration.

    Tuning profiles:
      Conservative: max_loss_pct=5, retrace=10, protect=3, hard_timeout=360min
      Moderate:     max_loss_pct=2.5, retrace=7, protect=1.5, hard_timeout=180min
      Aggressive:   max_loss_pct=1.5, retrace=5, protect=0.8, hard_timeout=90min
    """
    max_loss_pct: float = 2.5  # Max loss SPOT % below entry (hard stop)
    # Max loss ROE (margin) % — leverage-aware safety net. At 40x leverage a
    # 2.5% spot move = 100% ROE = entire margin gone, so the spot threshold
    # alone is meaningless on high-lev trades. The effective floor used at
    # check time is min(max_loss_pct, max_loss_roe_pct / leverage). Set to a
    # high value (e.g. 100) to disable the leveraged cap and use spot only.
    max_loss_roe_pct: float = 50.0
    protect_pct: float = 1.5  # Price must rise this % above entry before Phase 2
    retrace_threshold: float = 0.30  # Give back 30% of peak profit (Phase 2 default)
    hard_timeout_minutes: float = 180.0  # Emergency exit after this long
    # ── Breakeven ratchet (guaranteed-profit lock) ──────────────────────
    # Once a position's PEAK profit clears `breakeven_trigger_pct` (spot %),
    # the trailing floor may never fall below `breakeven_lock_pct` (spot %)
    # above entry. This is strictly risk-REDUCING on the upside: it only ever
    # raises the floor (for longs), never touches max_loss, and never affects
    # a position that hasn't yet reached the arm threshold. It targets the
    # documented leak where medium winners (peak +2–4%) round-trip back to
    # ~flat before the retrace floor catches them. Disabled when trigger<=0.
    breakeven_trigger_pct: float = 0.0  # Peak spot % that arms the lock (0 = off)
    breakeven_lock_pct: float = 0.0     # Floor spot % above entry once armed
    phase2_tiers: List[RetraceTier] = field(default_factory=lambda: [
        RetraceTier(5.0, 0.30),   # 5% profit → give back 30%
        RetraceTier(10.0, 0.40),  # 10% profit → lock tighter, give back 40%
        RetraceTier(20.0, 0.50),  # 20% profit → lock even tighter
        RetraceTier(50.0, 0.60),  # 50% profit → lock most profit
    ])
    consecutive_breaches_required: int = 1  # Number of consecutive floor breaches before exit


class DSLTracker:
    """Tracks DSL state for a single open position.

    Must be called on every price tick (e.g., from the scan loop's WS mids).
    """
    def __init__(self, coin: str, side: str, entry_px: float,
                 entry_time: float, policy: Optional[ExitPolicy] = None,
                 leverage: int = 1) -> None:
        self.coin = coin
        self.side = side  # "long" | "short"
        self.entry_px = entry_px
        self.entry_time = entry_time
        self.policy = policy or ExitPolicy()
        self.leverage = int(leverage) if leverage else 1

        # State
        self.peak_px = entry_px
        self.consecutive_breaches = 0
        self._last_floor: Optional[float] = None

    def is_long(self) -> bool:
        return self.side == "long"

    def _unrealized_pct(self, mark_px: float) -> float:
        if self.is_long():
            return (mark_px - self.entry_px) / self.entry_px * 100
        return (self.entry_px - mark_px) / self.entry_px * 100

    def _verdict(self, **kwargs: Any) -> ExitVerdict:
        """ExitVerdict pre-filled with coin/side/leverage so callers don't repeat."""
        return ExitVerdict(coin=self.coin, position_side=self.side,
                           leverage=self.leverage, **kwargs)

    def _active_tier(self, mark_px: float) -> RetraceTier:
        """Find the highest active retrace tier based on current profit."""
        upct = self._unrealized_pct(mark_px)
        active = RetraceTier(0.0, self.policy.retrace_threshold)  # default
        for tier in self.policy.phase2_tiers:
            if upct >= tier.pct_above_entry:
                active = tier
        return active

    def check(self, mark_px: float) -> ExitVerdict:
        """Evaluate DSL floor against current mark price. Call on every tick."""
        elapsed_min = (time.time() - self.entry_time) / 60
        upct = self._unrealized_pct(mark_px)
        is_long = self.is_long()
        pol = self.policy

        # Update peak (for longs: highest price seen; for shorts: lowest)
        peak_changed = False
        if is_long and mark_px > self.peak_px:
            self.peak_px = mark_px
            peak_changed = True
        elif not is_long and mark_px < self.peak_px:
            self.peak_px = mark_px
            peak_changed = True

        # ── Effective max-loss in SPOT % terms ───────────────────────
        # Two thresholds combine into one effective floor:
        #   * `max_loss_pct`        — direct spot-% cap (e.g. 2.5%)
        #   * `max_loss_roe_pct`    — ROE/margin cap, divided by leverage
        # At 40x leverage, max_loss_roe_pct=50 → 1.25% spot cap, which is
        # MUCH tighter than max_loss_pct=2.5. The min() takes whichever
        # fires first. Without this leverage-aware check, a 40x BTC long
        # would happily lose 100% ROE before the 2.5% spot trigger.
        lev = max(1, self.leverage)
        effective_max_loss = min(pol.max_loss_pct, pol.max_loss_roe_pct / lev)
        # Reason string surfaces both inputs so it's obvious post-hoc
        # which cap was binding for a given exit.

        # ── Hard timeout ──────────────────────────────────────────────
        if elapsed_min >= pol.hard_timeout_minutes:
            return self._verdict(
                exit=True, reason=f"hard_timeout ({elapsed_min:.0f}min)",
                floor_price=None, peak_price=self.peak_px, phase="timeout",
                unrealized_pct=upct,
            )

        # ── Compute floor ───────────────────────────────────────────
        # Floor only moves UP (for longs) — once it rises above entry,
        # it never falls back. This prevents giving back locked profit.
        if is_long:
            profit_pct = (mark_px - self.entry_px) / self.entry_px * 100
            loss_pct = (self.entry_px - mark_px) / self.entry_px * 100

            # Max loss check (uses leverage-aware effective floor)
            if loss_pct >= effective_max_loss:
                roe_loss = loss_pct * lev
                return self._verdict(
                    exit=True,
                    reason=(f"max_loss ({loss_pct:.2f}% spot / {roe_loss:.1f}% ROE "
                            f">= {effective_max_loss:.2f}% spot cap; "
                            f"spot_cap={pol.max_loss_pct}, roe_cap={pol.max_loss_roe_pct}/{lev}x)"),
                    floor_price=self.entry_px * (1 - effective_max_loss / 100),
                    peak_price=self.peak_px, phase="phase1", unrealized_pct=upct,
                )

            if profit_pct >= pol.protect_pct:
                # Phase 2: floor = entry + profit_range * (1 - retrace)
                tier = self._active_tier(self.peak_px)  # Use PEAK for tier, not current
                profit_range = self.peak_px - self.entry_px
                floor = self.entry_px + profit_range * (1 - tier.retrace_threshold)
            else:
                # Phase 1: floor at effective max loss
                floor = self.entry_px * (1 - effective_max_loss / 100)
        else:
            # Short side
            profit_pct = (self.entry_px - mark_px) / self.entry_px * 100
            loss_pct = (mark_px - self.entry_px) / self.entry_px * 100

            if loss_pct >= effective_max_loss:
                roe_loss = loss_pct * lev
                return self._verdict(
                    exit=True,
                    reason=(f"max_loss ({loss_pct:.2f}% spot / {roe_loss:.1f}% ROE "
                            f">= {effective_max_loss:.2f}% spot cap; "
                            f"spot_cap={pol.max_loss_pct}, roe_cap={pol.max_loss_roe_pct}/{lev}x)"),
                    floor_price=self.entry_px * (1 + effective_max_loss / 100),
                    peak_price=self.peak_px, phase="phase1", unrealized_pct=upct,
                )

            if profit_pct >= pol.protect_pct:
                tier = self._active_tier(self.peak_px)
                profit_range = self.entry_px - self.peak_px
                floor = self.entry_px - profit_range * (1 - tier.retrace_threshold)
            else:
                floor = self.entry_px * (1 + effective_max_loss / 100)

        # ── Breakeven ratchet ─────────────────────────────────────────
        # Once PEAK profit has cleared the arm threshold, clamp the floor to a
        # locked-in gain so the position can't round-trip back to flat. Uses
        # PEAK (not current) so a dip after a high doesn't disarm it. Long-only
        # raises the floor; short-only lowers it — never loosens either side.
        if pol.breakeven_trigger_pct > 0:
            if is_long:
                peak_profit_pct = (self.peak_px - self.entry_px) / self.entry_px * 100
                if peak_profit_pct >= pol.breakeven_trigger_pct:
                    floor = max(floor, self.entry_px * (1 + pol.breakeven_lock_pct / 100))
            else:
                peak_profit_pct = (self.entry_px - self.peak_px) / self.entry_px * 100
                if peak_profit_pct >= pol.breakeven_trigger_pct:
                    floor = min(floor, self.entry_px * (1 - pol.breakeven_lock_pct / 100))

        # Floor should never decrease for longs (or increase for shorts)
        prev_floor = self._last_floor
        if prev_floor is not None:
            if is_long:
                floor = max(floor, prev_floor)
            else:
                floor = min(floor, prev_floor)

        self._last_floor = floor
        if peak_changed or prev_floor != floor:
            _save_state()

        # ── Floor breach check ────────────────────────────────────────
        breached = (is_long and mark_px < floor) or (not is_long and mark_px > floor)
        if breached:
            self.consecutive_breaches += 1
            if self.consecutive_breaches >= pol.consecutive_breaches_required:
                return self._verdict(
                    exit=True,
                    reason=f"floor_breach ({self.consecutive_breaches}x consec, floor={floor:.2f})",
                    floor_price=floor, peak_price=self.peak_px,
                    phase="phase2" if self._unrealized_pct(mark_px) >= pol.protect_pct else "phase1",
                    unrealized_pct=upct,
                )
        else:
            self.consecutive_breaches = 0

        return self._verdict(
            exit=False, reason="", floor_price=self._last_floor,
            peak_price=self.peak_px,
            phase="phase2" if self._unrealized_pct(mark_px) >= pol.protect_pct else "phase1",
            unrealized_pct=upct,
        )

    def status(self, mark_px: float) -> Dict[str, Any]:
        """Return current DSL status dict (for logging/MCP)."""
        verdict = self.check(mark_px)
        return {
            "coin": self.coin,
            "side": self.side,
            "entry_px": self.entry_px,
            "mark_px": mark_px,
            "peak_px": verdict.peak_price,
            "floor_px": verdict.floor_price,
            "unrealized_pct": round(verdict.unrealized_pct, 2),
            "phase": verdict.phase,
            "consecutive_breaches": self.consecutive_breaches,
            "exit": verdict.exit,
            "exit_reason": verdict.reason,
        }


# ── Global tracker registry ──────────────────────────────────────────

_active_positions: Dict[str, DSLTracker] = {}
_loaded_from_disk = False


def _tracker_to_dict(t: DSLTracker) -> Dict[str, Any]:
    return {
        "coin": t.coin,
        "side": t.side,
        "leverage": t.leverage,
        "entry_px": t.entry_px,
        "entry_time": t.entry_time,
        "peak_px": t.peak_px,
        "consecutive_breaches": t.consecutive_breaches,
        "last_floor": t._last_floor,
        "policy": asdict(t.policy),
    }


def _tracker_from_dict(d: Dict[str, Any]) -> DSLTracker:
    pol_raw = d.get("policy") or {}
    tiers = [RetraceTier(**rt) for rt in pol_raw.get("phase2_tiers", [])]
    policy = ExitPolicy(
        max_loss_pct=pol_raw.get("max_loss_pct", ExitPolicy.max_loss_pct),
        max_loss_roe_pct=pol_raw.get("max_loss_roe_pct", ExitPolicy.max_loss_roe_pct),
        protect_pct=pol_raw.get("protect_pct", ExitPolicy.protect_pct),
        retrace_threshold=pol_raw.get("retrace_threshold", ExitPolicy.retrace_threshold),
        hard_timeout_minutes=pol_raw.get("hard_timeout_minutes", ExitPolicy.hard_timeout_minutes),
        phase2_tiers=tiers if tiers else ExitPolicy().phase2_tiers,
        consecutive_breaches_required=pol_raw.get("consecutive_breaches_required", 1),
        breakeven_trigger_pct=pol_raw.get("breakeven_trigger_pct", ExitPolicy.breakeven_trigger_pct),
        breakeven_lock_pct=pol_raw.get("breakeven_lock_pct", ExitPolicy.breakeven_lock_pct),
    )
    t = DSLTracker(d["coin"], d["side"], float(d["entry_px"]),
                   float(d.get("entry_time") or time.time()), policy,
                   leverage=int(d.get("leverage", 1) or 1))
    t.peak_px = float(d.get("peak_px", d["entry_px"]))
    t.consecutive_breaches = int(d.get("consecutive_breaches", 0))
    lf = d.get("last_floor")
    t._last_floor = float(lf) if lf is not None else None
    return t


def _save_state() -> None:
    """Atomically write the tracker registry to disk. Best-effort — never raises."""
    try:
        payload = {
            "version": _STATE_VERSION,
            "saved_at": int(time.time() * 1000),
            "positions": [_tracker_to_dict(t) for t in _active_positions.values()],
        }
        tmp = DSL_STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f)
        os.replace(tmp, DSL_STATE_FILE)
    except OSError as e:
        logger.warning(f"[dsl] failed to persist state: {e}")


def load_state(force: bool = False) -> None:
    """Load persisted trackers into `_active_positions`.

    Idempotent by default (skips after the first call in a process). Pass
    `force=True` from read-only consumers like the web dashboard that need to
    pick up the latest disk state on every request — the trading loop is in a
    different process and writes through the same file.
    """
    global _loaded_from_disk
    if _loaded_from_disk and not force:
        return
    _loaded_from_disk = True
    try:
        with open(DSL_STATE_FILE) as f:
            payload = json.load(f)
    except FileNotFoundError:
        return
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"[dsl] state file unreadable, ignoring: {e}")
        return
    if force:
        _active_positions.clear()
    for d in payload.get("positions", []):
        try:
            t = _tracker_from_dict(d)
            _active_positions[f"{t.coin}_{t.side}"] = t
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"[dsl] skipping malformed tracker entry: {e}")
    if not force:
        logger.info(f"[dsl] rehydrated {len(_active_positions)} tracker(s) from disk")


def register_position(coin: str, side: str, entry_px: float,
                      entry_time: Optional[float] = None,
                      policy: Optional[ExitPolicy] = None,
                      leverage: int = 1) -> DSLTracker:
    """Register a new position for DSL tracking."""
    key = f"{coin}_{side}"
    tracker = DSLTracker(coin, side, entry_px, entry_time or time.time(), policy,
                         leverage=leverage)
    _active_positions[key] = tracker
    _save_state()
    logger.info(f"[dsl] Registered {key} @ {entry_px} ({leverage}x)")
    return tracker


def deregister_position(coin: str, side: str) -> bool:
    """Remove a tracker (after a successful close). Returns True if removed."""
    key = f"{coin}_{side}"
    if key in _active_positions:
        del _active_positions[key]
        _save_state()
        logger.info(f"[dsl] Deregistered {key}")
        return True
    return False


def _policy_from_config() -> ExitPolicy:
    """Build an ExitPolicy from the live .agent-config.json dsl_exit block.

    Mirrors the executor's entry-time policy construction so a SYNTHESIZED
    tracker (post-blackout reconcile) gets the SAME stops a fresh entry would,
    instead of the looser ExitPolicy() class defaults. Lazy import avoids a
    config_store <-> dsl_exit import cycle. Falls back to class defaults if the
    config can't be read."""
    try:
        from hermes_trader.agents.config_store import read_agent_config
        dsl = read_agent_config().get("dsl_exit", {}) or {}
        return ExitPolicy(
            max_loss_pct=dsl.get("max_loss_pct", ExitPolicy.max_loss_pct),
            max_loss_roe_pct=dsl.get("max_loss_roe_pct", ExitPolicy.max_loss_roe_pct),
            protect_pct=dsl.get("protect_pct", ExitPolicy.protect_pct),
            retrace_threshold=dsl.get("retrace_threshold", ExitPolicy.retrace_threshold),
            hard_timeout_minutes=dsl.get("hard_timeout_minutes", ExitPolicy.hard_timeout_minutes),
            breakeven_trigger_pct=dsl.get("breakeven_trigger_pct", ExitPolicy.breakeven_trigger_pct),
            breakeven_lock_pct=dsl.get("breakeven_lock_pct", ExitPolicy.breakeven_lock_pct),
        )
    except Exception:
        return ExitPolicy()


def rehydrate_from_exchange(asset_positions: Iterable[Dict[str, Any]],
                            policy: Optional[ExitPolicy] = None,
                            default_leverage: int = 1,
                            queried_dexes: Optional[set] = None) -> None:
    """Reconcile the tracker registry with the exchange's live position list.

    Synthesizes a tracker for any open position without one (entry_time =
    now, since the original is unknown), and drops trackers for coins no
    longer open. When `queried_dexes` is given, a tracker is only dropped
    if its dex *successfully responded* this cycle — protecting trackers
    on timed-out dexes from being reset to fresh state next tick.
    """
    load_state()
    live_keys = set()
    added = 0
    for p in asset_positions or []:
        pos = p.get("position", {}) if isinstance(p, dict) else {}
        coin = pos.get("coin")
        if not coin:
            continue
        try:
            szi = float(pos.get("szi", "0") or 0)
            entry = float(pos.get("entryPx") or 0)
        except (TypeError, ValueError):
            continue
        if szi == 0 or entry <= 0:
            continue
        side = "long" if szi > 0 else "short"
        key = f"{coin}_{side}"
        live_keys.add(key)
        pos_leverage = pos.get("leverage", {})
        lev = int(pos_leverage.get("value", 0) or 0) if isinstance(pos_leverage, dict) else int(pos_leverage or 0)
        if not lev:
            lev = default_leverage
        if key not in _active_positions:
            # Inherit the CURRENT config exit policy, never the bare ExitPolicy()
            # default. A synthesize happens after a blackout-induced drop (the
            # exchange momentarily reported the position gone), and the default
            # is LOOSER (2.5%/50% ROE vs config 2.0%/30%) — re-synthesizing with
            # the default silently widened live stops ("policy drift"). Pull
            # config when the caller didn't pass an explicit policy.
            synth_policy = policy if policy is not None else _policy_from_config()
            _active_positions[key] = DSLTracker(coin, side, entry, time.time(), synth_policy,
                                                leverage=lev)
            added += 1
            logger.info(f"[dsl] Synthesized tracker for existing {key} @ {entry} ({lev}x)")

    def _key_in_queried_scope(k: str) -> bool:
        """True iff the dex behind this tracker key was queried this cycle.
        Key format `<coin>_<side>`; coin format `BTC` or `xyz:MU`."""
        if queried_dexes is None:
            return True
        coin, _, _ = k.rpartition("_")
        dex = coin.split(":", 1)[0] if ":" in coin else ""
        return dex in queried_dexes

    stale = [k for k in _active_positions
             if k not in live_keys and _key_in_queried_scope(k)]
    for k in stale:
        del _active_positions[k]
        logger.info(f"[dsl] Dropped stale tracker {k} (no live exchange position)")
    skipped = [k for k in _active_positions
               if k not in live_keys and not _key_in_queried_scope(k)]
    if skipped:
        logger.warning(
            f"[dsl] Preserving {len(skipped)} tracker(s) whose dex query failed "
            f"this cycle (will retry next tick): {', '.join(skipped[:5])}"
            + (f" +{len(skipped)-5} more" if len(skipped) > 5 else "")
        )

    if added or stale:
        _save_state()


def check_all_positions(mids: Dict[str, float]) -> List[ExitVerdict]:
    """Check all active positions against current mids. Call each scan tick.

    Returns list of ExitVerdict for positions that should be closed.
    """
    exits = []
    for tracker in list(_active_positions.values()):
        mark_px = mids.get(tracker.coin)
        # Handle both str and float values from different sources
        if mark_px is not None:
            try:
                mark_px = float(mark_px)
            except (ValueError, TypeError):
                continue
            if mark_px > 0:
                verdict = tracker.check(mark_px)
                if verdict.exit:
                    exits.append(verdict)
    return exits
