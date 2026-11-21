"""Gate tests for services/review/evals/ownership_eval.py: no network (fills
are injected), a small synthetic log and fill set standing in for the real
2026-11-10..11-18 window. The real network run against the real wallet and
the real /tmp/oil-loop/progress.log is `python3 -m
services.review.evals.ownership_eval` (periodic, paid in the sense of a
public API round trip, not gated here)."""
from __future__ import annotations

from datetime import datetime, timezone

from services.review.evals import ownership_eval as E

COIN = "xyz:BRENTOIL"


def _ms(s: str) -> int:
    fmt = "%Y-%m-%dT%H:%M:%SZ" if len(s) == 20 else "%Y-%m-%dT%H:%MZ"
    return int(datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).timestamp() * 1000)


def _fill(coin, side, sz, px, t, start, oid, closed_pnl=0.0, fee=0.0):
    return {"coin": coin, "side": side, "sz": sz, "px": px, "time": t, "startPosition": start,
            "closedPnl": closed_pnl, "fee": fee, "oid": oid}


# ── parse_placed_log ─────────────────────────────────────────────────────

def test_parse_placed_log_reads_oil_placed_lines_only(tmp_path):
    log = tmp_path / "progress.log"
    log.write_text(
        "DESK-TICK oil 2026-11-10T06:27Z coin=xyz:BRENTOIL mid=90.35 pos=8.17@90.351 action=PLACED x\n"
        "DESK-TICK oil 2026-11-10T06:35Z coin=xyz:BRENTOIL mid=89.9 pos=8.17@90.351 action=HOLD y\n"
        "DESK-TICK metals 2026-11-10T06:30Z coin=xyz:GOLD action=PLACED z\n"
        "DESK-TICK oil 2026-11-17T23:25:11Z coin=xyz:BRENTOIL action=PLACED w\n"
    )
    ts = E.parse_placed_log(str(log))
    assert ts == sorted([_ms("2026-11-10T06:27Z"), _ms("2026-11-17T23:25:11Z")])


def test_parse_placed_log_missing_file_is_empty(tmp_path):
    assert E.parse_placed_log(str(tmp_path / "nope.log")) == []


# ── log_backfill_desk ────────────────────────────────────────────────────

def test_log_backfill_desk_matches_a_trade_the_placed_line_opened():
    trade = {"entry_time": _ms("2026-11-10T06:27Z"), "exit_time": _ms("2026-11-10T08:50Z")}
    assert E.log_backfill_desk(trade, [_ms("2026-11-10T06:27Z")]) is True


def test_log_backfill_desk_false_far_from_any_placed_line():
    trade = {"entry_time": _ms("2026-11-17T23:15Z"), "exit_time": _ms("2026-11-17T23:25Z")}
    assert E.log_backfill_desk(trade, [_ms("2026-11-17T01:53Z")]) is False


def test_log_backfill_desk_matches_a_topup_logged_mid_trade():
    trade = {"entry_time": _ms("2026-11-16T08:00Z"), "exit_time": _ms("2026-11-16T08:34Z")}
    assert E.log_backfill_desk(trade, [_ms("2026-11-16T08:15Z")]) is True


def test_log_backfill_desk_false_on_an_empty_placed_log():
    trade = {"entry_time": _ms("2026-11-10T06:27Z"), "exit_time": _ms("2026-11-10T08:50Z")}
    assert E.log_backfill_desk(trade, []) is False


# ── classify ─────────────────────────────────────────────────────────────

def test_classify_log_backfill_when_no_cloid_but_a_placed_line_matches():
    trade = {"coin": COIN, "entry_time": _ms("2026-11-10T06:27Z"), "exit_time": _ms("2026-11-10T08:50Z"),
             "fills_open": [_fill(COIN, "B", 8.17, 90.35, _ms("2026-11-10T06:27Z"), 0, 1)]}
    owner, method = E.classify(trade, [_ms("2026-11-10T06:27Z")], journal=[])
    assert owner == "desk" and method == "log_backfill"


def test_classify_default_operator_with_neither_cloid_nor_log():
    trade = {"coin": COIN, "entry_time": _ms("2026-11-17T23:15Z"), "exit_time": _ms("2026-11-17T23:25Z"),
             "fills_open": [_fill(COIN, "B", 14.66, 90.7165, _ms("2026-11-17T23:15Z"), 0, 2)]}
    owner, method = E.classify(trade, [_ms("2026-11-10T06:27Z")], journal=[])
    assert owner == "operator" and method == "default_operator"


def test_classify_cloid_or_journal_when_the_fill_is_already_tagged():
    """A trade placed after the 2026-11-18 fix never needs the backfill."""
    from pathiel.agents import ownership as O
    trade = {"coin": COIN, "entry_time": _ms("2026-11-19T00:00Z"), "exit_time": _ms("2026-11-19T01:00Z"),
             "fills_open": [_fill(COIN, "B", 1.0, 90.0, _ms("2026-11-19T00:00Z"), 0, 3,
                                  closed_pnl=0, fee=0) | {"cloid": O.make_cloid()}]}
    owner, method = E.classify(trade, [], journal=[])
    assert owner == "desk" and method == "cloid_or_journal"


# ── score_ground_truth ───────────────────────────────────────────────────

