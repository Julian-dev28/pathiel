"""scripts/event_add.py, the one writer for the scan overlay, and the row
validator it shares with the curated file (scheduled_events.validate_event).
Everything writes under tmp_path."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from services.intel import scheduled_events as S

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("event_add", ROOT / "scripts" / "event_add.py")
A = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(A)

GOOD = {"date": "2026-11-10", "at_utc": "2026-11-10T15:00Z",
        "event": "Senate Foreign Relations hearing: Iran policy", "kind": "congress_hearing",
        "coins": ["oil"], "impact": 1, "verified": True,
        "source": "https://www.congress.gov/event/119th-congress/senate-event/338805"}


# ── the validator ───────────────────────────────────────────────────────────

def test_validator_accepts_the_good_row_and_the_optional_fields():
    assert S.validate_event(GOOD) is GOOD
    S.validate_event(dict(GOOD, at_utc=None, note="n", before_h=25, after_h=0, origin="scan", scanned_at="x"))


@pytest.mark.parametrize("patch,why", [
    ({"date": "2026-13-01"}, "not a real day"),
    ({"date": "11/10/2026"}, "YYYY-MM-DD"),
    ({"at_utc": "2026-11-10 15:00"}, "at_utc must be"),
    ({"at_utc": "2026-11-10T25:00Z"}, "not a real time"),
    ({"event": "  "}, "event must be"),
    ({"kind": "Congress Hearing"}, "kind must match"),
    ({"coins": []}, "coins must be"),
    ({"coins": ["gold"]}, "coins must be"),
    ({"impact": 4}, "impact must be"),
    ({"impact": "2"}, "impact must be"),
    ({"impact": True}, "impact must be"),
    ({"verified": "yes"}, "verified must be"),
    ({"source": ""}, "source must be"),
    ({"before_h": -1}, "before_h must be"),
    ({"origin": "manual"}, "origin must be"),
    ({"extra": 1}, "unknown field"),
])
def test_validator_rejects_bad_rows(patch, why):
    with pytest.raises(ValueError, match=why):
        S.validate_event(dict(GOOD, **patch))


def test_validator_names_missing_fields():
    with pytest.raises(ValueError, match="missing kind, coins"):
        S.validate_event({k: v for k, v in GOOD.items() if k not in ("kind", "coins")})


def test_normalize_title_and_key():
    assert S.normalize_title("  Senate Foreign Relations: Iran, Hormuz & oil!! ") == "senate foreign relations iran hormuz oil"
    a = dict(GOOD, event="SENATE foreign relations hearing - Iran policy")
    assert S.event_key(a) == S.event_key(GOOD)


# ── add / update / refuse / prune ───────────────────────────────────────────

def test_add_then_update_in_place_then_unchanged(tmp_path):
    ov = A.load_overlay(str(tmp_path / "o.json"))
    status, row = A.add_event(ov, GOOD, now="2026-11-05T12:00Z")
    assert status == "added" and row["origin"] == "scan" and row["scanned_at"] == "2026-11-05T12:00Z"
    status, row = A.add_event(ov, dict(GOOD, event="senate foreign relations hearing: IRAN POLICY", impact=2, note="moved"),
                              now="2026-11-05T13:00Z")
    assert status == "updated" and row["impact"] == 2 and row["note"] == "moved" and row["scanned_at"] == "2026-11-05T13:00Z"
    assert len(ov["events"]) == 1
    status, _ = A.add_event(ov, dict(GOOD, impact=2), now="2026-11-05T14:00Z")
    assert status == "unchanged" and ov["events"][0]["note"] == "moved" and ov["events"][0]["scanned_at"] == "2026-11-05T13:00Z"
    status, _ = A.add_event(ov, dict(GOOD, date="2026-11-11"))
    assert status == "added" and len(ov["events"]) == 2


def test_impact_3_refused_without_the_flag(tmp_path):
    ov = A.load_overlay(str(tmp_path / "o.json"))
    row = dict(GOOD, impact=3, event="Iran deal deadline", kind="iran_talks")
    with pytest.raises(A.ImpactRefused, match="allow-impact-3"):
        A.add_event(ov, row)
    assert ov["events"] == []
    assert A.add_event(ov, row, allow_impact_3=True)[0] == "added"


def test_a_row_the_curated_file_already_has_is_not_written(tmp_path):
    curated = tmp_path / "curated.json"
    curated.write_text(json.dumps({"events": [dict(GOOD, event="SENATE FOREIGN RELATIONS hearing: Iran policy")]}))
    ov = A.load_overlay(str(tmp_path / "o.json"))
    assert A.add_event(ov, GOOD, curated_path=str(curated))[0] == "curated"
    assert ov["events"] == []


def test_prune_and_remove(tmp_path):
    ov = A.load_overlay(str(tmp_path / "o.json"))
    for d in ("2026-10-30", "2026-11-01", "2026-11-10"):
        A.add_event(ov, dict(GOOD, date=d))
    assert A.prune_before(ov, "2026-11-01") == 1 and [e["date"] for e in ov["events"]] == ["2026-11-01", "2026-11-10"]
    assert A.remove_event(ov, S.event_key(dict(GOOD, date="2026-11-10"))) is True
    assert A.remove_event(ov, S.event_key(dict(GOOD, date="2026-11-10"))) is False
    with pytest.raises(ValueError):
        A.prune_before(ov, "yesterday")


def test_save_is_atomic_sorted_and_round_trips(tmp_path):
    path = tmp_path / "intel" / "scanned_events.json"
    ov = A.load_overlay(str(path))                       # missing file: an empty overlay
    assert ov["events"] == [] and ov["state"] == {}
    A.add_event(ov, dict(GOOD, date="2026-11-12"))
    A.add_event(ov, GOOD)
    ov["state"]["bill_actions"] = {"119/hr/1422": ["k"]}
    A.save_overlay(ov, str(path))
    assert not path.with_name(path.name + ".tmp").exists()
    back = json.loads(path.read_text())
    assert [e["date"] for e in back["events"]] == ["2026-11-10", "2026-11-12"]
    assert back["version"] == 1 and back["updated"] and back["state"]["bill_actions"]["119/hr/1422"] == ["k"]
    assert S.read_overlay(str(path))["events"] == back["events"]


# ── the CLI ─────────────────────────────────────────────────────────────────

def test_cli_add_list_prune(tmp_path, capsys):
    path = str(tmp_path / "o.json")
    rc = A.main(["--path", path, "--date", "2026-11-10", "--at-utc", "2026-11-10T15:00Z",
                 "--event", "Senate Foreign Relations hearing: Iran policy", "--kind", "congress_hearing",
                 "--coins", "oil,silver", "--impact", "1", "--verified", "--source", "https://congress.gov/x",
                 "--note", "SD-419"])
    assert rc == 0 and capsys.readouterr().out.startswith("ADDED 2026-11-10 2026-11-10T15:00Z i1 congress_hearing   oil,silver")
    rc = A.main(["--path", path, "--json", json.dumps(dict(GOOD, date="2026-10-01"))])
    assert rc == 0 and capsys.readouterr().out.startswith("ADDED 2026-10-01")
    assert A.main(["--path", path, "--list"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("2 scanned row(s)") and "SD-419" not in out and "Iran policy" in out
    assert A.main(["--path", path, "--prune-before", "2026-11-01"]) == 0
    assert "pruned 1 row(s)" in capsys.readouterr().out
    assert len(json.loads(Path(path).read_text())["events"]) == 1


def test_cli_refuses_impact_3_and_bad_rows(tmp_path, capsys):
    path = str(tmp_path / "o.json")
    rc = A.main(["--path", path, "--json", json.dumps(dict(GOOD, impact=3))])
    assert rc == 3 and "REFUSED" in capsys.readouterr().err and not Path(path).exists()
    rc = A.main(["--path", path, "--json", json.dumps(dict(GOOD, impact=3)), "--allow-impact-3"])
    assert rc == 0 and Path(path).exists()
    rc = A.main(["--path", path, "--json", json.dumps(dict(GOOD, coins=["gold"]))])
    assert rc == 2 and "INVALID coins must be" in capsys.readouterr().err
