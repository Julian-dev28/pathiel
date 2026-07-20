"""Gate tests for the news_surge_short LIVE book (reverse-refuted audit,
2026-07-20): SHORT a breaking Google-News coverage surge on xyz equities,
the exact inverse of the demolished news_catalyst LONG book. Crypto reads
still record (zero capital); only equities trade."""
import pytest

from hermes_trader.agents import news_surge_short_live as nssl
from hermes_trader.agents.news_catalyst import Article, CatalystReport


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(nssl, "_TS_FILE", str(tmp_path / "ts.json"))


def _captured(monkeypatch):
    rows_out = []

    def fake_many(book, rows):
        rows_out.append((book, rows))
        return len(rows or [])

    monkeypatch.setattr(nssl.shadow_ledger, "record_many", fake_many)
    return rows_out


def _report(breaking=False, surge=1.0, n=2):
    return CatalystReport(
        query="X", n_recent=n, breaking=breaking, surge_x=surge,
        headlines=[Article(title=f"headline {i}", url="u", domain="d", seen=None)
                   for i in range(4)],
    )


def test_is_xyz_equity_scope():
    assert nssl._is_xyz_equity("xyz:IBM") is True
    assert nssl._is_xyz_equity("BTC") is False
    assert nssl._is_xyz_equity("") is False


def test_records_to_a_new_book_never_the_old_long_ledger(monkeypatch):
    out = _captured(monkeypatch)
    monkeypatch.setattr(nssl, "coin_catalyst",
                        lambda c: _report(breaking=(c == "xyz:HOT"), surge=4.0 if c == "xyz:HOT" else 0.5))
    percs = [{"coin": "xyz:HOT", "mid": 2.5}, {"coin": "COLD", "mid": 1.0}]
    assert nssl.maybe_run({}, percs) == 2
    book, rows = out[0]
    assert book == "news_surge_short"
    by_coin = {r["coin"]: r for r in rows}
    assert by_coin["xyz:HOT"]["meta"]["breaking"] is True
    assert by_coin["xyz:HOT"]["meta"]["equity"] is True
    assert by_coin["COLD"]["meta"]["breaking"] is False   # the built-in null
    assert by_coin["COLD"]["meta"]["equity"] is False
    # SHORT-only: the entire ledger records the inverted side, both arms
    assert by_coin["xyz:HOT"]["side"] == "short"
    assert by_coin["COLD"]["side"] == "short"
    assert by_coin["xyz:HOT"]["stop_pct"] == 15.0


def test_throttle_one_pass_per_interval(monkeypatch):
    out = _captured(monkeypatch)
    monkeypatch.setattr(nssl, "coin_catalyst", lambda c: _report())
    percs = [{"coin": "A", "mid": 1.0}]
    assert nssl.maybe_run({}, percs) == 1
    assert nssl.maybe_run({}, percs) == 0   # inside the 30-min window
    assert len(out) == 1


def test_throttle_marks_before_reads_so_failures_dont_storm(monkeypatch):
    _captured(monkeypatch)

    def boom(c):
        raise OSError("rss down")

    monkeypatch.setattr(nssl, "coin_catalyst", boom)
    assert nssl.maybe_run({}, [{"coin": "A", "mid": 1.0}]) == 0
    assert nssl._last_pass_ms() > 0


def test_bounded_coins_dedup_and_bad_mids(monkeypatch):
    out = _captured(monkeypatch)
    monkeypatch.setattr(nssl, "coin_catalyst", lambda c: _report())
    percs = ([{"coin": "DUP", "mid": 1.0}, {"coin": "DUP", "mid": 1.0},
              {"coin": "NOMID", "mid": 0.0}]
             + [{"coin": f"C{i}", "mid": 1.0} for i in range(20)])
    n = nssl.maybe_run({}, percs)
    assert n <= nssl._MAX_COINS_PER_PASS
    coins = [r["coin"] for r in out[0][1]]
    assert coins.count("DUP") == 1 and "NOMID" not in coins


def test_hot_kill(monkeypatch):
    out = _captured(monkeypatch)
    assert nssl.maybe_run({"news_surge_short": {"enabled": False}},
                          [{"coin": "A", "mid": 1.0}]) == 0
    assert out == []


class _FakeClaims:
    def __init__(self):
        self.claimed, self.released = [], []

    def prune_to(self, held, book):
        pass

    def claimed_by_others(self, book):
        return set()

    def claim(self, coin, book):
        self.claimed.append(coin)
        return True

    def release(self, coin, book):
        self.released.append(coin)

    def save(self):
        pass


def _live_cfg(shadow_only=False, **overrides):
    cfg = {"enabled": True, "shadow_only": shadow_only,
           "notional_usd": 20.0, "leverage": 10,
           "stop_pct": 15.0, "hold_days": 1.0,
           "max_new_per_cycle": 1}
    cfg.update(overrides)
    return {"news_surge_short": cfg}


@pytest.fixture()
def _live_iso(tmp_path, monkeypatch):
    monkeypatch.setattr(nssl, "_SEEN_FILE", str(tmp_path / "live_seen.json"))
    monkeypatch.setattr(nssl, "active_position_coins", lambda: {})
    claims = _FakeClaims()
    monkeypatch.setattr(nssl, "get_claims_registry", lambda: claims)
    return claims


