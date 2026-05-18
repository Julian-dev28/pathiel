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
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MEMORY_FILE = os.path.join(_REPO_ROOT, ".agent-memory.json")

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
        self._watchlist: Dict[str, Dict[str, Any]] = {}
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

            # Rebuild watchlist
            self._watchlist.clear()
            for w in (data.get("watchlist") or []):
                self._watchlist[w["coin"]] = {
                    "coin": w["coin"],
                    "type": w.get("type", "perp"),
                    "mid": w.get("mid", 0),
                    "composite_score": w.get("composite_score", 0),
                    "last_perception_at": w.get("last_perception_at", 0),
                    "status": w.get("status", "scanning"),
                    "block_reason": w.get("block_reason"),
                }

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
                "watchlist": list(self._watchlist.values()),
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

    def update_watchlist(self, percs: List[Dict[str, Any]]) -> None:
        for p in percs:
            existing = self._watchlist.get(p["coin"])
            if existing:
                existing["mid"] = p.get("mid", existing["mid"])
                existing["composite_score"] = p.get("composite_score", existing["composite_score"])
                existing["last_perception_at"] = p.get("fired_at", existing["last_perception_at"])
                if existing["status"] == "scanning":
                    existing["status"] = "analyzed"
            else:
                self._watchlist[p["coin"]] = {
                    "coin": p["coin"],
                    "type": p.get("type", "perp"),
                    "mid": p.get("mid", 0),
                    "composite_score": p.get("composite_score", 0),
                    "last_perception_at": p.get("fired_at", int(time.time() * 1000)),
                    "status": "analyzed",
                }

        # Sweep unsighted coins to 'scanning'
        current_coins = {p["coin"] for p in percs}
        for coin, entry in self._watchlist.items():
            if coin not in current_coins:
                entry["status"] = "scanning"

    def update_equity(self, eq: float) -> None:
        self._equity = eq

    def track_daily_pnl(self, current_equity: float) -> None:
        """Reset baseline at UTC midnight so dailyPnl reflects today's gains."""
        from datetime import datetime, timezone
        today_utc = int(datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).timestamp())
        if self._day_start_ts < today_utc or self._start_of_day_equity == 0:
            self._start_of_day_equity = current_equity
            self._day_start_ts = today_utc
            self._daily_pnl = 0
        else:
            self._daily_pnl = current_equity - self._start_of_day_equity
        self._equity = current_equity

    def update_daily_pnl(self, pnl: float) -> None:
        self._daily_pnl = pnl

    def update_open_positions(self, pos: List[Dict[str, Any]]) -> None:
        self._open_positions = list(pos)

    def set_cooldown(self, coin: str, minutes: float) -> None:
        self._cooldowns[coin] = int(time.time() * 1000) + int(minutes * 60_000)

    def set_status(self, coin: str, status: str, block_reason: Optional[str] = None) -> None:
        entry = self._watchlist.get(coin)
        if entry:
            entry["status"] = status
            entry["block_reason"] = block_reason

    # ── Read operations ─────────────────────────────────────────────────────

    def get_recent_perceptions(self, limit: int = 20) -> List[Dict[str, Any]]:
        return self._perceptions[-limit:]

    def get_recent_analyses(self, limit: int = 20) -> List[Dict[str, Any]]:
        return self._analyses[-limit:]

    def get_recent_trades(self, limit: int = 20) -> List[Dict[str, Any]]:
        return self._trades[-limit:]

    def get_all_trades(self) -> List[Dict[str, Any]]:
        return list(self._trades)

    def get_all_analyses(self) -> List[Dict[str, Any]]:
        return list(self._analyses)

    def get_watchlist(self) -> List[Dict[str, Any]]:
        return sorted(self._watchlist.values(), key=lambda e: e["composite_score"], reverse=True)

    def get_watchlist_entry(self, coin: str) -> Optional[Dict[str, Any]]:
        return self._watchlist.get(coin)

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

    def get_equity(self) -> float:
        return self._equity

    def get_daily_pnl(self) -> float:
        return self._daily_pnl

    def get_open_positions(self) -> List[Dict[str, Any]]:
        return list(self._open_positions)

    def in_cooldown(self, coin: str) -> bool:
        expires = self._cooldowns.get(coin)
        if expires is None:
            return False
        return int(time.time() * 1000) < expires

    def get_full_state(self) -> Dict[str, Any]:
        return {
            "watchlist": self.get_watchlist(),
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
