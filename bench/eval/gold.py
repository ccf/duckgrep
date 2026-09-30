"""Answer keys from a fix patch: each changed line of the base file maps to its outermost enclosing function.

Rules (both languages):
- nested functions roll up to the outermost one;
- a change outside any function (class or module level) makes the file a file-only key, unless the same file
  already has a function key, which names the file anyway;
- blank lines, comments and import statements (`use` in Rust) are ignored, and so is test code: Python test
  files; Rust files under tests/, benches/ and examples/, `#[cfg(test)]` or `tests` modules and `#[test]` functions;
- Rust methods are `Type.method`, whatever trait they implement.

Hunks are located in the base file by their context, as `git apply` does, because SWE-bench line numbers are
often off by a few lines.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from dataclasses import dataclass

from unidiff import PatchSet

TEST_ATTR = re.compile(r"#\[\s*(?:cfg\s*\(\s*test\s*\)|(?:\w+::)*test)\s*\]")


@dataclass(frozen=True)
class Unit:
    qualname: str
    start: int  # first line, decorators and attributes included (1-based)
    end: int  # last line
    kind: str  # function | class | test-fn (a Rust test function) | test-mod (a Rust test module or impl)
    head: int = 0  # the `def`/`fn` line


def python_units(src: str) -> list[Unit]:
    out: list[Unit] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                q = prefix + child.name
                start = min([d.lineno for d in child.decorator_list] + [child.lineno])
                kind = "class" if isinstance(child, ast.ClassDef) else "function"
                out.append(Unit(q, start, child.end_lineno or child.lineno, kind, child.lineno))
                walk(child, q + ".")
            else:
                walk(child, prefix)

    walk(ast.parse(src), "")
    return out


def python_import_lines(src: str) -> set[int]:
    lines: set[int] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            lines.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return lines


_rust_parser = None


def rust_parser():
    global _rust_parser
    if _rust_parser is None:
        import tree_sitter_rust
        from tree_sitter import Language, Parser

        _rust_parser = Parser(Language(tree_sitter_rust.language()))
    return _rust_parser


def _text(node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", "replace")


def rust_type_name(node, src: bytes) -> str:
    """The bare type an impl block is for: Foo for `Foo<T>`, `&'a Foo`, `crate::m::Foo` or `dyn Foo`."""
    if node is None:
        return "?"
    if node.type in ("generic_type", "reference_type", "pointer_type"):
        return rust_type_name(node.child_by_field_name("type"), src)
    if node.type == "scoped_type_identifier":
        return _text(node.child_by_field_name("name"), src)
    if node.type == "dynamic_type":
        return rust_type_name(node.child_by_field_name("trait"), src)
    return _text(node, src)


def _attributes(node) -> list:
    """The attribute items directly above an item (tree-sitter-rust makes them preceding siblings)."""
    attrs = []
    prev = node.prev_named_sibling
    while prev is not None and prev.type == "attribute_item":
        attrs.append(prev)
        prev = prev.prev_named_sibling
    return attrs


def rust_units(src: str) -> list[Unit]:
    data = src.encode()
    out: list[Unit] = []

    def walk(node, prefix: str, in_test: bool) -> None:
        for c in node.children:
            if c.type not in ("function_item", "impl_item", "trait_item", "mod_item"):
                walk(c, prefix, in_test)
                continue
            attrs = _attributes(c)
            test = in_test or any(TEST_ATTR.search(_text(a, data)) for a in attrs)
            start = min([a.start_point[0] for a in attrs] + [c.start_point[0]]) + 1
            end = c.end_point[0] + 1
            if c.type == "function_item":
                name = _text(c.child_by_field_name("name"), data)
                out.append(Unit(prefix + name, start, end, "test-fn" if test else "function", c.start_point[0] + 1))
                walk(c, prefix + name + ".", test)
            elif c.type == "impl_item":
                owner = rust_type_name(c.child_by_field_name("type"), data)
                if test:
                    out.append(Unit(owner, start, end, "test-mod"))
                walk(c, owner + ".", test)
            elif c.type == "trait_item":
                walk(c, _text(c.child_by_field_name("name"), data) + ".", test)
            else:  # mod_item: module names don't qualify, the file path already names the module
                name = _text(c.child_by_field_name("name"), data)
                test = test or name in ("tests", "test")
                if test:
                    out.append(Unit(name, start, end, "test-mod"))
                walk(c, "", test)

    walk(rust_parser().parse(data).root_node, "", False)
    return out


def rust_use_lines(src: str) -> set[int]:
    lines: set[int] = set()
    stack = [rust_parser().parse(src.encode()).root_node]
    while stack:
        n = stack.pop()
        if n.type in ("use_declaration", "extern_crate_declaration"):
            lines.update(range(n.start_point[0] + 1, n.end_point[0] + 2))
        else:
            stack.extend(n.children)
    return lines


FUNCTION = ("function",)
TEST = ("test-fn", "test-mod")


def outermost(units: list[Unit], line: int, kinds: tuple[str, ...] = FUNCTION) -> Unit | None:
    """The outermost unit of one of `kinds` that contains `line`."""
    best = None
    for u in units:
        if u.kind in kinds and u.start <= line <= u.end and (best is None or u.start < best.start):
            best = u
    return best


def is_test_path(path: str, lang: str) -> bool:
    parts = path.split("/")
    name = parts[-1]
    if lang == "python":
        return (
            any(p in ("tests", "test", "testing") for p in parts[:-1])
            or name.startswith("test_")
            or name.endswith("_test.py")
            or name == "conftest.py"
        )
    return any(p in ("tests", "benches", "examples") for p in parts[:-1])


def _blank_or_comment(line: str, lang: str) -> bool:
    s = line.strip()
    return not s or s.startswith("#" if lang == "python" else "//")


def _locate(hunk, base: list[str]) -> int:
    """How far the hunk's old lines sit from where its header says (0 when the header is right)."""
    old = [ln.value.rstrip("\r\n") for ln in hunk if ln.is_context or ln.is_removed]
    if not old:
        return 0
    start = hunk.source_start - 1
    for same in (lambda a, b: a.rstrip() == b.rstrip(), lambda a, b: a.strip() == b.strip()):
        for delta in sorted(range(-len(base), len(base) + 1), key=abs):
            s = start + delta
            if 0 <= s and s + len(old) <= len(base) and all(same(base[s + k], old[k]) for k in range(len(old))):
                return delta
    raise ValueError(f"hunk at line {hunk.source_start} does not apply")


