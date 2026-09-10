"""Synthetic trading history for a public demo deployment.

The dashboard reads three files: a session log, a positions snapshot, and the
agent config. Every one of them is env-overridable, which means a demo needs no
changes to `pathia/dashboard.py` at all — point the three env vars at generated
files and the real rendering code runs against invented data. That is the whole
design. The demo exercises the production code path; only the inputs are fake.

Why generate rather than commit a fixture:

  - The dashboard is time-aware. `_summary_payload` reports "offline" when the
    newest heartbeat is older than 300s, and `read_position_snapshot` refuses a
    snapshot older than 120s. A committed fixture is stale the moment it lands,
    so the demo would show a dead bot. Timestamps have to be minted at boot.
  - Vercel's filesystem is read-only except `/tmp`, so the files have to be
    written at runtime regardless.
  - A generator is testable in a way a blob of JSON is not.

What this module will not do: emit a number the real system could not produce.
The equity walk, the win rate, and the position sizes are all bounded to what
the live config actually permits. A demo that shows a 400% month is a
performance claim, not a screenshot.

Every value is drawn from a seeded PRNG, so the same seed gives the same history
on every boot and across processes.
"""

from __future__ import annotations

import json
import os
import random
import time
from typing import Any, Dict, List, Tuple

# The five books the dashboard knows about, mirroring `_BOOKS` in
# pathia/dashboard.py. Kept as plain names here rather than imported: this
# service must not pull the trading stack in just to name a string.
BOOKS: Tuple[str, ...] = (
    "unlock_short_runin",
    "news_surge_short",
    "news_surge_multi",
    "social_trending",
    "xs_reversal",
)

COINS: Tuple[str, ...] = (
    "BTC", "ETH", "SOL", "ARB", "OP", "AVAX", "LINK", "SUI", "TIA", "INJ",
    "SEI", "APT", "DOGE", "WIF", "JUP",
)

# Starting equity for the demo account. Small on purpose: this system trades a
# real account in the low thousands, and a demo showing six figures would
# misrepresent what it is.
START_EQUITY = 4_820.0

HEARTBEAT_INTERVAL_S = 300
DEFAULT_HISTORY_DAYS = 7

# Per-heartbeat equity drift. Tuned so a week lands somewhere between -4% and
# +9% — the range the live system actually moves in. See `test_generator.py`,
# which fails if a seed escapes it.
_DRIFT_MEAN = 0.00028
_DRIFT_SD = 0.0034


def _agent_config() -> Dict[str, Any]:
    """A config with the books on, so the landing table is not five 'off' rows.

    Shapes match what `_book_size_str` reads: `notional_usd` + `leverage`, or
    `k_per_leg` for the basket book.
    """
    return {
        "mode": "LIVE",
        "leverage": 2,
        "scan_interval": 300,
        "min_score": 62,
        "unlock_short": {"enabled": True, "notional_usd": 120, "leverage": 2},
        "news_surge_short": {"enabled": True, "notional_usd": 90, "leverage": 2},
        "news_surge_multi": {"enabled": True, "notional_usd": 90, "leverage": 2,
                             "shadow_only": True},
        "social_trending": {"enabled": True, "notional_usd": 75, "leverage": 1},
        "xs_reversal": {"enabled": True, "k_per_leg": 3},
    }


def _walk_equity(rng: random.Random, steps: int) -> List[float]:
    """A bounded random walk.

    Plain Brownian motion drifts far enough over 2000 steps to produce a
    headline number the system has never printed, so the walk is pulled back
    toward its envelope whenever it leaves it. The result still looks like a
    trading curve; it just cannot run away.
    """
    equity = START_EQUITY
    out: List[float] = []
    for i in range(steps):
        equity *= 1.0 + rng.gauss(_DRIFT_MEAN, _DRIFT_SD)
        # Envelope widens with time, the way a real drawdown band does.
        frac = (i + 1) / steps
        lo = START_EQUITY * (1.0 - 0.06 * frac)
        hi = START_EQUITY * (1.0 + 0.11 * frac)
        equity = min(max(equity, lo), hi)
        out.append(round(equity, 2))
    return out