def test_breaking_equity_opens_live_short(monkeypatch, _live_iso):
    _captured(monkeypatch)
    monkeypatch.setattr(nssl, "coin_catalyst",
                        lambda c: _report(breaking=(c == "xyz:HOT"), surge=5.0 if c == "xyz:HOT" else 0.4))
    opened = []

    def execute(a):
        opened.append(a)
        return {"executed": True}

    nssl.maybe_run(_live_cfg(), [{"coin": "xyz:HOT", "mid": 2.0}, {"coin": "COLD", "mid": 1.0}],
                  [], execute)
    assert [a["coin"] for a in opened] == ["xyz:HOT"]
    a = opened[0]
    assert a["strategy_book"] == "news_surge_short" and a["side"] == "short"
    assert a["verdict"] == "SHORT"
    assert a["strategy_book_notional"] == 20.0 and a["leverage_override"] == 10
    assert a["min_short_volume_usd_override"] == 250_000.0
    assert a["dsl_exit_override"]["hard_timeout_minutes"] == 1440.0
    assert a["dsl_exit_override"]["max_loss_pct"] == 15.0
    assert a["dsl_exit_override"]["protect_pct"] == 9999.0


def test_breaking_crypto_is_recorded_not_traded(monkeypatch, _live_iso):
    """The evidence boundary: 6/7 graded episodes were equities, the lone
    crypto read was a single outlier (CASHCAT). Crypto breaking reads must
    keep recording but never place an order until they earn their own n>=8."""
    _captured(monkeypatch)
    monkeypatch.setattr(nssl, "coin_catalyst", lambda c: _report(breaking=True, surge=6.0))
    opened = []
    execute = lambda a: opened.append(a) or {"executed": True}

    n = nssl.maybe_run(_live_cfg(), [{"coin": "CASHCAT", "mid": 0.06}], [], execute)
    assert n == 1          # recorded
    assert opened == []    # never traded


def test_mixed_pass_trades_only_the_equity(monkeypatch, _live_iso):
    _captured(monkeypatch)
    monkeypatch.setattr(nssl, "coin_catalyst", lambda c: _report(breaking=True, surge=6.0))
    opened = []
    execute = lambda a: opened.append(a) or {"executed": True}

    nssl.maybe_run(_live_cfg(), [{"coin": "CASHCAT", "mid": 0.06},
                                {"coin": "xyz:IBM", "mid": 236.0}], [], execute)
    assert [a["coin"] for a in opened] == ["xyz:IBM"]


def test_live_arm_daily_dedup_and_shadow_kill(monkeypatch, _live_iso, tmp_path):
    _captured(monkeypatch)
    monkeypatch.setattr(nssl, "coin_catalyst", lambda c: _report(breaking=True, surge=4.0))
    opened = []
    execute = lambda a: opened.append(a) or {"executed": True}
    percs = [{"coin": "xyz:HOT", "mid": 2.0}]

    nssl.maybe_run(_live_cfg(), percs, [], execute)
    assert len(opened) == 1
    monkeypatch.setattr(nssl, "_TS_FILE", str(tmp_path / "ts2.json"))
    nssl.maybe_run(_live_cfg(), percs, [], execute)
    assert len(opened) == 1   # same-day dedup holds

    monkeypatch.setattr(nssl, "_TS_FILE", str(tmp_path / "ts3.json"))
    monkeypatch.setattr(nssl, "_SEEN_FILE", str(tmp_path / "seen3.json"))
    nssl.maybe_run(_live_cfg(shadow_only=True), percs, [], execute)
    assert len(opened) == 1   # shadow_only=true never executes


def test_ledger_rows_never_carry_live_handle(monkeypatch, _live_iso):
    out = _captured(monkeypatch)
    monkeypatch.setattr(nssl, "coin_catalyst", lambda c: _report(breaking=True, surge=4.0))
    nssl.maybe_run(_live_cfg(), [{"coin": "xyz:HOT", "mid": 2.0}], [], lambda a: {"executed": True})
    _, rows = out[0]
    assert all("_rep" not in r["meta"] for r in rows)
    assert rows[0]["meta"]["shadow"] is False


def test_block_reason_reads_the_real_key_not_blocked_by(monkeypatch, caplog, _live_iso):
    """maybe_execute's early gates (e.g. hip3_dex_underfunded) return the
    explanation under 'reason', not 'blocked_by'."""
    _captured(monkeypatch)
    monkeypatch.setattr(nssl, "coin_catalyst", lambda c: _report(breaking=True, surge=4.0))
    blocked = {"executed": False,
              "reason": "hip3_dex_underfunded (xyz: $0.01). Transfer USDC to 'xyz' via the HL frontend."}
    with caplog.at_level("WARNING"):
        nssl.maybe_run(_live_cfg(), [{"coin": "xyz:SKHY", "mid": 236.0}], [],
                      lambda a: blocked)
    assert any("hip3_dex_underfunded" in r.message for r in caplog.records)
    assert not any("not opened: None" in r.message for r in caplog.records)
