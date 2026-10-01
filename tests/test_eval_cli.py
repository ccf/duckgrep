import argparse
import json
import os

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

    def prepare(tasks, setups, cache, log=print, save=lambda row: None, workers=1):
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


def test_prepare_keeps_a_row_saved_after_it_gave_up(tmp_path, monkeypatch):
    import threading

    monkeypatch.setattr(config, "SUITES_DIR", tmp_path)
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setenv("DUCKGREP_EVAL_CACHE", str(tmp_path / "cache"))
    row = {"setup": "duckgrep", "repo": "o/r", "commit": "c", "index_seconds": 1.0, "index_mb": 0.1}
    late = []

    def prepare(tasks, setups, cache, log=print, save=lambda row: None, workers=1):
        # a pair still building after a second Ctrl-C: the interpreter waits for it at exit, and it saves then
        late.append(threading.Timer(0.2, save, args=(row,)))
        late[0].start()
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.workspace, "prepare", prepare)
    suite.save(
        tmp_path / "pilot-localization.jsonl", [Task("t1", "localization", "python", "o/r", "c", "p", ("a.py:f",), "s")]
    )
    with pytest.raises(KeyboardInterrupt):
        cli.main(["prepare", "--setups", "duckgrep"])
    late[0].join()
    saved = tmp_path / "runs" / "pilot" / "prepare.jsonl"
    assert [json.loads(line) for line in saved.read_text().splitlines()] == [row]


def test_prepare_takes_a_worker_count_and_saves_whole_rows_from_several_threads(tmp_path, monkeypatch):
    import threading

    monkeypatch.setattr(config, "SUITES_DIR", tmp_path)
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setenv("DUCKGREP_EVAL_CACHE", str(tmp_path / "cache"))
    seen = {}

    def prepare(tasks, setups, cache, log=print, save=lambda row: None, workers=None):
        seen.update(workers=workers, setups=setups)
        rows = [{"setup": "duckgrep", "repo": f"o/r{i}", "commit": "c", "pad": "x" * 100_000} for i in range(8)]
        threads = [threading.Thread(target=save, args=(row,)) for row in rows]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    monkeypatch.setattr(cli.workspace, "prepare", prepare)
    suite.save(
        tmp_path / "pilot-localization.jsonl", [Task("t1", "localization", "python", "o/r", "c", "p", ("a.py:f",), "s")]
    )
    assert cli.main(["prepare", "--setups", "duckgrep,duckgrep-hint"]) == 0
    assert seen == {"workers": 6, "setups": ["duckgrep", "duckgrep-hint"]}
    assert cli.main(["prepare", "--parallel", "2"]) == 0
    assert seen["workers"] == 2
    lines = (tmp_path / "runs" / "pilot" / "prepare.jsonl").read_text().splitlines()
    assert len(lines) == 16 and all(json.loads(line)["setup"] == "duckgrep" for line in lines)


def test_run_rejects_unknown_task_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SUITES_DIR", tmp_path)
    monkeypatch.setattr(cli, "claude_path", lambda given=None: "claude")
    suite.save(
        tmp_path / "pilot-localization.jsonl", [Task("t1", "localization", "python", "o/r", "c", "p", ("a.py:f",), "s")]
    )
    assert cli.main(["run", "--tasks", "t1,nope"]) == 2


