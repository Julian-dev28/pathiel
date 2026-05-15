"""Agent analysis and trade models.

Converts TypeScript AgentAnalysis and AgentTrade types from lib/agent/memory.ts.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel

from hermes_agent.models.types import AgentVerdict


class AgentAnalysis(BaseModel):
    """Result of a deep AI research analysis on a perception."""
    id: str
    perception_id: str
    coin: str
    verdict: AgentVerdict
    confidence: float
    side: Optional[str] = None  # 'long', 'short', or None
    entry_px: Optional[float] = None
    stop_px: Optional[float] = None
    tp_px: Optional[float] = None
    reasoning: str
    news_context: Optional[str] = None
    created_at: int  # Date.now() in ms


class AgentTrade(BaseModel):
    """Record of an executed trade."""
    id: str
    analysis_id: str
    coin: str
    side: str  # 'long' or 'short'
    entry_px: float
    size_usd: float
    order_id: Optional[str] = None
    exit_px: Optional[float] = None
    pnl: Optional[float] = None
    executed_at: int  # Date.now() in ms
    exited_at: Optional[int] = None


class WatchlistEntry(BaseModel):
    """A coin on the agent watchlist."""
    coin: str
    type: str  # 'perp' or 'spot'
    mid: float
    composite_score: float
    last_perception_at: int
    status: str  # 'scanning', 'analyzing', 'analyzed', 'blocked', 'executed'
    block_reason: Optional[str] = None
