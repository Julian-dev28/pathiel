"""Persistent agent memory — a disk-backed singleton loaded from .agent-memory.json."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Anchored to the repo root (mirrors config_store.py), not os.getcwd() — so the
# MCP server and the trading loop always share one .agent-memory.json regardless
# of which directory each was launched from.
# Override with HERMES_AGENT_MEMORY_FILE when deploying behind a mounted volume.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MEMORY_FILE = os.environ.get(
    "HERMES_AGENT_MEMORY_FILE",
    os.path.join(_REPO_ROOT, ".agent-memory.json"),
)

MAX_PERCEPTIONS = 500
MAX_ANALYSES = 200
MAX_TRADES = 100
MAX_CLOSES = 500  # realized trade outcomes — backs win-rate / payoff / risk-of-ruin / Phase-3 stats


class AgentMemory:
    """Singleton — persistent in-memory state + disk persistence."""

    _instance: Optional["AgentMemory"] = None

    def __init__(self) -> None:
        self._perceptions: List[Dict[str, Any]] = []
        self._analyses: List[Dict[str, Any]] = []
        self._trades: List[Dict[str, Any]] = []
        self._closes: List[Dict[str, Any]] = []  # realized exits (the trade-outcome store)
        self._cooldowns: Dict[str, int] = {}
        self._equity: float = 0
        self._daily_pnl: float = 0
        self._peak_daily_pnl: float = 0  # high-water mark of daily_pnl (intraday, resets at UTC roll)
        self._start_of_day_equity: float = 0
        self._day_start_ts: int = 0
        self._open_positions: List[Dict[str, Any]] = []
        self._initialized = False

    @classmethod
    def get_instance(cls) -> "AgentMemory":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── Persistence ─────────────────────────────────────────────────────────

    def load(self) -> None:
        """Load state from disk."""
        if self._initialized:
            return
        try:
            with open(MEMORY_FILE, "r") as f:
                data = json.load(f)

            self._perceptions = (data.get("perceptions") or [])[:MAX_PERCEPTIONS]
            self._analyses = (data.get("analyses") or [])[:MAX_ANALYSES]
            self._trades = (data.get("trades") or [])[:MAX_TRADES]
            self._closes = (data.get("closes") or [])[:MAX_CLOSES]

            # Rebuild cooldowns
            self._cooldowns.clear()
            now = int(time.time() * 1000)
            for c in (data.get("cooldowns") or []):
                if c.get("expires", 0) > now:
                    self._cooldowns[c["coin"]] = c["expires"]

            self._equity = data.get("equity", 0)
            self._daily_pnl = data.get("dailyPnl", 0)
            self._start_of_day_equity = data.get("startOfDayEquity", 0)
            self._day_start_ts = data.get("dayStartTs", 0)
            self._open_positions = data.get("openPositions", [])

            logger.info(
                f"[memory] loaded {len(self._perceptions)} perceptions, "
                f"{len(self._analyses)} analyses, {len(self._trades)} trades from {MEMORY_FILE}"
            )
        except FileNotFoundError:
            logger.info("[memory] no existing memory file found, starting fresh")
        except Exception as e:
            logger.error(f"[memory] load failed: {e}")

        self._initialized = True

    def flush(self) -> None:
        """Save current state to disk.

        GUARD: never flush from an un-hydrated singleton. A process that imports
        memory but didn't call load() (a test, the dashboard server, an MCP tool)
        has empty in-memory state; flushing it would TRUNCATE the live
        .agent-memory.json over good data (observed 2026-06-15: a pytest run wiped
        92 trades + the day's SOD baseline, forcing a SOD re-baseline on restart).
        Only the loaded owner (the trading loop) may persist.
        """
        if not self._initialized:
            return
        try:
            data = {
                "perceptions": self._perceptions,
                "analyses": self._analyses,
                "trades": self._trades,
                "closes": self._closes,
                "cooldowns": [{"coin": coin, "expires": exp} for coin, exp in self._cooldowns.items()],
                "equity": self._equity,
                "dailyPnl": self._daily_pnl,
                "startOfDayEquity": self._start_of_day_equity,
                "dayStartTs": self._day_start_ts,
                "openPositions": self._open_positions,
            }
            with open(MEMORY_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"[memory] save failed: {e}")

    # ── Write operations ────────────────────────────────────────────────────

    def record_perception(self, p: Dict[str, Any]) -> None:
        self._perceptions.append(p)
        if len(self._perceptions) > MAX_PERCEPTIONS:
            self._perceptions.pop(0)

    def record_analysis(self, a: Dict[str, Any]) -> None:
        self._analyses.append(a)
        if len(self._analyses) > MAX_ANALYSES:
            self._analyses.pop(0)

    def record_trade(self, t: Dict[str, Any]) -> None:
        self._trades.append(t)
        if len(self._trades) > MAX_TRADES:
            self._trades.pop(0)

    def record_close(self, c: Dict[str, Any]) -> None:
        """Append a realized exit to the outcome store and persist.

        This is THE source of realized PnL — previously outcomes only existed in
        log text (trades[].pnl was never populated), so win-rate / payoff / RoR /
        Phase-3 stats had nothing to read. Called from close_position_market so a
        single chokepoint covers DSL, AI-close, and kill-switch exits.
        Expected keys: coin, side, entry_px, exit_px, spot_pct, realized_pnl_pct
        (leveraged, net fees), realized_pnl_usd, leverage, closed_at.
        """
        self._closes.append(c)
        if len(self._closes) > MAX_CLOSES:
            self._closes.pop(0)
        self.flush()

    def update_equity(self, eq: float) -> None:
        self._equity = eq

    def track_daily_pnl(self, current_equity: float, net_contributions: float = 0.0) -> None:
        """Reset baseline at UTC midnight so dailyPnl reflects today's gains.

        `net_contributions` is the cumulative USDC flow into the tradeable
        equity pool since `_day_start_ts` (positive = money came in,
        negative = money left). Subtracting it makes daily PnL invariant
        to deposits, withdrawals, and spot↔perp transfers — otherwise a
        $50 spot→perp transfer looks like $50 of trading profit. Callers
        that don't have a ledger source should pass 0 (degrades to the
        old behavior).
        """
        from datetime import datetime, timezone
        import time as _time
        # ── Partial-dex degraded-read filter ────────────────────────────
        # A flaky per-dex query can drop a whole clearinghouse from the
        # aggregate (observed 2026-06-12 08:06: aggregate momentarily read
        # xyz-only $59.7 vs true $98.7 → dailyPnl printed −$39 and tripped
        # the daily-loss gate; had it landed in the heartbeat instead, the
        # HARD kill-switch would have flattened the whole book on fiction).
        # A real >25% equity move inside 3 minutes is impossible at ~2x
        # gross book without liquidation, so reject fast spikes and keep
        # the prior reading; a SUSTAINED move re-asserts itself after 180s
        # and is then accepted (genuine crash detection delayed ≤3min).
        now_s = _time.time()
        prev_eq = getattr(self, "_last_eq_reading", 0.0)
        prev_ts = getattr(self, "_last_eq_reading_ts", 0.0)
        if (prev_eq > 0 and current_equity > 0
                and (now_s - prev_ts) < 180
                and abs(current_equity - prev_eq) / prev_eq > 0.25):
            logger.error(
                f"[memory] IMPLAUSIBLE equity swing ${prev_eq:.2f} -> "
                f"${current_equity:.2f} in {now_s - prev_ts:.0f}s — suspected "
                f"partial-dex degraded read; IGNORING this tick (kill-switch "
                f"protected). If real, it will re-assert after 180s."
            )
            return
        self._last_eq_reading = current_equity
        self._last_eq_reading_ts = now_s

        today_utc = int(datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).timestamp())
        if self._day_start_ts < today_utc or self._start_of_day_equity == 0:
            self._start_of_day_equity = current_equity
            self._day_start_ts = today_utc
            self._daily_pnl = 0
            self._peak_daily_pnl = 0  # reset high-water mark at the UTC day roll
        else:
            self._daily_pnl = current_equity - self._start_of_day_equity - net_contributions
        # Track the day's peak PnL so a give-back breaker can lock in green days.
        self._peak_daily_pnl = max(self._peak_daily_pnl, self._daily_pnl)
        self._equity = current_equity

    def peak_daily_pnl(self) -> float:
        """Intraday high-water mark of daily PnL (resets at UTC midnight)."""
        return self._peak_daily_pnl

    # ── Loss cooldown (anti-revenge re-entry) ───────────────────────────────
    # Backed by the persisted `_cooldowns` dict (coin -> expires_ms), which was
    # serialized but never written/read until 2026-06-11 — wired up after TON
    # was churned 3x in one day (-1.4%, -0.9%, -6.5% ROE): the AI re-bought the
    # same falling name as soon as the standard 60min cooldown expired.

    def set_loss_cooldown(self, coin: str, until_ms: int) -> None:
        """Block re-entry on `coin` until `until_ms` (epoch ms)."""
        self._cooldowns[coin] = int(until_ms)
        self.flush()

    def loss_cooldown_remaining_min(self, coin: str) -> float:
        """Minutes left on `coin`'s loss cooldown (0 when expired/absent)."""
        exp = self._cooldowns.get(coin)
        if not exp:
            return 0.0
        import time as _t
        remaining = (int(exp) - int(_t.time() * 1000)) / 60_000
        if remaining <= 0:
            self._cooldowns.pop(coin, None)
            return 0.0
        return remaining

    def update_open_positions(self, pos: List[Dict[str, Any]]) -> None:
        self._open_positions = list(pos)

    def open_position_coins(self) -> set:
        """Set of coins with a live (non-zero) open position. The loop exempts
        these from the pre-research cooldown so the AI can still issue a CLOSE
        on something we already hold — AI-driven exits must never be starved by
        the re-entry cooldown."""
        coins = set()
        for p in self._open_positions:
            if not isinstance(p, dict):
                continue
            pos = p.get("position", p)
            coin = pos.get("coin")
            try:
                if coin and float(pos.get("szi", 0) or 0) != 0:
                    coins.add(coin)
            except (TypeError, ValueError):
                continue
        return coins

    # ── Read operations ─────────────────────────────────────────────────────

    def get_recent_perceptions(self, limit: int = 20) -> List[Dict[str, Any]]:
        return self._perceptions[-limit:]

    def get_recent_analyses(self, limit: int = 20) -> List[Dict[str, Any]]:
        return self._analyses[-limit:]

    def get_recent_trades(self, limit: int = 20) -> List[Dict[str, Any]]:
        return self._trades[-limit:]

    def latest_trade_ts_by_coin(self, limit: int = 20) -> Dict[str, int]:
        """Map each coin to its NEWEST executed_at within the last `limit`
        trades. Backs the loop's pre-research cooldown — must be the newest,
        not the oldest, or a coin traded twice in the window keeps paying for
        redundant LLM research while it's still inside its cooldown."""
        out: Dict[str, int] = {}
        for t in self.get_recent_trades(limit):  # chronological → newest wins
            if t.get("coin") and t.get("executed_at"):
                out[t["coin"]] = t["executed_at"]
        return out

    def get_all_trades(self) -> List[Dict[str, Any]]:
        return list(self._trades)

    def get_all_analyses(self) -> List[Dict[str, Any]]:
        return list(self._analyses)

    def get_analysis_by_id(self, id: str) -> Optional[Dict[str, Any]]:
        for a in self._analyses:
            if a["id"] == id:
                return a
        return None

    def get_win_rate(self) -> Dict[str, float]:
        # Prefer the realized outcome store; fall back to the legacy (never-
        # populated) trades[].pnl shape for backward compat.
        if self._closes:
            wins = sum(1 for c in self._closes if (c.get("realized_pnl_pct") or 0) > 0)
            total = len(self._closes)
            return {"wins": wins, "total": total, "rate": wins / total if total else 0}
        closed = [t for t in self._trades if t.get("exitPx") is not None and t.get("pnl") is not None]
        wins = sum(1 for t in closed if (t.get("pnl") or 0) > 0)
        total = len(closed)
        return {"wins": wins, "total": total, "rate": wins / total if total > 0 else 0}

    def get_payoff_stats(self, limit: int = 200) -> Dict[str, float]:
        """Realized win-rate + payoff ratio (avg win / avg loss) from the outcome
        store — the inputs to risk-of-ruin and the Phase-3 report. Uses leveraged
        realized_pnl_pct (net fees). Returns zeros when there are no closes yet."""
        rows = self._closes[-limit:]
        wins = [float(c.get("realized_pnl_pct") or 0) for c in rows if (c.get("realized_pnl_pct") or 0) > 0]
        losses = [abs(float(c.get("realized_pnl_pct") or 0)) for c in rows if (c.get("realized_pnl_pct") or 0) <= 0]
        n = len(rows)
        win_rate = len(wins) / n if n else 0.0
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0
        payoff = (avg_win / avg_loss) if avg_loss > 0 else 0.0
        return {
            "n": n, "win_rate": win_rate, "avg_win_pct": avg_win,
            "avg_loss_pct": avg_loss, "payoff_ratio": payoff,
        }

    def get_closes(self, limit: int = 200) -> List[Dict[str, Any]]:
        return self._closes[-limit:]

    def get_daily_pnl(self) -> float:
        return self._daily_pnl

    def get_day_start_ts(self) -> int:
        """UTC-midnight unix-seconds timestamp for the in-progress trading day."""
        return self._day_start_ts

    def get_full_state(self) -> Dict[str, Any]:
        return {
            "recent_perceptions": self.get_recent_perceptions(),
            "recent_analyses": self.get_recent_analyses(),
            "recent_trades": self.get_recent_trades(),
            "win_rate": self.get_win_rate(),
            "equity": self._equity,
            "daily_pnl": self._daily_pnl,
            "start_of_day_equity": self._start_of_day_equity,
            "open_positions": self._open_positions,
        }


# Module-level singleton.
memory = AgentMemory.get_instance()