def test_run_stops_before_spending_when_a_setup_is_misconfigured(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SUITES_DIR", tmp_path)
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(cli, "claude_path", lambda given=None: "true")
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
    monkeypatch.setattr(cli, "claude_path", lambda given=None: "true")
    monkeypatch.setattr(cli.runner, "probe", lambda task, setup, cache, claude: [])
    monkeypatch.setattr(cli.runner, "unprepared", lambda runs, cache: [])

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
    assert all(m["claude_path"] == "true" for m in meta)


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


def test_build_refuses_a_curation_file_that_names_a_task_it_did_not_draw(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "SUITES_DIR", tmp_path)
    from bench.eval.tasks import localization

    made = [Task(i, "localization", "rust", "o/r", "c", "p", ("a.rs:f",), "s") for i in ("keep", "bundled")]
    monkeypatch.setattr(localization, "build", lambda **kw: made)
    rows = [
        {"id": "bundled", "reason": "an unrelated feature"},
        {"id": "Bundled", "reason": "a typo, or a task a filter already drops"},
        {"id": "o-callers-f", "kind": "structural", "reason": "not built this time, so not checked"},
    ]
    (tmp_path / "x-curation.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert cli.main(["--suite", "x", "build", "--kind", "localization"]) == 2
    err = capsys.readouterr().err
    assert "Bundled" in err and "bundled'" not in err and "o-callers-f" not in err
    assert not (tmp_path / "x-localization.jsonl").exists()


def fake_cli(path, version):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\necho '{version} (Claude Code)'\n")
    path.chmod(0o755)
    return path


def test_claude_is_the_binary_the_installers_link_points_at_now(tmp_path, monkeypatch):
    versions = tmp_path / ".local" / "share" / "claude" / "versions"
    fake_cli(versions / "2.1.286", "2.1.286")
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "claude").symlink_to(versions / "2.1.286")  # the native installer moves this link
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert cli.claude_path() == os.path.realpath(versions / "2.1.286")
    assert cli.claude_path(str(fake_cli(versions / "2.1.285", "2.1.285"))) == os.path.realpath(versions / "2.1.285")


def resumable(tmp_path, monkeypatch, version):
    """A batch named x whose earliest recorded run ran Claude Code `version`; the probes and the batch recorded."""
    monkeypatch.setattr(config, "SUITES_DIR", tmp_path)
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setenv("DUCKGREP_EVAL_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    suite.save(
        tmp_path / "pilot-localization.jsonl", [Task("t1", "localization", "python", "o/r", "c", "p", ("a.py:f",), "s")]
    )
    out = tmp_path / "runs" / "x"
    out.mkdir(parents=True)
    rows = [{"task": "t0", "setup": "baseline", "rep": 1}, {"task": "t0", "setup": "baseline", "rep": 2}]
    rows[1]["cli_version"] = version  # the first has no version: cut off before its init event
    (out / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    seen = {"probes": [], "batches": 0}
    monkeypatch.setattr(cli.runner, "probe", lambda task, setup, cache, claude: seen["probes"].append(claude) or [])
    monkeypatch.setattr(cli.runner, "unprepared", lambda runs, cache: [])

    class Batch:
        def __init__(self, *a, **k):
            seen["batches"] += 1

        def run(self):
            return None

    monkeypatch.setattr(cli.runner, "Batch", Batch)
    return seen


def test_a_resume_refuses_another_claude_code_version_and_names_the_one_to_pass(tmp_path, monkeypatch, capsys):
    seen = resumable(tmp_path, monkeypatch, "2.1.285")
    kept = fake_cli(tmp_path / "home" / ".local" / "share" / "claude" / "versions" / "2.1.285", "2.1.285")
    updated = fake_cli(tmp_path / "bin" / "claude", "2.1.286")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    assert cli.main(["run", "--name", "x"]) == 1
    said = capsys.readouterr()
    assert "2.1.285" in said.err and "2.1.286" in said.err and f"--claude {kept}" in said.err
    assert seen == {"probes": [], "batches": 0}  # before the probes too
    assert cli.main(["run", "--name", "x", "--claude", str(kept)]) == 0
    assert seen["batches"] == 1 and set(seen["probes"]) == {os.path.realpath(kept)}
    meta = json.loads((tmp_path / "runs" / "x" / "meta.json").read_text())
    assert meta[-1]["claude_path"] == os.path.realpath(kept) and meta[-1]["claude"].startswith("2.1.285")
    assert updated.exists()


def test_check_takes_the_claude_to_probe(tmp_path, monkeypatch):
    seen = resumable(tmp_path, monkeypatch, "2.1.285")
    kept = fake_cli(tmp_path / "v" / "2.1.284", "2.1.284")
    assert cli.main(["check", "--claude", str(kept)]) == 0  # check spends nothing: no version to hold it to
    assert set(seen["probes"]) == {os.path.realpath(kept)}


def test_run_refuses_a_results_directory_another_run_holds_and_leaves_no_trace(tmp_path, monkeypatch, capsys):
    resumable(tmp_path, monkeypatch, "2.1.285")
    monkeypatch.setattr(cli, "claude_path", lambda given=None: str(fake_cli(tmp_path / "bin" / "claude", "2.1.285")))

    def busy(*a, **k):
        raise cli.runner.Busy("another run is using runs/x (pid 1)")

    monkeypatch.setattr(cli.runner, "Batch", busy)
    assert cli.main(["run", "--name", "x"]) == 1
    assert "another run is using" in capsys.readouterr().err
    assert not (tmp_path / "runs" / "x" / "meta.json").exists()  # its meta would name an invocation that never ran


def test_run_refuses_to_start_until_every_scheduled_pair_is_prepared(tmp_path, monkeypatch, capsys):
    preflight = cli.runner.unprepared
    seen = resumable(tmp_path, monkeypatch, "2.1.285")
    monkeypatch.setattr(cli.runner, "unprepared", preflight)  # the real one, not the fixture's
    monkeypatch.setattr(cli, "claude_path", lambda given=None: str(fake_cli(tmp_path / "bin" / "claude", "2.1.285")))
    assert cli.main(["run", "--name", "x"]) == 1
    err = capsys.readouterr().err
    assert "3 worktrees are not prepared" in err and "no worktree" in err and "prepare" in err
    assert seen["batches"] == 0 and len(seen["probes"]) == 5  # after the free check, before any paid run
