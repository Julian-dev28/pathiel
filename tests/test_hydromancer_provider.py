from __future__ import annotations

import json

import pytest

from pathia.data_providers.hydromancer import (
    HydromancerClient,
    HydromancerConfig,
    HydromancerError,
    build_ws_url,
    chunk_records,
    stream_record,
    subscribe_message,
    unsubscribe_message,
)
from pathia.data_providers.warehouse import JsonlWarehouse


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class _Session:
    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    def post(self, url, *, json, headers, timeout):
        self.calls.append({
            "url": url,
            "json": json,
            "headers": headers,
            "timeout": timeout,
        })
        return self.resp


def _client(session):
    return HydromancerClient(
        HydromancerConfig(
            api_key="hydro-key",
            base_url="https://hydro.local",
            ws_url="wss://hydro.local/ws",
            timeout_s=3.5,
        ),
        session=session,
    )


def test_info_posts_to_hydromancer_with_bearer_auth():
    session = _Session(_Resp({"ok": True}))
    out = _client(session).info({"type": "fundingHistory", "coin": "BTC", "startTime": 1})

    assert out == {"ok": True}
    assert session.calls == [{
        "url": "https://hydro.local/info",
        "json": {"type": "fundingHistory", "coin": "BTC", "startTime": 1},
        "headers": {
            "Authorization": "Bearer hydro-key",
            "Content-Type": "application/json",
        },
        "timeout": 3.5,
    }]


def test_info_rejects_missing_api_key_before_network_call():
    session = _Session(_Resp({"ok": True}))
    client = HydromancerClient(HydromancerConfig(api_key="", base_url="https://hydro.local"), session=session)

    with pytest.raises(HydromancerError, match="HYDROMANCER_API_KEY"):
        client.info({"type": "fundingHistory"})
    assert session.calls == []


def test_convenience_wrappers_preserve_endpoint_type_names():
    session = _Session(_Resp([]))
    client = _client(session)

    client.user_fills_by_time("0xabc", 10, 20)
    client.market_liquidity_history("BTC", 100, 200)
    client.max_market_order_ntls()

    assert [c["json"]["type"] for c in session.calls] == [
        "userFillsByTime",
        "marketLiquidityHistory",
        "maxMarketOrderNtls",
    ]
    assert session.calls[0]["json"] == {
        "type": "userFillsByTime",
        "user": "0xabc",
        "startTime": 10,
        "endTime": 20,
    }


def test_error_status_raises_provider_error():
    session = _Session(_Resp({"error": "Invalid API key"}, status_code=401))

    with pytest.raises(HydromancerError, match="Invalid API key"):
        _client(session).historical_orders("0xabc")


def test_websocket_url_and_subscription_messages():
    assert build_ws_url("k", base_ws_url="wss://example/ws") == (
        "wss://example/ws?token=k&liveFormat=chunked-v1"
    )
    assert subscribe_message("userFills", addresses=["0xabc"]) == {
        "type": "subscribe",
        "subscription": {"type": "userFills", "addresses": ["0xabc"]},
    }
    assert unsubscribe_message("fundingRates", coin="BTC") == {
        "type": "unsubscribe",
        "subscription": {"type": "fundingRates", "coin": "BTC"},
    }


def test_stream_records_are_warehouse_ready():
    rec = stream_record("allFills", {"coin": "BTC"}, received_at_ms=123)
    assert rec == {
        "source": "hydromancer",
        "stream": "allFills",
        "received_at": 123,
        "payload": {"coin": "BTC"},
    }

    rows = chunk_records("fundingRates", [{"coin": "BTC"}, {"coin": "ETH"}])
    assert [r["payload"]["coin"] for r in rows] == ["BTC", "ETH"]
    assert {r["stream"] for r in rows} == {"fundingRates"}


def test_jsonl_warehouse_appends_and_reads(tmp_path):
    wh = JsonlWarehouse(root=str(tmp_path / "warehouse"))

    wh.append("hydro_funding", {"coin": "BTC", "rate": 0.01})
    n = wh.append_many("hydro_funding", [{"coin": "ETH", "rate": -0.02}])

    assert n == 1
    assert wh.read_all("hydro_funding") == [
        {"coin": "BTC", "rate": 0.01},
        {"coin": "ETH", "rate": -0.02},
    ]


def test_jsonl_warehouse_rejects_path_traversal(tmp_path):
    wh = JsonlWarehouse(root=str(tmp_path))
    with pytest.raises(ValueError):
        wh.append("../bad", {"x": 1})
