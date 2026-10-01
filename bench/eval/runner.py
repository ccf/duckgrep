"""Run every (task, setup, repetition) of a suite: in a seeded random order, a few at a time, capped, resumable.

Results go to runs/<name>/results.jsonl, one line per finished run; a restart skips what is there. Each run's raw
stream is kept as runs/<name>/<task>/<setup>-<rep>.jsonl.gz.
"""

from __future__ import annotations

import fcntl
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
    """The run failed outside the agent (login, rate limit, API outage). The run is not recorded. A transient
    error is retried; a permanent one (login, billing, credentials) stops the batch. `cost` is what the run
    spent before it failed, which still counts against the batch's cap."""

    def __init__(self, message: str, cost: float = 0.0, permanent: bool = False):
        super().__init__(message)
        self.cost = cost
        self.permanent = permanent


class Interrupted(RuntimeError):
    """The batch was interrupted before the run started: nothing ran, nothing was spent."""


class Busy(RuntimeError):
    """Another process is running a batch on the same results directory."""


PROBE_TIMEOUT_S = 300


INTERRUPTED = "interrupted; rerun the same command to resume"
_live: dict[subprocess.Popen, str] = {}  # the runs in flight (process -> run id), for an interrupt
_live_lock = threading.Lock()
_stopping = threading.Event()  # set by an interrupt under _live_lock, so a run starting as it comes is not missed


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


def started_on(results: Path) -> str | None:
    """The Claude Code version a batch began on: its earliest recorded run's. None until a run has one."""
    if not results.exists():
        return None
    with open(results) as f:
        for line in f:
            try:
                version = json.loads(line).get("cli_version")
            except json.JSONDecodeError:
                continue  # a last line cut off mid-write; the batch cuts it when it starts
            if version:
                return version
    return None


def cut_partial_line(path: Path) -> int:
    """Cut a last line the harness left partly written (killed mid-append) back to the last whole line, so that
    reading never fails and the next append starts a line of its own. Returns the bytes cut."""
    if not path.exists():
        return 0
    with open(path, "rb+") as f:
        size = end = f.seek(0, os.SEEK_END)
        keep = 0
        while end > 0:
            start = max(0, end - (1 << 16))
            f.seek(start)
            newline = f.read(end - start).rfind(b"\n")
            if newline >= 0:
                keep = start + newline + 1
                break
            end = start
        if keep < size:
            f.truncate(keep)
    return size - keep


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
    home = setups.serena_home(tmp / "serena-home", cache) if setup.base == "serena" else None
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


def unprepared(runs: list[Run], cache: Path) -> list[str]:
    """What `prepare` has not built for the scheduled runs, one line per worktree. A missing worktree would stop
    the batch whenever its turn came; a missing index or Serena project would let that run build it on the clock."""
    missing = {}
    for r in runs:
        base = setups.SETUPS[r.setup].base
        wt = workspace.worktree_path(cache, r.setup, r.task.repo, r.task.commit)
        if wt in missing:
            continue
        if not (wt / ".git").exists():
            missing[wt] = f"{wt}: no worktree"
        elif base == "duckgrep" and not (wt / ".duckgrep" / "index.duckdb").exists():
            missing[wt] = f"{wt}: no duckgrep index"
        elif base == "serena" and not workspace.serena_project_file(cache, wt).exists():
            missing[wt] = f"{wt}: Serena is not warmed up"
        else:
            missing[wt] = None
    return [m for m in missing.values() if m]


def visible(cache: Path, task: Task, left: list[str]) -> set[str] | None:
    """The files a run could see: those at the task's commit and those it created (`left`, its worktree changes
    in porcelain form). None when the commit cannot be listed."""
    files = workspace.listing(cache, task.repo, task.commit)
    if files is None:
        return None
    return set(files) | {c[3:] for c in left if c[:2] in ("??", "!!")}


