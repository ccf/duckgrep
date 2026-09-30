"""The A/B evaluation of duckgrep against plain Claude Code and Serena.

    uv run python -m bench.eval [--suite pilot] build | prepare | check | run | rescore | report

`build` needs the network; `check` is free; `run` spends money (see --max-total-usd).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import config, report, runner, suite, workspace
from .setups import SETUPS

REPO = Path(__file__).resolve().parents[2]


def setup_list(value: str) -> list[str]:
    names = [s for s in value.split(",") if s]
    unknown = sorted(set(names) - set(SETUPS))
    if unknown or not names:
        raise argparse.ArgumentTypeError(f"setups must be among {sorted(SETUPS)}, got {value!r}")
    return names


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m bench.eval", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--suite", default="pilot", help="suite name: bench/eval/suites/<suite>-*.jsonl")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="draw the suite's tasks and derive their answer keys (network)")
    b.add_argument("--kind", choices=["localization", "structural", "all"], default="all")
    b.add_argument("--seed", type=int, default=config.SEED)
    p = sub.add_parser("prepare", help="clone and check out every task's repo; build indexes and warm Serena up")
    p.add_argument("--setups", type=setup_list, default=list(SETUPS))
    p.add_argument("--parallel", type=int, default=config.PREPARE_PARALLEL, help="repo/commit pairs built at once")
    c = sub.add_parser("check", help="verify each setup's configuration, free")
    c.add_argument("--setups", type=setup_list, default=list(SETUPS))
    r = sub.add_parser("run", help="run the suite; this costs money")
    r.add_argument("--setups", type=setup_list, default=list(SETUPS))
    r.add_argument("--reps", type=int, default=config.REPETITIONS)
    r.add_argument("--parallel", type=int, default=config.PARALLEL)
    r.add_argument("--tasks", help="comma-separated task ids (default: every task)")
    r.add_argument("--name", help="results directory under bench/eval/runs (default: the suite name)")
    r.add_argument("--max-total-usd", type=float, default=200.0, help="stop starting runs past this spend")
    rs = sub.add_parser("rescore", help="recompute each run's measurements from its transcript, free")
    rs.add_argument("--name", help="results directory (default: the suite name)")
    rp = sub.add_parser("report", help="write the report")
    rp.add_argument("--name", help="results directory (default: the suite name)")
    rp.add_argument("--write", action="store_true", help="also put it into bench/RESULTS.md")
    return ap


def build(a) -> int:
    """Draw the suite's tasks by its profile (an unknown name draws like the pilot), minus those its curation
    file, <suite>-curation.jsonl, drops: one {"id", "reason"} per line."""
    cache = config.cache_dir()
    profile = config.PROFILES.get(a.suite, config.PROFILES["pilot"])
    curation = config.SUITES_DIR / f"{a.suite}-curation.jsonl"
    dropped = {r["id"]: r["reason"] for r in report.load(curation)}
    from .tasks import localization, structural

    for kind, builder in (("localization", localization), ("structural", structural)):
        if a.kind not in (kind, "all"):
            continue
        tasks = builder.build(seed=a.seed, profile=profile, cache=cache)
        kept = [t for t in tasks if t.id not in dropped]
        suite.save(config.SUITES_DIR / f"{a.suite}-{kind}.jsonl", kept)
        print(
            f"{len(kept)} {kind} tasks"
            + (f" ({len(tasks) - len(kept)} dropped by curation)" if len(kept) < len(tasks) else "")
        )
    return 0


def check(tasks: list, setup_names: list[str], cache, claude: str) -> bool:
    """Probe each setup on one task per language (free); print a line per probe; True if all are clean."""
    ok = True
    for lang in sorted({t.lang for t in tasks}):
        sample = next(t for t in tasks if t.lang == lang)
        for setup in setup_names:
            problems = runner.probe(sample, setup, cache, claude)
            ok = ok and not problems
            print(f"{setup:13} {lang:7} {'ok' if not problems else '; '.join(problems)}")
    return ok


def harness(root: Path = REPO) -> dict:
    """The commit of this repo that measures the runs, and whether the working tree differs from it (a new,
    uncommitted module counts)."""
    commit = workspace.git("rev-parse", "HEAD", cwd=root).strip()
    dirty = bool(workspace.git("status", "--porcelain", cwd=root).strip())
    return {"commit": commit, "dirty": dirty}


def claude_path() -> str:
    found = shutil.which("claude")
    if not found:
        sys.exit("claude is not on PATH")
    return found


def main(argv: list[str] | None = None) -> int:
    a = parser().parse_args(argv)
    if a.cmd == "build":
        return build(a)
    cache = config.cache_dir()
    if a.cmd == "report":
        name = a.name or a.suite
        records = report.load(config.RUNS_DIR / name / "results.jsonl")
        if not records:
            print(f"no results in {config.RUNS_DIR / name}")
            return 1
        text = report.build(records, report.load(config.RUNS_DIR / a.suite / "prepare.jsonl"), name)
        (config.RUNS_DIR / name / "report.md").write_text(text)
        if a.write:
            report.write_section(config.RESULTS_MD, name, text)
        print(text)
        return 0
    tasks = suite.load_suite(a.suite, config.SUITES_DIR)
    if a.cmd == "rescore":
        out = config.RUNS_DIR / (a.name or a.suite)
        if not (out / "results.jsonl").exists():
            print(f"no results in {out}")
            return 1
        changed = runner.rescore(out, tasks, cache)
        missing = changed.pop("missing", 0)
        print(f"rescored {out / 'results.jsonl'} (the first version is results.orig.jsonl); records changed by field:")
        for field, n in sorted(changed.items()):
            print(f"  {field}: {n}")
        if missing:
            print(f"{missing} records have no transcript or task and were kept as recorded")
        return 0
    if a.cmd == "prepare":
        out = config.RUNS_DIR / a.suite
        out.mkdir(parents=True, exist_ok=True)
        lock = threading.Lock()  # prepare calls save from several threads
        with open(out / "prepare.jsonl", "a") as f:

            def save(row: dict) -> None:  # as each is measured: preparing takes an hour, and a later step can fail
                with lock:
                    f.write(json.dumps(row) + "\n")
                    f.flush()

            workspace.prepare(tasks, a.setups, cache, save=save, workers=a.parallel)
        return 0
    wanted = a.tasks.split(",") if getattr(a, "tasks", None) else None
    chosen = [t for t in tasks if wanted is None or t.id in wanted]
    if wanted and len(chosen) != len(set(wanted)):
        print(f"unknown task ids: {sorted(set(wanted) - {t.id for t in chosen})}", file=sys.stderr)
        return 2
    claude = claude_path()
    version = subprocess.run([claude, "--version"], capture_output=True, text=True).stdout.strip()
    print(f"claude {version}")
    if not check(chosen, a.setups, cache, claude):  # before every batch too: a misconfigured setup costs nothing yet
        return 1
    if a.cmd == "check":
        return 0
    workspace.require_space(cache)
    out = config.RUNS_DIR / (a.name or a.suite)
    out.mkdir(parents=True, exist_ok=True)
    meta = {
        "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "harness": harness(),
        "suite": a.suite,
        "tasks": len(chosen),
        "setups": a.setups,
        "reps": a.reps,
        "seed": config.SEED,
        "claude": version,
        "model": config.MODEL,
        "effort": config.EFFORT,
        "max_turns": config.MAX_TURNS,
        "max_budget_usd": config.MAX_BUDGET_USD,
    }
    path = out / "meta.json"  # one entry per invocation: a resumed batch keeps what started it
    earlier = json.loads(path.read_text()) if path.exists() else []
    earlier = [earlier] if isinstance(earlier, dict) else earlier
    path.write_text(json.dumps(earlier + [meta], indent=2) + "\n")
    runs = runner.schedule(chosen, a.setups, a.reps, config.SEED)
    print(f"{len(runs)} runs, results in {out}")
    stopped = runner.Batch(runs, cache, out, a.parallel, a.max_total_usd, claude=claude).run()
    if stopped:
        print(f"stopped early: {stopped}; rerun the same command to resume")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
