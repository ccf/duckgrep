import argparse
import json

import pytest

from bench.eval import __main__ as cli
from bench.eval import config, suite
from bench.eval.suite import Task


def test_setup_list():
    assert cli.setup_list("baseline,serena") == ["baseline", "serena"]
    with pytest.raises(argparse.ArgumentTypeError):
        cli.setup_list("baseline,grep")


def test_report_without_results(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    assert cli.main(["--suite", "nothing", "report"]) == 1
    assert "no results" in capsys.readouterr().out


def test_prepare_writes_each_row_as_soon_as_it_is_measured(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SUITES_DIR", tmp_path)
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setenv("DUCKGREP_EVAL_CACHE", str(tmp_path / "cache"))
    row = {"setup": "duckgrep", "repo": "o/r", "commit": "c", "index_seconds": 1.0, "index_mb": 0.1}

    def prepare(tasks, setups, cache, log=print, save=lambda row: None):
        save(row)
        raise RuntimeError("the next repo failed")  # an hour in, say

    monkeypatch.setattr(cli.workspace, "prepare", prepare)
    suite.save(
        tmp_path / "pilot-localization.jsonl", [Task("t1", "localization", "python", "o/r", "c", "p", ("a.py:f",), "s")]
    )
    with pytest.raises(RuntimeError, match="the next repo failed"):
        cli.main(["prepare", "--setups", "duckgrep"])
    saved = tmp_path / "runs" / "pilot" / "prepare.jsonl"
    assert saved.exists() and [json.loads(line) for line in saved.read_text().splitlines()] == [row]


def test_run_rejects_unknown_task_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SUITES_DIR", tmp_path)
    monkeypatch.setattr(cli, "claude_path", lambda: "claude")
    suite.save(
        tmp_path / "pilot-localization.jsonl", [Task("t1", "localization", "python", "o/r", "c", "p", ("a.py:f",), "s")]
    )
    assert cli.main(["run", "--tasks", "t1,nope"]) == 2


def test_run_stops_before_spending_when_a_setup_is_misconfigured(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SUITES_DIR", tmp_path)
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(cli, "claude_path", lambda: "true")
    monkeypatch.setattr(
        cli.runner,
        "probe",
        lambda task, setup, cache, claude: ["MCP server serena is failed"] if setup == "serena" else [],
    )
    monkeypatch.setattr(cli.runner, "Batch", lambda *a, **k: pytest.fail("a batch started"))
    suite.save(
        tmp_path / "pilot-localization.jsonl", [Task("t1", "localization", "python", "o/r", "c", "p", ("a.py:f",), "s")]
    )
    assert cli.main(["run"]) == 1


def test_rescore_rewrites_the_named_results(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "SUITES_DIR", tmp_path)
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setenv("DUCKGREP_EVAL_CACHE", str(tmp_path / "cache"))
    suite.save(
        tmp_path / "pilot-localization.jsonl", [Task("t1", "localization", "python", "o/r", "c", "p", ("a.py:f",), "s")]
    )
    assert cli.main(["rescore", "--name", "smoke"]) == 1  # nothing to rescore
    (tmp_path / "runs" / "smoke").mkdir(parents=True)
    (tmp_path / "runs" / "smoke" / "results.jsonl").write_text("{}\n")
    seen = {}

    def rescore(out, tasks, cache):
        seen.update(out=out, tasks=[t.id for t in tasks], cache=cache)
        return {"turns_to_locate": 3}

    monkeypatch.setattr(cli.runner, "rescore", rescore)
    assert cli.main(["rescore", "--name", "smoke"]) == 0
    assert seen == {"out": tmp_path / "runs" / "smoke", "tasks": ["t1"], "cache": tmp_path / "cache"}
    assert "turns_to_locate: 3" in capsys.readouterr().out