def test_score_ground_truth_all_correct():
    rows = [
        {"entry": "2026-11-10 06:27Z", "net_usd": -12.46, "owner": "desk"},
        {"entry": "2026-11-17 23:15Z", "net_usd": 0.39, "owner": "operator"},
    ]
    score = E.score_ground_truth(rows, desk_dates=("2026-11-10",), operator_net=(0.39,))
    assert score["n_ok"] == 2 and score["n"] == 2 and score["accuracy"] == 1.0


def test_score_ground_truth_flags_a_missing_desk_entry():
    rows = [{"entry": "2026-11-12 03:58Z", "net_usd": 30.0, "owner": "desk"}]
    score = E.score_ground_truth(rows, desk_dates=("2026-11-10",), operator_net=())
    assert score["n_ok"] == 0 and not score["checks"][0]["ok"]


def test_score_ground_truth_flags_an_operator_net_that_is_actually_desk():
    """The real test of the backfill's precision: a close near the target
    net $ exists, but it is classified desk -- that is a wrong claim, not a
    match, and must fail the check."""
    rows = [{"entry": "2026-11-10 06:27Z", "net_usd": 0.40, "owner": "desk"}]
    score = E.score_ground_truth(rows, desk_dates=(), operator_net=(0.39,))
    assert score["n_ok"] == 0


def test_score_ground_truth_flags_a_net_outside_tolerance():
    rows = [{"entry": "2026-11-10 06:27Z", "net_usd": -5.0, "owner": "operator"}]
    score = E.score_ground_truth(rows, desk_dates=(), operator_net=(-13.34,), net_tolerance=1.0)
    assert score["n_ok"] == 0 and score["checks"][0]["delta"] == 8.34


# ── run(): end to end, fills injected, no network ───────────────────────

def test_run_end_to_end_with_synthetic_fills_matches_ground_truth(tmp_path):
    log = tmp_path / "progress.log"
    log.write_text(
        "DESK-TICK oil 2026-11-10T06:27Z coin=xyz:BRENTOIL action=PLACED market long\n"
    )
    desk_entry_t = _ms("2026-11-10T06:27Z")
    desk_exit_t = _ms("2026-11-10T08:50Z")
    op_entry_t = _ms("2026-11-17T23:15Z")
    op_exit_t = _ms("2026-11-17T23:25Z")
    fills = [
        _fill(COIN, "B", 8.17, 90.35, desk_entry_t, 0, 1, fee=0.1),
        _fill(COIN, "A", 8.17, 89.0, desk_exit_t, 8.17, 2, closed_pnl=-12.0, fee=0.1),
        _fill(COIN, "B", 14.66, 90.7165, op_entry_t, 0, 3, fee=0.1),
        _fill(COIN, "A", 14.66, 90.76, op_exit_t, 14.66, 4, closed_pnl=0.6, fee=0.1),
    ]

    def fake_fetch(user, start, end=None):
        assert user == E.USER
        return fills

    rep = E.run(desk_entry_t - 1000, op_exit_t + 1000, str(log), fetch_fills=fake_fetch,
               desk_dates=("2026-11-10",), operator_net=(0.39,))
    assert rep["fills"]["brent"] == 4 and rep["fills"]["brent_with_cloid"] == 0
    assert rep["n_trades"] == 2 and rep["n_desk"] == 1 and rep["n_operator"] == 1
    gt = rep["ground_truth"]
    assert gt["n_ok"] == gt["n"] == 2 and gt["accuracy"] == 1.0
    desk_row = next(r for r in rep["trades"] if r["owner"] == "desk")
    assert desk_row["method"] == "log_backfill"
    op_row = next(r for r in rep["trades"] if r["owner"] == "operator")
    assert op_row["method"] == "default_operator"


def test_main_writes_the_report_and_exits_zero_on_a_passing_run(tmp_path, capsys, monkeypatch):
    log = tmp_path / "progress.log"
    log.write_text("DESK-TICK oil 2026-11-10T06:27Z coin=xyz:BRENTOIL action=PLACED market long\n")
    out = tmp_path / "ownership_eval.json"
    fills = [
        _fill(COIN, "B", 8.17, 90.35, _ms("2026-11-10T06:27Z"), 0, 1, fee=0.1),
        _fill(COIN, "A", 8.17, 89.0, _ms("2026-11-10T08:50Z"), 8.17, 2, closed_pnl=-12.0, fee=0.1),
        _fill(COIN, "B", 14.66, 90.7165, _ms("2026-11-17T23:15Z"), 0, 3, fee=0.1),
        _fill(COIN, "A", 14.66, 90.76, _ms("2026-11-17T23:25Z"), 14.66, 4, closed_pnl=0.6, fee=0.1),
    ]
    # main() always scores against the real window's ground truth; this
    # run's synthetic data only re-creates a slice of it, so the ground
    # truth itself is trimmed to match (same technique as the test above,
    # applied to the module-level defaults main() reads).
    monkeypatch.setattr(E, "GROUND_TRUTH_DESK_DATES", ("2026-11-10",))
    monkeypatch.setattr(E, "GROUND_TRUTH_OPERATOR_NET", (0.39,))

    import pathiel.client.hl_client as hl
    monkeypatch.setattr(hl, "fetch_user_fills_by_time", lambda user, start, end=None: fills)

    code = E.main(["--start", "2026-11-10", "--end", "2026-11-18", "--log", str(log), "--out", str(out)])
    assert code == 0 and out.exists()
    printed = capsys.readouterr().out
    assert "ground truth" in printed and "PASS" in printed