def measure(tr: stream.Transcript, task: Task, setup_name: str, cache: Path, left: list[str] | tuple = ()) -> dict:
    """What a record takes from a run's transcript: the configuration check, the metrics, the answer and its
    score, and turns to locate. `left` is what the run left in its worktree. `rescore` recomputes exactly
    these."""
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
        "turns_to_locate": locate.turns_to_locate(
            tr, found, (str(wt), os.path.realpath(wt)), visible(cache, task, list(left))
        ),
    }


def rescore(out_dir: Path, tasks: list[Task], cache: Path) -> Counter:
    """Recompute every record's `measure` from its saved transcript, after a change to how runs are measured.
    The results as first written are kept in results.orig.jsonl. Returns how many records each field changed in,
    and under "missing" how many were kept as recorded, lacking their transcript, task or source."""
    results = out_dir / "results.jsonl"
    original = out_dir / "results.orig.jsonl"
    if not original.exists():
        shutil.copyfile(results, original)
    by_id = {t.id: t for t in tasks}
    changed: Counter = Counter()
    rows = []
    for rec in recorded(results):
        transcript = out_dir / rec["task"] / f"{rec['setup']}-{rec['rep']}.jsonl.gz"
        task = by_id.get(rec["task"])
        read = workspace.reader(cache, task.repo, task.commit) if task else None
        # without the task's source at its commit, turns to locate would lose its definition lines: keep the record
        sources = task is not None and all(read(e.partition(":")[0]) is not None for e in task.gold)
        if sources and transcript.exists():
            with gzip.open(transcript, "rt") as f:
                new = measure(stream.read(f), task, rec["setup"], cache, rec.get("worktree_changes") or [])
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
            if _stopping.is_set():  # the interrupt came while this run was being set up
                raise Interrupted(INTERRUPTED)
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
                    if _stopping.is_set():  # the interrupt came between the check above and Popen, and missed it
                        os.killpg(proc.pid, signal.SIGKILL)
                try:
                    proc.wait(timeout=config.WALL_LIMIT_S)
                except subprocess.TimeoutExpired:
                    killed = True
                finally:
                    _kill_group(proc)
                    setups.sweep(run_id)
                    with _live_lock:
                        _live.pop(proc, None)
                        interrupted = _stopping.is_set()
            wall = time.monotonic() - started
        with open(raw) as f:
            tr = stream.read(f)
        with open(raw, "rb") as src, gzip.open(raw.with_name(raw.name + ".gz"), "wb") as dst:
            shutil.copyfileobj(src, dst)
        raw.unlink()
        spent = tr.result.get("total_cost_usd")
        if spent is None:
            spent = stream.cost(stream.tokens(tr.result, tr.usage_by_message))
        if not tr.result and not killed and not interrupted:  # a crash, say under memory pressure: not the setup's
            raise InfrastructureError(
                f"{task.id}/{run.setup}-{run.rep}: claude exited with status {proc.returncode} before its result",
                cost=spent,
            )
        if stream.infrastructure_error(tr) and not killed and not interrupted:
            raise InfrastructureError(
                f"{task.id}/{run.setup}-{run.rep}: {tr.api_error or tr.result.get('result')}",
                cost=spent,
                permanent=stream.permanent_error(tr),
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
            **measure(tr, task, run.setup, cache, workspace.changes(wt)),
        }
    finally:
        changed, moved = workspace.reset(wt, task.commit)
    record["worktree_changes"], record["head_moved"] = changed, moved
    return record


