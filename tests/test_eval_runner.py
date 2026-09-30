import gzip
import json
import os
import sys
import threading
import time

import pytest
from eval_helpers import origin

from bench.eval import config, runner, workspace
from bench.eval.suite import Task

RUNS = os.path.join(os.path.dirname(__file__), "eval_runs")


def task(i, commit="c" * 40):
    return Task(f"t{i}", "localization", "python", "o/r", commit, "p", ("src/a.py:needle_fn",), "s")


def test_schedule_is_complete_and_seeded():
    tasks = [task(1), task(2)]
    runs = runner.schedule(tasks, ["baseline", "duckgrep"], 2, seed=3)
    assert len(runs) == 8 and len({r.key for r in runs}) == 8
    assert [r.key for r in runs] == [r.key for r in runner.schedule(tasks, ["baseline", "duckgrep"], 2, seed=3)]
    assert [r.key for r in runs] != [r.key for r in runner.schedule(tasks, ["baseline", "duckgrep"], 2, seed=4)]


def fake_record(r, attempt, ok=True, cost=0.01):
    return {
        "task": r.task.id,
        "setup": r.setup,
        "rep": r.rep,
        "attempt": attempt,
        "config_ok": ok,
        "config_problems": [] if ok else ["tools differ"],
        "tool_calls": 1,
        "cost_usd": cost,
        "cli_cost_usd": cost,
        "score": {"success": True},
    }


def results(out):
    with open(out / "results.jsonl") as f:
        return [json.loads(line) for line in f]


def test_batch_records_every_run_once_and_resumes(tmp_path):
    runs = runner.schedule([task(1), task(2)], ["baseline"], 2, seed=1)
    batch = runner.Batch(
        runs, tmp_path, tmp_path / "out", parallel=2, execute_fn=lambda r, *a: fake_record(r, a[2]), log=lambda _: None
    )
    assert batch.run() is None
    assert sorted((x["task"], x["rep"]) for x in results(tmp_path / "out")) == [
        ("t1", 1),
        ("t1", 2),
        ("t2", 1),
        ("t2", 2),
    ]
    again = runner.Batch(
        runs, tmp_path, tmp_path / "out", execute_fn=lambda *a: pytest.fail("rerun"), log=lambda _: None
    )
    assert again.pending == [] and again.run() is None


def test_a_failed_configuration_check_is_retried_once(tmp_path):
    runs = runner.schedule([task(1)], ["baseline"], 1, seed=1)
    batch = runner.Batch(
        runs,
        tmp_path,
        tmp_path / "out",
        execute_fn=lambda r, c, o, attempt, cl: fake_record(r, attempt, ok=attempt == 2),
        log=lambda _: None,
    )
    batch.run()
    [rec] = results(tmp_path / "out")
    assert rec["attempt"] == 2 and rec["config_ok"]


def test_an_infrastructure_error_stops_the_batch_without_recording(tmp_path):
    runs = runner.schedule([task(1), task(2)], ["baseline"], 1, seed=1)

    def execute(r, *a):
        if r.task.id == "t1":
            raise runner.InfrastructureError("authentication_failed")
        return fake_record(r, 1)

    stopped = runner.Batch(runs, tmp_path, tmp_path / "out", parallel=1, execute_fn=execute, log=lambda _: None).run()
    assert "authentication_failed" in stopped
    assert (
        "t1" not in [x["task"] for x in results(tmp_path / "out")]
        if (tmp_path / "out/results.jsonl").exists()
        else True
    )


def test_the_spending_cap_stops_new_runs(tmp_path):
    runs = runner.schedule([task(1), task(2), task(3)], ["baseline"], 2, seed=1)
    stopped = runner.Batch(
        runs,
        tmp_path,
        tmp_path / "out",
        parallel=1,
        max_total_usd=0.025,
        execute_fn=lambda r, *a: fake_record(r, 1),
        log=lambda _: None,
    ).run()
    assert "cap" in stopped and len(results(tmp_path / "out")) == 3