def _apply(base: list[str], hunks: list) -> tuple[list[str], dict[tuple[int, int], int]]:
    """The patched file, and the new line number of each added line, keyed by (hunk, line in hunk)."""
    new: list[str] = []
    where: dict[tuple[int, int], int] = {}
    pos = 0
    for hi, (hunk, delta) in enumerate(hunks):
        first = (hunk.source_start - 1 if hunk.source_length else hunk.source_start) + delta
        new.extend(base[pos:first])
        pos = first
        for li, ln in enumerate(hunk):
            if ln.is_context:
                new.append(base[pos])
                pos += 1
            elif ln.is_removed:
                pos += 1
            elif ln.is_added:
                new.append(ln.value.rstrip("\r\n"))
                where[(hi, li)] = len(new)
    new.extend(base[pos:])
    return new, where


def _insertion_owner(units: list[Unit], before: int, added: list[str], base: list[str], lang: str) -> Unit | None:
    """The function that code inserted after line `before` belongs to, or None when it lands outside one."""
    f = outermost(units, before) if before else None
    if f is None:
        return None
    if lang == "rust":
        return f if before < f.end else None  # after the closing brace is outside
    first = next(v for v in added if v.strip())
    head = base[f.head - 1]
    return f if len(first) - len(first.lstrip()) > len(head) - len(head.lstrip()) else None


RUST_VISIBILITY = re.compile(r"\bpub(?:\s*\((?:crate|super|self|in\s+[\w:]+)\))?\s+")


def _visibility_only(hunk, lang: str) -> set[int]:
    """Indices of the hunk's lines that only change a Rust item's visibility (`fn` <-> `pub fn` <->
    `pub(crate) fn`): each removed line of a change block paired with its added line, when the two differ in
    nothing else. Such an edit exposes a function to other code; it doesn't fix it."""
    if lang != "rust":
        return set()
    items = list(hunk)
    same: set[int] = set()
    i = 0
    while i < len(items):
        if not items[i].is_removed:
            i += 1
            continue
        removed = []
        while i < len(items) and items[i].is_removed:
            removed.append(i)
            i += 1
        added = []
        while i < len(items) and items[i].is_added:
            added.append(i)
            i += 1
        if len(removed) != len(added):
            continue

        def bare(k: int) -> str:
            return " ".join(RUST_VISIBILITY.sub("", items[k].value).split())

        for r, a in zip(removed, added, strict=True):
            if items[r].value.strip() != items[a].value.strip() and bare(r) == bare(a):
                same |= {r, a}
    return same


