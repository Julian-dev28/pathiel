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


class AgentMemory:
    """Singleton — persistent in-memory state + disk persistence."""

    _instance: Optional["AgentMemory"] = None

    def __init__(self) -> None:
        self._perceptions: List[Dict[str, Any]] = []
        self._analyses: List[Dict[str, Any]] = []
        self._trades: List[Dict[str, Any]] = []
        self._cooldowns: Dict[str, int] = {}
        self._equity: float = 0
        self._daily_pnl: float = 0
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
        """Save current state to disk."""
        try:
            data = {
                "perceptions": self._perceptions,
                "analyses": self._analyses,
                "trades": self._trades,
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
        today_utc = int(datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).timestamp())
        if self._day_start_ts < today_utc or self._start_of_day_equity == 0:
            self._start_of_day_equity = current_equity
            self._day_start_ts = today_utc
            self._daily_pnl = 0
        else:
            self._daily_pnl = current_equity - self._start_of_day_equity - net_contributions
        self._equity = current_equity

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
        closed = [t for t in self._trades if t.get("exitPx") is not None and t.get("pnl") is not None]
        wins = sum(1 for t in closed if (t.get("pnl") or 0) > 0)
        total = len(closed)
        return {"wins": wins, "total": total, "rate": wins / total if total > 0 else 0}

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
