"""Run every (task, setup, repetition) of a suite: in a seeded random order, a few at a time, capped, resumable.

Results go to runs/<name>/results.jsonl, one line per finished run; a restart skips what is there. Each run's raw
stream is kept as runs/<name>/<task>/<setup>-<rep>.jsonl.gz.
"""

from __future__ import annotations

import gzip
import json
import os
import random
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import config, score, setups, stream, workspace
from .suite import Task


class InfrastructureError(RuntimeError):
    """The run failed outside the agent (login, rate limit, API outage). The batch stops; the run is redone later."""


@dataclass(frozen=True)
class Run:
    task: Task
    setup: str
    rep: int

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.task.id, self.setup, self.rep)


def schedule(tasks: list[Task], setup_names: list[str], reps: int, seed: int) -> list[Run]:
    """Every run in a seeded random order, so no setup systematically runs with a warmer prompt cache."""
    runs = [Run(t, s, r) for t in tasks for s in setup_names for r in range(1, reps + 1)]
    random.Random(seed).shuffle(runs)
    return runs


def finished(results: Path) -> set[tuple[str, str, int]]:
    if not results.exists():
        return set()
    with open(results) as f:
        return {(r["task"], r["setup"], r["rep"]) for r in map(json.loads, filter(str.strip, f))}


def _kill_group(proc: subprocess.Popen) -> None:
    """Stop the run's whole process group: claude, its MCP servers and their language servers."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        time.sleep(0.5)


def _mcp_file(setup: setups.Setup, task: Task, wt: Path, cache: Path, tmp: Path) -> Path | None:
    """Write the run's --mcp-config file (and its own SERENA_HOME) into `tmp`; None for the baseline."""
    home = setups.serena_home(tmp / "serena-home", cache) if setup.name == "serena" else None
    cfg = setups.mcp_config(setup, wt, task.repo, cache, home)
    if cfg is None:
        return None
    path = tmp / "mcp.json"
    path.write_text(json.dumps(cfg))
    return path


def probe(task: Task, setup_name: str, cache: Path, claude: str) -> list[str]:
    """Run a setup's exact command without USER. Login then fails before any model call, so the probe costs
    nothing, but the startup event still shows the tools, MCP servers, plugins and skills a run would get."""
    setup = setups.SETUPS[setup_name]
    wt = workspace.worktree_path(cache, setup_name, task.repo, task.commit)
    if not (wt / ".git").exists():
        return [f"{wt} is missing: run `prepare` first"]
    (cache / "tmp").mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache / "tmp") as tmp:
        argv = setups.command(setup, task.prompt, _mcp_file(setup, task, wt, cache, Path(tmp)), claude=claude)
        out = subprocess.run(
            argv,
            cwd=wt,
            env=setups.environment(with_user=False),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=300,
        )
    tr = stream.read(out.stdout.splitlines())
    problems = stream.config_problems(tr, setup.expected_tools, set(setup.servers))
    if tr.api_error != "authentication_failed":
        problems.append("the probe did not stop at login; check that it cost nothing")
    return problems


def execute(run: Run, cache: Path, out_dir: Path, attempt: int, claude: str) -> dict:
    """One run: start claude in the setup's worktree, capture the stream, score it. The worktree is put back at
    the task's commit before the run and after it, however the run ends."""
    task, setup = run.task, setups.SETUPS[run.setup]
    wt = workspace.worktree_path(cache, run.setup, task.repo, task.commit)
    if not (wt / ".git").exists():
        raise RuntimeError(f"{wt} is missing: run `prepare` first")
    run_dir = out_dir / task.id
    run_dir.mkdir(parents=True, exist_ok=True)
    raw = run_dir / f"{run.setup}-{run.rep}.jsonl"
    (cache / "tmp").mkdir(parents=True, exist_ok=True)
    workspace.reset(wt, task.commit)  # whatever an interrupted run left behind
    try:
        with tempfile.TemporaryDirectory(dir=cache / "tmp") as tmp:
            argv = setups.command(setup, task.prompt, _mcp_file(setup, task, wt, cache, Path(tmp)), claude=claude)
            started = time.monotonic()
            killed = False
            with open(raw, "w") as out, open(run_dir / f"{run.setup}-{run.rep}.stderr", "w") as err:
                proc = subprocess.Popen(
                    argv,
                    cwd=wt,
                    env=setups.environment(),
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=err,
                    start_new_session=True,
                )
                try:
                    proc.wait(timeout=config.WALL_LIMIT_S)
                except subprocess.TimeoutExpired:
                    killed = True
                finally:
                    _kill_group(proc)
            wall = time.monotonic() - started
        with open(raw) as f:
            tr = stream.read(f)
        with open(raw, "rb") as src, gzip.open(raw.with_name(raw.name + ".gz"), "wb") as dst:
            shutil.copyfileobj(src, dst)
        raw.unlink()
        if stream.infrastructure_error(tr) and not killed:
            raise InfrastructureError(f"{task.id}/{run.setup}-{run.rep}: {tr.api_error or tr.result.get('result')}")
        m = stream.metrics(tr)
        answer = score.parse_answer(m["final_text"])
        roots = (str(wt), os.path.realpath(wt))
        problems = stream.config_problems(tr, setup.expected_tools, set(setup.servers))
        record = {
            "task": task.id,
            "setup": run.setup,
            "rep": run.rep,
            "attempt": attempt,
            "seed": config.SEED,
            "kind": task.kind,
            "lang": task.lang,
            "repo": task.repo,
            "stratum": task.stratum,
            "config_ok": not problems,
            "config_problems": problems,
            "killed": killed,
            "wall_s": round(wall, 1),
            **m,
            "answer": answer,
            "score": score.score(answer, task.gold, task.answer, roots).as_dict(),
            "turns_to_locate": stream.turns_to_locate(tr, task.gold),
        }
    finally:
        changed, moved = workspace.reset(wt, task.commit)
    record["worktree_changes"], record["head_moved"] = changed, moved
    return record


