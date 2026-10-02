"""copycat: mirror up to 10 Hyperliquid leaderboard wallets at once.

UNTESTED BY OPERATOR BUILD, 2026-10-02
---------------------------------------
No backtest exists for "mirror 10 leaderboard wallets, equal split of the
sleeve." One of the 10 default leaders (0xe282...df29) is the exact wallet
copy_trade_live.py mirrors, and W-CP1 (research/alpha_swarm/findings/
W-CP1_copy_0xe282.md) REFUTED copying that wallet at every multiplier
0.5x-3x across every 2025-03..10 start month. This book is graded the same
way (mark-to-market drawdown of its own sleeve) and can be demoted the same
way. It is not evidence the other 9 leaders are any better.

WHY THIS BOOK HAS FIXED SIZING, NOT JUST PROPORTIONAL (operator decision,
2026-10-02)
---------------------------------------------------------------------------
The original build only sized proportionally: our notional = his_added
fraction of his own equity, applied to our allocation. That is correct for
an account the same order of magnitude as the leaders' -- it breaks down
completely for this operator's real account ($1.29 equity at the time of
this change). A leader with $1M+ equity adding a $50k position is a 5% move
for him; 5% of a $1.29 allocation is six cents, which rounds to $0 after
the exchange's size precision. Proportional-only sizing made this book
mathematically unable to ever open a position for a small account, no
matter how long it ran. `sizing_mode` exists so a small-account copytrader
can pick a FIXED dollar size per leader instead -- the exact control
Hyperdash itself does not give you. See SIZING below.

THE RULE
--------
  leaders   up to MAX_LEADERS (10) wallet addresses, `copycat.leaders` in
            config. Lowercased, deduped (first occurrence wins), capped at
            10. Defaults to DEFAULT_LEADERS below if missing/invalid/empty.
  ownership our account holds ONE net position per coin, so only one leader
            can own a coin's copy at a time. Leaders are processed in config
            order every cycle; the first leader (that cycle) to open a coin
            owns it until OUR copy of it closes. Every other leader's change
            on that coin is ignored meanwhile (skip reason
            "owned by leader 0x...").
  reduce    the same fraction of OUR position he took off his; flat when he
            is flat; a flip (long<->short in one read) is a close then a
            fresh open. Identical in every sizing_mode -- only opens/adds
            differ by mode.
  leverage  leverage_mode "max": min(the coin's exchange max, max_position_
            leverage). "match": min(ceil(his leverage on that coin), the
            coin's max, max_position_leverage). "fixed": min(the configured
            `leverage` int, the coin's max, max_position_leverage). All
            three are capped by max_position_leverage no matter what.
  margin    margin_mode "isolated" or "cross", passed to set_leverage as
            is_cross. A market flagged onlyIsolated by the exchange is forced
            isolated regardless of this setting (exchange-layer behavior,
            not this book's).
  stop      none, ever. The leader has no stop and a stop of ours would
            desync the mirror from what he actually did.

SIZING (`sizing_mode`)
-----------------------
  proportional   The original rule. A fresh open's notional = his_added_
                 notional / his_equity * allocation * size_multiplier, where
                 allocation = our_equity * sleeve_frac / len(configured
                 leaders) (a fixed, equal split, not weighted by his size)
                 and his_added_notional = size_added * his entryPx on that
                 coin (his own average entry, not the mark -- HL's
                 clearinghouseState gives both; positionValue/szi is the
                 mark and is NOT used here). An add to an existing copy uses
                 the same formula on his incremental size. size_multiplier
                 (default 1.0, clamp [0,100]) is the "re-copy with higher
                 size" knob for this mode only; it does nothing in the two
                 fixed modes below. This mode needs our equity to be a
                 meaningful fraction of the leaders' for the math to produce
                 a tradeable size -- see the note above.
  fixed_margin   DEFAULT. A fresh open commits a fixed dollar MARGIN,
                 fixed_margin_usd (default $0.25, clamped > 0), at whatever
                 leverage leverage_mode computes for that coin: notional =
                 fixed_margin_usd * exchange_leverage. This is the mode a
                 $1.29 account needs: 50x leverage on a $0.25 margin is a
                 $12.50 position, comfortably over HL's floor, regardless of
                 how large the leader's own book is.
  fixed_notional A fresh open is sized directly in dollars of NOTIONAL,
                 fixed_notional_usd (default $10.50, the exchange floor,
                 clamped >= $10.50) -- leverage only affects margin used,
                 not position size.
  In BOTH fixed modes, an ADD to an existing copy does not re-apply the
  fixed amount (that would re-fix the position to the same size every add,
  never growing with the leader) -- it scales OUR existing position by his
  fractional change: our_add_size = |our_szi| * (|his_cur| - |his_prev|) /
  |his_prev|, converted to a dollar notional at the current mid before the
  same margin-room cap and min_order_bump rule every mode shares. Reduces,
  closes and flips are unaffected by sizing_mode entirely.
  The margin-room cap (dex_available * leverage * 0.95) and min_order_bump
  apply identically after the mode-specific notional is computed, in every
  mode -- a fixed-mode position can still be skipped if there is no margin
  room, exactly like a proportional one.

PER-LEADER OVERRIDES (`leader_overrides`)
--------------------------------------------
  `copycat.leader_overrides` is {address: {subset of sizing_mode,
  size_multiplier, fixed_margin_usd, fixed_notional_usd, leverage_mode,
  leverage, margin_mode, max_worse_entry_pct}}. Every other setting
  (enabled, leaders, sleeve_frac, max_position_leverage,
  include_spot_staked, only_new_positions, min_order_bump) is book-wide
  only -- it would not make sense to copy the SAME account on two different
  entry-baseline rules, for instance. Addresses are lowercased on read;
  unknown sub-keys are dropped rather than raising, same defensive posture
  as every other field. `leader_settings(config, leader)` returns one
  leader's EFFECTIVE settings (the book-wide block with his override
  merged on top, normalized through the exact same clamp pipeline as
  `settings()`) and is what `maybe_run` actually uses per leader -- so a
  leader with no override gets identical numbers to the book-wide
  `settings()` output, by construction, not by a second hand-written path.
  The effective sizing_mode and leverage actually used are recorded in the
  copy's own state (`state["copies"][coin]`) at open time and in that
  open's log_event, so the dashboard/activity feed can show what rule a
  position was actually opened under even if the config changes later.

CONFIG (`copycat` key; every default below matches .agent-config.json)
------------------------------------------------------------------------
  enabled               bool, default false.
  shadow_only           bool, default false. enabled AND NOT shadow_only
                        gates new opens/adds; exits always mirror regardless.
  leaders               list of addresses, default DEFAULT_LEADERS (10
                        Hyperliquid leaderboard wallets). See `settings()`.
  sleeve_frac           float [0,1], default 0.25. This book's share of
                        account equity, BEFORE the N-way leader split.
  max_position_leverage int [1,50], default 50. Hard ceiling on the exchange
                        leverage any single mirrored coin opens at, no
                        matter what leverage_mode computes.
  leverage_mode         "max" | "match" | "fixed", default "max".
  leverage              int [1,50], default 20. Only used by leverage_mode
                        "fixed".
  margin_mode           "isolated" | "cross", default "isolated".
  include_spot_staked   bool, default false. false: size a leader's equity
                        off perp accountValue (every queried dex) + his spot
                        USDC only (same as copy_trade -- unified margin means
                        his spot USDC is his collateral). true: ALSO add
                        spot_other_usd + staked_hype_usd from
                        venue.leader_extras(). If that extras read fails
                        while this is true, the leader is skipped entirely
                        for the cycle -- we never size a mirror off a wrong
                        equity, and that is worth a missed cycle. Only
                        matters in sizing_mode "proportional".
  only_new_positions    bool, default false. true: copy_trade's original
                        behavior -- whatever a leader holds the first time
                        the book sees him is ignored until he goes flat on
                        it, then reopens it, and only THAT reopen is copied.
                        false (the default here): his full current book at
                        first sight is a candidate to copy right away, sized
                        on his full current notional, subject to every other
                        gate below (claims, margin room, worse-entry, min
                        order). A newly ADDED leader (not previously in
                        state) always gets this same fresh-baseline
                        treatment; it is per leader, not per book.
  max_worse_entry_pct   float [0,100], default 10.0. An open or add is
                        skipped when our current mid is worse than the
                        leader's own average entry (entryPx) by more than
                        this percent: a long needs mid <= entry*(1+p); a
                        short needs mid >= entry*(1-p). This is NOT a
                        permanent ignore -- the skip stays pending and is
                        re-checked every cycle the leader still holds the
                        position, because price can come back into range.
                        Reduces and closes are never gated by this; exits
                        always mirror. Per-leader overridable.
  min_order_bump        bool, default true. When a computed open/add
                        notional is below HL's $10.50 floor, round it UP to
                        $10.50 so the position still opens, but ONLY if the
                        computed notional is at least 25% of that floor
                        ($2.625) -- otherwise the gap is treated as noise and
                        skipped exactly like copy_trade does today (no bump,
                        the attempted open stays permanently ignored for
                        that leader on that coin until he goes flat). The
                        25% line exists so a one-size-fits-all bump cannot
                        10x a near-zero notional.
  sizing_mode           "proportional" | "fixed_margin" | "fixed_notional",
                        default "fixed_margin". Per-leader overridable. See
                        SIZING above.
  size_multiplier       float [0,100], default 1.0. Proportional mode only.
                        Per-leader overridable.
  fixed_margin_usd      float, default 0.25, clamped > 0 (floor $0.01).
                        Per-leader overridable.
  fixed_notional_usd    float, default 10.50, clamped >= $10.50 (the
                        exchange floor). Per-leader overridable.
  leader_overrides      {address: {subset of the per-leader-overridable
                        keys above}}, default {}. See PER-LEADER OVERRIDES.

THE RISK, PLAINLY
------------------
10 live wallets, isolated-or-cross, up to 50x, with no stop anywhere in the
rule -- same shape as copy_trade's one leader, times ten. At 40x isolated,
liquidation sits about 2.5% adverse minus maintenance margin away from entry;
at max_position_leverage's ceiling of 50x it is closer still. leverage_mode
"fixed" at its default of 20x is already a 5%-ish move from liquidation on
an isolated position. A leader who gets liquidated or dumps into a falling
market is mirrored with zero delay-adjustment beyond max_worse_entry_pct,
which only blocks NEW exposure, never an exit. This book is exempt from the
hard daily-loss flatten, the DSL exit tracker and the slot count in
scripts/trading_loop.py, for the same reason copy_trade and drawdown_ladder
are: a stop we apply that the leader does not have breaks the mirror, and
this book's whole thesis is following his exits exactly. The daily-loss GATE
still blocks every new open and add through
`allow_entries`/`daily_pnl`/`daily_loss_limit` -- it just cannot close what
is already open. `mtm_decision` demotes the book once its own mark-to-market
drawdown passes DEMOTE_DRAWDOWN (-50% of sleeve, same bar as copy_trade);
demotion stops new copies and keeps mirroring exits of whatever is already
open, exactly like disabled/shadow_only/OFF mode. In the fixed sizing modes,
every leader's fresh open costs the SAME fixed dollar amount regardless of
how many leaders fire at once -- unlike proportional mode, there is no
sleeve_frac-based ceiling tying total exposure to account equity, so a tiny
fixed_margin_usd is itself the risk control, not sleeve_frac.

CLAIMS
------
"copycat" is in pathiel/agents/rebalancer_owned.py `_ACTIVE_CLAIM_BOOKS`.
Without it every `claims.claim(coin, "copycat")` returns False and the book
sits at "claim refused" forever; tests/test_live_book_order_path.py pins the
claim set to the live book set so it cannot drift out again.

WHAT IS DELIBERATELY NOT HANDLED
-----------------------------------
A leader removed from `copycat.leaders` keeps being read (an "orphan") for as
long as this book still holds a copy opened from him, so reduces and closes
keep mirroring correctly. He never gets NEW exposure again -- no fresh opens,
no adds to what is already copied from him -- even if the coin he holds is
later freed up by another book's copy closing. If an orphan's position is
unchanged across the cycle where it would otherwise become eligible again,
nothing re-evaluates it until his size moves. This is intentional: an orphan
leader is being wound down, not actively managed.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional, Set

from pathiel.agents import copy_trade_live as ct
from pathiel.agents.atomic_io import append_json_line, read_json, write_json_atomic
from pathiel.agents.drawdown_ladder_live import book_drawdown
from pathiel.agents.rebalancer_owned import get_claims_registry, state_file

logger = logging.getLogger(__name__)

_BOOK_NAME = "copycat"

MAX_LEADERS = 10
DEFAULT_LEADERS: List[str] = [
    "0x95da8596c44dd09f4b8becce87ad3b7894fb2328",
    "0xac142fee46f8dacbfef23e1e41d79f1f7233487e",
    "0xe2823659be02e0f48a4660e4da008b5e1abfdf29",
    "0x0b38c01118342098d6f108cc3741349089cd9590",
    "0xf97ad6704baec104d00b88e0c157e2b7b3a1ddd1",
    "0x04a97ae7f350a22cd0cdb6b1875e8905b76495aa",
    "0xe09726ff25f5001f37b15049f54116cb83d7d0fe",
    "0xf5079c84c34051d7c4cd87494fda534fe7ecdf6d",
    "0xd260b2216b735277da6771564a01c04856e78321",
    "0xbe10fd36393c8b677281d0bc2cf1bb8c98ad4b34",
]
DEFAULT_SLEEVE_FRAC = 0.25
DEFAULT_MAX_POSITION_LEVERAGE = 50
DEFAULT_LEVERAGE_MODE = "max"
DEFAULT_LEVERAGE = 20                       # leverage_mode "fixed"
DEFAULT_MARGIN_MODE = "isolated"
DEFAULT_MAX_WORSE_ENTRY_PCT = 10.0
DEFAULT_MIN_ORDER_BUMP = True
DEMOTE_DRAWDOWN = -0.5             # same bar as copy_trade
_MIN_ORDER_USD = 10.5              # pathiel.client.exchange.MIN_ORDER_USD
_BUMP_FLOOR_FRAC = 0.25            # min_order_bump only bumps above this fraction of the floor
_DAY_MS = 86_400_000

DEFAULT_SIZING_MODE = "fixed_margin"        # small-account default; see module docstring
DEFAULT_SIZE_MULTIPLIER = 1.0
DEFAULT_FIXED_MARGIN_USD = 0.25
DEFAULT_FIXED_NOTIONAL_USD = _MIN_ORDER_USD
_FIXED_MARGIN_USD_FLOOR = 0.01
_LEADER_OVERRIDE_KEYS = frozenset({
    "sizing_mode", "size_multiplier", "fixed_margin_usd", "fixed_notional_usd",
    "leverage_mode", "leverage", "margin_mode", "max_worse_entry_pct",
})

_STATE = state_file(".copycat.json")
_EQUITY_LOG = state_file(".copycat_equity.jsonl")


class LiveVenue:
    """Exchange calls, lazily imported like copy_trade_live's."""

    def leader_state(self, user: str) -> Optional[Dict[str, Any]]:
        """{"equity_perp_usdc", "spot_usdc", "positions": {coin: {szi,
        entry_px, lev}}}, or None if any read failed. A failed read must
        never look like a flat leader."""
        from pathiel.client.hl_client import _http_post
        positions: Dict[str, Dict[str, float]] = {}
        equity_perp = 0.0
        for dex in ct.DEXES:
            st = _http_post("/info", {"type": "clearinghouseState", "user": user, "dex": dex},
                            timeout=10)
            if not isinstance(st, dict) or "assetPositions" not in st:
                return None
            equity_perp += float((st.get("marginSummary") or {}).get("accountValue") or 0)
            for ap in st["assetPositions"]:
                p = ap.get("position") or {}
                szi = float(p.get("szi") or 0)
                if not szi:
                    continue
                coin = p["coin"] if (not dex or ":" in p["coin"]) else f"{dex}:{p['coin']}"
                positions[coin] = {"szi": szi, "entry_px": float(p.get("entryPx") or 0),
                                   "lev": float((p.get("leverage") or {}).get("value") or 1)}
        spot = _http_post("/info", {"type": "spotClearinghouseState", "user": user}, timeout=10)
        if not isinstance(spot, dict) or "balances" not in spot:
            return None
        spot_usdc = sum(float(b.get("total") or 0) for b in spot["balances"] if b.get("coin") == "USDC")
        return {"equity_perp_usdc": equity_perp, "spot_usdc": spot_usdc, "positions": positions}

    def leader_extras(self, user: str) -> Optional[Dict[str, Any]]:
        """{"spot_usdc", "spot_other_usd", "staked_hype_usd"} or None.
        Written by another agent; contract only, see module docstring."""
        from pathiel.client.exchange import fetch_leader_extras
        return fetch_leader_extras(user)

    def mid(self, coin: str) -> float:
        from pathiel.client.exchange import get_hl_price
        return get_hl_price(coin)

    def max_leverage(self, coin: str) -> int:
        from pathiel.client.exchange import get_max_leverage
        return get_max_leverage(coin)

    def set_leverage(self, coin: str, leverage: int, is_cross: Optional[bool]) -> Dict[str, Any]:
        """{"ok", "is_cross", ...}. is_cross is the ACTUALLY applied value --
        an isolated-only market forces isolated regardless of what was asked
        for, and the caller stores the applied value, not the request."""
        from pathiel.client.exchange import set_leverage
        return set_leverage(coin, leverage, is_cross=is_cross, strict=True)

    def market(self, coin: str, is_buy: bool, size: float, mid: float,
               reduce_only: bool = False) -> Dict[str, Any]:
        from pathiel.client.exchange import place_hl_order
        return place_hl_order(is_buy, size, mid, coin, reduce_only=reduce_only)

    def size_for(self, coin: str, notional: float, mid: float) -> float:
        from pathiel.client.exchange import entry_size_for_notional
        return entry_size_for_notional(coin, notional, mid)

    def close(self, coin: str) -> bool:
        from pathiel.agents.executor import close_position_market
        return bool(close_position_market(coin).get("ok"))


