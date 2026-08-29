"""wallet_follow — zero-capital shadow recorder of 9 verified profitable HL wallets.

Spec: research/rebuild_2026_07_18/VERIFIED_TRADERS.md §4. This is a NEW data
source (copy-trading signals), exactly the frontier the alpha-hunt swarm said
candle-space saturation demands. Nothing here trades; promotion runs through
scripts/shadow_status.py at the pre-committed bar (>= 30 resolved episodes,
VALIDATED classify) PLUS the matched random-time null in
scripts/wallet_follow_null.py (>= 2,000 same-coin/same-side draws from the
trailing 90d, price@12bps, require p < 0.01). REFUTED at the standard bar ->
delete this module same day (operator refuted-rule).

Mechanics (spec §4, throttled to one poll per ``poll_minutes``, default 30):
one `clearinghouseState` per wallet per poll — weight 2 x 9 per 30 min
~= 0.6 weight/min. Raw `hl_client._http_post` only; never imports hyperfeed.
Last szi per (wallet, coin) persists in the state file. Detected deltas:

- OPEN  (|szi| 0 -> nonzero, or sign flip): gradeable row, side long/short,
  entry_ref_px = OUR mid at detection (copy latency is priced into the grade),
  horizon 3d / stop 20% (matching the
  follow set's 12h-2.3d central hold mass; recorded per-row for re-grades).
- ADD   (same-sign |szi| growth >= 25%): meta-only row (side="meta_add",
  horizon 0 -> ungradeable by design) for later add-following analysis.
- CLOSE (szi -> 0): meta-only row (side="meta_close") with the wallet's exit
  ts + our mid at detection, so an exit-copy variant can be graded offline.

Dedup (spec §4): ONE open gradeable signal per (coin, side) account-wide until
the first signal resolves (signal_bar_t + resolve_after_ms(horizon)); further
wallets opening the same (coin, side) inside that window append a meta-only
"meta_consensus" row (the append-only ledger cannot mutate the original row's
consensus_n=1) instead of a new gradeable row. Ledger-side, dedup_episodes
collapses any residual same-coin clusters at grade time.

Bootstrap rule (PIT honesty, not in the spec text): the first time a wallet is
ever seen, its existing positions are BASELINED without recording — they were
opened before observation started and are stale entries, not fresh signals.

Honest failure modes (record anyway): <=30-min copy latency eats fast entries;
whale fills may BE the price impact; the follow set is survivorship-selected
today. All three are what the forward grade + matched null measure.
"""
from __future__ import annotations

import json
import logging
import statistics
import time
from typing import Any, Callable, Dict, List, Optional

from hermes_trader.agents import shadow_ledger
from hermes_trader.agents.rebalancer_owned import state_file
from hermes_trader.agents.shadow_ledger import grade_interval, resolve_after_ms, simulate_exit

logger = logging.getLogger(__name__)

BOOK = "wallet_follow"
HORIZON_DAYS = 3.0
STOP_PCT = 20.0
ADD_FRAC = 0.25                      # spec: position grows >= 25% -> meta_add
DEFAULT_POLL_MINUTES = 30.0
_HOUR_MS = 3_600_000
_MIN_MS = 60_000

# Frozen follow set (spec §4): the 9 verified copyable wallets — median hold
# >= 4h or campaign style, self-computed 3mo PnL >= $2M. Full addresses frozen
# from research/rebuild_2026_07_18/verified_traders_data.json (gate-tested to
# match). Refresh QUARTERLY by re-running the §7 pipeline; never mid-quarter
# (selection drift). Excluded: all HFT/MM wallets (0xa312114b, 0xf02d16a2
# fail the >= 4h bar).
FOLLOW_SET = (
    "0xe867fbdad3291530e41530301ecb77693850c78e",
    "0x9e8b1e51c642f4c8b87c6ba11c53d516a218afc4",
    "0xda744273f80b22412417f7cfe0503f3d721f987d",
    "0x48d826da83e69844f2f84b2db50703a933d137a2",
    "0xd1dd6d99c5fb5d31ff52eacce5046c7158859e85",
    "0xcf90cfecf74e631feea816d02e757c0c8e895c0e",
    "0x0Ddf9bAe2aF4B874B96D287a5aD42Eb47138A902",
    "0x469E9A7f624b04C24f0E64EDF8d8a277e6bf58A5",
    "0xFce053a5e461683454bF37Ad66d20344c0e3F4C0",
)

_STATE_FILE = state_file(".wallet_follow_state.json")


