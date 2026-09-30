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
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import config, locate, score, setups, stream, workspace
from .suite import Task


class InfrastructureError(RuntimeError):
    """The run failed outside the agent (login, rate limit, API outage). The batch stops; the run is redone later.
    `cost` is what the run spent before it failed, which still counts against the batch's cap."""

    def __init__(self, message: str, cost: float = 0.0):
        super().__init__(message)
        self.cost = cost


PROBE_TIMEOUT_S = 300


INTERRUPTED = "interrupted; rerun the same command to resume"
_live: dict[subprocess.Popen, str] = {}  # the runs in flight (process -> run id), for an interrupt
_live_lock = threading.Lock()


def charged(rec: dict) -> float:
    """What a recorded run may have cost: Claude Code's own total, or the per-run cap when the run left none
    (a killed run), plus the attempt a failed configuration check discarded."""
    cost = rec.get("cli_cost_usd")
    return (config.MAX_BUDGET_USD if cost is None else cost) + (rec.get("discarded_cost_usd") or 0.0)


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


def recorded(results: Path) -> list[dict]:
    if not results.exists():
        return []
    with open(results) as f:
        return [json.loads(line) for line in f if line.strip()]


def finished(results: Path) -> set[tuple[str, str, int]]:
    return {(r["task"], r["setup"], r["rep"]) for r in recorded(results)}


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
    run_id = uuid.uuid4().hex
    with tempfile.TemporaryDirectory(dir=cache / "tmp") as tmp:
        argv = setups.command(setup, task.prompt, _mcp_file(setup, task, wt, cache, Path(tmp)), claude=claude)
        try:
            out = subprocess.run(
                argv,
                cwd=wt,
                env=setups.environment(with_user=False, run_id=run_id),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=PROBE_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            return [f"the probe did not stop at login within {PROBE_TIMEOUT_S} s"]
        finally:
            setups.sweep(run_id)  # the timed-out claude too; and Serena starts its language server even when idle
    tr = stream.read(out.stdout.splitlines())
    problems = stream.config_problems(tr, setup.expected_tools, set(setup.servers))
    if tr.api_error != "authentication_failed":
        problems.append("the probe did not stop at login; check that it cost nothing")
    return problems


def measure(tr: stream.Transcript, task: Task, setup_name: str, cache: Path) -> dict:
    """What a record takes from a run's transcript: the configuration check, the metrics, the answer and its
    score, and turns to locate. `rescore` recomputes exactly these."""
    setup = setups.SETUPS[setup_name]
    wt = workspace.worktree_path(cache, setup_name, task.repo, task.commit)
    m = stream.metrics(tr)
    answer = score.parse_answer(m["final_text"])
    problems = stream.config_problems(tr, setup.expected_tools, set(setup.servers))
    found = locate.targets(task.gold, workspace.reader(cache, task.repo, task.commit))
    return {
        "config_ok": not problems,
        "config_problems": problems,
        **m,
        "answer": answer,
        "score": score.score(answer, task.gold, task.answer, (str(wt), os.path.realpath(wt))).as_dict(),
        "turns_to_locate": locate.turns_to_locate(tr, found, (str(wt), os.path.realpath(wt))),
    }


def rescore(out_dir: Path, tasks: list[Task], cache: Path) -> Counter:
    """Recompute every record's `measure` from its saved transcript, after a change to how runs are measured.
    The results as first written are kept in results.orig.jsonl. Returns how many records each field changed in."""
    results = out_dir / "results.jsonl"
    original = out_dir / "results.orig.jsonl"
    if not original.exists():
        shutil.copyfile(results, original)
    by_id = {t.id: t for t in tasks}
    changed: Counter = Counter()
    rows = []
    for rec in recorded(results):
        transcript = out_dir / rec["task"] / f"{rec['setup']}-{rec['rep']}.jsonl.gz"
        if rec["task"] in by_id and transcript.exists():
            with gzip.open(transcript, "rt") as f:
                new = measure(stream.read(f), by_id[rec["task"]], rec["setup"], cache)
            changed.update(k for k, v in new.items() if rec.get(k) != v)
            rec = {**rec, **new}
        else:
            changed["missing"] += 1
        rows.append(rec)
    tmp = results.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r) + "\n" for r in rows))
    tmp.replace(results)
    return changed


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
            run_id = uuid.uuid4().hex
            with open(raw, "w") as out, open(run_dir / f"{run.setup}-{run.rep}.stderr", "w") as err:
                proc = subprocess.Popen(
                    argv,
                    cwd=wt,
                    env=setups.environment(run_id=run_id),
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=err,
                    start_new_session=True,
                )
                with _live_lock:
                    _live[proc] = run_id
                try:
                    proc.wait(timeout=config.WALL_LIMIT_S)
                except subprocess.TimeoutExpired:
                    killed = True
                finally:
                    _kill_group(proc)
                    setups.sweep(run_id)
                    with _live_lock:
                        _live.pop(proc, None)
            wall = time.monotonic() - started
        with open(raw) as f:
            tr = stream.read(f)
        with open(raw, "rb") as src, gzip.open(raw.with_name(raw.name + ".gz"), "wb") as dst:
            shutil.copyfileobj(src, dst)
        raw.unlink()
        if stream.infrastructure_error(tr) and not killed:
            spent = tr.result.get("total_cost_usd")
            if spent is None:
                spent = stream.cost(stream.tokens(tr.result, tr.usage_by_message))
            raise InfrastructureError(
                f"{task.id}/{run.setup}-{run.rep}: {tr.api_error or tr.result.get('result')}", cost=spent
            )
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
            "killed": killed,
            "wall_s": round(wall, 1),
            **measure(tr, task, run.setup, cache),
        }
    finally:
        changed, moved = workspace.reset(wt, task.commit)
    record["worktree_changes"], record["head_moved"] = changed, moved
    return record


