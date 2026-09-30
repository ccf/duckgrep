"""Turns-to-locate: the first round trip whose tool results show a gold location.

A function key counts as located when a result shows its definition, in any of three ways:
- its `def`/`fn`/`class` line (or a decorator or attribute above it), by line number in its file, as Read, Grep,
  grep -n, sed -n and Serena's symbol locations show it;
- its qualified name in its file, from a structured result (a duckgrep row, a Serena symbol);
- in output without usable line numbers, its definition line, when the call names its file or, naming no file,
  the function itself.
A call to it, a docstring, or another function with the same name does not count. A file key counts as located
when a call or its result names the file.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from . import gold
from .stream import Call, Transcript

EXTENSIONS = ("py", "pyi", "rs")
SOURCE = rf"[\w./@+-]*?\.(?:{'|'.join(EXTENSIONS)})"
PATH_LINE = re.compile(rf"^\s*(?P<path>{SOURCE})[:-](?P<n>\d+)[:-]")  # grep -rn, rg -n, Grep across files
LEAD = re.compile(r"^\s*(?P<n>\d+)(?:\t|:|-)")  # one file: Read, grep -n, cat -n
FILE = re.compile(rf"(?<![\w/.@+-])({SOURCE})(?![\w.])")
SED = re.compile(r"\bsed\s+-n\s+['\"]?(\d+),(\d+)p")
CD = re.compile(r"^\s*cd\s+(\S+)\s*(?:;|&&)")
SERENA_MARK = re.compile(r"^\s*>\s*(\d+):")  # a reference line in Serena's content_around_reference
GENERIC = {"__init__.py", "__main__.py", "mod.rs", "lib.rs", "main.rs"}  # file names that need their directory


@dataclass(frozen=True)
class Target:
    path: str
    qualname: str = ""  # "" for a file key
    spans: tuple[tuple[int, int], ...] = ()  # (first line, def line) per definition; () when not found
    kind: str = "function"


def targets(entries: Iterable[str], read: Callable[[str], str | None]) -> list[Target]:
    """Each gold entry with its definition lines at the task's commit; `read(path)` gives the file or None."""
    out = []
    for entry in entries:
        path, _, qual = entry.partition(":")
        if not qual:
            out.append(Target(path))
            continue
        src = read(path)
        units: list[gold.Unit] = []
        if src is not None:
            try:
                units = gold.rust_units(src) if path.endswith(".rs") else gold.python_units(src)
            except (SyntaxError, ValueError):
                units = []
        found = [u for u in units if u.qualname == qual]
        kind = "class" if any(u.kind == "class" for u in found) else "function"
        out.append(Target(path, qual, tuple((u.start, u.head or u.start) for u in found), kind))
    return out


@dataclass
class Evidence:
    pairs: list[tuple[str, int]] = field(default_factory=list)  # (path, line) shown
    names: list[tuple[str, str]] = field(default_factory=list)  # (path, qualified name) named by a structured tool
    loose: list[str] = field(default_factory=list)  # output lines that could not be placed in a file
    files: list[str] = field(default_factory=list)  # source files the call's input names
    text: str = ""  # the call's input and result


def _join(cwd: str | None, path: str) -> str:
    return f"{cwd.rstrip('/')}/{path}" if cwd and not path.startswith("/") else path


def _input_files(call: Call) -> tuple[list[str], str | None]:
    """The source files a call's input names, relative ones joined to a leading `cd`, and that directory."""
    if call.name == "Bash":
        command = call.input.get("command", "")
        cd = CD.match(command)
        cwd = cd.group(1).strip("'\"") if cd else None
        return sorted({_join(cwd, f) for f in FILE.findall(command)}), cwd
    return sorted({f for v in call.input.values() if isinstance(v, str) for f in FILE.findall(v)}), None


def _duckgrep(result: str, ev: Evidence) -> None:
    """duckgrep's rows: a path column goes with the line and name columns of its side (dst_* or the rest)."""
    try:
        tsv = json.loads(result).get("result", "")
    except (json.JSONDecodeError, AttributeError):
        tsv = result
    rows = [r.split("\t") for r in str(tsv).splitlines() if r.strip()]
    if not rows:
        return
    head = [h.strip().lower() for h in rows[0]]
    for pi, ph in enumerate(head):
        if ph not in ("path", "src_path", "dst_path", "file"):
            continue
        side = ph.startswith("dst")
        lines = [i for i, h in enumerate(head) if "line" in h and h.startswith("dst") == side]
        quals = [
            i
            for i, h in enumerate(head)
            if h in ("qualname", "caller", "scope", "src_scope", "symbol", "dst_qualname")
            and h.startswith("dst") == side
        ]
        for row in rows[1:]:
            path = row[pi].strip() if pi < len(row) else ""
            if not path:
                continue
            ev.pairs += [(path, int(row[i])) for i in lines if i < len(row) and row[i].strip().isdigit()]
            ev.names += [(path, row[i].strip()) for i in quals if i < len(row) and row[i].strip()]
    ev.loose += [cell for row in rows[1:] for cell in row]


