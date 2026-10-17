"""CLI tests: python -m services.strategies {list,brief,refresh,grade}.
Invokes main() directly (fast, in-process) rather than subprocess."""
from __future__ import annotations

import json

import pytest

from services.strategies import __main__ as cli
from services.strategies.tests.conftest import minimal_analytics


def test_cmd_list_human_readable(capsys):
    rc = cli.main(["list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "oil" in out and "btc" in out


def test_cmd_list_json(capsys):
    rc = cli.main(["--json", "list"])
    assert rc == 0
    rows = json.loads(capsys.readouterr().out)
    assert {r["id"] for r in rows} == {"oil", "btc", "top5", "ai", "metals", "indices"}


def test_cmd_brief_known_desk(capsys, stub_live_analytics):
    stub_live_analytics(minimal_analytics("oil", mid=99.0, coin="xyz:CL"))
    rc = cli.main(["brief", "oil"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Oil (oil)" in out


def test_cmd_brief_json(capsys, stub_live_analytics):
    stub_live_analytics(minimal_analytics("oil", mid=99.0, coin="xyz:CL"))
    rc = cli.main(["--json", "brief", "oil"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["spec"]["id"] == "oil"


def test_cmd_brief_unknown_desk(capsys):
    rc = cli.main(["brief", "not-a-real-desk"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "unknown desk" in err


def test_cmd_refresh_reports_state(capsys, stub_live_analytics):
    stub_live_analytics(minimal_analytics("oil", mid=99.0, coin="xyz:CL"))
    rc = cli.main(["refresh", "oil", "--force"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("oil: ")


def test_cmd_refresh_all_runs_every_desk(capsys, stub_live_analytics):
    stub_live_analytics(lambda spec: minimal_analytics(spec.id, mid=1.0, coin=spec.instruments[0].coin))
    rc = cli.main(["refresh", "--all"])
    assert rc == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 6
    assert {line.split(":")[0] for line in lines} == {"oil", "btc", "top5", "ai", "metals", "indices"}


def test_cmd_refresh_all_json_is_a_list(capsys, stub_live_analytics):
    stub_live_analytics(lambda spec: minimal_analytics(spec.id, mid=1.0, coin=spec.instruments[0].coin))
    rc = cli.main(["--json", "refresh", "--all"])
    assert rc == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 6


def test_cmd_refresh_requires_desk_or_all():
    with pytest.raises(SystemExit):
        cli.main(["refresh"])


def test_cmd_refresh_error_exit_code(capsys, monkeypatch):
    import services.strategies.pipeline as pipeline_mod

    def boom(spec, **kwargs):
        raise RuntimeError("boom")
    monkeypatch.setattr(pipeline_mod.analytics_mod, "build_analytics", boom)

    rc = cli.main(["refresh", "oil"])
    assert rc == 1
    assert "oil: error" in capsys.readouterr().out


def test_cmd_grade_no_theses(capsys):
    rc = cli.main(["grade"])
    assert rc == 0
    assert "graded 0 theses" in capsys.readouterr().out


def test_main_requires_a_subcommand():
    with pytest.raises(SystemExit):
        cli.main([])