# ── pure pieces ──────────────────────────────────────────────────────────────

def _normalize_leaders(raw: Any) -> List[str]:
    """Lowercase, dedupe (first occurrence wins), cap at MAX_LEADERS. Falls
    back to DEFAULT_LEADERS when `raw` is missing, not a list, or empty."""
    if not isinstance(raw, list) or not raw:
        raw = DEFAULT_LEADERS
    out: List[str] = []
    for a in raw:
        if not isinstance(a, str):
            continue
        lo = a.strip().lower()
        if lo and lo not in out:
            out.append(lo)
    return out[:MAX_LEADERS]


def _normalize_leader_overrides(raw: Any) -> Dict[str, Dict[str, Any]]:
    """Lowercase address keys, keep only the override-eligible sub-keys
    (_LEADER_OVERRIDE_KEYS). Values are NOT clamped here -- they are raw
    until merged + normalized per leader in `leader_settings`, through the
    exact same pipeline `settings()` uses for the book-wide block."""
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(raw, dict):
        return out
    for addr, sub in raw.items():
        if not isinstance(addr, str) or not isinstance(sub, dict):
            continue
        filtered = {k: v for k, v in sub.items() if k in _LEADER_OVERRIDE_KEYS}
        if filtered:
            out[addr.strip().lower()] = filtered
    return out