def _num(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _load_state() -> Dict[str, Any]:
    try:
        raw = json.load(open(_STATE_FILE))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _save_state(state: Dict[str, Any]) -> None:
    try:
        with open(_STATE_FILE, "w") as fh:
            json.dump(state, fh)
    except Exception:
        pass


def _fetch_positions(addr: str) -> Optional[Dict[str, Dict[str, float]]]:
    """Main-dex clearinghouseState (weight 2) -> {coin: {szi, entry_px, ntl}}.

    Returns None on ANY fetch failure so a transient outage is a skipped wallet,
    never a fake "went flat everywhere" (which would burst spurious CLOSE rows
    and then re-OPEN everything on recovery)."""
    try:
        from hermes_trader.client.hl_client import _http_post
        res = _http_post("/info", {"type": "clearinghouseState", "user": addr},
                         timeout=10)
    except Exception as exc:
        logger.warning(f"[wallet-follow] fetch failed for {addr[:10]}: {exc}")
        return None
    if not isinstance(res, dict):
        return None
    out: Dict[str, Dict[str, float]] = {}
    for p in res.get("assetPositions") or []:
        pos = (p or {}).get("position") or {}
        coin = str(pos.get("coin") or "")
        szi = _num(pos.get("szi"))
        if coin and szi != 0.0:
            out[coin] = {"szi": szi, "entry_px": _num(pos.get("entryPx")),
                         "ntl": abs(_num(pos.get("positionValue")))}
    return out


def _prune_open_signals(open_sigs: Dict[str, Any], now_ms: int) -> Dict[str, Any]:
    """Drop resolved (coin, side) locks — dedup holds only until resolution."""
    window = resolve_after_ms(HORIZON_DAYS)
    return {k: v for k, v in open_sigs.items()
            if now_ms < int(_num((v or {}).get("t"))) + window}


def maybe_record(config: Dict[str, Any],
                 universe: Optional[List[Dict[str, Any]]] = None,
                 now_ms: Optional[int] = None,
                 fetch_positions: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
                 ) -> int:
    """Call once per v1 scan cycle. Internally throttled to `poll_minutes`.
    Returns the number of NEW gradeable OPEN rows recorded (meta rows excluded)."""
    cfg = (config or {}).get("wallet_follow") or {}
    if not bool(cfg.get("enabled", True)):
        return 0
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    state = _load_state()
    poll_ms = float(cfg.get("poll_minutes", DEFAULT_POLL_MINUTES)) * _MIN_MS
    if now - int(_num(state.get("last_poll_ms"))) < poll_ms:
        return 0
    state["last_poll_ms"] = now              # throttle even if every fetch fails

    mids: Dict[str, float] = {}
    for m in universe or []:
        c = m.get("coin") or ""
        px = _num(m.get("midPx") or m.get("markPx"))
        if c and px > 0:
            mids[c] = px

    fetch = fetch_positions or _fetch_positions
    wallets: Dict[str, Dict[str, float]] = state.get("wallets") or {}
    open_sigs = _prune_open_signals(state.get("open_signals") or {}, now)
    bar_t = (now // _HOUR_MS) * _HOUR_MS
    n_opens = 0

    for addr in FOLLOW_SET:
        cur = fetch(addr)
        if cur is None:
            continue                          # fetch failure: keep last szi, retry next poll
        if addr not in wallets:               # bootstrap: baseline, never record stale entries
            wallets[addr] = {c: p["szi"] for c, p in cur.items()}
            logger.info(f"[wallet-follow] baselined {addr[:10]} "
                        f"({len(cur)} open positions, not recorded)")
            continue
        prev = wallets[addr]
        for coin in sorted(set(prev) | set(cur)):
            old = _num(prev.get(coin))
            info = cur.get(coin) or {}
            new = _num(info.get("szi"))
            commit = True
            if old == 0.0 and new != 0.0:                       # OPEN
                commit, opened = _handle_open(coin, new, info, mids, open_sigs,
                                              addr, bar_t, now, flipped=False)
                n_opens += opened
            elif old != 0.0 and new != 0.0 and (old > 0) != (new > 0):   # sign flip = OPEN
                commit, opened = _handle_open(coin, new, info, mids, open_sigs,
                                              addr, bar_t, now, flipped=True)
                n_opens += opened
            elif old != 0.0 and new == 0.0:                     # CLOSE -> meta row
                shadow_ledger.record(
                    BOOK, coin=coin, side="meta_close", signal_bar_t=bar_t,
                    entry_ref_px=0.0, horizon_days=0.0, stop_pct=0.0,
                    meta={"wallet": addr, "closed_side": "long" if old > 0 else "short",
                          "exit_ts": now, "exit_ref_px": mids.get(coin, 0.0),
                          "shadow": True})
            elif old != 0.0 and (old > 0) == (new > 0) and abs(new) >= abs(old) * (1 + ADD_FRAC):
                shadow_ledger.record(                          # ADD -> meta row
                    BOOK, coin=coin, side="meta_add", signal_bar_t=bar_t,
                    entry_ref_px=0.0, horizon_days=0.0, stop_pct=0.0,
                    meta={"wallet": addr, "prev_szi": old, "new_szi": new,
                          "wallet_ntl": info.get("ntl", 0.0),
                          "add_frac": round(abs(new) / abs(old) - 1.0, 4),
                          "shadow": True})
            if commit:
                if new == 0.0:
                    prev.pop(coin, None)
                else:
                    prev[coin] = new

    state["wallets"] = wallets
    state["open_signals"] = open_sigs
    _save_state(state)
    return n_opens


def _handle_open(coin: str, szi: float, info: Dict[str, float], mids: Dict[str, float],
                 open_sigs: Dict[str, Any], addr: str, bar_t: int, now_ms: int,
                 flipped: bool) -> tuple[bool, int]:
    """One wallet's OPEN. Returns (commit_szi, n_gradeable_rows_recorded).

    Dedup: an unresolved (coin, side) signal absorbs later wallets as consensus
    meta rows. A missing mid defers the szi commit so the NEXT poll retries the
    open at a then-available detection price (unlock-recorder pattern)."""
    side = "long" if szi > 0 else "short"
    key = f"{coin}|{side}"
    sig = open_sigs.get(key)
    if sig is not None:                       # unresolved: consensus, not a new row
        wallets_in = sig.setdefault("wallets", [])
        if addr not in wallets_in:
            wallets_in.append(addr)
            shadow_ledger.record(
                BOOK, coin=coin, side="meta_consensus", signal_bar_t=bar_t,
                entry_ref_px=0.0, horizon_days=0.0, stop_pct=0.0,
                meta={"wallet": addr, "open_side": side,
                      "consensus_n": len(wallets_in),
                      "open_signal_t": int(_num(sig.get("t"))), "shadow": True})
            logger.info(f"[wallet-follow] {coin} {side}: consensus "
                        f"{len(wallets_in)} (+{addr[:10]})")
        return True, 0
    px = mids.get(coin)
    if not px:
        return False, 0                       # no mid: retry next poll, keep old szi
    shadow_ledger.record(
        BOOK, coin=coin, side=side, signal_bar_t=bar_t, entry_ref_px=px,
        horizon_days=HORIZON_DAYS, stop_pct=STOP_PCT,
        meta={"wallet": addr, "wallet_ntl": info.get("ntl", 0.0),
              "wallet_entry_px": info.get("entry_px", 0.0), "consensus_n": 1,
              "flipped": flipped, "shadow": True})
    open_sigs[key] = {"t": bar_t, "wallets": [addr]}
    logger.info(f"[wallet-follow] {coin}: {addr[:10]} opened {side}"
                f"{' (flip)' if flipped else ''} — shadow {side.upper()} @ {px}")
    return True, 1


# ── Matched random-time null (spec §4 grading bar) ────────────────────────────

MC_N_DRAWS = 2000            # spec: >= 2,000 draws
MC_COST_BPS = 12.0           # spec: price@12bps
MC_P_REQUIRED = 0.01         # spec: require p < 0.01


def mc_null_pvalue(events: List[Dict[str, Any]],
                   bars_by_coin: Dict[str, List[Any]],
                   n_draws: int = MC_N_DRAWS,
                   cost_bps: float = MC_COST_BPS,
                   seed: int = 1337) -> Optional[Dict[str, Any]]:
    """Matched null: same coins, same sides, same horizon/stop, RANDOM entry
    times from the supplied bars (trailing 90d when called by the CLI). The
    W-U1_unlock_backtest.py::mc_pvalue pattern, side-aware.

    events: [{"coin","side","horizon_days","stop_pct","ret"}] — `ret` is the
    observed NET return (already @cost_bps). bars_by_coin: chronological daily
    bars per coin. p = P(null mean >= observed mean); every draw enters at a
    random bar CLOSE and simulates the exact simulate_exit path net of
    cost_bps. Deterministic under `seed`. Returns None if nothing is testable.
    """
    import random
    rng = random.Random(seed)
    cost = float(cost_bps) / 10000.0
    usable: List[Dict[str, Any]] = []
    for e in events or []:
        _, _, horizon = grade_interval(float(e.get("horizon_days") or 0.0))
        bars = bars_by_coin.get(str(e.get("coin"))) or []
        if horizon > 0 and len(bars) >= horizon + 5:
            usable.append({**e, "_h": horizon})
    if not usable:
        return None
    obs = statistics.mean([float(e["ret"]) for e in usable])
    hits = draws = 0
    while draws < int(n_draws):
        sample: List[float] = []
        for e in usable:
            bars = bars_by_coin[str(e["coin"])]
            h = e["_h"]
            i = rng.randrange(0, len(bars) - h - 1)
            entry_px = shadow_ledger._f(bars[i], "c")
            if entry_px <= 0:
                continue
            sim = simulate_exit(str(e["side"]), entry_px, bars[i + 1:],
                                float(e["stop_pct"]), h)
            if sim is not None:
                sample.append(sim[0] - cost)
        draws += 1
        if not sample:
            continue
        if statistics.mean(sample) >= obs:
            hits += 1
    p = hits / max(1, draws)
    return {"p": p, "obs_mean_pct": round(100 * obs, 4), "n_events": len(usable),
            "n_draws": draws, "pass": p < MC_P_REQUIRED}