def _serena(node, path: str | None, ev: Evidence) -> None:
    """Serena's JSON: symbols (name path, 0-based body location) under a relative path or a file key."""
    if isinstance(node, dict):
        path = node.get("relative_path", path)
        if "name_path" in node and path:
            ev.names.append((path, node["name_path"]))
            start = (node.get("body_location") or {}).get("start_line")
            if isinstance(start, int):
                ev.pairs.append((path, start + 1))
        for key, value in node.items():
            inner = key if FILE.fullmatch(key) else path
            if isinstance(value, str) and key not in ("name_path", "relative_path", "kind"):
                ev.loose += value.splitlines()
            else:
                _serena(value, inner, ev)
    elif isinstance(node, list):
        for value in node:
            if isinstance(value, str):
                ev.loose += value.splitlines()
                for line in value.splitlines():
                    if (m := SERENA_MARK.match(line)) and path:
                        ev.pairs.append((path, int(m.group(1)) + 1))
            else:
                _serena(value, path, ev)


def evidence(call: Call) -> Evidence:
    """What one tool call showed: numbered lines placed in files, symbols it named, and the rest."""
    files, cwd = _input_files(call)
    ev = Evidence(files=files, text=json.dumps(call.input) + "\n" + call.result)
    if call.name == "Read":
        path = call.input.get("file_path", "")
        for line in call.result.splitlines():
            if m := LEAD.match(line):
                ev.pairs.append((path, int(m.group("n"))))
    elif call.name.startswith("mcp__duckgrep"):
        _duckgrep(call.result, ev)
    elif call.name.startswith("mcp__serena"):
        try:
            data = json.loads(call.result)
        except json.JSONDecodeError:
            ev.loose += call.result.splitlines()
        else:
            _serena(data, call.input.get("relative_path") or None, ev)
    else:  # Grep, Bash, Glob and anything else: text
        one = files[0] if len(files) == 1 else None
        for line in call.result.splitlines():
            if m := PATH_LINE.match(line):
                ev.pairs.append((_join(cwd, m.group("path")), int(m.group("n"))))
            elif one and (m := LEAD.match(line)):
                ev.pairs.append((one, int(m.group("n"))))
            else:
                ev.loose.append(line)
        if one and call.name == "Bash":
            for a, b in SED.findall(call.input.get("command", "")):
                ev.pairs += [(one, n) for n in range(int(a), int(b) + 1)]
    return ev


def same_path(shown: str, path: str) -> bool:
    shown = shown.strip().strip("'\"").removeprefix("./")
    return shown == path or shown.endswith("/" + path)


def _mentions(text: str, path: str) -> bool:
    """The text names the file: its path, or its file name (with its directory's name, for a generic one)."""
    if _names_path(text, path):
        return True
    parts = path.split("/")
    name = parts[-1]
    if not re.search(rf"(?<![\w.@-]){re.escape(name)}(?!\w)", text):
        return False
    return name not in GENERIC or (len(parts) > 1 and re.search(rf"(?<![\w.@-]){re.escape(parts[-2])}(?!\w)", text))


def canon(name_path: str) -> str:
    """A Serena name path or a duckgrep qualified name as `Type.method`: an impl block by its type, generics and
    module paths dropped."""
    parts = []
    for seg in name_path.split("/"):
        while re.search(r"<[^<>]*>", seg):
            seg = re.sub(r"<[^<>]*>", "", seg)
        seg = seg.strip()
        if seg.startswith("impl "):
            seg = seg.split(" for ")[-1].removeprefix("impl ").strip()
        parts += [p for p in seg.split("::")[-1].split(".") if p]
    return ".".join(parts)


def definition(target: Target) -> re.Pattern | None:
    name = re.escape(target.qualname.split(".")[-1])
    if target.kind == "class" and target.path.endswith((".py", ".pyi")):
        return re.compile(rf"(?<![\w.])class\s+{name}\b")
    if target.path.endswith((".py", ".pyi")):
        return re.compile(rf"(?<![\w.])(?:async\s+)?def\s+{name}\s*\(")
    if target.path.endswith(".rs"):
        return re.compile(rf"(?<![\w.])fn\s+{name}\s*[<(]")
    return None


def _names_path(text: str, path: str) -> bool:
    return bool(re.search(rf"(?<![\w.@-]){re.escape(path)}(?!\w)", text))


def _located(ev: Evidence, t: Target, call: Call) -> bool:
    if not t.qualname:
        shown = ev.files + [p for p, _ in ev.pairs]
        return _names_path(ev.text, t.path) or any(same_path(p, t.path) for p in shown)
    if any(same_path(p, t.path) and any(a <= n <= b for a, b in t.spans) for p, n in ev.pairs):
        return True
    want = canon(t.qualname)
    if any(same_path(p, t.path) and (canon(n) == want or canon(n).endswith("." + want)) for p, n in ev.names):
        return True
    pattern = definition(t)
    if pattern is None or not any(pattern.search(line) for line in ev.loose):
        return False
    if _mentions(ev.text, t.path):
        return True
    named = re.search(rf"(?<![\w.]){re.escape(t.qualname)}(?!\w)", json.dumps(call.input))
    return not ev.files and bool(named)


def turns_to_locate(tr: Transcript, found: list[Target]) -> int | None:
    """The first round trip whose tool results show one of `found`; None if none ever did."""
    for call in sorted(tr.calls, key=lambda c: c.round):
        ev = evidence(call)
        if any(_located(ev, t, call) for t in found):
            return call.round
    return None
