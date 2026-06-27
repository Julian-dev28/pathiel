import json

from hermes_trader.agents import data_logger


def test_data_logger_skips_spot_and_zero_rows(monkeypatch, tmp_path):
    log_file = tmp_path / "funding_oi.jsonl"
    ts_file = tmp_path / "funding_oi.ts"
    monkeypatch.setattr(data_logger, "_LOG_FILE", str(log_file))
    monkeypatch.setattr(data_logger, "_TS_FILE", str(ts_file))
    monkeypatch.setattr(data_logger.time, "time", lambda: 1_000_000.0)

    data_logger.maybe_log(
        {"data_logger": {"enabled": True, "interval_hours": 1.0}},
        [
            {
                "coin": "BTC",
                "type": "perp",
                "dex": None,
                "funding": 0.00001,
                "openInterest": 10.0,
                "markPx": 100.0,
                "dayNtlVlm": 1_000_000,
            },
            {
                "coin": "@1",
                "type": "spot",
                "funding": 0,
                "openInterest": 0,
                "markPx": 1.0,
                "dayNtlVlm": 1_000,
            },
            {
                "coin": "DEAD",
                "type": "perp",
                "funding": 0,
                "openInterest": 0,
                "markPx": 1.0,
                "dayNtlVlm": 1_000,
            },
        ],
    )

    rec = json.loads(log_file.read_text())
    assert rec["n"] == 1
    assert rec["rows"] == [
        {
            "c": "BTC",
            "type": "perp",
            "dex": None,
            "f": 0.00001,
            "oi": 10.0,
            "px": 100.0,
            "v": 1_000_000,
        }
    ]


def test_data_logger_keeps_zero_funding_with_open_interest(monkeypatch, tmp_path):
    log_file = tmp_path / "funding_oi.jsonl"
    ts_file = tmp_path / "funding_oi.ts"
    monkeypatch.setattr(data_logger, "_LOG_FILE", str(log_file))
    monkeypatch.setattr(data_logger, "_TS_FILE", str(ts_file))
    monkeypatch.setattr(data_logger.time, "time", lambda: 1_000_000.0)

    data_logger.maybe_log(
        {"data_logger": {"enabled": True}},
        [{"coin": "ETH", "type": "perp", "funding": 0, "openInterest": 3.0, "markPx": 10.0}],
    )

    rec = json.loads(log_file.read_text())
    assert rec["rows"][0]["c"] == "ETH"
    assert rec["rows"][0]["f"] == 0
    assert rec["rows"][0]["oi"] == 3.0
