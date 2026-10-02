"""Render the landing page's race terminals from saved A/B transcripts.

    uv run python site/tools/extract_replay.py

Reads two runs of each replayed question (Claude Code alone and with duckgrep) from
bench/eval/runs/full (gitignored), and writes each question's terminals into site/index.html
between its `<!-- race:<id> -->` markers. The page is complete without JavaScript: every step,
its output and the run's totals are in the HTML, and replay.js only animates them.

Tool calls and their output are the transcript's own, with three edits: the worktree's absolute
path is cut to the repo-relative one, long output stops after OUT_LINES lines with a count of the
rest, and duckgrep's tab-separated result is set as an aligned table. Each step carries the time
it landed (ms from the run's start) and the run's tokens and cost so far. Streamed usage undercounts
output (thinking arrives only in the final tally), so the recorded totals are apportioned over the API
calls by their streamed share: the counters rise in proportion and freeze exactly on the totals, which
the last step carries.
"""

from __future__ import annotations

import gzip
import html
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bench.eval import stream  # noqa: E402

RUNS = ROOT / "bench/eval/runs/full"
SUITE = ROOT / "bench/eval/suites/full-structural.jsonl"
PAGE = ROOT / "site/index.html"

# (marker id, task); each is replayed for these two setups
QUESTIONS = (
    ("python", "flask-callers-show_server_banner"),
    ("rust", "ripgrep-two-hop-eprint_nothing_searched"),
)
SIDES = (("alone", "baseline", "Claude Code alone"), ("duckgrep", "duckgrep-hint", "Claude Code + duckgrep"))
OUT_LINES = 8
WORKTREE = re.compile(r"/[^\s\"']*/code-tasks/wt/[^/]+/[^/]+/")
SQL_WORDS = re.compile(
    r"\b(SELECT|FROM|WHERE|AND|OR|NOT|UNION|ALL|JOIN|ON|AS|ORDER|BY|GROUP|LIMIT|DISTINCT|IN|LIKE|WITH)\b", re.I
)


def esc(s: str) -> str:
    return html.escape(s, quote=False)


def mark(text: str, symbol: str) -> str:
    """Escape text, wrapping each occurrence of the asked-about symbol in the match highlight."""
    parts = text.split(symbol)
    return f'<mark class="dg-match">{esc(symbol)}</mark>'.join(esc(p) for p in parts)


def sql_html(sql: str, symbol: str) -> str:
    out, pos = [], 0
    for m in re.finditer(r"'[^']*'|\b\w+(?=\()|\b\w+\b|[^\w']+", sql):
        tok = m.group(0)
        if tok.startswith("'"):
            inner = tok[1:-1]
            body = mark(inner, symbol) if symbol in inner else esc(inner)
            out.append(f"<span class=\"str\">'{body}'</span>")
        elif SQL_WORDS.fullmatch(tok):
            out.append(f'<span class="kw">{esc(tok)}</span>')
        elif sql[m.end() : m.end() + 1] == "(":
            out.append(f'<span class="fn">{esc(tok)}</span>')
        else:
            out.append(esc(tok))
        pos = m.end()
    assert pos == len(sql)
    return "".join(out)


def clip(lines: list[str]) -> tuple[list[str], int]:
    return (lines, 0) if len(lines) <= OUT_LINES else (lines[: OUT_LINES - 1], len(lines) - OUT_LINES + 1)


def grep_line(line: str, symbol: str) -> str:
    m = re.match(r"^([^:\s]+):(\d+):(.*)$", line)
    if not m:
        m2 = re.match(r"^(\d+):(.*)$", line)
        if m2:
            return f'<span class="ln">{m2.group(1)}</span>:{mark(m2.group(2), symbol)}'
        return mark(line, symbol)
    path, ln, text = m.groups()
    return f'<span class="path">{esc(path)}</span>:<span class="ln">{ln}</span>:{mark(text, symbol)}'