@dataclass(frozen=True)
class Key:
    entries: tuple[str, ...]  # sorted: "path:Qual.name" and "path"
    files: tuple[str, ...]  # the non-test source files the patch changes
    functions: int


def _finish(funcs: set[str], file_level: set[str]) -> tuple[str, ...]:
    """Drop file-only keys that a function key in the same file already implies."""
    named = {f.partition(":")[0] for f in funcs}
    return tuple(sorted(funcs | (file_level - named)))


def derive(patch: str, read: Callable[[str], str | None], lang: str) -> Key:
    """The answer key of `patch`; `read(path)` returns a file's text at the base commit (None if absent)."""
    ext = ".py" if lang == "python" else ".rs"
    units_of = python_units if lang == "python" else rust_units
    imports_of = python_import_lines if lang == "python" else rust_use_lines
    funcs: set[str] = set()
    file_level: set[str] = set()
    files: list[str] = []
    for pf in PatchSet(patch):
        path = pf.path
        if not path.endswith(ext) or is_test_path(path, lang):
            continue
        files.append(path)
        if pf.is_added_file or pf.is_removed_file:
            file_level.add(path)
            continue
        src = read(pf.source_file[2:] if pf.source_file.startswith("a/") else pf.source_file)
        if src is None:
            raise ValueError(f"{path} is missing at the base commit")
        base = src.split("\n")
        units = units_of(src)
        old_imports = imports_of(src)
        hunks = [(h, _locate(h, base)) for h in pf]
        new, where = _apply(base, hunks)
        try:
            new_imports = imports_of("\n".join(new))
        except SyntaxError:
            new_imports = set()

        def mark(f: Unit | None, path: str = path) -> None:
            if f:
                funcs.add(f"{path}:{f.qualname}")
            else:
                file_level.add(path)

        for hi, (hunk, delta) in enumerate(hunks):
            items = list(hunk)
            exposed = _visibility_only(hunk, lang)
            for i, ln in enumerate(items):
                if i in exposed:
                    continue
                if ln.is_removed:
                    line = ln.source_line_no + delta
                    if _blank_or_comment(ln.value, lang) or line in old_imports or outermost(units, line, TEST):
                        continue
                    mark(outermost(units, line))
                elif ln.is_added and (i == 0 or not items[i - 1].is_added):  # the first line of an insertion
                    run = []
                    for j in range(i, len(items)):
                        if not items[j].is_added:
                            break
                        if j in exposed:
                            continue
                        if not (_blank_or_comment(items[j].value, lang) or where[(hi, j)] in new_imports):
                            run.append(items[j].value.rstrip("\r\n"))
                    if not run:
                        continue
                    before = next((x.source_line_no for x in reversed(items[:i]) if x.source_line_no), None)
                    before = (before if before is not None else hunk.source_start - 1) + delta
                    if before and outermost(units, before, TEST):
                        continue
                    mark(_insertion_owner(units, before, run, base, lang))
    return Key(_finish(funcs, file_level), tuple(dict.fromkeys(files)), len(funcs))


def normalize_listed(entries: list[str], read: Callable[[str], str | None]) -> tuple[str, ...]:
    """A Python key as czlll/SWE-bench_Lite's edit_functions lists it, under the rules above: classes become
    file-only keys and nested functions roll up. Raises ValueError for a name that isn't in the file."""
    funcs, file_level = set(), set()
    units_of: dict[str, list[Unit]] = {}
    for entry in entries:
        path, _, qual = entry.partition(":")
        if path not in units_of:
            src = read(path)
            if src is None:
                raise ValueError(f"{path} is missing at the base commit")
            units_of[path] = python_units(src)
        unit = next((u for u in units_of[path] if u.qualname == qual), None)
        if unit is None:
            raise ValueError(f"{entry} is not defined in the base file")
        if unit.kind == "class":
            file_level.add(path)
        else:
            funcs.add(f"{path}:{outermost(units_of[path], unit.head).qualname}")
    return _finish(funcs, file_level)


def same_key(derived: tuple[str, ...], listed: tuple[str, ...]) -> bool:
    """Equal up to one naming convention: czlll lists a class where the edit is in its __init__."""

    def canon(entries):
        funcs = {e for e in entries if ":" in e and not e.endswith(".__init__")}
        files = {e.partition(":")[0] for e in entries if ":" not in e or e.endswith(".__init__")}
        return _finish(funcs, files)

    return canon(derived) == canon(listed)