class Batch:
    """Runs a schedule with `parallel` workers. Runs that share a worktree never overlap. The spending cap covers
    what earlier invocations of the same batch spent, so resuming never renews it."""

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
        self.unrecorded = out_dir / "unrecorded.jsonl"  # spend of runs never recorded: interrupted, failed
        earlier = recorded(self.results)
        done = {(r["task"], r["setup"], r["rep"]) for r in earlier}
        self.pending = [r for r in runs if r.key not in done]
        self.cache, self.out_dir, self.parallel = cache, out_dir, parallel
        self.max_total_usd, self.execute, self.claude, self.log = max_total_usd, execute_fn, claude, log
        self.busy: set[Path] = set()
        self.cond = threading.Condition()
        self.spent = sum(charged(r) for r in earlier) + sum(u["cost_usd"] for u in recorded(self.unrecorded))
        self.stopped: str | None = None
        self.interrupted = False
        self.completed = 0
        self.working = 0  # workers still running

    def _take(self) -> Run | None:
        with self.cond:
            while True:
                if self.stopped or not self.pending:
                    return None  # nothing pending is a finished batch, whatever it spent
                if self.spent >= self.max_total_usd:
                    self.stopped = f"spent ${self.spent:.2f}, the batch cap is ${self.max_total_usd:.2f}"
                elif workspace.free_gb(self.cache) < config.MIN_FREE_GB:
                    self.stopped = f"less than {config.MIN_FREE_GB} GB free under {self.cache}"
                if self.stopped:
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
            self.spent += charged(rec)
            self.completed += 1

    def _charge_unrecorded(self, r: Run, cost: float, why: str) -> None:
        """Count what a run spent that no results line will show, so a resume counts it too."""
        with self.cond:
            with open(self.unrecorded, "a") as f:
                f.write(
                    json.dumps({"task": r.task.id, "setup": r.setup, "rep": r.rep, "why": why, "cost_usd": cost}) + "\n"
                )
                f.flush()
                os.fsync(f.fileno())
            self.spent += cost

    def _worker(self) -> None:
        while (r := self._take()) is not None:
            try:
                rec = self.execute(r, self.cache, self.out_dir, 1, self.claude)
                if not rec["config_ok"] and not self.interrupted:  # discard, and retry once
                    self.log(f"config check failed, retrying: {r.key} {rec['config_problems']}")
                    first = rec
                    try:
                        rec = self.execute(r, self.cache, self.out_dir, 2, self.claude)
                    except BaseException:
                        self._charge_unrecorded(r, charged(first), "discarded attempt")
                        raise
                    rec["discarded_cost_usd"] = charged(first)
                if self.interrupted:  # a run the interrupt cut short is redone on resume, never scored
                    self._charge_unrecorded(r, charged(rec), "interrupted")
                    continue
                self._record(rec)
                self.log(
                    f"[{self.completed}] {r.task.id} {r.setup}-{r.rep}: {rec['tool_calls']} calls, "
                    f"${rec['cost_usd']:.3f}, success={rec['score']['success']}"
                )
            except InfrastructureError as e:
                self._charge_unrecorded(r, e.cost, "infrastructure error")
                with self.cond:
                    self.stopped = self.stopped or f"infrastructure error: {e}"
                    self.cond.notify_all()
            except Exception as e:  # a harness fault: stop rather than record a wrong result
                with self.cond:
                    self.stopped = self.stopped or f"{r.task.id} {r.setup}-{r.rep} failed: {e!r}"
                    self.cond.notify_all()
            finally:
                self._release(r)

    def interrupt(self) -> None:
        """Stop now: start nothing more and kill the runs in flight. They reset their worktrees as they unwind
        and are not recorded, so a resume redoes them."""
        with self.cond:
            self.interrupted = True
            self.stopped = INTERRUPTED
            self.cond.notify_all()
        with _live_lock:
            live = list(_live.items())
        for proc, run_id in live:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            setups.sweep(run_id)

    def _work(self) -> None:
        try:
            self._worker()
        finally:
            with self.cond:
                self.working -= 1
                self.cond.notify_all()

    def _wait(self) -> None:
        # not Thread.join: in CPython 3.12 a KeyboardInterrupt inside join() marks the thread stopped while it
        # still runs, so a second join() would return before the worker had reset its worktree
        with self.cond:
            while self.working:
                self.cond.wait(0.2)

    def run(self) -> str | None:
        """Run everything pending; return why the batch stopped early, or None if it finished. Ctrl-C and
        SIGTERM interrupt it cleanly."""
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.working = self.parallel
        for _ in range(self.parallel):
            threading.Thread(target=self._work, daemon=True).start()
        main = threading.current_thread() is threading.main_thread()
        previous = signal.signal(signal.SIGTERM, _raise_interrupt) if main else None
        try:
            self._wait()
        except KeyboardInterrupt:
            self.interrupt()
            self._wait()
        finally:
            if main:
                signal.signal(signal.SIGTERM, previous if previous is not None else signal.SIG_DFL)
        return self.stopped


def _raise_interrupt(signum, frame) -> None:
    raise KeyboardInterrupt