class Batch:
    """Runs a schedule with `parallel` workers. Runs that share a worktree never overlap."""

    def __init__(
        self,
        runs: list[Run],
        cache: Path,
        out_dir: Path,
        parallel: int = config.PARALLEL,
        max_total_usd: float = 200.0,
        execute_fn: Callable[..., dict] = execute,
        claude: str = "claude",
        log: Callable[[str], None] = print,
    ):
        self.results = out_dir / "results.jsonl"
        done = finished(self.results)
        self.pending = [r for r in runs if r.key not in done]
        self.cache, self.out_dir, self.parallel = cache, out_dir, parallel
        self.max_total_usd, self.execute, self.claude, self.log = max_total_usd, execute_fn, claude, log
        self.busy: set[Path] = set()
        self.cond = threading.Condition()
        self.spent = 0.0
        self.stopped: str | None = None
        self.completed = 0

    def _take(self) -> Run | None:
        with self.cond:
            while True:
                if not self.stopped and workspace.free_gb(self.cache) < config.MIN_FREE_GB:
                    self.stopped = f"less than {config.MIN_FREE_GB} GB free under {self.cache}"
                if self.stopped or not self.pending:
                    return None
                for i, r in enumerate(self.pending):
                    wt = workspace.worktree_path(self.cache, r.setup, r.task.repo, r.task.commit)
                    if wt not in self.busy:
                        self.busy.add(wt)
                        return self.pending.pop(i)
                self.cond.wait()

    def _release(self, r: Run) -> None:
        with self.cond:
            self.busy.discard(workspace.worktree_path(self.cache, r.setup, r.task.repo, r.task.commit))
            self.cond.notify_all()

    def _record(self, rec: dict) -> None:
        with self.cond:
            with open(self.results, "a") as f:
                f.write(json.dumps(rec, sort_keys=True) + "\n")
                f.flush()
                os.fsync(f.fileno())
            self.spent += rec.get("cli_cost_usd") or 0.0
            self.completed += 1
            if self.spent >= self.max_total_usd and not self.stopped:
                self.stopped = f"spent ${self.spent:.2f}, the batch cap is ${self.max_total_usd:.2f}"

    def _worker(self) -> None:
        while (r := self._take()) is not None:
            try:
                rec = self.execute(r, self.cache, self.out_dir, 1, self.claude)
                if not rec["config_ok"]:  # discard, and retry once
                    self.log(f"config check failed, retrying: {r.key} {rec['config_problems']}")
                    rec = self.execute(r, self.cache, self.out_dir, 2, self.claude)
                self._record(rec)
                self.log(
                    f"[{self.completed}] {r.task.id} {r.setup}-{r.rep}: {rec['tool_calls']} calls, "
                    f"${rec['cost_usd']:.3f}, success={rec['score']['success']}"
                )
            except InfrastructureError as e:
                with self.cond:
                    self.stopped = self.stopped or f"infrastructure error: {e}"
                    self.cond.notify_all()
            except Exception as e:  # a harness fault: stop rather than record a wrong result
                with self.cond:
                    self.stopped = self.stopped or f"{r.task.id} {r.setup}-{r.rep} failed: {e!r}"
                    self.cond.notify_all()
            finally:
                self._release(r)

    def run(self) -> str | None:
        """Run everything pending; return why the batch stopped early, or None if it finished."""
        self.out_dir.mkdir(parents=True, exist_ok=True)
        workers = [threading.Thread(target=self._worker, daemon=True) for _ in range(self.parallel)]
        for w in workers:
            w.start()
        for w in workers:
            w.join()
        return self.stopped