def table_html(tsv: str, symbol: str) -> str:
    rows = [r.split("\t") for r in tsv.split("\n") if r]
    head, body = rows[0], rows[1:]
    widths = [max(len(r[i]) if i < len(r) else 0 for r in rows) for i in range(len(head))]

    def cells(r: list[str], cls: list[str | None]) -> str:
        out = []
        for i, w in enumerate(widths):
            v = r[i] if i < len(r) else ""
            pad = " " * (w - len(v))
            body = mark(v, symbol) if symbol in v else esc(v)
            out.append((f'<span class="{cls[i]}">{body}</span>' if cls[i] and v else body) + pad)
        return "  ".join(out).rstrip()

    kinds = [("path" if h.endswith("path") else "ln" if h == "line" else None) for h in head]
    body, more = clip(body)
    lines = [
        f'<span class="ctx">{esc("  ".join(h.ljust(w) for h, w in zip(head, widths, strict=True)).rstrip())}</span>'
    ]
    lines.append(f'<span class="ctx">{"  ".join("-" * w for w in widths)}</span>')
    lines += [cells(r, kinds) for r in body]
    if more:
        lines.append(f'<span class="ctx">… {more} more rows</span>')
    return "\n".join(lines)


def quoted(key: str, value) -> str:
    """A Grep argument as shown: the pattern in quotes, the rest as is."""
    return f'"{value}"' if key == "pattern" else str(value)


def call_html(call: stream.Call, symbol: str) -> tuple[str, str, str]:
    """(tool label, call line, output) for one tool call."""
    args = {k: WORKTREE.sub("", v) if isinstance(v, str) else v for k, v in call.input.items()}
    result = WORKTREE.sub("", call.result)
    if call.name.startswith("mcp__duckgrep__"):
        payload = json.loads(result)
        out = table_html(payload["result"], symbol) if "result" in payload else esc(str(payload))
        return "query", sql_html(args["sql"], symbol), out
    if call.name == "Bash":
        line = f'<span class="prompt">$</span> {mark(args["command"], symbol)}'
    else:
        line = "  ".join(
            f'<span class="ctx">{esc(k)}:</span> {mark(quoted(k, v), symbol)}'
            for k, v in args.items()
            if k not in ("output_mode",)
        )
    lines, more = clip([ln for ln in result.split("\n") if ln.strip()])
    out = "\n".join(grep_line(ln, symbol) for ln in lines)
    if more:
        out += f'\n<span class="ctx">… {more} more lines</span>'
    return call.name, line, out


def events(path: Path) -> tuple[stream.Transcript, list[dict]]:
    """The transcript, and its steps in order: each tool call when its result landed and each note the
    agent wrote mid-run, with the API round trip it belongs to."""
    raw = gzip.open(path, "rt").read().splitlines()
    tr = stream.read(raw)
    by_id = {c.id: c for c in tr.calls}
    rounds: dict[str, int] = {}
    steps, start = [], None
    for line in raw:
        e = json.loads(line)
        ts = e.get("timestamp")
        t = datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None
        msg = e.get("message") or {}
        if e.get("type") == "assistant":
            rnd = rounds.setdefault(msg["id"], len(rounds) + 1)
            for b in msg.get("content") or []:
                if b.get("type") == "text" and b["text"].strip():
                    steps.append({"kind": "note", "text": b["text"].strip(), "t": t, "round": rnd})
        elif e.get("type") == "user":
            for b in msg.get("content") if isinstance(msg.get("content"), list) else []:
                if b.get("type") == "tool_result" and b.get("tool_use_id") in by_id:
                    c = by_id[b["tool_use_id"]]
                    steps.append({"kind": "call", "call": c, "t": t, "round": c.round})
    end = max(s["t"] for s in steps)
    start = end.timestamp() - tr.result["duration_ms"] / 1000
    for s in steps:
        s["ms"] = round((s["t"].timestamp() - start) * 1000)
    # the last note is the answer, shown from the scored record instead
    if steps and steps[-1]["kind"] == "note":
        steps.pop()
    return tr, steps