class Batch:
    """Runs a schedule with `parallel` workers. Runs that share a resource never overlap: a worktree, or the cargo
    target directory that every Serena run on one Rust repo builds in. The spending cap covers what earlier
    invocations of the same batch spent, so resuming never renews it. No run starts while the cache's volume is
    unreadable, and none that overlapped such an outage is recorded."""

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
        volume_probe: Callable[[], object] | None = None,
    ):
        self.results = out_dir / "results.jsonl"
        self.unrecorded = out_dir / "unrecorded.jsonl"  # spend of runs never recorded: interrupted, failed
        out_dir.mkdir(parents=True, exist_ok=True)
        self.lock = _hold(out_dir / ".lock")  # until run() ends: two batches on one directory pay twice
        try:
            self._cut_partial_lines(log)
            earlier = recorded(self.results)
            unrecorded = recorded(self.unrecorded)
        except BaseException:
            self.lock.close()
            raise
        done = {(r["task"], r["setup"], r["rep"]) for r in earlier}
        self.pending = [r for r in runs if r.key not in done]
        self.cache, self.out_dir, self.parallel = cache, out_dir, parallel
        self.max_total_usd, self.execute, self.claude, self.log = max_total_usd, execute_fn, claude, log
        self.volume_probe = volume_probe  # None: list the cache (workspace.watch)
        self.volume: workspace.Watch | None = None  # while run() runs
        self.unmeasurable_since: float | None = None  # when free_gb began to fail, while it fails
        self.busy: set[Path] = set()  # the resources of the runs in flight
        self.analyzers = 0  # runs in flight that start a rust-analyzer: Serena on a Rust task
        self.cond = threading.Condition()
        self.spent = sum(charged(r) for r in earlier) + sum(u["cost_usd"] for u in unrecorded)
        # the Claude Code version the batch began on, from its earliest recorded run (None until one has a version)
        self.cli_version = next((r["cli_version"] for r in earlier if r.get("cli_version")), None)
        # recorded runs in a row, up to the latest, that ended on an API error; a resume continues the streak
        self.api_errors = next((i for i, r in enumerate(reversed(earlier)) if not r.get("api_error")), len(earlier))
        self.stopped: str | None = None
        self.interrupted = False
        self.completed = 0
        self.working = 0  # workers still running

    def _cut_partial_lines(self, log: Callable[[str], None]) -> None:
        """A line cut off mid-write is dropped; its run's cost is unknown, so it is charged at the per-run cap, and
        a record's run is redone."""
        for path in (self.unrecorded, self.results):
            cut = cut_partial_line(path)
            if cut:
                row = dict.fromkeys(("task", "setup", "rep")) | {"why": "partial record", "file": path.name}
                with open(self.unrecorded, "a") as f:
                    f.write(json.dumps({**row, "cost_usd": config.MAX_BUDGET_USD}) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
                log(f"cut {cut} bytes of a partly written last line from {path}; charged ${config.MAX_BUDGET_USD:.2f}")

    @staticmethod
    def _starts_rust_analyzer(r: Run) -> bool:
        return setups.SETUPS[r.setup].base == "serena" and r.task.lang == "rust"

    def _resources(self, r: Run) -> set[Path]:
        held = {workspace.worktree_path(self.cache, r.setup, r.task.repo, r.task.commit)}
        if setups.SETUPS[r.setup].base == "serena" and r.task.lang == "rust":
            held.add(setups.cargo_target(self.cache, r.task.repo))
        return held

    def _limit(self, uncharged: float = 0.0) -> str | None:
        """Why no attempt may start now: the spending cap, counting `uncharged` spend on top of what is charged, or
        the disk. Called holding `cond`."""
        spent = self.spent + uncharged
        if spent >= self.max_total_usd:
            return f"spent ${spent:.2f}, the batch cap is ${self.max_total_usd:.2f}"
        if workspace.free_gb(self.cache) < config.MIN_FREE_GB:
            return f"less than {config.MIN_FREE_GB} GB free under {self.cache}"
        return None

    def _may_retry(self, uncharged: float = 0.0) -> bool:
        """Whether a retry may start: a retry spends like a new run, so it stops at the same limits. `uncharged`
        is spend not charged yet: the discarded attempt a record will carry."""
        with self.cond:
            try:
                self.stopped = self.stopped or self._limit(uncharged)
            except OSError:  # the disk cannot be measured now: the next _take measures it, or waits
                pass
            return not self.stopped

    def _volume_back(self) -> bool:
        """Wait, holding `cond`, while the cache's volume is unreadable; False if the batch stopped meanwhile. An
        outage of VOLUME_OUTAGE_LIMIT_S stops it."""
        while not self.stopped and self.volume is not None and self.volume.down():
            lasted = self.volume.downtime()
            if lasted >= config.VOLUME_OUTAGE_LIMIT_S:
                self.stopped = f"the cache's volume has been unreadable for {lasted:.0f} s"
                self.cond.notify_all()
                break
            self.cond.wait(config.VOLUME_PROBE_S)
        return not self.stopped

    def _hit_outage(self, began: float) -> bool:
        return self.volume is not None and self.volume.overlaps(began, time.monotonic())

    def _spent_by(self, r: Run) -> float:
        """What an attempt that raised may have spent: what its transcript streamed, nothing if it left none."""
        for name, opener in ((f"{r.setup}-{r.rep}.jsonl.gz", gzip.open), (f"{r.setup}-{r.rep}.jsonl", open)):
            path = self.out_dir / r.task.id / name
            if path.exists():
                try:
                    with opener(path, "rt") as f:
                        tr = stream.read(f)
                except (OSError, EOFError):
                    return config.MAX_BUDGET_USD
                spent = tr.result.get("total_cost_usd")
                return stream.cost(stream.tokens(tr.result, tr.usage_by_message)) if spent is None else spent
        return 0.0

    def _take(self) -> Run | None:
        with self.cond:
            while True:
                if self.stopped or not self.pending:
                    return None  # nothing pending is a finished batch, whatever it spent
                if not self._volume_back():
                    return None
                try:
                    self.stopped = self._limit()
                    self.unmeasurable_since = None
                except OSError as e:  # the volume, again: wait as for an outage
                    now = time.monotonic()
                    self.unmeasurable_since = self.unmeasurable_since or now
                    if now - self.unmeasurable_since < config.VOLUME_OUTAGE_LIMIT_S:
                        self.cond.wait(config.VOLUME_PROBE_S)
                        continue
                    self.stopped = f"the cache has been unreadable for {now - self.unmeasurable_since:.0f} s: {e!r}"
                if self.stopped:
                    return None
                for i, r in enumerate(self.pending):
                    lsp = self._starts_rust_analyzer(r)
                    if lsp and self.analyzers >= config.RUST_LSP_LIMIT:
                        continue  # each rust-analyzer can take several GB
                    held = self._resources(r)
                    if not held & self.busy:
                        self.busy |= held
                        self.analyzers += lsp
                        return self.pending.pop(i)
                self.cond.wait()

    def _release(self, r: Run) -> None:
        with self.cond:
            self.busy -= self._resources(r)
            self.analyzers -= self._starts_rust_analyzer(r)
            self.cond.notify_all()

    def _record(self, rec: dict) -> None:
        with self.cond:
            key = (rec["task"], rec["setup"], rec["rep"])
            if rec.get("api_error") and self.api_errors >= config.API_ERROR_STREAK:
                # the streak below stopped the batch; a run of it that was still in flight isn't kept either
                self._charge_unrecorded(key, charged(rec), "API-error streak", self._set_aside(key))
                return
            with open(self.results, "a") as f:
                f.write(json.dumps(rec, sort_keys=True) + "\n")
                f.flush()
                os.fsync(f.fileno())
            self.spent += charged(rec)
            self.completed += 1
            version = rec.get("cli_version")  # a run cut off before its init event has none
            if version and self.cli_version is None:
                self.cli_version = version
            elif version and version != self.cli_version:  # kept, but the batch no longer measures one version
                self.stopped = self.stopped or (
                    f"Claude Code changed mid-batch: {rec['task']} {rec['setup']}-{rec['rep']} ran {version}, "
                    f"the batch began on {self.cli_version}"
                )
                self.cond.notify_all()
            self.api_errors = self.api_errors + 1 if rec.get("api_error") else 0
            if self.api_errors >= config.API_ERROR_STREAK:
                # one such run is its own outcome (a request too long); a streak is something else failing, a CLI
                # or API change, so none of it is kept: a resume redoes those runs
                for dropped in self._unwrite(self.api_errors):
                    self.spent -= charged(dropped)
                    self.completed = max(0, self.completed - 1)
                    k = (dropped["task"], dropped["setup"], dropped["rep"])
                    self._charge_unrecorded(k, charged(dropped), "API-error streak", self._set_aside(k))
                self.stopped = self.stopped or (
                    f"{self.api_errors} runs in a row ended on an API error, the last {rec['api_error']}; none of "
                    "them is kept, so a resume redoes them"
                )
                self.cond.notify_all()

    def _unwrite(self, n: int) -> list[dict]:
        """Take the last `n` records back out of the results file, and return them. Called holding `cond`."""
        lines = self.results.read_text().splitlines(keepends=True)
        tmp = self.results.with_suffix(".tmp")
        with open(tmp, "w") as f:
            f.writelines(lines[:-n])
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.results)
        return [json.loads(line) for line in lines[-n:]]

    def _set_aside(self, key: tuple[str, str, int]) -> str | None:
        """Keep an attempt that won't be recorded out of the next one's way: its transcript (compressed, or raw when
        it failed first) and stderr become <setup>-<rep>.unrecorded-<n>.*, so the run's own name only ever holds
        the recorded attempt. `key` is the run's (task, setup, rep). Returns the transcript's new name, None if the
        attempt left none."""
        task_id, setup, rep = key
        run_dir, stem = self.out_dir / task_id, f"{setup}-{rep}"
        kinds = (".jsonl.gz", ".jsonl", ".stderr")
        transcripts = [ext for ext in kinds[:2] if (run_dir / f"{stem}{ext}").exists()]
        if not transcripts:
            return None
        n = 1
        while any((run_dir / f"{stem}.unrecorded-{n}{ext}").exists() for ext in kinds):
            n += 1
        for ext in kinds:
            if (run_dir / f"{stem}{ext}").exists():
                (run_dir / f"{stem}{ext}").rename(run_dir / f"{stem}.unrecorded-{n}{ext}")
        return f"{stem}.unrecorded-{n}{transcripts[0]}"

    def _charge_unrecorded(
        self, key: tuple[str, str, int], cost: float, why: str, transcript: str | None = None
    ) -> None:
        """Count what a run (its (task, setup, rep)) spent that no results line will show, so a resume counts it."""
        row = {"task": key[0], "setup": key[1], "rep": key[2], "why": why, "cost_usd": cost}
        if transcript:
            row["transcript"] = transcript
        with self.cond:
            with open(self.unrecorded, "a") as f:
                f.write(json.dumps(row) + "\n")
                f.flush()
                os.fsync(f.fileno())
            self.spent += cost

    def _pause(self, seconds: float) -> bool:
        """Wait on the batch's condition, so an interrupt (or any stop) ends the wait; False if it was cut short."""
        deadline = time.monotonic() + seconds
        with self.cond:
            while not self.stopped:
                left = deadline - time.monotonic()
                if left <= 0:
                    return True
                self.cond.wait(left)
        return False

    def _attempt(self, r: Run, attempt: int) -> dict:
        """One execution, redone after each of RETRY_WAITS_S while it fails with a transient API error, or while
        it overlaps an outage of the cache's volume, whatever it returned or raised then: such a run's tools and
        git calls failed for reasons outside it. Every failed try is charged as unrecorded spend; the last failure,
        a permanent one, one after the batch stopped or reached a limit, or a harness fault is raised."""
        for wait in (*config.RETRY_WAITS_S, None):
            began = time.monotonic()
            rec, failure = None, None
            try:
                rec = self.execute(r, self.cache, self.out_dir, attempt, self.claude)
            except Interrupted:
                raise
            except Exception as e:
                failure = e
            outage = self._hit_outage(began)
            if not outage and failure is None:
                return rec
            if not outage and not isinstance(failure, InfrastructureError):
                raise failure  # a harness fault
            if rec is not None:
                cost = charged(rec)
            else:
                cost = failure.cost if isinstance(failure, InfrastructureError) else self._spent_by(r)
            self._charge_unrecorded(
                r.key, cost, "cache unreadable" if outage else "infrastructure error", self._set_aside(r.key)
            )
            if outage:
                error = InfrastructureError(
                    f"{r.task.id}/{r.setup}-{r.rep} ran while the cache's volume was unreadable"
                    + (f": {failure}" if failure else "")
                )
            else:
                error = failure
            if (not outage and error.permanent) or wait is None:
                raise error
            self.log(
                f"{'cache unreadable' if outage else 'transient API error'}, retrying {r.key} in {wait:g} s: {error}"
            )
            with self.cond:
                back = self._volume_back()
            if not back or not self._pause(wait) or not self._may_retry():
                raise error
        raise AssertionError("unreachable")

    def _worker(self) -> None:
        while True:
            try:
                r = self._take()
            except Exception as e:  # the cache's volume unreadable, say: never let the batch look finished
                with self.cond:
                    self.stopped = self.stopped or f"could not start another run: {e!r}"
                    self.cond.notify_all()
                return
            if r is None:
                return
            try:
                rec = self._attempt(r, 1)
                if not rec["config_ok"] and not self.interrupted:  # discard, and retry once
                    self.log(f"config check failed, retrying: {r.key} {rec['config_problems']}")
                    first, kept = rec, self._set_aside(r.key)
                    if not self._may_retry(charged(first)):
                        self._charge_unrecorded(r.key, charged(first), "discarded attempt", kept)
                        continue
                    try:
                        rec = self._attempt(r, 2)
                    except BaseException:
                        self._charge_unrecorded(r.key, charged(first), "discarded attempt", kept)
                        raise
                    rec["discarded_cost_usd"] = charged(first)
                if self.interrupted:  # a run the interrupt cut short is redone on resume, never scored
                    self._charge_unrecorded(r.key, charged(rec), "interrupted", self._set_aside(r.key))
                    continue
                self._record(rec)
                self.log(
                    f"[{self.completed}] {r.task.id} {r.setup}-{r.rep}: {rec['tool_calls']} calls, "
                    f"${rec['cost_usd']:.3f}, success={rec['score']['success']}"
                )
            except Interrupted:  # nothing started; the batch has stopped already
                pass
            except InfrastructureError as e:  # already charged; retries are spent
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
        """Stop now: start nothing more and kill the runs in flight, and any run about to start. They reset their
        worktrees as they unwind and are not recorded, so a resume redoes them."""
        with self.cond:
            self.interrupted = True
            self.stopped = INTERRUPTED
            self.cond.notify_all()
        with _live_lock:
            _stopping.set()
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
        _stopping.clear()  # an earlier batch's interrupt
        main = threading.current_thread() is threading.main_thread()
        previous = signal.signal(signal.SIGTERM, _raise_interrupt) if main else None
        try:
            with workspace.watch(self.cache, probe=self.volume_probe) as self.volume:
                self.working = self.parallel
                for _ in range(self.parallel):
                    threading.Thread(target=self._work, daemon=True).start()
                try:
                    self._wait()
                except KeyboardInterrupt:
                    self.interrupt()
                    self._wait()
        finally:
            if main:
                signal.signal(signal.SIGTERM, previous if previous is not None else signal.SIG_DFL)
            if not self.working:
                _stopping.clear()  # the interrupt is over; runs executed after this batch must start
                self.lock.close()  # and another invocation may take the directory
        return self.stopped


def _hold(path: Path):
    """An exclusive lock on `path`, held while the returned file stays open. Busy if another process holds it."""
    f = open(path, "a+")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.seek(0)
        holder = f.read().strip()
        f.close()
        raise Busy(
            f"another run is using {path.parent}" + (f" (pid {holder})" if holder else "") + "; wait for it or stop it"
        ) from None
    f.truncate(0)
    f.write(str(os.getpid()))
    f.flush()
    return f


def _raise_interrupt(signum, frame) -> None:
    raise KeyboardInterrupt
