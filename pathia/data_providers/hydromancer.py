"""Hydromancer data-plane client.

This is intentionally separate from ``pathia.client.hl_client``:
Hydromancer is useful for research/backfill/streaming data, but the live
execution path should continue to use the native Hyperliquid SDK/API.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional
from urllib.parse import urlencode

import requests

MAINNET_REST_URL = "https://api.hydromancer.xyz"
TESTNET_REST_URL = "https://api-testnet.hydromancer.xyz"
MAINNET_WS_URL = "wss://api.hydromancer.xyz/ws"
TESTNET_WS_URL = "wss://api-testnet.hydromancer.xyz/ws"


class HydromancerError(RuntimeError):
    """Raised when Hydromancer returns an error response or invalid payload."""


@dataclass(frozen=True)
class HydromancerConfig:
    api_key: str
    base_url: str = MAINNET_REST_URL
    ws_url: str = MAINNET_WS_URL
    timeout_s: float = 10.0

    @classmethod
    def from_env(cls) -> "HydromancerConfig":
        api_key = os.environ.get("HYDROMANCER_API_KEY", "").strip()
        testnet = os.environ.get("HYDROMANCER_TESTNET", "").strip().lower() in {
            "1", "true", "yes", "on",
        }
        return cls(
            api_key=api_key,
            base_url=os.environ.get(
                "HYDROMANCER_BASE_URL",
                TESTNET_REST_URL if testnet else MAINNET_REST_URL,
            ).rstrip("/"),
            ws_url=os.environ.get(
                "HYDROMANCER_WS_URL",
                TESTNET_WS_URL if testnet else MAINNET_WS_URL,
            ).rstrip("/"),
            timeout_s=float(os.environ.get("HYDROMANCER_TIMEOUT_S", "10")),
        )


class HydromancerClient:
    """Small POST-/info client for Hydromancer REST endpoints.

    The client accepts an injectable ``requests.Session``-like object, so tests
    can validate all request shapes without touching the network.
    """

    def __init__(
        self,
        config: Optional[HydromancerConfig] = None,
        *,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.config = config or HydromancerConfig.from_env()
        self.session = session or requests.Session()

    def _headers(self) -> Dict[str, str]:
        if not self.config.api_key:
            raise HydromancerError("HYDROMANCER_API_KEY is not set")
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

    def info(self, payload: Dict[str, Any]) -> Any:
        req_type = payload.get("type")
        if not isinstance(req_type, str) or not req_type:
            raise ValueError("Hydromancer /info payload requires a non-empty string 'type'")
        resp = self.session.post(
            f"{self.config.base_url}/info",
            json=payload,
            headers=self._headers(),
            timeout=self.config.timeout_s,
        )
        if getattr(resp, "status_code", 200) >= 400:
            try:
                body = resp.json()
            except Exception:
                body = getattr(resp, "text", "")
            raise HydromancerError(f"Hydromancer {req_type} failed: {body}")
        try:
            return resp.json()
        except Exception as exc:
            raise HydromancerError(f"Hydromancer {req_type} returned non-JSON") from exc

    # Convenience wrappers for endpoints that are useful to this repo's research
    # and attribution pipeline. They preserve Hydromancer's ``type`` names so
    # request logs remain easy to compare to docs.

    def funding_history(
        self,
        coin: str,
        start_time: int,
        end_time: Optional[int] = None,
    ) -> Any:
        payload: Dict[str, Any] = {
            "type": "fundingHistory",
            "coin": coin,
            "startTime": int(start_time),
        }
        if end_time is not None:
            payload["endTime"] = int(end_time)
        return self.info(payload)

    def user_fills_by_time(
        self,
        user: str,
        start_time: int,
        end_time: Optional[int] = None,
    ) -> Any:
        payload: Dict[str, Any] = {
            "type": "userFillsByTime",
            "user": user,
            "startTime": int(start_time),
        }
        if end_time is not None:
            payload["endTime"] = int(end_time)
        return self.info(payload)

    def user_non_funding_ledger_updates(self, user: str, start_time: int) -> Any:
        return self.info({
            "type": "userNonFundingLedgerUpdates",
            "user": user,
            "startTime": int(start_time),
        })

    def historical_orders(self, user: str) -> Any:
        return self.info({"type": "historicalOrders", "user": user})

    def market_liquidity(self, coin: str) -> Any:
        return self.info({"type": "marketLiquidity", "coin": coin})

    def market_liquidity_history(self, coin: str, start_time: int, end_time: int) -> Any:
        return self.info({
            "type": "marketLiquidityHistory",
            "coin": coin,
            "startTime": int(start_time),
            "endTime": int(end_time),
        })

    def slippage_history(self, coin: str, start_time: int, end_time: int) -> Any:
        return self.info({
            "type": "slippageHistory",
            "coin": coin,
            "startTime": int(start_time),
            "endTime": int(end_time),
        })

    def max_market_order_ntls(self) -> Any:
        return self.info({"type": "maxMarketOrderNtls"})


def build_ws_url(
    api_key: str,
    *,
    base_ws_url: str = MAINNET_WS_URL,
    live_format: Optional[str] = "chunked-v1",
) -> str:
    if not api_key:
        raise ValueError("api_key is required")
    params = {"token": api_key}
    if live_format:
        params["liveFormat"] = live_format
    return f"{base_ws_url}?{urlencode(params)}"


def subscribe_message(subscription_type: str, **fields: Any) -> Dict[str, Any]:
    if not subscription_type:
        raise ValueError("subscription_type is required")
    return {
        "type": "subscribe",
        "subscription": {
            "type": subscription_type,
            **{k: v for k, v in fields.items() if v is not None},
        },
    }


def unsubscribe_message(subscription_type: str, **fields: Any) -> Dict[str, Any]:
    msg = subscribe_message(subscription_type, **fields)
    msg["type"] = "unsubscribe"
    return msg


def stream_record(
    stream: str,
    payload: Dict[str, Any],
    *,
    received_at_ms: Optional[int] = None,
    source: str = "hydromancer",
) -> Dict[str, Any]:
    """Normalize a live message for append-only local storage."""
    return {
        "source": source,
        "stream": stream,
        "received_at": int(received_at_ms if received_at_ms is not None else time.time() * 1000),
        "payload": payload,
    }


def chunk_records(stream: str, rows: Iterable[Dict[str, Any]]) -> list[Dict[str, Any]]:
    """Wrap a batch of provider rows as independent warehouse records."""
    now = int(time.time() * 1000)
    return [stream_record(stream, row, received_at_ms=now) for row in rows]
