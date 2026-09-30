"""Parse an agent's final answer and score it against the task's answer key."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

FENCE = re.compile(r"```[A-Za-z]*[ \t]*\n(.*?)```", re.S)
AS_TRAIT = re.compile(r"<\s*([^<>]+?)\s+as\s+[^<>]+>")  # <Type as Trait> -> Type
GENERIC_ARGS = re.compile(r"(?<=\w)<[^<>]*>")  # Type<T> -> Type
CALL_PARENS = re.compile(r"\(.*\)$")


def parse_answer(text: str | None) -> list[str] | None:
    """The `locations` list of the last fenced JSON block that has one, or None."""
    for block in reversed(FENCE.findall(text or "")):
        try:
            obj = json.loads(block)
        except json.JSONDecodeError:
            continue
        locs = obj.get("locations") if isinstance(obj, dict) else None
        if isinstance(locs, list) and all(isinstance(x, str) for x in locs):
            return locs
    return None


def _module_prefixes(path: str) -> list[str]:
    """Dotted module paths a name in `path` may be written under, longest first: src.pkg.mod., pkg.mod., mod."""
    stem, dot, ext = path.rpartition(".")
    if not dot or ext not in ("py", "rs"):
        return []
    parts = [p for p in stem.split("/") if p]
    if ext == "rs" and parts and parts[-1] in ("mod", "lib", "main"):
        parts = parts[:-1]
    return [".".join(parts[i:]) + "." for i in range(len(parts))]


def normalize(loc: str, roots: tuple[str, ...] = ()) -> tuple[str, str]:
    """(path relative to the repo root, qualified name) from an answer entry; the name is "" for a bare path.

    Rust spellings fold to the Python form: `Type::method`, `<Type as Trait>::method`, `impl Type/method` and
    `Type<T>::method` all become `Type.method`."""
    loc = loc.strip().strip("`").strip()
    path, _, name = loc.partition(":")
    path = path.strip()
    for root in sorted(roots, key=len, reverse=True):
        root = root.rstrip("/") + "/"
        if root != "/" and path.startswith(root):
            path = path[len(root) :]
            break
    while path.startswith("./"):
        path = path[2:]
    path = path.lstrip("/")
    name = name.strip().lstrip(":").strip()
    if name.startswith("impl "):
        name = name[len("impl ") :].strip()
    previous = None
    while previous != name:
        previous = name
        name = AS_TRAIT.sub(r"\1", name)
        name = GENERIC_ARGS.sub("", name)
    name = CALL_PARENS.sub("", name.replace("::", ".").replace("/", ".")).strip(". ")
    if name.startswith("crate."):
        name = name[len("crate.") :]
    for prefix in _module_prefixes(path):
        if name.startswith(prefix):
            name = name[len(prefix) :]
            break
    if name.isdigit():  # "path:123" is a line number, not a name
        name = ""
    return path, name


def _key(entry: str) -> tuple[str, str]:
    path, _, name = entry.partition(":")
    return path, name


def _hits(gold: tuple[str, str], pred: tuple[str, str]) -> bool:
    """A named function matches its key, and so does a function nested in it (keys roll up to the outermost).
    In Rust, a name may also carry inline module prefixes the key leaves out: `tests.it_works`, `imp.Type.m`."""
    if gold[0] != pred[0]:
        return False
    names = [pred[1]]
    if pred[0].endswith(".rs"):
        parts = pred[1].split(".")
        while len(parts) > 1 and parts[0].islower():
            parts = parts[1:]
            names.append(".".join(parts))
    return any(n == gold[1] or n.startswith(gold[1] + ".") for n in names)


def _f1(p: float, r: float) -> float:
    return 2 * p * r / (p + r) if p + r else 0.0


@dataclass(frozen=True)
class Score:
    parsed: bool
    success: bool  # every key entry was named: Agentless's superset rule
    precision: float | None
    recall: float | None
    f1: float | None
    file_recall: float | None  # share of the key's files that were named

    def as_dict(self) -> dict:
        return dict(self.__dict__)


UNPARSED = Score(parsed=False, success=False, precision=0.0, recall=0.0, f1=0.0, file_recall=0.0)


def score(answer: list[str] | None, gold: tuple[str, ...] | list[str], kind: str, roots: tuple[str, ...] = ()) -> Score:
    """Score `answer` (from parse_answer) against `gold`. `kind` is the task's answer type: functions or files."""
    if answer is None:
        return UNPARSED
    preds = list(dict.fromkeys(normalize(a, roots) for a in answer if a.strip()))
    keys = [_key(g) for g in gold]
    gold_files = list(dict.fromkeys(p for p, _ in keys))
    named_files = {p for p, _ in preds}
    file_recall = sum(f in named_files for f in gold_files) / len(gold_files)

    if kind == "files":
        pred_files = list(dict.fromkeys(p for p, _ in preds))
        tp = sum(f in set(gold_files) for f in pred_files)
        precision = tp / len(pred_files) if pred_files else 0.0
        recall = file_recall
        return Score(True, recall == 1.0, precision, recall, _f1(precision, recall), file_recall)

    func_keys = [k for k in keys if k[1]]
    file_only = {p for p, n in keys if not n}
    named = [p for p in preds if p[1]]
    covered = all(any(_hits(k, p) for p in named) for k in func_keys) and file_only <= named_files
    if not func_keys:
        return Score(True, covered, None, None, None, file_recall)
    # a named entry in a file whose key is file-only (a class- or module-level edit) is neither right nor wrong
    scored = [p for p in named if p[0] not in file_only or any(_hits(k, p) for k in func_keys)]
    precision = sum(any(_hits(k, p) for k in func_keys) for p in scored) / len(scored) if scored else 0.0
    recall = sum(any(_hits(k, p) for p in named) for k in func_keys) / len(func_keys)
    return Score(True, covered, precision, recall, _f1(precision, recall), file_recall)
