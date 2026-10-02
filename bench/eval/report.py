"""Paired comparisons of each setup against the baseline, task by task, as a markdown report.

The unit is the task: a setup's repetitions of a task are averaged first. Counts and token-like metrics are
compared on a log scale (the effect is a ratio of geometric means); success, F1 and turns-to-locate as
differences. Intervals are 95% bootstrap intervals over tasks; p-values are Wilcoxon signed-rank tests,
Holm-corrected across every comparison in the report.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy import stats

from . import config

BOOTSTRAP = 10_000
CONTRAST_ORDER = ("duckgrep", "duckgrep-hint", "serena", "serena-hint")  # the rest follow, sorted
TABLES = (
    ("localization", "python", "Python localization"),
    ("localization", "rust", "Rust localization"),
    ("structural", "python", "Python structural questions"),
    ("structural", "rust", "Rust structural questions"),
)


@dataclass(frozen=True)
class Metric:
    name: str
    get: Callable[[dict], float | None]
    scale: str  # log, log1p (ratios of geometric means) or diff
    lower_is_better: bool
    unit: str = "n"  # n, tokens, usd, share (a proportion) or score


METRICS = (
    Metric("tool calls", lambda r: r["tool_calls"], "log1p", True),
    Metric("round trips", lambda r: r["rounds"], "log1p", True),
    Metric("tokens", lambda r: r["tokens_total"], "log", True, "tokens"),
    Metric("cost ($)", lambda r: r["cost_usd"], "log", True, "usd"),
    # a run that never saw a key location counts as its rounds + 1, rather than dropping the task (which would
    # compare a setup that often fails to locate only on its easy tasks); "located" reports how often that was
    Metric(
        "turns to locate",
        lambda r: r["rounds"] + 1 if r["turns_to_locate"] is None else r["turns_to_locate"],
        "diff",
        True,
    ),
    Metric("located", lambda r: float(r["turns_to_locate"] is not None), "diff", False, "share"),
    Metric("success", lambda r: float(r["score"]["success"]), "diff", False, "share"),
    Metric("F1", lambda r: r["score"]["f1"], "diff", False, "score"),
)


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def contrasts(records: list[dict]) -> list[str]:
    """Every setup but the baseline, which each is compared with."""
    names = {r["setup"] for r in records} - {"baseline"}
    return [s for s in CONTRAST_ORDER if s in names] + sorted(names - set(CONTRAST_ORDER))


def run_values(records: list[dict], metric: Metric) -> dict[tuple[str, str], dict[int, float]]:
    """Each usable run's value, by (task, setup) and repetition. A run without a value is left out: on a log
    scale that is a run that never reached the model (zero tokens), which would otherwise pull its task's mean
    toward zero."""
    out: dict[tuple[str, str], dict[int, float]] = defaultdict(dict)
    for r in records:
        value = metric.get(r) if r["config_ok"] else None
        if value is not None and not (metric.scale == "log" and value <= 0):
            out[(r["task"], r["setup"])][r["rep"]] = float(value)
    return out


def task_means(records: list[dict], metric: Metric) -> dict[tuple[str, str], float]:
    """Mean over repetitions per (task, setup)."""
    return {key: float(np.mean(list(reps.values()))) for key, reps in run_values(records, metric).items() if reps}


@dataclass
class Comparison:
    table: str
    setup: str
    metric: Metric
    n: int
    base: float  # the baseline's typical value (geometric mean on log scales)
    other: float
    effect: float  # ratio on log scales, difference otherwise
    low: float
    high: float
    p: float
    win: float  # share of tasks where the setup did better; ties count half
    p_holm: float = float("nan")


def _forward(scale: str, v: np.ndarray) -> np.ndarray:
    return np.log(v) if scale == "log" else np.log1p(v) if scale == "log1p" else v


def _typical(scale: str, v: np.ndarray) -> float:
    if scale == "log":
        return float(np.exp(np.log(v).mean()))
    if scale == "log1p":
        return float(np.expm1(np.log1p(v).mean()))
    return float(v.mean())


def compare(table: str, setup: str, metric: Metric, pairs: list[tuple[float, float]], seed: int) -> Comparison:
    """`pairs` are (setup, baseline) task means."""
    x = np.array([a for a, _ in pairs], dtype=float)
    y = np.array([b for _, b in pairs], dtype=float)
    d = _forward(metric.scale, x) - _forward(metric.scale, y)
    rng = np.random.default_rng(seed)
    boots = d[rng.integers(0, len(d), size=(BOOTSTRAP, len(d)))].mean(axis=1)
    lo, hi = np.percentile(boots, [2.5, 97.5])
    mean = float(d.mean())
    if metric.scale == "diff":
        effect, low, high = mean, float(lo), float(hi)
    else:
        effect, low, high = math.exp(mean), math.exp(lo), math.exp(hi)
    p = 1.0 if not np.any(d != 0) else float(stats.wilcoxon(d).pvalue)
    better = (x < y) if metric.lower_is_better else (x > y)
    win = float((better.sum() + 0.5 * (x == y).sum()) / len(d))
    return Comparison(
        table, setup, metric, len(d), _typical(metric.scale, y), _typical(metric.scale, x), effect, low, high, p, win
    )


def holm(ps: list[float]) -> list[float]:
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    out = [1.0] * len(ps)
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(ps) - rank) * ps[i]))
        out[i] = running
    return out


def comparisons(records: list[dict], seed: int = config.SEED) -> list[Comparison]:
    found = []
    setups = contrasts(records)
    for kind, lang, title in TABLES + (("localization", "rust-live", "Rust localization, post-cutoff issues"),):
        if lang == "rust-live":
            rows = [r for r in records if r["kind"] == kind and r["lang"] == "rust" and r["stratum"] == "live"]
        else:
            rows = [r for r in records if r["kind"] == kind and r["lang"] == lang]
        for metric in METRICS:
            values = run_values(rows, metric)
            for setup in setups:
                pairs = []
                for t, s in sorted(values):
                    if s != "baseline" or (t, setup) not in values:
                        continue
                    base, other = values[(t, "baseline")], values[(t, setup)]
                    both = sorted(set(base) & set(other))  # a batch that stopped early may lack some repetitions
                    if both:
                        pairs.append(
                            (float(np.mean([other[r] for r in both])), float(np.mean([base[r] for r in both])))
                        )
                if len(pairs) >= 2:
                    found.append(compare(title, setup, metric, pairs, seed))
    for c, p in zip(found, holm([c.p for c in found]), strict=True):
        c.p_holm = p
    return found


def _num(v: float, unit: str) -> str:
    return {
        "tokens": f"{v:,.0f}",
        "usd": f"{v:.4f}",
        "share": f"{v:.0%}",
        "score": f"{v:.2f}",
    }.get(unit, f"{v:,.1f}")


def _effect(c: Comparison) -> str:
    if c.metric.scale != "diff":
        return f"×{c.effect:.2f} [{c.low:.2f}, {c.high:.2f}]"
    if c.metric.unit == "share":
        return f"{100 * c.effect:+.0f} pp [{100 * c.low:+.0f}, {100 * c.high:+.0f}]"
    return f"{c.effect:+.2f} [{c.low:+.2f}, {c.high:+.2f}]"


def metric_table(found: list[Comparison], title: str) -> list[str]:
    rows = [c for c in found if c.table == title]
    if not rows:
        return []
    out = [f"### {title}", ""]
    out.append("| metric | tasks | baseline | setup | vs baseline [95% CI] | p | p (Holm) | win rate |")
    out.append("|---|---:|---:|---|---|---:|---:|---:|")
    for c in rows:
        unit = c.metric.unit
        out.append(
            f"| {c.metric.name} | {c.n} | {_num(c.base, unit)} | {c.setup} {_num(c.other, unit)} | {_effect(c)} | "
            f"{c.p:.3f} | {c.p_holm:.3f} | {c.win:.0%} |"
        )
    return out + [""]


def reading(found: list[Comparison]) -> list[str]:
    """How to read the tables, and whether any comparison could pass the correction at this many tasks."""
    text = (
        "How to read the tables: each row pairs a setup with the baseline task by task, a task's repetitions "
        "averaged first. Tokens and cost are compared as ratios of geometric means; tool calls and round trips as "
        "ratios on log(1 + n), that is of 1 + n; turns to locate, located, success and F1 as differences, shares "
        "in percentage points. Intervals are 95% bootstrap intervals over tasks. p is a Wilcoxon signed-rank test "
        f"and p (Holm) corrects it across all {len(found)} comparisons in the report."
    )
    if found:
        most = max(c.n for c in found)
        floor = 2 / 2**most  # the smallest two-sided p the test can give with `most` tasks
        if floor * len(found) >= 0.05:
            text += (
                f" With at most {most} tasks per comparison the smallest possible p is {floor:.4f}, so no "
                "comparison can reach p < 0.05 after correction: read the intervals and win rates."
            )
    return [text, ""]


def adoption_table(records: list[dict]) -> list[str]:
    out = [
        "### Adoption",
        "",
        "| setup | kind | runs | used its tool | its share of calls | calls (used / not) |",
        "|---|---|---:|---:|---:|---|",
    ]
    for setup in contrasts(records):
        for kind in ("localization", "structural"):
            rows = [r for r in records if r["setup"] == setup and r["kind"] == kind and r["config_ok"]]
            if not rows:
                continue
            used = [r for r in rows if r["adopted"]]
            not_used = [r for r in rows if not r["adopted"]]

            def avg(rs):
                return f"{np.mean([r['tool_calls'] for r in rs]):.1f}" if rs else "–"

            out.append(
                f"| {setup} | {kind} | {len(rows)} | {len(used) / len(rows):.0%} | "
                f"{np.mean([r['mcp_share'] for r in rows]):.0%} | {avg(used)} / {avg(not_used)} |"
            )
    return out + [""]


def repo_table(records: list[dict]) -> list[str]:
    out = [
        "### By repo",
        "",
        "| kind | repo | setup | runs | tool calls | cost ($) | success |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for r in records:
        if r["config_ok"]:
            groups[(r["kind"], r["repo"].lower(), r["setup"])].append(r)
    for (kind, _, setup), rs in sorted(groups.items()):
        repo = Counter(r["repo"] for r in rs).most_common(1)[0][0]
        out.append(
            f"| {kind} | {repo} | {setup} | {len(rs)} | {np.mean([r['tool_calls'] for r in rs]):.1f} | "
            f"{np.mean([r['cost_usd'] for r in rs]):.3f} | {np.mean([r['score']['success'] for r in rs]):.0%} |"
        )
    return out + [""]


def variance_table(records: list[dict]) -> list[str]:
    """Run-to-run versus task-to-task spread, which sets the repetitions and task counts of the full run."""
    out = [
        "### Variance",
        "",
        "| setup | metric | within-task SD | between-task SD | within share of variance |",
        "|---|---|---:|---:|---:|",
    ]
    for setup in ["baseline"] + contrasts(records):
        for label, get in (
            ("log tokens", lambda r: math.log(max(r["tokens_total"], 1))),
            ("log(1 + tool calls)", lambda r: math.log1p(r["tool_calls"])),
        ):
            by_task: dict[str, list[float]] = defaultdict(list)
            for r in records:
                if r["setup"] == setup and r["config_ok"]:
                    by_task[r["task"]].append(get(r))
            reps = [v for v in by_task.values() if len(v) > 1]
            if len(reps) < 2:
                continue
            within = float(np.mean([np.var(v, ddof=1) for v in reps]))
            between = float(np.var([np.mean(v) for v in reps], ddof=1))
            share = within / (within + between) if within + between else 0.0
            out.append(f"| {setup} | {label} | {math.sqrt(within):.2f} | {math.sqrt(between):.2f} | {share:.0%} |")
    return out + [""]


def setup_costs(prepared: list[dict]) -> list[str]:
    if not prepared:
        return []
    out = [
        "### Setup costs (not included above)",
        "",
        "| setup | worktrees | total seconds | total MB |",
        "|---|---:|---:|---:|",
    ]
    for setup in ("duckgrep", "serena"):
        rows = [p for p in prepared if p["setup"] == setup]
        if rows:
            secs = sum(p.get("index_seconds", 0) + p.get("serena_seconds", 0) for p in rows)
            sizes = [p.get("index_mb", p.get("serena_mb")) for p in rows]
            mb = "–" if None in sizes else f"{sum(sizes):,.0f}"  # not recorded (before serena_mb existed)
            out.append(f"| {setup} | {len(rows)} | {secs:,.0f} | {mb} |")
    return out + [""]


def _changed(path: str) -> str:
    path = path[3:] if len(path) > 3 and path[2] == " " else path  # the porcelain status
    return "Python bytecode" if "__pycache__/" in path or path.endswith(".pyc") else path


def summary(records: list[dict], unrecorded: list[dict] = ()) -> list[str]:
    n = len(records)
    happened = [
        (sum(not r["config_ok"] for r in records), "failed the configuration check twice (excluded)"),
        (
            sum(r["killed"] for r in records),
            "hit the wall-clock limit (their tokens are summed from their API calls, output as a lower bound)",
        ),
        (
            sum(r["config_ok"] and not r["tokens_total"] for r in records),
            "never reached the model (left out of token and cost means)",
        ),
        (sum(r["is_error"] for r in records), "ended in an error (turn or budget cap)"),
    ]
    said = [f"{k} {what}" for k, what in happened if k]
    text = f"{n} runs: " + ", ".join(said) + "." if said else f"{n} runs, all completed normally."
    dirty = [r for r in records if r["worktree_changes"]]
    if dirty:
        files = Counter(f for r in dirty for f in {_changed(p) for p in r["worktree_changes"]})
        listed = ", ".join(f"{f} ({k} run{'s' if k > 1 else ''})" for f, k in files.most_common(8))
        text += f" {len(dirty)} left files in their worktree, all restored after the run: {listed}."
    cost = sum(r["cost_usd"] for r in records)
    cli = sum(r.get("cli_cost_usd") or 0 for r in records)
    versions = sorted({str(r["cli_version"]) for r in records})
    models = sorted({str(r["model"]) for r in records})
    text += (
        f" Cost ${cost:,.2f} at list rates (Claude Code billed ${cli:,.2f}). Claude Code {', '.join(versions)}; "
        f"model {', '.join(models)}."
    )
    if unrecorded:  # interrupted, failed, retried or taken-back attempts: charged, never scored
        extra = sum(u["cost_usd"] for u in unrecorded)
        text += (
            f" {len(unrecorded)} {'attempt that was' if len(unrecorded) == 1 else 'attempts that were'} not recorded"
            f" spent ${extra:,.2f} more, ${cli + extra:,.2f} in all."
        )
    return [text, ""]


def build(
    records: list[dict], prepared: list[dict], name: str, seed: int = config.SEED, unrecorded: list[dict] = ()
) -> str:
    found = comparisons(records, seed)
    lines = [f"## A/B evaluation: {name}", ""] + summary(records, unrecorded) + reading(found)
    for title in [t for *_, t in TABLES] + ["Rust localization, post-cutoff issues"]:
        lines += metric_table(found, title)
    lines += adoption_table(records) + repo_table(records) + variance_table(records) + setup_costs(prepared)
    return "\n".join(lines).rstrip() + "\n"


def write_section(path: Path, name: str, text: str) -> None:
    """Put `text` between this report's markers in RESULTS.md, replacing an earlier version."""
    start, end = f"<!-- eval:{name} -->", f"<!-- /eval:{name} -->"
    block = f"{start}\n{text}{end}\n"
    current = path.read_text() if path.exists() else ""
    if start in current and end in current:
        before, rest = current.split(start, 1)
        after = rest.split(end, 1)[1].lstrip("\n")
        path.write_text(before + block + after)
    else:
        path.write_text(current.rstrip("\n") + "\n\n" + block if current else block)
