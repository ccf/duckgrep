import gzip
import json
import os
import sys
import threading
import time

import pytest
from eval_helpers import origin

from bench.eval import config, runner, setups, workspace
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


@pytest.fixture(autouse=True)
def tiny_retry_waits(monkeypatch):
    monkeypatch.setattr(config, "RETRY_WAITS_S", (0.01, 0.01, 0.01))


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


def test_a_permanent_infrastructure_error_stops_the_batch_at_once_without_recording(tmp_path):
    runs = runner.schedule([task(1), task(2)], ["baseline"], 1, seed=1)
    calls = []

    def execute(r, *a):
        calls.append(r.task.id)
        raise runner.InfrastructureError("authentication_failed", cost=0.3, permanent=True)

    stopped = runner.Batch(runs, tmp_path, tmp_path / "out", parallel=1, execute_fn=execute, log=lambda _: None).run()
    assert "authentication_failed" in stopped
    assert len(calls) == 1 and not (tmp_path / "out/results.jsonl").exists()  # no retry, no second run


def unrecorded(out):
    with open(out / "unrecorded.jsonl") as f:
        return [json.loads(line) for line in f]


def test_a_transient_error_is_retried_and_its_attempt_charged(tmp_path):
    runs = runner.schedule([task(1)], ["baseline"], 1, seed=1)
    calls = []

    def execute(r, *a):
        calls.append(r.task.id)
        if len(calls) == 1:
            raise runner.InfrastructureError("overloaded", cost=0.3)
        return fake_record(r, a[2])

    batch = runner.Batch(runs, tmp_path, tmp_path / "out", parallel=1, execute_fn=execute, log=lambda _: None)
    assert batch.run() is None
    [rec] = results(tmp_path / "out")
    assert rec["task"] == "t1" and len(calls) == 2
    assert [u["cost_usd"] for u in unrecorded(tmp_path / "out")] == [0.3]
    assert batch.spent == pytest.approx(0.3 + 0.01)


def test_a_transient_error_waits_as_long_as_the_configuration_says_and_then_stops_the_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RETRY_WAITS_S", (0.01, 0.02, 0.03))
    waits = []
    original = runner.Batch._pause
    monkeypatch.setattr(runner.Batch, "_pause", lambda self, s: waits.append(s) or original(self, s))
    runs = runner.schedule([task(1), task(2)], ["baseline"], 1, seed=1)
    calls = []

    def execute(r, *a):
        calls.append(r.task.id)
        raise runner.InfrastructureError("rate_limit", cost=0.1)

    batch = runner.Batch(runs, tmp_path, tmp_path / "out", parallel=1, execute_fn=execute, log=lambda _: None)
    stopped = batch.run()
    assert "rate_limit" in stopped and waits == [0.01, 0.02, 0.03]
    assert calls == [runs[0].task.id] * 4  # the first try and three retries; the other task never starts
    assert len(unrecorded(tmp_path / "out")) == 4 and batch.spent == pytest.approx(0.4)
    assert not (tmp_path / "out/results.jsonl").exists()


def test_a_retry_never_starts_past_the_spending_cap(tmp_path):
    runs = runner.schedule([task(1)], ["baseline"], 1, seed=1)
    calls = []

    def execute(r, *a):
        calls.append(1)
        raise runner.InfrastructureError("overloaded", cost=0.3)

    batch = runner.Batch(
        runs, tmp_path, tmp_path / "out", parallel=1, max_total_usd=0.25, execute_fn=execute, log=lambda _: None
    )
    assert "cap" in batch.run() and len(calls) == 1 and batch.spent == pytest.approx(0.3)


def test_a_configuration_retry_never_starts_past_the_spending_cap(tmp_path):
    runs = runner.schedule([task(1)], ["baseline"], 1, seed=1)
    attempts = []

    def execute(r, cache, out, attempt, claude):
        attempts.append(attempt)
        return fake_record(r, attempt, ok=False, cost=0.3)

    batch = runner.Batch(
        runs, tmp_path, tmp_path / "out", parallel=1, max_total_usd=0.25, execute_fn=execute, log=lambda _: None
    )
    assert "cap" in batch.run() and attempts == [1]
    assert not (tmp_path / "out/results.jsonl").exists()
    assert [u["why"] for u in unrecorded(tmp_path / "out")] == ["discarded attempt"]
    assert batch.spent == pytest.approx(0.3)


