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


def test_each_run_invocation_is_kept_in_meta_with_the_harness_commit(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SUITES_DIR", tmp_path)
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setenv("DUCKGREP_EVAL_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(cli, "claude_path", lambda: "true")
    monkeypatch.setattr(cli.runner, "probe", lambda task, setup, cache, claude: [])

    class Batch:
        def __init__(self, *a, **k):
            pass

        def run(self):
            return None

    monkeypatch.setattr(cli.runner, "Batch", Batch)
    suite.save(
        tmp_path / "pilot-localization.jsonl", [Task("t1", "localization", "python", "o/r", "c", "p", ("a.py:f",), "s")]
    )
    assert cli.main(["run", "--name", "x"]) == 0
    assert cli.main(["run", "--name", "x", "--reps", "3"]) == 0  # resuming, say
    meta = json.loads((tmp_path / "runs" / "x" / "meta.json").read_text())
    assert [m["reps"] for m in meta] == [2, 3]
    assert all(len(m["harness"]["commit"]) == 40 and "dirty" in m["harness"] and m["started"] for m in meta)


def test_harness_reports_its_commit_and_any_difference_from_it(tmp_path):
    from eval_helpers import origin

    repo, commit = origin(tmp_path, {"a.py": "x = 1\n"})
    assert cli.harness(repo) == {"commit": commit, "dirty": False}
    (repo / "new.py").write_text("")  # an untracked module changes what runs, too
    assert cli.harness(repo)["dirty"]


def test_build_drops_the_tasks_its_curation_file_lists(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SUITES_DIR", tmp_path)
    from bench.eval.tasks import localization

    made = [Task(i, "localization", "rust", "o/r", "c", "p", ("a.rs:f",), "s") for i in ("keep", "bundled")]
    monkeypatch.setattr(localization, "build", lambda **kw: made)
    (tmp_path / "x-curation.jsonl").write_text(json.dumps({"id": "bundled", "reason": "an unrelated feature"}) + "\n")
    assert cli.main(["--suite", "x", "build", "--kind", "localization"]) == 0
    assert [t.id for t in suite.load(tmp_path / "x-localization.jsonl")] == ["keep"]
