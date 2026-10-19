"""`GET /api/accounts/active/summary` — the active account's live Hyperliquid
state, dressed up with plain-English reasons trading is (or isn't) available.

Built on `pathiel.client.hl_client.fetch_account_state`, which this module
calls with `include_hip3=True` so HIP-3 dex equity and positions are folded
into the totals, exactly like the dashboard's own summary does.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from contracts.accounts.models import ActiveSummary
from services.accounts import view

logger = logging.getLogger(__name__)


def _blockers_for(account, state_ok: bool) -> List[str]:
    blockers: List[str] = []
    if account is None:
        blockers.append("No account connected yet — connect a Hyperliquid wallet to trade.")
        return blockers
    if account.mode == "watch":
        blockers.append("Watch-only account: connect a wallet to trade.")
    elif account.source != "env" and account.verified_at is None:
        blockers.append("Agent not yet approved by Hyperliquid — finish connecting this account.")
    if not state_ok:
        blockers.append("Could not read this account's live state from Hyperliquid right now.")
    return blockers


def get_active_summary(max_age_s: Optional[float] = None) -> ActiveSummary:
    return get_active_snapshot(max_age_s)[0]


def get_active_snapshot(max_age_s: Optional[float] = None
                        ) -> Tuple[ActiveSummary, Optional[Dict[str, Any]]]:
    """get_active_summary() plus the raw fetch_account_state() dict it was
    built from (per-dex equity, positions, spot), or None when there is no
    account or the read failed. For callers that need the detail (the MCP
    account_overview tool) without paying for a second Hyperliquid read.

    The state comes from pathiel.client.account_snapshot: shared by every
    page and poll, served at once and re-read in the background (one read in
    flight at a time). `max_age_s`: for a caller that must not see an older
    read (the MCP passes 0: always a new read, shared with concurrent calls)."""
    account = view.get_effective_active_account()
    if account is None:
        return ActiveSummary(account=None, can_trade=False,
                             blockers=_blockers_for(None, state_ok=True)), None

    state = None
    state_ok = True
    try:
        from pathiel.client.account_snapshot import account_state
        state = account_state(account.main_address, include_hip3=True, max_age_s=max_age_s)
    except Exception as e:
        logger.warning(f"[accounts.summary] account read failed for "
                       f"{account.main_address}: {e}")
        state_ok = False

    blockers = _blockers_for(account, state_ok)
    can_trade = account.mode == "trade" and account.verified_at is not None and not blockers

    if state is None:
        # _blockers_for always adds the "could not read live state" blocker
        # when state_ok is False, so blockers is never empty here — can_trade
        # is correctly False and there is nothing else numeric to report.
        return ActiveSummary(account=account, can_trade=can_trade, blockers=blockers), None

    return ActiveSummary(
        account=account,
        equity_usd=round(float(state.get("equity", 0.0) or 0.0), 2),
        withdrawable_usd=round(float(state.get("withdrawable_aggregated", 0.0) or 0.0), 2),
        open_positions=len(state.get("asset_positions") or []),
        can_trade=can_trade,
        blockers=blockers,
    ), state