def side_html(task: dict, record: dict, setup: str, label: str, side: str, base: dict | None = None) -> str:
    """One lane. With `base` (the run without duckgrep), each total also shows its change against it."""
    tr, steps = events(RUNS / task["id"] / f"{setup}-1.jsonl.gz")
    symbol = re.search(r"`([^`]+)`", task["prompt"]).group(1)
    usage = list(tr.usage_by_message.values())

    def streamed(upto: int) -> tuple[int, float]:
        tok = {k: 0 for k in stream.config.RATES}
        for u in usage[:upto]:
            for k, v in stream.tokens({"usage": u}).items():
                tok[k] += v
        return sum(tok.values()), stream.cost(tok)

    all_tokens, all_cost = streamed(len(usage))

    def spent(upto: int) -> tuple[int, float]:
        tokens, cost = streamed(upto)
        return round(record["tokens_total"] * tokens / all_tokens), record["cost_usd"] * cost / all_cost

    items, calls = [], 0
    for s in steps:
        tokens, cost = spent(s["round"])
        if s["kind"] == "call":
            calls += 1
            tool, line, out = call_html(s["call"], symbol)
            body = (
                f'<p class="step__call"><span class="step__tool">{esc(tool)}</span> <code>{line}</code></p>'
                f'<pre class="step__out">{out}</pre>'
            )
        else:
            body = f'<p class="step__note">{mark(s["text"], symbol)}</p>'
        items.append(
            f'<li class="step" data-ms="{s["ms"]}" data-calls="{calls}" data-tokens="{tokens}" '
            f'data-cost="{cost:.4f}">{body}</li>'
        )
    names = ", ".join(a.split(":", 1)[1] for a in sorted(record["answer"]))
    verdict = "correct" if record["score"]["success"] else "incorrect"
    items.append(
        f'<li class="step step--answer" data-ms="{record["duration_ms"]}" data-calls="{record["tool_calls"]}" '
        f'data-tokens="{record["tokens_total"]}" data-cost="{record["cost_usd"]:.4f}">'
        f'<p class="step__answer"><span class="step__tool">answer</span> {esc(names)} '
        f'<span class="verdict">{verdict}</span></p></li>'
    )

    def delta(key: str) -> str:
        if base is None:
            return ""
        a, b = record[key], base[key]
        text = f"{a - b:+d}" if key == "tool_calls" else f"{(a / b - 1) * 100:+.0f}%"
        return f' <span class="meter__d">{text.replace("-", "−")}</span>'

    k = record["tokens_total"] / 1000
    meter = (
        f'<dl class="meter" aria-label="{esc(label)}: totals">'
        f'<div><dt>tool calls</dt><dd><span data-meter="calls">{record["tool_calls"]}</span>{delta("tool_calls")}</dd></div>'
        f'<div><dt>tokens</dt><dd><span data-meter="tokens">{k:.1f}k</span>{delta("tokens_total")}</dd></div>'
        f'<div><dt>cost</dt><dd><span data-meter="cost">${record["cost_usd"]:.4f}</span>{delta("cost_usd")}</dd></div>'
        "</dl>"
    )
    return (
        f'<figure class="lane lane--{side}">'
        f'<figcaption class="dg-term__bar lane__bar">{esc(label)}</figcaption>'
        f'{meter}<ol class="steps dg-term">{"".join(items)}</ol></figure>'
    )


def main() -> None:
    suite = {t["id"]: t for t in map(json.loads, SUITE.open())}
    records = {}
    for line in (RUNS / "results.jsonl").open():
        r = json.loads(line)
        records[(r["task"], r["setup"], r["rep"])] = r
    page = PAGE.read_text()
    for marker, task_id in QUESTIONS:
        task = suite[task_id]
        base = records[(task_id, SIDES[0][1], 1)]
        lanes = "".join(
            side_html(task, records[(task_id, setup, 1)], setup, label, side, None if i == 0 else base)
            for i, (side, setup, label) in enumerate(SIDES)
        )
        html_block = f'<div class="race__lanes" data-task="{task_id}">{lanes}</div>'
        assert "/code-tasks/" not in html_block and "/Users/" not in html_block, "an absolute path leaked"
        page, n = re.subn(
            rf"(<!-- race:{marker} -->).*?(<!-- /race:{marker} -->)",
            lambda m, block=html_block: m.group(1) + "\n" + block + "\n" + m.group(2),
            page,
            flags=re.S,
        )
        assert n == 1, f"no race:{marker} markers in {PAGE}"
    PAGE.write_text(page)


if __name__ == "__main__":
    main()