def _normalize(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """The clamp/default pipeline shared by `settings()` (the book-wide
    `copycat` block) and `leader_settings()` (one leader's block with his
    override merged on top) -- one pipeline, so the two can never clamp a
    value differently."""

    def _clamped_float(key: str, default: float, lo: float, hi: Optional[float] = None) -> float:
        try:
            v = float(cfg.get(key, default))
        except (TypeError, ValueError):
            v = default
        v = max(lo, v)
        if hi is not None:
            v = min(hi, v)
        return v

    def _clamped_int(key: str, default: int, lo: int, hi: int) -> int:
        try:
            v = int(cfg.get(key, default))
        except (TypeError, ValueError):
            v = default
        return max(lo, min(hi, v))

    def _enum(key: str, default: str, choices: Set[str]) -> str:
        v = cfg.get(key, default)
        return v if v in choices else default

    return {
        "enabled": bool(cfg.get("enabled", False)),
        "shadow_only": bool(cfg.get("shadow_only", False)),
        "leaders": _normalize_leaders(cfg.get("leaders")),
        "sleeve_frac": _clamped_float("sleeve_frac", DEFAULT_SLEEVE_FRAC, 0.0, 1.0),
        "max_position_leverage": _clamped_int(
            "max_position_leverage", DEFAULT_MAX_POSITION_LEVERAGE, 1, 50),
        "leverage_mode": _enum("leverage_mode", DEFAULT_LEVERAGE_MODE,
                               {"max", "match", "fixed"}),
        "leverage": _clamped_int("leverage", DEFAULT_LEVERAGE, 1, 50),
        "margin_mode": _enum("margin_mode", DEFAULT_MARGIN_MODE, {"isolated", "cross"}),
        "include_spot_staked": bool(cfg.get("include_spot_staked", False)),
        "only_new_positions": bool(cfg.get("only_new_positions", False)),
        "max_worse_entry_pct": _clamped_float(
            "max_worse_entry_pct", DEFAULT_MAX_WORSE_ENTRY_PCT, 0.0, 100.0),
        "min_order_bump": bool(cfg.get("min_order_bump", DEFAULT_MIN_ORDER_BUMP)),
        "sizing_mode": _enum("sizing_mode", DEFAULT_SIZING_MODE,
                             {"proportional", "fixed_margin", "fixed_notional"}),
        "size_multiplier": _clamped_float(
            "size_multiplier", DEFAULT_SIZE_MULTIPLIER, 0.0, 100.0),
        "fixed_margin_usd": _clamped_float(
            "fixed_margin_usd", DEFAULT_FIXED_MARGIN_USD, _FIXED_MARGIN_USD_FLOOR),
        "fixed_notional_usd": _clamped_float(
            "fixed_notional_usd", DEFAULT_FIXED_NOTIONAL_USD, _MIN_ORDER_USD),
        "leader_overrides": _normalize_leader_overrides(cfg.get("leader_overrides")),
    }


def settings(config: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize + clamp the book-wide `copycat` config. Pure: no I/O, no
    state. The dashboard and maybe_run both read through this (or through
    `leader_settings` for the per-leader-overridable fields) so they can
    never disagree about what the effective settings are."""
    cfg = (config or {}).get(_BOOK_NAME) or {}
    return _normalize(cfg)


def leader_settings(config: Dict[str, Any], leader: str) -> Dict[str, Any]:
    """One leader's EFFECTIVE settings: the book-wide `copycat` block with
    that leader's `leader_overrides` entry (only the override-eligible
    keys) merged on top, then run through the identical `_normalize`
    pipeline `settings()` uses. Pure: no I/O, no state. A leader with no
    override gets numbers identical to `settings(config)`, by
    construction -- there is no second hand-written clamp path to drift
    from the first. The returned dict has the same shape as `settings()`
    minus `leader_overrides` itself (which describes every leader, not
    this one, and would be confusing to return as part of one leader's
    own effective settings)."""
    cfg = (config or {}).get(_BOOK_NAME) or {}
    override: Dict[str, Any] = {}
    overrides = cfg.get("leader_overrides")
    if isinstance(overrides, dict):
        raw = overrides.get(str(leader).strip().lower())
        if isinstance(raw, dict):
            override = raw
    merged = dict(cfg)
    for k, v in override.items():
        if k in _LEADER_OVERRIDE_KEYS:
            merged[k] = v
    result = _normalize(merged)
    result.pop("leader_overrides", None)
    return result


def owned_coins(path: str = _STATE) -> Set[str]:
    state = read_json(path, default=None)
    if not isinstance(state, dict):
        return set()
    return set((state.get("copies") or {}).keys())


def mtm_decision(rows: List[Dict[str, Any]], live: Optional[bool]) -> Dict[str, Any]:
    """The grader's call, on mark-to-market book equity. Closed copies are
    not graded: with no stop, a copy closes when the leader takes profit."""
    dd = book_drawdown(rows)
    if dd is None:
        return {"book": _BOOK_NAME, "n": 0, "verdict": "PENDING", "action": "none",
                "why": "no mark-to-market history yet"}
    if dd <= DEMOTE_DRAWDOWN:
        return {"book": _BOOK_NAME, "n": len(rows), "verdict": "REFUTED",
                "action": "demote" if live else "none",
                "why": f"mark-to-market drawdown {dd:.1%} of sleeve is past {DEMOTE_DRAWDOWN:.0%}"}
    return {"book": _BOOK_NAME, "n": len(rows), "verdict": "LIVE", "action": "none",
            "why": f"drawdown {dd:.1%} of sleeve, inside {DEMOTE_DRAWDOWN:.0%}"}


def load_equity_log(path: str = _EQUITY_LOG) -> List[Dict[str, Any]]:
    """Same JSONL shape as copy_trade's; reuses its parser with copycat's own
    default path."""
    return ct.load_equity_log(path)


# ── the run ──────────────────────────────────────────────────────────────────

def _fresh_state() -> Dict[str, Any]:
    return {"leaders": {}, "copies": {}, "realized_cum": 0.0}


def maybe_run(config: Dict[str, Any],
              positions: Optional[List[Dict[str, Any]]],
              equity: float,
              dex_available: Dict[str, float],
              daily_pnl: float,
              daily_loss_limit: float,
              allow_entries: bool = True,
              now_ms: Optional[int] = None,
              venue: Any = None,
              log_event: Callable[[Dict[str, Any]], None] = lambda e: None,
              state_path: str = _STATE,
              equity_log_path: str = _EQUITY_LOG) -> Dict[str, Any]:
    """Diff every configured leader's positions against last cycle and
    mirror the changes, one net position per coin across the whole book.

    Exits are mirrored whenever a leader's read is good, even disabled,
    demoted, shadow_only or OFF: a copy already on the exchange must still
    follow him out. A per-leader read failure (position state or, with
    include_spot_staked, the extras read) skips only that leader this cycle
    and never looks like him going flat.
    """
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    venue = venue or LiveVenue()
    out: Dict[str, Any] = {"book": _BOOK_NAME, "opened": [], "added": [], "reduced": [],
                           "closed": [], "skipped": {}}
    if equity <= 0:
        out["skipped"]["all"] = "degraded account read"
        return out

    s = settings(config)
    enabled = s["enabled"] and not s["shadow_only"]
    entries_ok = enabled and allow_entries and daily_pnl > daily_loss_limit

    state = read_json(state_path, default=None)
    if (not isinstance(state, dict) or not isinstance(state.get("leaders"), dict)
            or not isinstance(state.get("copies"), dict)):
        state = _fresh_state()
    state.setdefault("realized_cum", 0.0)

    claims = get_claims_registry()
    ours: Dict[str, Dict[str, Any]] = {}
    for p in positions or []:
        pos = p.get("position") or {}
        if pos.get("coin"):
            ours[pos["coin"]] = pos

    leaders_cfg = s["leaders"]
    leaders_set = set(leaders_cfg)
    orphans = sorted(a for a in state["leaders"] if a not in leaders_set)
    allocation = (equity * s["sleeve_frac"] / len(leaders_cfg)) if leaders_cfg else 0.0

    for leader in list(leaders_cfg) + orphans:
        is_orphan = leader not in leaders_set
        es = leader_settings(config, leader)      # this leader's effective settings
        lead = venue.leader_state(leader)
        if lead is None:
            out["skipped"][f"leader:{leader}"] = "leader read failed"
            continue

        if es["include_spot_staked"]:
            extras = venue.leader_extras(leader)
            if extras is None:
                out["skipped"][f"leader:{leader}"] = "extras read failed"
                continue
            his_equity = (float(lead.get("equity_perp_usdc") or 0) + float(lead.get("spot_usdc") or 0)
                         + float(extras.get("spot_other_usd") or 0)
                         + float(extras.get("staked_hype_usd") or 0))
        else:
            his_equity = float(lead.get("equity_perp_usdc") or 0) + float(lead.get("spot_usdc") or 0)

        now_pos = lead.get("positions") or {}
        lstate = state["leaders"].get(leader)
        if lstate is None:
            if es["only_new_positions"]:
                lstate = {"last": {c: p["szi"] for c, p in now_pos.items()},
                         "ignored": sorted(now_pos)}
                state["leaders"][leader] = lstate
                log_event({"event": _BOOK_NAME, "action": "baseline", "leader": leader,
                          "ignored": lstate["ignored"]})
                continue
            lstate = {"last": {}, "ignored": []}
            state["leaders"][leader] = lstate

        last = lstate["last"]
        ignored = set(lstate["ignored"])
        retry: Dict[str, float] = {}         # coin -> prev to freeze; failed/pending retried next cycle

        for coin in sorted(set(last) | set(now_pos)):
            prev = float(last.get(coin) or 0.0)
            cur = float((now_pos.get(coin) or {}).get("szi") or 0.0)
            if prev == cur:
                continue
            flipped = bool(prev) and bool(cur) and (prev > 0) != (cur > 0)

            existing_copy = state["copies"].get(coin)
            if existing_copy and existing_copy.get("leader") != leader:
                out["skipped"][coin] = f"owned by leader {existing_copy['leader']}"
                continue

            if coin in ignored:
                if cur and not flipped:
                    continue
                ignored.discard(coin)
                if not cur:
                    continue
                prev = 0.0                   # a flip is a fresh open

            copy = existing_copy
            mine = ours.get(coin)
            if copy and not mine:
                # Our copy is gone (liquidated, closed by hand): stop
                # mirroring this coin until the leader is flat on it again.
                state["copies"].pop(coin, None)
                claims.release(coin, _BOOK_NAME)
                if cur:
                    ignored.add(coin)
                out["skipped"][coin] = "our copy vanished"
                continue

            if copy and (not cur or flipped):
                _record_realized(state, mine, venue, coin)
                if venue.close(coin):
                    state["copies"].pop(coin, None)
                    claims.release(coin, _BOOK_NAME)
                    out["closed"].append(coin)
                    log_event({"event": _BOOK_NAME, "action": "close", "leader": leader, "coin": coin})
                else:
                    out["skipped"][coin] = "close failed, retrying next cycle"
                    retry[coin] = prev
                    continue
                if not flipped:
                    continue
                copy, prev = None, 0.0

            if copy and abs(cur) < abs(prev):
                frac = (abs(prev) - abs(cur)) / abs(prev)
                szi = float(mine["szi"])
                mid = venue.mid(coin)
                if mid <= 0:
                    out["skipped"][coin] = "no mid"
                    retry[coin] = prev
                    continue
                size = abs(szi) * frac
                if (abs(szi) - size) * mid < _MIN_ORDER_USD:
                    _record_realized(state, mine, venue, coin)
                    ok = venue.close(coin)
                    if ok:
                        state["copies"].pop(coin, None)
                        claims.release(coin, _BOOK_NAME)
                else:
                    ok = venue.market(coin, szi < 0, venue.size_for(coin, size * mid, mid), mid,
                                      reduce_only=True).get("ok")
                    if ok:
                        state["realized_cum"] += (size if szi > 0 else -size) * (
                            mid - float(mine.get("entryPx") or mid))
                if not ok:
                    out["skipped"][coin] = "reduce failed, retrying next cycle"
                    retry[coin] = prev
                    continue
                out["reduced"].append(coin)
                log_event({"event": _BOOK_NAME, "action": "reduce", "leader": leader, "coin": coin,
                          "frac": round(frac, 4)})
            elif abs(cur) > abs(prev):
                lp = now_pos[coin]
                if is_orphan:
                    if not copy and cur:
                        ignored.add(coin)
                    out["skipped"][coin] = "orphan leader: no new exposure"
                elif not entries_ok:
                    if not copy and cur:
                        ignored.add(coin)
                    out["skipped"][coin] = "entries blocked"
                else:
                    mid = venue.mid(coin)
                    if mid <= 0:
                        out["skipped"][coin] = "no mid"
                        retry[coin] = prev
                    else:
                        entry_px = float(lp.get("entry_px") or 0)
                        is_long = cur > 0
                        pct = es["max_worse_entry_pct"] / 100.0
                        worse = entry_px > 0 and (
                            (is_long and mid > entry_px * (1 + pct))
                            or (not is_long and mid < entry_px * (1 - pct)))
                        if worse:
                            out["skipped"][coin] = (
                                f"mid {mid:.6g} worse than entry {entry_px:.6g} by "
                                f"more than {es['max_worse_entry_pct']:.1f}%")
                            retry[coin] = prev   # pending, not permanent: re-check every cycle
                        else:
                            _add(coin, cur, prev, lp, leader, his_equity, allocation, mine, es,
                                copy, dex_available, state, claims, venue, log_event, out,
                                now_ms, ignored, mid)

        new_last = {c: p["szi"] for c, p in now_pos.items()}
        for coin, frozen_prev in retry.items():
            new_last[coin] = frozen_prev
        lstate["last"] = new_last
        lstate["ignored"] = sorted(ignored)

    claims.save()
    _snapshot(state, ours, equity, s["sleeve_frac"], now_ms, equity_log_path)
    write_json_atomic(state_path, state, indent=1)
    return out


def _add(coin: str, cur: float, prev: float, lp: Dict[str, Any], leader: str,
         his_equity: float, allocation: float, mine: Optional[Dict[str, Any]],
         es: Dict[str, Any], copy: Optional[Dict[str, Any]],
         dex_available: Dict[str, float], state: Dict[str, Any], claims: Any, venue: Any,
         log_event: Callable[[Dict[str, Any]], None], out: Dict[str, Any], now_ms: int,
         ignored: Set[str], mid: float) -> None:
    """`es` is this leader's EFFECTIVE settings (`leader_settings`), already
    merged with any `leader_overrides` entry for `leader`."""
    if not copy:
        owner = claims.owner_of(coin)
        if owner not in (None, _BOOK_NAME):
            ignored.add(coin)
            out["skipped"][coin] = f"claimed by {owner}"
            return

    coin_max = venue.max_leverage(coin)
    cap = min(int(coin_max), es["max_position_leverage"])
    if es["leverage_mode"] == "match":
        lev = ct.copy_leverage(float(lp.get("lev") or 1), 1.0, cap)
    elif es["leverage_mode"] == "fixed":
        lev = max(1, min(cap, int(es["leverage"])))
    else:
        lev = max(1, cap)

    sizing_mode = es["sizing_mode"]
    if sizing_mode == "proportional":
        his_added_ntl = (abs(cur) - abs(prev)) * float(lp.get("entry_px") or 0)
        # our_equity=allocation, sleeve_frac=1.0: reduces to his_added_ntl /
        # his_equity * allocation * size_multiplier, this mode's sizing rule.
        ntl = ct.copy_notional(his_added_ntl, his_equity, allocation, 1.0, es["size_multiplier"])
    elif not copy:
        # Fresh open, fixed sizing: a fixed dollar amount, independent of
        # the leader's or our own equity -- the whole point for a small
        # account that proportional sizing rounds to $0.
        ntl = es["fixed_margin_usd"] * lev if sizing_mode == "fixed_margin" \
            else es["fixed_notional_usd"]
    else:
        # Add, fixed sizing: scale OUR existing size by his fractional
        # change (never re-apply the fixed amount, or every add would
        # re-fix the position back to the same size).
        frac_added = (abs(cur) - abs(prev)) / abs(prev)
        ntl = abs(float(mine["szi"])) * frac_added * mid

    dex = coin.split(":", 1)[0] if ":" in coin else ""
    room = max(0.0, float(dex_available.get(dex, 0.0))) * lev * 0.95
    ntl = min(ntl, room)

    if ntl < _MIN_ORDER_USD:
        if es["min_order_bump"] and ntl >= _BUMP_FLOOR_FRAC * _MIN_ORDER_USD:
            ntl = _MIN_ORDER_USD
        else:
            if not copy:
                ignored.add(coin)
            out["skipped"][coin] = f"copy ${ntl:.2f} under the ${_MIN_ORDER_USD} minimum"
            return

    if not copy:
        if not claims.claim(coin, _BOOK_NAME):
            ignored.add(coin)
            out["skipped"][coin] = "claim refused"
            return
        is_cross_requested = es["margin_mode"] == "cross"
        lev_res = venue.set_leverage(coin, lev, is_cross_requested)
        if not (isinstance(lev_res, dict) and lev_res.get("ok")):
            claims.release(coin, _BOOK_NAME)
            ignored.add(coin)
            out["skipped"][coin] = "set_leverage failed"
            return
        applied_cross = lev_res.get("is_cross", is_cross_requested)
    else:
        applied_cross = copy.get("is_cross")

    res = venue.market(coin, cur > 0, venue.size_for(coin, ntl, mid), mid)
    if not res.get("ok"):
        if not copy:
            claims.release(coin, _BOOK_NAME)
            ignored.add(coin)
        out["skipped"][coin] = f"order rejected: {res.get('error')}"
        return

    dex_available[dex] = float(dex_available.get(dex, 0.0)) - ntl / lev
    if copy:
        out["added"].append(coin)
    else:
        state["copies"][coin] = {"leader": leader, "opened_ms": now_ms, "leverage": lev,
                                 "is_cross": applied_cross, "sizing_mode": sizing_mode}
        out["opened"].append(coin)
    log_event({"event": _BOOK_NAME, "action": "add" if copy else "open", "leader": leader,
              "coin": coin, "side": "long" if cur > 0 else "short", "notional": round(ntl, 2),
              "leverage": lev, "is_cross": applied_cross, "sizing_mode": sizing_mode})


def _record_realized(state: Dict[str, Any], mine: Optional[Dict[str, Any]], venue: Any,
                     coin: str) -> None:
    """Estimate of what a full close realizes, at mid. The grader reads the
    book's drawdown, not this number's cents."""
    if not mine:
        return
    mid = venue.mid(coin)
    if mid > 0:
        state["realized_cum"] += float(mine["szi"]) * (mid - float(mine.get("entryPx") or mid))


def _snapshot(state: Dict[str, Any], ours: Dict[str, Dict[str, Any]], equity: float,
             sleeve_frac: float, now_ms: int, path: str) -> None:
    day = now_ms // _DAY_MS
    if state.get("snap_day") == day:
        return
    unreal = sum(float((ours.get(c) or {}).get("unrealizedPnl") or 0) for c in state["copies"])
    append_json_line(path, {"t": now_ms, "pnl": round(state["realized_cum"] + unreal, 4),
                            "sleeve": round(equity * sleeve_frac, 4)})
    state["snap_day"] = day