def test_runs_sharing_a_worktree_never_overlap(tmp_path):
    runs = runner.schedule([task(1, "a" * 40), task(2, "b" * 40)], ["baseline"], 3, seed=1)
    active, lock, clashes = set(), threading.Lock(), []

    def execute(r, *a):
        wt = workspace.worktree_path(tmp_path, r.setup, r.task.repo, r.task.commit)
        with lock:
            clashes.append(wt in active)
            active.add(wt)
        time.sleep(0.05)
        with lock:
            active.discard(wt)
        return fake_record(r, 1)

    runner.Batch(runs, tmp_path, tmp_path / "out", parallel=3, execute_fn=execute, log=lambda _: None).run()
    assert len(clashes) == 6 and not any(clashes)


def fake_claude(tmp_path, stream_lines, extra=""):
    """An executable standing in for `claude`: records its argv and environment, leaves a stray file in its
    working directory, runs `extra` (Python source), and prints a recorded stream."""
    stream = tmp_path / "stream.jsonl"
    stream.write_text("\n".join(json.dumps(e) for e in stream_lines) + "\n")
    seen = tmp_path / "seen.json"
    exe = tmp_path / "claude"
    exe.write_text(
        f"#!{sys.executable}\n"
        "import json, os, subprocess, sys\n"
        f"json.dump({{'argv': sys.argv, 'env': dict(os.environ)}}, open({str(seen)!r}, 'w'))\n"
        "open('stray.txt', 'w').write('x')\n"
        f"{extra}\n"
        f"sys.stdout.write(open({str(stream)!r}).read())\n"
    )
    exe.chmod(0o755)
    return str(exe), seen


def recorded_stream(answer):
    with open(os.path.join(RUNS, "plain.jsonl")) as f:
        events = [json.loads(line) for line in f]
    for e in events:
        if e.get("subtype") == "init":
            e["model"] = config.MODEL
        if e.get("type") == "result":
            e["result"] = answer
    return events


def test_execute_runs_scores_and_restores(tmp_path, monkeypatch):
    src, commit = origin(tmp_path, {"src/a.py": "def needle_fn():\n    pass\n"})
    cache = tmp_path / "cache"
    workspace.worktree("o/r", commit, "baseline", cache, url=str(src))
    answer = 'Found it.\n\n```json\n{"locations": ["src/a.py:needle_fn"]}\n```'
    exe, seen = fake_claude(tmp_path, recorded_stream(answer))
    monkeypatch.setenv("GIT_DIR", "/should/not/leak")
    run = runner.Run(task(1, commit), "baseline", 1)
    rec = runner.execute(run, cache, tmp_path / "out", 1, exe)
    assert rec["config_ok"] and rec["score"]["success"] and rec["answer"] == ["src/a.py:needle_fn"]
    assert rec["turns_to_locate"] == 1 and rec["tool_calls"] == 2 and not rec["killed"]
    assert rec["worktree_changes"] == ["?? stray.txt"]
    wt = workspace.worktree_path(cache, "baseline", "o/r", commit)
    assert not (wt / "stray.txt").exists()
    raw = tmp_path / "out" / "t1" / "baseline-1.jsonl.gz"
    assert raw.exists() and not raw.with_suffix("").exists()
    with gzip.open(raw, "rt") as f:
        assert len(f.read().splitlines()) == len(recorded_stream(answer))
    got = json.loads(seen.read_text())
    added_by_python = {"__CF_USER_TEXT_ENCODING", "LC_CTYPE"}  # the fake is a Python script; macOS and PEP 538
    assert set(got["env"]) - added_by_python == {
        "HOME",
        "PATH",
        "USER",
        "TMPDIR",
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY",
        "ENABLE_TOOL_SEARCH",
        "CODE_TASKS_RUN",  # the run's marker, so the harness can find every process the run started
    }
    assert got["argv"][got["argv"].index("--model") + 1] == config.MODEL


def test_execute_turns_an_auth_failure_into_an_infrastructure_error(tmp_path):
    src, commit = origin(tmp_path, {"src/a.py": "x = 1\n"})
    cache = tmp_path / "cache"
    workspace.worktree("o/r", commit, "baseline", cache, url=str(src))
    with open(os.path.join(RUNS, "auth_failure.jsonl")) as f:
        exe, _ = fake_claude(tmp_path, [json.loads(line) for line in f])
    with pytest.raises(runner.InfrastructureError):
        runner.execute(runner.Run(task(1, commit), "baseline", 1), cache, tmp_path / "out", 1, exe)
    assert not (workspace.worktree_path(cache, "baseline", "o/r", commit) / "stray.txt").exists()