def test_every_charged_attempt_keeps_its_transcript(tmp_path):
    runs = runner.schedule([task(1)], ["baseline"], 1, seed=1)
    tries = []

    def execute(r, cache, out, attempt, claude):  # an API error, then a failed configuration check, then a run
        tries.append(attempt)
        (out / r.task.id).mkdir(parents=True, exist_ok=True)
        with gzip.open(out / r.task.id / f"{r.setup}-{r.rep}.jsonl.gz", "wt") as f:
            f.write(f"try {len(tries)}")
        (out / r.task.id / f"{r.setup}-{r.rep}.stderr").write_text(f"try {len(tries)}")
        if len(tries) == 1:
            raise runner.InfrastructureError("overloaded", cost=0.1)
        return fake_record(r, attempt, ok=len(tries) == 3)

    assert (
        runner.Batch(runs, tmp_path, tmp_path / "out", parallel=1, execute_fn=execute, log=lambda _: None).run() is None
    )
    run_dir = tmp_path / "out" / "t1"

    def text(name):
        with gzip.open(run_dir / name, "rt") as f:
            return f.read()

    assert tries == [1, 1, 2] and text("baseline-1.jsonl.gz") == "try 3"  # the recorded attempt's, where rescore looks
    assert text("baseline-1.unrecorded-1.jsonl.gz") == "try 1" and text("baseline-1.unrecorded-2.jsonl.gz") == "try 2"
    assert (run_dir / "baseline-1.unrecorded-2.stderr").read_text() == "try 2"
    assert [u.get("transcript") for u in unrecorded(tmp_path / "out")] == ["baseline-1.unrecorded-1.jsonl.gz"]


def test_a_permanent_error_on_a_retry_stops_the_batch_at_once(tmp_path):
    runs = runner.schedule([task(1)], ["baseline"], 1, seed=1)
    calls = []

    def execute(r, *a):
        calls.append(1)
        raise runner.InfrastructureError("overloaded" if len(calls) == 1 else "billing_error", permanent=len(calls) > 1)

    stopped = runner.Batch(runs, tmp_path, tmp_path / "out", parallel=1, execute_fn=execute, log=lambda _: None).run()
    assert "billing_error" in stopped and len(calls) == 2