def build_session_log(
    now_ms: int, seed: int = 20260910, days: int = DEFAULT_HISTORY_DAYS,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """The synthetic session log (oldest first) and the positions left open.

    Returns both because the snapshot has to agree with the log: the "3 open"
    on the last heartbeat and the rows in the positions table are the same
    three trades, and the only way to guarantee that is to hand the open list
    straight to `build_position_snapshot`.

    Emits the event vocabulary the dashboard actually classifies: `loop_start`,
    `loop_heartbeat`, `scan`, `execute`, `dsl_exit`, `slots_full_skip` and the
    occasional `error`. Anything the dashboard would render as "other" is left
    out; a demo full of unclassified rows teaches nobody anything.
    """
    rng = random.Random(seed)
    steps = (days * 86_400) // HEARTBEAT_INTERVAL_S
    # The newest heartbeat lands ~40s ago so the dashboard reads "scanning"
    # rather than "stale" (its cutoff is 300s).
    end_ms = now_ms - 40_000
    # (steps - 1), not steps: the loop below emits at start_ms + i*interval for
    # i in [0, steps), so the final heartbeat lands on end_ms only if the first
    # one is one interval closer. Off by one here put the newest heartbeat 340s
    # in the past and the dashboard reported the bot "stale" on every boot.
    start_ms = end_ms - (steps - 1) * HEARTBEAT_INTERVAL_S * 1000

    equity_curve = _walk_equity(rng, steps)
    events: List[Dict[str, Any]] = [{
        "event": "loop_start",
        "ts": start_ms - 2_000,
        "scan_interval": 300,
        "min_score": 62,
        "config": {"mode": "LIVE", "leverage": 2},
    }]

    open_positions: List[Dict[str, Any]] = []
    day_start_equity = equity_curve[0]
    current_day = -1

    for i, equity in enumerate(equity_curve):
        ts = start_ms + i * HEARTBEAT_INTERVAL_S * 1000
        day = ts // 86_400_000
        if day != current_day:
            current_day = day
            day_start_equity = equity

        # ── close anything that has run its course ──────────────────────────
        for pos in list(open_positions):
            if ts < pos["closes_at"]:
                continue
            # Round once, then derive. Rounding the two independently made
            # `leveraged_pct` disagree with `unrealized_pct * leverage` in the
            # third decimal, and the dashboard computes the leveraged figure
            # that same way in `_closed_trades_payload` — so the trade table and
            # the event it came from would print different numbers.
            spot_pct = round(pos["outcome_pct"], 3)
            events.append({
                "event": "dsl_exit",
                "ts": ts,
                "coin": pos["coin"],
                "side": pos["side"],
                "leverage": pos["leverage"],
                "unrealized_pct": spot_pct,
                "leveraged_pct": round(spot_pct * pos["leverage"], 6),
                "pass": 3 if spot_pct > 0 else 1,
                "reason": ("trail_floor_hit" if spot_pct > 0 else "stop_loss"),
                "detail": (f"floor {spot_pct * 0.6:.2f}% crossed"
                           if spot_pct > 0 else "hard stop at -15%"),
                "executed": True,
            })
            open_positions.remove(pos)

        # ── scan every heartbeat, the way the loop does ─────────────────────
        watched = rng.sample(COINS, 6)
        scores = {c: round(rng.uniform(18, 88), 1) for c in watched}
        triggers = sum(1 for v in scores.values() if v >= 62)
        events.append({
            "event": "scan",
            "ts": ts + 1_000,
            "coins": watched,
            "coin_scores": scores,
            "triggers": triggers,
        })

        # ── act on a trigger, if there is room ──────────────────────────────
        if triggers and rng.random() < 0.16:
            if len(open_positions) >= 3:
                events.append({
                    "event": "slots_full_skip",
                    "ts": ts + 2_000,
                    "coin": watched[0],
                    "open": len(open_positions),
                    "max": 3,
                })
            else:
                # Never two positions in one coin: Hyperliquid's oneWay mode
                # nets them into a single line, so a demo showing a WIF long
                # and a WIF short side by side is a state the exchange cannot
                # be in. Pick the best-scoring coin that is not already open.
                held = {p["coin"] for p in open_positions}
                ranked = sorted(scores, key=lambda c: scores[c], reverse=True)
                candidates = [c for c in ranked if c not in held]
                if not candidates:
                    continue
                coin = candidates[0]
                book = rng.choice(BOOKS)
                side = "short" if "short" in book or "reversal" in book else "long"
                leverage = 1 if book in ("social_trending", "xs_reversal") else 2
                events.append({
                    "event": "execute",
                    "ts": ts + 2_000,
                    "coin": coin,
                    "book": book,
                    "side": side,
                    "executed": True,
                    "blocked_by": None,
                })
                # Outcome fixed at open, so the close is consistent with it.
                # Win rate ~52% with winners a little larger than losers: the
                # shape a positive-expectancy book actually has, not a fantasy.
                won = rng.random() < 0.52
                outcome = (rng.uniform(0.9, 7.4) if won else -rng.uniform(0.8, 5.1))
                open_positions.append({
                    "coin": coin,
                    "side": side,
                    "book": book,
                    "reason": _open_reason(book, coin, scores[coin], rng),
                    "leverage": leverage,
                    "outcome_pct": outcome,
                    "entry_ts": ts + 2_000,
                    "closes_at": ts + int(rng.uniform(3, 30)) * 3_600_000,
                })

        # ── the heartbeat itself ────────────────────────────────────────────
        daily_pnl = round(equity - day_start_equity, 2)
        spot_usdc = round(equity * 0.06, 2)
        events.append({
            "event": "loop_heartbeat",
            "ts": ts + 3_000,
            "equity": round(equity - spot_usdc, 2),
            "spot_usdc": spot_usdc,
            "available": round(equity * 0.41, 2),
            "daily_pnl": daily_pnl,
            "open_positions": len(open_positions),
            "dex_equity": {"main": round(equity * 0.78, 2),
                           "xyz": round(equity * 0.22, 2)},
            "dex_available": {"main": round(equity * 0.33, 2),
                              "xyz": round(equity * 0.08, 2)},
            "config": {"mode": "LIVE", "leverage": 2},
        })

        # Rare, and always recoverable — the log should look like a real one.
        if rng.random() < 0.0016:
            events.append({
                "event": "error",
                "ts": ts + 4_000,
                "scope": "hl_client",
                "error": "read timeout on info endpoint; retried once, ok",
            })

    events.sort(key=lambda e: e["ts"])
    return events, open_positions


def build_position_snapshot(now_ms: int,
                            open_positions: List[Dict[str, Any]],
                            seed: int = 20260910) -> Dict[str, Any]:
    """The positions snapshot, in Hyperliquid's own account-state shape.

    Built from the positions the session log left open, so the "3 open" on the
    heartbeat and the rows in the positions table are the same three trades. A
    demo whose panels disagree with each other is worse than an empty one.
    """
    rng = random.Random(seed ^ 0x5f5e)
    # Rough marks, only used to make the rows internally consistent.
    marks = {"BTC": 111_940.0, "ETH": 4_218.0, "SOL": 231.4, "ARB": 0.83,
             "OP": 1.42, "AVAX": 34.1, "LINK": 24.6, "SUI": 3.71, "TIA": 5.02,
             "INJ": 27.3, "SEI": 0.48, "APT": 9.14, "DOGE": 0.246,
             "WIF": 1.93, "JUP": 1.16}

    asset_positions: List[Dict[str, Any]] = []
    for pos in open_positions:
        coin = pos["coin"]
        mark = marks.get(coin, 10.0)
        notional = rng.uniform(70, 140)
        size = notional / mark
        szi = size if pos["side"] == "long" else -size
        # Part-way to its eventual outcome, so the unrealized number is
        # consistent with the close the log will eventually record.
        progress = rng.uniform(0.15, 0.7)
        move_pct = pos["outcome_pct"] * progress
        entry = mark / (1 + move_pct / 100 * (1 if pos["side"] == "long" else -1))
        unrealized = notional * (move_pct / 100)
        margin = notional / pos["leverage"]
        asset_positions.append({
            "type": "oneWay",
            "position": {
                "coin": coin,
                "szi": f"{szi:.4f}",
                "leverage": {"type": "isolated", "value": pos["leverage"],
                             "rawUsd": f"{margin:.6f}"},
                "entryPx": f"{entry:.5g}",
                "positionValue": f"{notional:.6f}",
                "unrealizedPnl": f"{unrealized:.6f}",
                "returnOnEquity": f"{move_pct * pos['leverage'] / 100:.6f}",
                "liquidationPx": f"{entry * (0.55 if pos['side'] == 'long' else 1.45):.5g}",
                "marginUsed": f"{margin:.6f}",
                "maxLeverage": 20,
                "cumFunding": {"allTime": f"{rng.uniform(-0.4, 0.4):.6f}",
                               "sinceOpen": f"{rng.uniform(-0.2, 0.2):.6f}",
                               "sinceChange": f"{rng.uniform(-0.2, 0.2):.6f}"},
            },
        })
    return {"saved_at": now_ms, "asset_positions": asset_positions}


# What each book says when it fires. Phrased the way the loop writes it, because
# this string is rendered verbatim as the "why this opened" line on the
# positions table.
_REASON_TEMPLATES = {
    "unlock_short_runin": "unlock {pct:.1f}% of circulating in {hrs}h, score {score}",
    "news_surge_short": "coverage {mult:.1f}x baseline over 6h, score {score}",
    "news_surge_multi": "{n} firehoses surging together, score {score}",
    "social_trending": "entered CoinGecko trending at rank {rank}, score {score}",
    "xs_reversal": "top-decile 3d return, funding {bps:+.0f}bp off venue baseline",
}


def _open_reason(book: str, coin: str, score: float, rng: random.Random) -> str:
    """One sentence explaining why a position exists."""
    return _REASON_TEMPLATES[book].format(
        pct=rng.uniform(1.0, 4.2), hrs=rng.choice([48, 60, 72]),
        mult=rng.uniform(2.1, 8.4), n=rng.randint(4, 11),
        rank=rng.randint(1, 7), bps=rng.uniform(-45, 45),
        score=round(score, 1), coin=coin,
    )


def build_agent_memory(open_positions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The entry-context map the positions table reads for its "why" line.

    Keyed `<coin>_<side>`, matching `memory.peek_entry_context`. Without this
    file every open position renders with a blank reason, which strips out the
    one thing that makes the table more than a list of tickers.
    """
    return {
        "entryCtx": {
            f"{p['coin']}_{p['side']}": {"book": p["book"], "reason": p["reason"]}
            for p in open_positions
        }
    }


def build_shadow_ledgers(now_ms: int, seed: int = 20260910,
                         days: int = DEFAULT_HISTORY_DAYS) -> Dict[str, List[Dict[str, Any]]]:
    """Per-book signal inventory, keyed by book name.

    This feeds the book league table, which reports how many candidate signals
    each book has logged and how many have resolved. The counts are what make
    the table honest in production — a book with 4 signals is not evidence —
    so the demo gives each book a plausible inventory rather than a round
    number, and leaves a slice of every book pending.

    Record shape is what `shadow_ledger.summary` reads: `ts`, `coin`,
    `signal_bar_t`, `entry_ref_px`, `horizon_days`.
    """
    rng = random.Random(seed ^ 0xb00c)
    window_ms = days * 86_400_000
    out: Dict[str, List[Dict[str, Any]]] = {}
    for book in BOOKS:
        n = rng.randint(38, 240)
        recs: List[Dict[str, Any]] = []
        for _ in range(n):
            ts = now_ms - rng.randint(0, window_ms)
            horizon = rng.choice([1.0, 1.0, 2.0, 3.0])
            recs.append({
                "book": book,
                "coin": rng.choice(COINS),
                "ts": ts,
                "signal_bar_t": ts - rng.randint(0, 3_600_000),
                "entry_ref_px": round(rng.uniform(0.2, 640.0), 4),
                "horizon_days": horizon,
                "side": "short" if "short" in book or "reversal" in book else "long",
            })
        recs.sort(key=lambda r: r["ts"])
        out[book] = recs
    return out


def materialize(dest_dir: str, now_ms: int | None = None,
                seed: int = 20260910,
                days: int = DEFAULT_HISTORY_DAYS) -> Dict[str, str]:
    """Write the three files and return the env vars that point at them.

    The caller sets these into `os.environ` before importing the dashboard.
    Returns a mapping rather than mutating the environment itself, so a test
    can call this without leaking state into the rest of the suite.
    """
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    os.makedirs(dest_dir, exist_ok=True)

    events, open_positions = build_session_log(now_ms, seed=seed, days=days)
    log_path = os.path.join(dest_dir, "session-log.jsonl")
    with open(log_path, "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")

    snap_path = os.path.join(dest_dir, "positions-snapshot.json")
    with open(snap_path, "w") as f:
        json.dump(build_position_snapshot(now_ms, open_positions, seed=seed), f)

    mem_path = os.path.join(dest_dir, "agent-memory.json")
    with open(mem_path, "w") as f:
        json.dump(build_agent_memory(open_positions), f)

    cfg_path = os.path.join(dest_dir, "agent-config.json")
    with open(cfg_path, "w") as f:
        json.dump(_agent_config(), f)

    # `shadow_ledger` resolves its directory as <PATHIA_STATE_DIR>/shadow_ledger,
    # so the state dir is what gets exported, not the ledger dir itself.
    ledger_dir = os.path.join(dest_dir, "state", "shadow_ledger")
    os.makedirs(ledger_dir, exist_ok=True)
    for book, recs in build_shadow_ledgers(now_ms, seed=seed, days=days).items():
        with open(os.path.join(ledger_dir, f"{book}.jsonl"), "w") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")

    return {
        "SESSION_LOG_PATH": log_path,
        "PATHIA_POSITIONS_SNAPSHOT_FILE": snap_path,
        "PATHIA_AGENT_CONFIG_FILE": cfg_path,
        "PATHIA_AGENT_MEMORY_FILE": mem_path,
        "PATHIA_STATE_DIR": os.path.join(dest_dir, "state"),
    }