def test_execute_puts_back_a_moved_head_and_ignored_files(tmp_path):
    src, _ = origin(tmp_path, {"src/a.py": "def needle_fn():\n    pass\n", ".gitignore": "build/\n"})
    (src / "src/a.py").write_text("def needle_fn():\n    return 1\n")
    workspace.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-am", "second", cwd=src)
    commit = workspace.git("rev-parse", "HEAD", cwd=src).strip()
    cache = tmp_path / "cache"
    wt = workspace.worktree("o/r", commit, "baseline", cache, url=str(src))
    moves = "subprocess.run(['git', 'checkout', '-q', '--detach', 'HEAD~1'], check=True)\n"
    moves += "os.makedirs('build', exist_ok=True); open('build/out.o', 'w').write('x')"
    exe, _ = fake_claude(tmp_path, recorded_stream("no answer"), extra=moves)
    rec = runner.execute(runner.Run(task(1, commit), "baseline", 1), cache, tmp_path / "out", 1, exe)
    assert rec["head_moved"] and "!! build/out.o" in rec["worktree_changes"]
    assert workspace.git("rev-parse", "HEAD", cwd=wt).strip() == commit
    assert not (wt / "build").exists() and not (wt / "stray.txt").exists()


def test_low_disk_stops_the_batch_before_the_next_run(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "free_gb", lambda path: 1.0)
    runs = runner.schedule([task(1)], ["baseline"], 1, seed=1)
    stopped = runner.Batch(
        runs, tmp_path, tmp_path / "out", execute_fn=lambda *a: pytest.fail("ran"), log=lambda _: None
    ).run()
    assert "GB free" in stopped


def test_a_harness_fault_stops_the_batch(tmp_path):
    runs = runner.schedule([task(1)], ["baseline"], 1, seed=1)

    def broken(*a):
        raise RuntimeError("worktree is missing")

    stopped = runner.Batch(runs, tmp_path, tmp_path / "out", execute_fn=broken, log=lambda _: None).run()
    assert "worktree is missing" in stopped


def test_the_wall_clock_limit_kills_the_run_and_its_children(tmp_path, monkeypatch):
    src, commit = origin(tmp_path, {"src/a.py": "x = 1\n"})
    cache = tmp_path / "cache"
    workspace.worktree("o/r", commit, "baseline", cache, url=str(src))
    pidfile = tmp_path / "child.pid"
    exe = tmp_path / "claude"
    exe.write_text(
        f"#!{sys.executable}\n"
        "import subprocess, sys, time\n"
        "child = subprocess.Popen(['sleep', '60'])\n"
        f"open({str(pidfile)!r}, 'w').write(str(child.pid))\n"
        'print(\'{"type": "system", "subtype": "init"}\', flush=True)\n'
        "time.sleep(60)\n"
    )
    exe.chmod(0o755)
    monkeypatch.setattr(config, "WALL_LIMIT_S", 1)
    rec = runner.execute(runner.Run(task(1, commit), "baseline", 1), cache, tmp_path / "out", 1, str(exe))
    assert rec["killed"] and rec["is_error"] and not rec["score"]["parsed"]
    child = int(pidfile.read_text())
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)


def test_a_run_leaves_no_process_behind_even_in_a_session_of_its_own(tmp_path):
    src, commit = origin(tmp_path, {"src/a.py": "x = 1\n"})
    cache = tmp_path / "cache"
    workspace.worktree("o/r", commit, "baseline", cache, url=str(src))
    pidfile = tmp_path / "server.pid"
    escape = (  # like Serena starting a language server with start_new_session=True
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)\n"
        f"open({str(pidfile)!r}, 'w').write(str(child.pid))"
    )
    exe, _ = fake_claude(tmp_path, recorded_stream("no answer"), extra=escape)
    runner.execute(runner.Run(task(1, commit), "baseline", 1), cache, tmp_path / "out", 1, exe)
    child = int(pidfile.read_text())
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            pytest.fail("the process escaped the run's process group and outlived the run")
    finally:
        try:
            os.kill(child, 9)
        except ProcessLookupError:
            pass