def test_an_interrupt_ends_a_retry_wait_promptly(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RETRY_WAITS_S", (60,))
    runs = runner.schedule([task(1)], ["baseline"], 1, seed=1)
    calls = []

    def execute(r, *a):
        calls.append(1)
        raise runner.InfrastructureError("overloaded", cost=0.1)

    batch = runner.Batch(runs, tmp_path, tmp_path / "out", parallel=1, execute_fn=execute, log=lambda _: None)
    threading.Timer(0.3, batch.interrupt).start()
    t0 = time.monotonic()
    stopped = batch.run()
    assert "interrupted" in stopped and time.monotonic() - t0 < 10 and len(calls) == 1


def test_a_run_on_another_claude_code_version_is_recorded_and_then_stops_the_batch(tmp_path):
    runs = runner.schedule([task(1), task(2), task(3)], ["baseline"], 1, seed=1)
    first = runs[0].key

    def execute(r, *a):
        return {**fake_record(r, 1), "cli_version": "2.1.285" if r.key == first else "2.1.290"}

    stopped = runner.Batch(runs, tmp_path, tmp_path / "out", parallel=1, execute_fn=execute, log=lambda _: None).run()
    assert "2.1.285" in stopped and "2.1.290" in stopped and "Claude Code" in stopped
    assert len(results(tmp_path / "out")) == 2  # the odd run is kept; the third never starts


def test_a_resumed_batch_keeps_the_version_it_began_on(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    with open(out / "results.jsonl", "w") as f:
        f.write(json.dumps({**fake_record(runner.Run(task(9), "baseline", 1), 1), "cli_version": "2.1.285"}) + "\n")
    runs = runner.schedule([task(1), task(2)], ["baseline"], 1, seed=1)

    def execute(r, *a):
        return {**fake_record(r, 1), "cli_version": "2.1.290"}

    stopped = runner.Batch(runs, tmp_path, out, parallel=1, execute_fn=execute, log=lambda _: None).run()
    assert "2.1.285" in stopped and len(results(out)) == 2


def test_runs_without_a_recorded_version_do_not_set_or_break_the_batchs_version(tmp_path):
    runs = runner.schedule([task(1), task(2), task(3)], ["baseline"], 1, seed=1)
    versions = iter([None, "2.1.285", None])  # a run killed before its init event has none

    def execute(r, *a):
        return {**fake_record(r, 1), "cli_version": next(versions)}

    assert (
        runner.Batch(runs, tmp_path, tmp_path / "out", parallel=1, execute_fn=execute, log=lambda _: None).run() is None
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


def rust_task(i, repo, commit):
    return Task(f"r{i}", "localization", "rust", repo, commit, "p", ("src/a.rs:needle_fn",), "s")


def test_serena_runs_on_one_rust_repo_never_overlap_but_other_runs_are_not_held_back(tmp_path):
    a, b = "a" * 40, "b" * 40
    serena_runs = [
        runner.Run(rust_task(1, "o/x", a), "serena", 1),
        runner.Run(rust_task(2, "o/x", b), "serena-hint", 1),  # another commit and worktree, the same cargo target
    ]
    baselines = [runner.Run(rust_task(3, "o/x", a), "baseline", 1), runner.Run(rust_task(4, "o/x", b), "baseline", 1)]
    lock, serena_on, baselines_done = threading.Lock(), threading.Event(), threading.Event()
    state = {"serena": 0, "most": 0, "baselines": 0, "overlapped": 0}

    def execute(r, *a):
        if r.setup == "baseline":
            assert serena_on.wait(10)  # a baseline run starts while a Serena run is going
            with lock:
                state["overlapped"] += state["serena"] > 0
                state["baselines"] += 1
                if state["baselines"] == 2:
                    baselines_done.set()
        else:
            with lock:
                state["serena"] += 1
                state["most"] = max(state["most"], state["serena"])
            serena_on.set()
            assert baselines_done.wait(10)
            time.sleep(0.05)  # long enough for a second Serena run to start if it could
            with lock:
                state["serena"] -= 1
        return fake_record(r, 1)

    batch = runner.Batch(
        serena_runs + baselines, tmp_path, tmp_path / "out", parallel=4, execute_fn=execute, log=lambda _: None
    )
    assert batch.run() is None
    assert state["most"] == 1 and state["overlapped"] == 2 and len(results(tmp_path / "out")) == 4


def test_only_serena_on_a_rust_task_holds_the_cargo_target(tmp_path):
    batch = runner.Batch([], tmp_path, tmp_path / "out", log=lambda _: None)
    wt = workspace.worktree_path(tmp_path, "serena", "o/x", "a" * 40)
    target = tmp_path / "cargo-target" / "o__x"
    assert batch._resources(runner.Run(rust_task(1, "o/x", "a" * 40), "serena", 1)) == {wt, target}
    assert target in batch._resources(runner.Run(rust_task(1, "o/x", "b" * 40), "serena-hint", 1))
    for setup in ("baseline", "duckgrep", "duckgrep-hint"):
        assert batch._resources(runner.Run(rust_task(1, "o/x", "a" * 40), setup, 1)) == {
            workspace.worktree_path(tmp_path, setup, "o/x", "a" * 40)
        }
    assert batch._resources(runner.Run(task(1, "a" * 40), "serena", 1)) == {
        workspace.worktree_path(tmp_path, "serena", "o/r", "a" * 40)
    }  # Python: Serena has no cargo


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


NEEDLE_SRC = 'def hello():\n    return "hi"\n\ndef needle_fn():\n    return hello()\n'  # what tests/eval_runs saw


def test_execute_counts_a_function_located_when_its_definition_shows(tmp_path):
    src, commit = origin(tmp_path, {"src/a.py": NEEDLE_SRC})
    cache = tmp_path / "cache"
    workspace.worktree("o/r", commit, "baseline", cache, url=str(src))
    events = recorded_stream('```json\n{"locations": ["src/a.py:hello"]}\n```')
    # round 1's grep now also shows a call to hello; its definition first shows in round 2's read
    shown = "src/a.py:4:def needle_fn():"
    events = json.loads(json.dumps(events).replace(shown, shown + "\\nsrc/a.py:5:    return hello()"))
    # the recording's sanitised root stands for the worktree it ran in; a path outside the run's own is another checkout
    wt = workspace.worktree_path(cache, "baseline", "o/r", commit)
    events = json.loads(json.dumps(events).replace("/work/proj", str(wt)))
    exe, _ = fake_claude(tmp_path, events)
    hello = Task("t1", "localization", "python", "o/r", commit, "p", ("src/a.py:hello",), "s")
    rec = runner.execute(runner.Run(hello, "baseline", 1), cache, tmp_path / "out", 1, exe)
    assert rec["score"]["success"] and rec["turns_to_locate"] == 2


def test_execute_runs_scores_and_restores(tmp_path, monkeypatch):
    src, commit = origin(tmp_path, {"src/a.py": NEEDLE_SRC})
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
        "DISABLE_AUTOUPDATER",
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


def test_execute_records_a_run_that_ended_on_its_own_api_error(tmp_path):
    src, commit = origin(tmp_path, {"src/a.py": NEEDLE_SRC})
    cache = tmp_path / "cache"
    workspace.worktree("o/r", commit, "baseline", cache, url=str(src))
    events = recorded_stream("API Error: 400 due to tool use concurrency issues.")
    result = events.pop()
    events += [
        {"type": "assistant", "error": "invalid_request", "message": {"model": "<synthetic>", "content": []}},
        {**result, "terminal_reason": "api_error", "is_error": True},
    ]
    exe, _ = fake_claude(tmp_path, events)
    rec = runner.execute(runner.Run(task(1, commit), "baseline", 1), cache, tmp_path / "out", 1, exe)
    assert rec["api_error"] == "invalid_request" and rec["is_error"] and not rec["score"]["success"]


def test_a_streak_of_runs_ending_on_an_api_error_stops_the_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "API_ERROR_STREAK", 3)
    runs = runner.schedule([task(i) for i in range(6)], ["baseline"], 1, seed=1)
    errors = iter(["max_output_tokens", "invalid_request", None, "invalid_request", "invalid_request"])

    def execute(r, *a):
        return {**fake_record(r, 1), "api_error": next(errors, "invalid_request")}

    stopped = runner.Batch(runs, tmp_path, tmp_path / "out", parallel=1, execute_fn=execute, log=lambda _: None).run()
    assert "3 runs in a row" in stopped and "invalid_request" in stopped
    assert len(results(tmp_path / "out")) == 6  # a success broke the first streak

    runs = runner.schedule([task(i) for i in range(6, 9)], ["baseline"], 1, seed=1)
    stopped = runner.Batch(
        runs, tmp_path, tmp_path / "out", parallel=1, execute_fn=execute, log=lambda _: None
    ).run()  # a resume continues the streak it stopped on
    assert "4 runs in a row" in stopped and len(results(tmp_path / "out")) == 7


@pytest.mark.parametrize("name, server", [("duckgrep-hint", "duckgrep"), ("serena-hint", "serena")])
def test_a_hinted_run_uses_its_bases_worktree_and_server_and_adds_the_hint(tmp_path, name, server):
    src, commit = origin(tmp_path, {"src/a.py": NEEDLE_SRC})
    cache = tmp_path / "cache"
    workspace.worktree("o/r", commit, server, cache, url=str(src))
    mcp = tmp_path / "mcp-seen.json"
    copy = f"open({str(mcp)!r}, 'w').write(open(sys.argv[sys.argv.index('--mcp-config') + 1]).read())"
    exe, seen = fake_claude(tmp_path, recorded_stream("no answer"), extra=copy)
    rec = runner.execute(runner.Run(task(1, commit), name, 1), cache, tmp_path / "out", 1, exe)
    argv = json.loads(seen.read_text())["argv"]
    assert argv[argv.index("--append-system-prompt") + 1] == setups.SETUPS[name].hint
    assert list(json.loads(mcp.read_text())["mcpServers"]) == [server] and rec["setup"] == name


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


def test_a_lost_cache_volume_stops_the_batch_instead_of_ending_it_as_finished(tmp_path, monkeypatch):
    def unreadable(path):
        raise PermissionError(1, "Operation not permitted", str(path))  # a privacy-service outage, say

    monkeypatch.setattr(workspace, "free_gb", unreadable)
    runs = runner.schedule([task(1), task(2)], ["baseline"], 1, seed=1)
    stopped = runner.Batch(
        runs, tmp_path, tmp_path / "out", parallel=2, execute_fn=lambda *a: pytest.fail("ran"), log=lambda _: None
    ).run()
    assert stopped and "Operation not permitted" in stopped


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


def test_the_spending_cap_counts_what_earlier_invocations_spent(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    with open(out / "results.jsonl", "w") as f:
        for rep in (1, 2, 3):
            f.write(json.dumps(fake_record(runner.Run(task(9), "baseline", rep), 1, cost=0.01)) + "\n")
    runs = runner.schedule([task(1), task(2)], ["baseline"], 2, seed=1)
    stopped = runner.Batch(
        runs,
        tmp_path,
        out,
        parallel=1,
        max_total_usd=0.035,
        execute_fn=lambda r, *a: fake_record(r, 1),
        log=lambda _: None,
    ).run()
    assert "cap" in stopped and len(results(out)) == 4  # one new run takes the total to $0.04


def test_a_discarded_attempt_counts_against_the_cap(tmp_path):
    runs = runner.schedule([task(1), task(2)], ["baseline"], 1, seed=1)
    stopped = runner.Batch(
        runs,
        tmp_path,
        tmp_path / "out",
        parallel=1,
        max_total_usd=0.035,
        execute_fn=lambda r, c, o, attempt, cl: fake_record(r, attempt, ok=attempt == 2, cost=0.02),
        log=lambda _: None,
    ).run()
    [rec] = results(tmp_path / "out")
    assert rec["discarded_cost_usd"] == 0.02 and "cap" in stopped


def test_a_killed_run_counts_at_the_per_run_cap(tmp_path):
    def killed(r, *a):
        return {**fake_record(r, 1), "killed": True, "cli_cost_usd": None}

    runs = runner.schedule([task(1), task(2)], ["baseline"], 1, seed=1)
    stopped = runner.Batch(
        runs, tmp_path, tmp_path / "out", parallel=1, max_total_usd=1.0, execute_fn=killed, log=lambda _: None
    ).run()
    assert "cap" in stopped and len(results(tmp_path / "out")) == 1


def slow_batch(tmp_path):
    """A batch of one run whose fake claude signals that it started, leaves a stray file, then hangs."""
    src, commit = origin(tmp_path, {"src/a.py": "x = 1\n"})
    cache = tmp_path / "cache"
    wt = workspace.worktree("o/r", commit, "baseline", cache, url=str(src))
    started = tmp_path / "started"
    hang = f"open({str(started)!r}, 'w').write('1'); import time; time.sleep(20)"
    exe, _ = fake_claude(tmp_path, recorded_stream("no answer"), extra=hang)
    runs = runner.schedule([task(1, commit)], ["baseline"], 1, seed=1)
    return runner.Batch(runs, cache, tmp_path / "out", claude=exe, log=lambda _: None), wt, started


def once_started(started, action):
    def wait_then_act():
        deadline = time.monotonic() + 20
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        action()

    threading.Thread(target=wait_then_act, daemon=True).start()


def test_ctrl_c_stops_live_runs_restores_them_and_records_nothing(tmp_path):
    import _thread

    batch, wt, started = slow_batch(tmp_path)
    once_started(started, _thread.interrupt_main)
    t0 = time.monotonic()
    try:
        stopped = batch.run()
    except KeyboardInterrupt:
        pytest.fail("Ctrl-C escaped the batch with its runs still going")
    assert "interrupted" in stopped and time.monotonic() - t0 < 15
    assert not (tmp_path / "out" / "results.jsonl").exists()
    assert not (wt / "stray.txt").exists()


def test_sigterm_stops_the_batch_like_ctrl_c(tmp_path):
    import signal

    batch, wt, started = slow_batch(tmp_path)
    seen = {}

    def terminate():
        seen["handler"] = signal.getsignal(signal.SIGTERM)
        if seen["handler"] is not signal.SIG_DFL:  # never kill the test runner itself
            os.kill(os.getpid(), signal.SIGTERM)

    once_started(started, terminate)
    stopped = batch.run()
    assert seen["handler"] is not signal.SIG_DFL, "run() installs no SIGTERM handler"
    assert "interrupted" in stopped and not (wt / "stray.txt").exists()
    assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL  # restored afterwards


def test_an_interrupt_before_the_run_starts_keeps_it_from_starting(tmp_path, monkeypatch):
    batch, wt, started = slow_batch(tmp_path)
    reset = workspace.reset
    calls = []

    def interrupted_during_reset(path, commit):  # after _take, before Popen: nothing in flight to kill yet
        calls.append(1)
        if len(calls) == 1:
            batch.interrupt()
        return reset(path, commit)

    monkeypatch.setattr(runner.workspace, "reset", interrupted_during_reset)
    t0 = time.monotonic()
    assert "interrupted" in batch.run() and time.monotonic() - t0 < 10
    assert not started.exists() and not (tmp_path / "out" / "unrecorded.jsonl").exists()  # nothing spent
    assert not (tmp_path / "out" / "results.jsonl").exists()

    quick = tmp_path / "quick"  # the interrupt is over: a later batch in the same process runs
    quick.mkdir()
    exe, _ = fake_claude(quick, recorded_stream("no answer"))
    runs = runner.schedule([task(1, workspace.git("rev-parse", "HEAD", cwd=wt).strip())], ["baseline"], 1, seed=1)
    assert runner.Batch(runs, tmp_path / "cache", tmp_path / "out", claude=exe, log=lambda _: None).run() is None
    assert len(results(tmp_path / "out")) == 1


def test_an_interrupt_just_after_the_run_started_kills_it(tmp_path, monkeypatch):
    batch, wt, started = slow_batch(tmp_path)
    popen = runner.subprocess.Popen

    def interrupted_at_start(argv, *a, **k):  # before execute put the process where an interrupt looks
        proc = popen(argv, *a, **k)
        if argv[0] == batch.claude:
            batch.interrupt()
        return proc

    monkeypatch.setattr(runner.subprocess, "Popen", interrupted_at_start)
    t0 = time.monotonic()
    assert "interrupted" in batch.run() and time.monotonic() - t0 < 10  # the fake hangs for 20 s
    assert not (tmp_path / "out" / "results.jsonl").exists() and not (wt / "stray.txt").exists()
    assert [u["why"] for u in unrecorded(tmp_path / "out")] == ["interrupted"]


def crashing_claude(tmp_path, crashes=1):
    """A fake claude that streams a run but, the first `crashes` times, dies with status 3 before its result."""
    count = tmp_path / "count"
    stream_file = tmp_path / "stream.jsonl"
    crash = (
        f"n = len(open({str(count)!r}).read()) if os.path.exists({str(count)!r}) else 0\n"
        f"open({str(count)!r}, 'a').write('x')\n"
        f"if n < {crashes}:\n"
        f"    lines = open({str(stream_file)!r}).read().splitlines()\n"
        "    sys.stdout.write(''.join(x + '\\n' for x in lines if '\"type\": \"result\"' not in x))\n"
        "    sys.stdout.flush()\n"
        "    os._exit(3)"
    )
    return fake_claude(tmp_path, recorded_stream("no answer"), extra=crash)[0]


def test_a_claude_that_dies_mid_run_is_an_infrastructure_error_not_the_setups_failure(tmp_path):
    src, commit = origin(tmp_path, {"src/a.py": NEEDLE_SRC})
    cache = tmp_path / "cache"
    workspace.worktree("o/r", commit, "baseline", cache, url=str(src))
    exe = crashing_claude(tmp_path)
    with pytest.raises(runner.InfrastructureError, match="exited with status 3") as e:
        runner.execute(runner.Run(task(1, commit), "baseline", 1), cache, tmp_path / "out", 1, exe)
    assert not e.value.permanent and e.value.cost > 0  # from the usage streamed before it died

    logged = []
    runs = runner.schedule([task(1, commit)], ["baseline"], 1, seed=1)
    (tmp_path / "again").mkdir()
    batch = runner.Batch(runs, cache, tmp_path / "out2", claude=crashing_claude(tmp_path / "again"), log=logged.append)
    assert batch.run() is None  # retried, and the retry finished
    assert len(results(tmp_path / "out2")) == 1
    assert [u["why"] for u in unrecorded(tmp_path / "out2")] == ["infrastructure error"]
    assert any("exited with status 3" in line for line in logged)


def test_spend_of_runs_that_were_never_recorded_counts_after_a_resume(tmp_path):
    runs = runner.schedule([task(1), task(2), task(3)], ["baseline"], 1, seed=1)
    out = tmp_path / "out"
    batch = None

    def execute(r, c, o, attempt, cl):
        if r.task.id == "t1":  # interrupted mid-run
            batch.interrupt()
            return fake_record(r, attempt, cost=0.5)
        return fake_record(r, attempt, cost=0.01)

    batch = runner.Batch(runs, tmp_path, out, parallel=1, execute_fn=execute, log=lambda _: None)
    batch.run()
    again = runner.Batch(runs, tmp_path, out, execute_fn=execute, log=lambda _: None)
    recorded_cost = sum(r["cli_cost_usd"] for r in results(out)) if (out / "results.jsonl").exists() else 0
    assert again.spent == pytest.approx(recorded_cost + 0.5)


def test_an_infrastructure_error_and_a_failed_retry_count_their_spend(tmp_path):
    def execute(r, c, o, attempt, cl):
        if r.task.id == "t1":
            raise runner.InfrastructureError("rate limited", cost=0.3, permanent=True)
        if attempt == 1:
            return fake_record(r, attempt, ok=False, cost=0.2)  # t2's first attempt fails its check
        raise runner.InfrastructureError("overloaded", cost=0.0, permanent=True)

    out = tmp_path / "out"
    for t in (task(1), task(2)):
        runner.Batch(
            runner.schedule([t], ["baseline"], 1, seed=1), tmp_path, out, execute_fn=execute, log=lambda _: None
        ).run()
    again = runner.Batch(
        runner.schedule([task(1), task(2)], ["baseline"], 1, seed=1),
        tmp_path,
        out,
        execute_fn=execute,
        log=lambda _: None,
    )
    assert again.spent == pytest.approx(0.3 + 0.2)


def test_a_batch_that_finishes_past_the_cap_has_not_stopped_early(tmp_path):
    runs = runner.schedule([task(1)], ["baseline"], 1, seed=1)
    out = tmp_path / "out"
    assert (
        runner.Batch(
            runs, tmp_path, out, max_total_usd=0.005, execute_fn=lambda r, *a: fake_record(r, 1), log=lambda _: None
        ).run()
        is None
    )
    assert (
        runner.Batch(
            runs, tmp_path, out, max_total_usd=0.005, execute_fn=lambda *a: pytest.fail("rerun"), log=lambda _: None
        ).run()
        is None
    )


def test_a_probe_that_times_out_is_reported_not_raised(tmp_path, monkeypatch):
    src, commit = origin(tmp_path, {"src/a.py": "x = 1\n"})
    cache = tmp_path / "cache"
    workspace.worktree("o/r", commit, "baseline", cache, url=str(src))
    exe, _ = fake_claude(tmp_path, [], extra="import time; time.sleep(30)")
    monkeypatch.setattr(runner, "PROBE_TIMEOUT_S", 1)
    problems = runner.probe(task(1, commit), "baseline", cache, exe)
    assert any("within 1 s" in p for p in problems)


def test_rescore_recomputes_what_a_transcript_gives_and_keeps_the_original(tmp_path):
    src, commit = origin(tmp_path, {"src/a.py": NEEDLE_SRC})
    cache, out = tmp_path / "cache", tmp_path / "out"
    workspace.worktree("o/r", commit, "baseline", cache, url=str(src))
    exe, _ = fake_claude(tmp_path, recorded_stream('```json\n{"locations": ["src/a.py:needle_fn"]}\n```'))
    t = task(1, commit)
    rec = runner.execute(runner.Run(t, "baseline", 1), cache, out, 1, exe)
    stale = {**rec, "turns_to_locate": None, "adopted": True, "score": {"success": False}, "wall_s": 12.5}
    (out / "results.jsonl").write_text(json.dumps(stale) + "\n")
    changed = runner.rescore(out, [t], cache)
    new = json.loads((out / "results.jsonl").read_text())
    assert new["turns_to_locate"] == 1 and not new["adopted"] and new["score"]["success"]
    assert new["wall_s"] == 12.5 and new["worktree_changes"] == rec["worktree_changes"]  # not from the transcript
    assert changed["turns_to_locate"] == 1 and changed["adopted"] == 1
    assert json.loads((out / "results.orig.jsonl").read_text()) == stale
    runner.rescore(out, [t], cache)  # again: the original stays the first version
    assert json.loads((out / "results.orig.jsonl").read_text()) == stale


def test_rescore_keeps_a_record_whose_source_is_gone(tmp_path):
    src, commit = origin(tmp_path, {"src/a.py": NEEDLE_SRC})
    cache, out = tmp_path / "cache", tmp_path / "out"
    workspace.worktree("o/r", commit, "baseline", cache, url=str(src))
    exe, _ = fake_claude(tmp_path, recorded_stream('```json\n{"locations": ["src/a.py:needle_fn"]}\n```'))
    t = task(1, commit)
    rec = runner.execute(runner.Run(t, "baseline", 1), cache, out, 1, exe)
    (out / "results.jsonl").write_text(json.dumps({**rec, "turns_to_locate": 7}) + "\n")
    (cache / "repos" / "o__r.git").rename(cache / "repos" / "moved.git")  # the clone left the cache
    changed = runner.rescore(out, [t], cache)
    assert json.loads((out / "results.jsonl").read_text())["turns_to_locate"] == 7 and changed["missing"] == 1


def test_the_files_a_run_could_see_are_the_commits_and_those_it_created(tmp_path):
    src, commit = origin(tmp_path, {"src/a.py": NEEDLE_SRC})
    cache = tmp_path / "cache"
    workspace.clone("o/r", cache, url=str(src))
    left = ["?? tests/a.py", " M src/a.py", "!! src/__pycache__/a.cpython-312.pyc"]
    assert runner.visible(cache, task(1, commit), left) == {
        "src/a.py",
        "tests/a.py",
        "src/__pycache__/a.cpython-312.pyc",
    }
    assert runner.visible(cache, task(1, "f" * 40), left) is None  # no commit to list: nothing to narrow by


def test_serena_runs_on_rust_across_repos_run_at_most_the_limit_at_once(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUST_LSP_LIMIT", 2)
    serena_runs = [runner.Run(rust_task(i, f"o/r{i}", "a" * 40), "serena", 1) for i in range(4)]  # four cargo targets
    baselines = [runner.Run(rust_task(10 + i, f"o/r{i}", "a" * 40), "baseline", 1) for i in range(2)]
    lock, state = threading.Lock(), {"serena": 0, "most": 0, "baseline_during_serena": 0}

    def execute(r, *a):
        with lock:
            if r.setup == "serena":
                state["serena"] += 1
                state["most"] = max(state["most"], state["serena"])
            else:
                state["baseline_during_serena"] += state["serena"] > 0
        time.sleep(0.1)
        if r.setup == "serena":
            with lock:
                state["serena"] -= 1
        return fake_record(r, 1)

    batch = runner.Batch(serena_runs + baselines, tmp_path, tmp_path / "out", parallel=4, execute_fn=execute,
                         log=lambda _: None)  # fmt: skip
    assert batch.run() is None
    assert state["most"] == 2 and state["baseline_during_serena"] >= 1 and len(results(tmp_path / "out")) == 6


def test_a_second_invocation_on_one_results_directory_refuses_to_start(tmp_path):
    import subprocess

    out = tmp_path / "out"
    out.mkdir()
    holder = subprocess.Popen(  # another `run` on the same results, in a process of its own
        [sys.executable, "-c", "import fcntl, sys, time\n"
         f"f = open({str(out / '.lock')!r}, 'a'); fcntl.flock(f, fcntl.LOCK_EX)\n"
         "print('held', flush=True); time.sleep(30)"],
        stdout=subprocess.PIPE, text=True,
    )  # fmt: skip
    try:
        assert holder.stdout.readline().strip() == "held"
        runs = runner.schedule([task(1)], ["baseline"], 1, seed=1)
        with pytest.raises(runner.Busy, match="another"):
            runner.Batch(runs, tmp_path, out, execute_fn=lambda *a: pytest.fail("ran"), log=lambda _: None)
    finally:
        holder.kill()
        holder.wait()
    batch = runner.Batch(runs, tmp_path, out, execute_fn=lambda r, *a: fake_record(r, 1), log=lambda _: None)
    assert batch.run() is None and len(results(out)) == 1
    again = runner.Batch(runs, tmp_path, out, log=lambda _: None)  # run() let go of it
    assert again.pending == []


@pytest.mark.parametrize("name", ["results.jsonl", "unrecorded.jsonl"])
def test_a_partly_written_last_line_is_cut_and_its_run_charged_at_the_cap(tmp_path, name):
    out = tmp_path / "out"
    out.mkdir()
    done = fake_record(runner.Run(task(1), "baseline", 1), 1)
    whole = {"results.jsonl": done, "unrecorded.jsonl": {"task": "t9", "why": "interrupted", "cost_usd": 0.2}}
    (out / name).write_text(json.dumps(whole[name]) + "\n" + json.dumps(done)[:40])  # the harness killed mid-write
    logged = []
    runs = runner.schedule([task(1), task(2)], ["baseline"], 1, seed=1)
    batch = runner.Batch(runs, tmp_path, out, execute_fn=lambda r, *a: fake_record(r, 1), log=logged.append)
    assert (out / name).read_text().splitlines()[0] == json.dumps(whole[name])
    assert unrecorded(out)[-1]["why"] == "partial record" and unrecorded(out)[-1]["cost_usd"] == config.MAX_BUDGET_USD
    earlier = {"results.jsonl": 0.01, "unrecorded.jsonl": 0.2}[name]
    assert batch.spent == pytest.approx(earlier + config.MAX_BUDGET_USD) and any(name in m for m in logged)
    assert batch.run() is None
    assert {r["task"] for r in results(out)} == {"t1", "t2"}  # every line whole again
    assert runner.Batch(runs, tmp_path, out, log=lambda _: None).spent == pytest.approx(batch.spent)  # charged once


def test_every_scheduled_pair_must_be_prepared_for_its_setup(tmp_path):
    src, commit = origin(tmp_path, {"src/a.py": NEEDLE_SRC})
    cache = tmp_path / "cache"
    for setup in ("baseline", "duckgrep", "serena"):
        workspace.worktree("o/r", commit, setup, cache, url=str(src))
    names = ["baseline", "duckgrep", "duckgrep-hint", "serena", "serena-hint"]
    runs = runner.schedule([task(1, commit), task(2, "f" * 40)], names, 2, seed=1)
    missing = runner.unprepared(runs, cache)
    duckgrep = workspace.worktree_path(cache, "duckgrep", "o/r", commit)
    serena = workspace.worktree_path(cache, "serena", "o/r", commit)
    assert len(missing) == 2 + 3  # each worktree once, however many setups and repetitions share it
    assert any(str(duckgrep) in m and "index" in m for m in missing)
    assert any(str(serena) in m and "Serena" in m for m in missing)
    assert sum("f" * 12 in m and "no worktree" in m for m in missing) == 3
    (duckgrep / ".duckgrep").mkdir()
    (duckgrep / ".duckgrep" / "index.duckdb").write_bytes(b"")
    workspace.serena_project_file(cache, serena).parent.mkdir(parents=True)
    workspace.serena_project_file(cache, serena).write_text("")
    assert len(runner.unprepared(runs, cache)) == 3 and runner.unprepared(runs[:0], cache) == []
