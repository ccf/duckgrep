"""Structural questions (callers, two-hop callers, importers) with keys from static analysis.

Python keys come from jedi, Rust keys from rust-analyzer's SCIP index. A key is kept only when it is complete:
every call site of the target's name must resolve, to the target or to something else. A site the analyzer can't
resolve (dynamic dispatch, code behind an inactive cfg) might be a caller the key would miss, so such a question is
dropped. Each key is also checked against `git grep -w`: every site in it must be one of grep's hits.
"""

from __future__ import annotations

import ast
import os
import random
import re
import subprocess
import sysconfig
from collections import defaultdict
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from pathlib import Path

from .. import config, gold, prompts, setups, workspace
from ..suite import Task

CALLERS_SIZE = (2, 15)
TWO_HOP_SIZE = (3, 25)
IMPORTERS_SIZE = (2, 15)
FUNCTION_LIKE = ("function", "test-fn")
# the keys count a recursive function as its own caller; the question says so (a pilot run left it out on purpose)
EVERY = "including test functions and, if it calls itself, the function itself"


@dataclass(frozen=True)
class Def:
    path: str
    qualname: str  # Session.request; in Rust, Type.method
    line: int  # 1-based line of the name
    col: int  # 0-based column of the name: characters for Python, bytes for Rust
    test: bool = False  # defined in test code
    head: int = 0  # the def/fn line, which tells apart two functions of one name in one file

    @property
    def name(self) -> str:
        return self.qualname.split(".")[-1]


@dataclass(frozen=True)
class Site:
    path: str
    line: int
    col: int
    caller: str | None  # qualname of the outermost enclosing function; None at module level
    caller_head: int | None = None  # that function's def/fn line


@dataclass
class Module:
    file: str  # the file that defines it
    label: str  # how a question names it
    grep: str  # the word its imports contain
    importers: dict[str, list[int]]  # importing file -> lines of the import


@dataclass
class Analysis:
    root: Path
    defs: list[Def] = field(default_factory=list)
    calls: dict[str, list[Site]] = field(default_factory=lambda: defaultdict(list))  # by the called name
    modules: list[Module] = field(default_factory=list)
    resolve: Callable[[Site], set | None] = lambda site: None  # what a site refers to; None when unknown
    identity: Callable[[Def], object] = lambda d: None  # what `resolve` returns for a site that calls d
    # (path, line, column) of every reference to d that must be one of its call sites; None when unknown
    references: Callable[[Def], set | None] = lambda d: set()
    # whether a call that resolves elsewhere (to `got`) can still reach d at run time: through a base class's
    # method or a trait's, or a receiver the resolver narrowed to another class
    may_reach: Callable[[Site, Def, set], bool] = lambda site, d, got: False

    def callers(self, d: Def) -> set[Site] | None:
        """The call sites of `d`; None when the set can't be trusted to be complete."""
        mine = self.identity(d)
        if mine is None:
            return None
        found = set()
        for site in self.calls.get(d.name, []):
            got = self.resolve(site)
            if not got:
                return None  # unresolved: it might call d
            if mine in got:
                if len(got) > 1 or site.caller is None:
                    return None  # ambiguous, or a call from module level, which no function answers
                found.add(site)
            elif self.may_reach(site, d, got):
                return None  # it resolves elsewhere, but dispatch can take it to d
        refs = self.references(d)
        if refs is None or refs - {(s.path, s.line, s.col) for s in found}:
            return None  # a reference that is not a call site we saw: a missed call, or a function value
        return found

    def def_of(self, path: str, qualname: str, head: int | None = None) -> Def | None:
        return next(
            (d for d in self.defs if d.path == path and d.qualname == qualname and head in (None, d.head)), None
        )


def _site(path: str, line: int, col: int, caller: gold.Unit | None) -> Site:
    return Site(path, line, col, caller.qualname if caller else None, caller.head if caller else None)


def grep_hits(root: Path, word: str) -> set[tuple[str, int]]:
    """(path, line) of every `git grep -w word` hit. -z separates the fields with NULs, since matched lines may
    hold colons, form feeds or other characters that would split them."""
    out = workspace.git("grep", "-n", "-z", "-w", "-I", "--no-color", "-e", word, cwd=root, check=False)
    hits = set()
    for record in out.split("\n"):
        fields = record.split("\0", 2)
        if len(fields) == 3 and fields[1].isdigit():
            hits.add((fields[0], int(fields[1])))
    return hits


def grep_confirms(root: Path, word: str, places: set[tuple[str, int]]) -> bool:
    return places <= grep_hits(root, word)


# ---- Python: ast finds definitions, call sites and imports; jedi says what each call site refers to ----


def python_module(path: str) -> str | None:
    """The dotted module a file defines: src/requests/utils.py -> requests.utils."""
    if not path.endswith(".py"):
        return None
    parts = path[:-3].split("/")
    if parts[0] in ("src", "lib") and len(parts) > 1:
        parts = parts[1:]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) or None


def _imported(tree: ast.AST, rel: str) -> list[tuple[str, int]]:
    """(module, line) for every module an import statement in the file names, relative imports resolved:
    `from a.b import c` names a.b and a.b.c (c may be a submodule); `import a.b` names a.b."""
    own = python_module(rel) or ""
    package = own if rel.endswith("__init__.py") else own.rpartition(".")[0]
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [(al.name, al.lineno) for al in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".") if package else []
                parts = parts[: len(parts) - node.level + 1] if node.level > 1 else parts
                base = ".".join(parts + ([node.module] if node.module else []))
            else:
                base = node.module or ""
            out.append((base, node.lineno))
            out += [(f"{base}.{al.name}", al.lineno) for al in node.names if al.name != "*"]
    return out


def _base_names(node: ast.ClassDef) -> set[str]:
    """The simple names of a class's bases: `Field`, `fields.Field` and `typing.Mapping[str, str]` alike."""
    out = set()
    for b in node.bases:
        b = b.value if isinstance(b, ast.Subscript) else b
        if isinstance(b, ast.Attribute):
            out.add(b.attr)
        elif isinstance(b, ast.Name):
            out.add(b.id)
    return out


def _reachable(links: dict[str, set[str]], start: str) -> set[str]:
    """Every class `links` leads to from `start`, transitively: its ancestors, given each class's bases."""
    seen: set[str] = set()
    todo = [start]
    while todo:
        for c in links.get(todo.pop(), ()):
            if c not in seen:
                seen.add(c)
                todo.append(c)
    return seen


def python_analysis(root: Path) -> Analysis:
    import jedi

    a = Analysis(root)
    sources: dict[str, str] = {}
    imports: dict[str, list[tuple[str, int]]] = {}
    bases: dict[str, set[str]] = defaultdict(set)  # class name -> its bases' names, by simple name across the repo
    for rel in [f for f in workspace.git("ls-files", "*.py", cwd=root).splitlines() if f]:
        src = (root / rel).read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        sources[rel] = src
        imports[rel] = _imported(tree, rel)
        lines = src.split("\n")
        units = gold.python_units(src)
        test = gold.is_test_path(rel, "python")
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                top = gold.outermost(units, node.lineno)
                if top is None or top.head != node.lineno:
                    continue  # nested: only its parent calls it
                text = lines[node.lineno - 1]
                col = text.index(node.name, text.index("def") + 3)
                a.defs.append(Def(rel, top.qualname, node.lineno, col, test, node.lineno))
            elif isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Name):
                    name, line, bcol = f.id, f.lineno, f.col_offset
                elif isinstance(f, ast.Attribute) and f.end_lineno is not None and f.end_col_offset is not None:
                    name, line, bcol = f.attr, f.end_lineno, f.end_col_offset - len(f.attr.encode())
                else:
                    continue
                col = len(lines[line - 1].encode()[:bcol].decode("utf-8", "replace"))
                a.calls[name].append(_site(rel, line, col, gold.outermost(units, line)))
            elif isinstance(node, ast.ClassDef):
                bases[node.name] |= _base_names(node)
    subclasses: dict[str, set[str]] = defaultdict(set)
    for cls, parents in bases.items():
        for b in parents:
            subclasses[b].add(cls)
    methods: dict[str, set[str]] = defaultdict(set)  # class name -> the names of the methods it defines
    for d in a.defs:
        if "." in d.qualname:
            methods[d.qualname.split(".")[-2]].add(d.name)

    for rel in sources:
        module = python_module(rel)
        in_package = rel.endswith("__init__.py") or (root / rel).with_name("__init__.py").exists()
        if module and in_package and not gold.is_test_path(rel, "python"):
            found: dict[str, list[int]] = defaultdict(list)
            for other, names in imports.items():
                for name, line in names:
                    if name == module and other != rel:
                        found[other].append(line)
            a.modules.append(Module(rel, f"the module `{module}`", module.split(".")[-1], dict(found)))

    paths = sysconfig.get_paths()
    src_dir = root / "src"
    # the repo's own code and the standard library only: never an installed copy of the package
    sys_path = [str(src_dir if src_dir.is_dir() else root)] + sorted(
        {paths["stdlib"], paths["platstdlib"], os.path.join(paths["stdlib"], "lib-dynload")}
    )
    project = jedi.Project(str(root), sys_path=sys_path, smart_sys_path=False)
    scripts: dict[str, jedi.Script] = {}
    memo: dict[Site, set | None] = {}
    owners: dict[Site, set[str]] = {}  # the classes whose methods a site resolves to

    def resolve(site: Site) -> set | None:
        if site not in memo:
            try:
                names = script(site.path).goto(site.line, site.col, follow_imports=True)
            except Exception:
                names = []
            got = set()
            owners[site] = set()
            for n in names:
                try:
                    where = str(Path(n.module_path).relative_to(root)) if n.module_path else "<builtin>"
                except ValueError:
                    where = "<external>"
                got.add((where, n.line))
                try:
                    parent = n.parent()
                except Exception:
                    parent = None
                if parent is not None and parent.type == "class":
                    owners[site].add(parent.name)
            memo[site] = got or None
        return memo[site]

    assigned: dict[Site, set[str]] = {}

    def receiver_types(site: Site) -> set[str]:
        """The classes jedi infers for every assignment of a call's receiver (`x` in `x.m()`, `h` in `self.h.m()`):
        in the receiver's own scope for a local, anywhere in the file for an attribute."""
        if site not in assigned:
            assigned[site] = set()
            line = sources[site.path].split("\n")[site.line - 1]
            end = start = site.col - 1
            while start > 0 and (line[start - 1].isalnum() or line[start - 1] == "_"):
                start -= 1
            if 0 <= start < end and line[end] == ".":
                try:
                    s = script(site.path)
                    here = s.goto(site.line, start)
                    defs = [r for r in s.get_references(site.line, start, scope="file") if r.is_definition()]
                    if here and not (start and line[start - 1] == "."):  # a local: its scope's assignments
                        scope = here[0].parent()
                        defs = [r for r in defs if (p := r.parent()) and scope and p.line == scope.line]
                    assigned[site] = {t.name for r in defs for t in r.infer()}
                except Exception:
                    pass
        return assigned[site]

    def may_reach(site: Site, d: Def, got: set) -> bool:
        """Whether dispatch can take a call jedi resolved elsewhere to d, a method: the call resolves to a base
        class's method (`self.run()` in Base, `m.keys()` on a typing.Mapping), or its receiver is also assigned
        an instance of d's class somewhere jedi's typing didn't follow (pytest's `rewrite_hook`)."""
        if "." not in d.qualname:
            return False  # a function: nothing dispatches to it
        cls = d.qualname.split(".")[-2]
        if owners.get(site, set()) & _reachable(bases, cls):
            return True
        if not any(where in sources for where, _ in got):
            return False  # a builtin's or a library's own method, not a base class's
        inheriting = {c for c in _reachable(subclasses, cls) if d.name not in methods.get(c, ())}
        return bool(receiver_types(site) & ({cls} | inheriting))

    import_lines = {rel: gold.python_import_lines(src) for rel, src in sources.items()}
    refs_memo: dict[Def, set | None] = {}

    def script(rel: str) -> jedi.Script:
        if rel not in scripts:
            scripts[rel] = jedi.Script(sources[rel], path=str(root / rel), project=project)
        return scripts[rel]

    def references(d: Def) -> set | None:
        """Where jedi sees d used, except its definition and import lines. A use that is not a call (a callback,
        `map(d, ...)`) would make "who calls it" ambiguous, so `callers` rejects any that aren't call sites."""
        if d not in refs_memo:
            try:
                found = script(d.path).get_references(d.line, d.col, include_builtins=False)
            except Exception:
                refs_memo[d] = None
                return None
            out = set()
            for r in found:
                try:
                    path = str(Path(r.module_path).relative_to(root)) if r.module_path else None
                except ValueError:
                    path = None
                if path is None or path not in sources or r.line is None:
                    continue
                if (path, r.line) == (d.path, d.line) or r.line in import_lines[path]:
                    continue
                out.add((path, r.line, r.column))
            refs_memo[d] = out
        return refs_memo[d]

    a.resolve = resolve
    a.identity = lambda d: (d.path, d.line)
    a.references = references
    a.may_reach = may_reach
    return a


# ---- Rust: tree-sitter finds definitions, call sites and `use` declarations; SCIP says what each refers to ----


def scip_index(root: Path, repo: str, commit: str, cache: Path) -> Path:
    """rust-analyzer's SCIP index of a worktree, made once (about a minute for fd)."""
    out = cache / "scip" / f"{workspace.slug(repo)}@{commit[:12]}.scip"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        exe = workspace.rust_analyzer(cache)
        env = {k: os.environ[k] for k in ("HOME", "USER", "TMPDIR") if k in os.environ}
        env.update(setups.rust_env(cache, repo))
        tmp = out.with_suffix(".part")
        subprocess.run(
            [str(exe), "scip", str(root), "--output", str(tmp)], env=env, check=True, capture_output=True, timeout=3600
        )
        tmp.rename(out)
    return out


def _callee(node):
    """The identifier a call expression calls: f, x.f, T::f or f::<T>."""
    if node is None:
        return None
    if node.type == "identifier":
        return node
    if node.type == "field_expression":
        return node.child_by_field_name("field")
    if node.type == "scoped_identifier":
        return node.child_by_field_name("name")
    if node.type == "generic_function":
        return _callee(node.child_by_field_name("function"))
    return None


def _macro_call(ident) -> bool:
    """An identifier in a macro's arguments followed by a parenthesised token tree: `f(x)` in `assert_eq!(f(x), 1)`.
    tree-sitter leaves macro arguments as token trees, so these calls are no call_expression."""
    nxt = ident.next_sibling
    return nxt is not None and nxt.type == "token_tree" and nxt.child_count > 0 and nxt.children[0].type == "("


def _mod_file(path: str, name: str, root: Path) -> str | None:
    """The file `mod name;` in `path` loads: <dir>/name.rs or <dir>/name/mod.rs."""
    p = Path(path)
    base = p.parent if p.name in ("mod.rs", "lib.rs", "main.rs") else p.parent / p.stem
    for cand in (base / f"{name}.rs", base / name / "mod.rs"):
        if (root / cand).is_file():
            return str(cand)
    return None


def rust_analysis(root: Path, index: Path) -> Analysis:
    from .. import scip_pb2

    idx = scip_pb2.Index()
    idx.ParseFromString(index.read_bytes())
    at: dict[tuple[str, int, int], set[str]] = defaultdict(set)  # (path, line, column) -> symbols there
    where: dict[str, list[tuple[str, int, int, bool]]] = defaultdict(list)  # symbol -> (path, line, col, is_def)
    for doc in idx.documents:
        for o in doc.occurrences:
            line, col = o.range[0] + 1, o.range[1]
            at[(doc.relative_path, line, col)].add(o.symbol)
            where[o.symbol].append(
                (doc.relative_path, line, col, bool(o.symbol_roles & scip_pb2.SymbolRole.Definition))
            )
    a = Analysis(root)
    uses: dict[str, list[tuple[int, int]]] = {}
    comments: dict[str, list[tuple[tuple[int, int], tuple[int, int]]]] = defaultdict(list)  # 1-based line, column
    mods: list[tuple[str, str, str]] = []  # (symbol, module file, module name)
    for rel in [f for f in workspace.git("ls-files", "*.rs", cwd=root).splitlines() if f]:
        src = (root / rel).read_text(encoding="utf-8", errors="replace")
        data = src.encode()
        units = gold.rust_units(src)
        test_file = gold.is_test_path(rel, "rust")
        uses[rel] = []
        stack = [(gold.rust_parser().parse(data).root_node, False)]
        while stack:
            n, in_macro = stack.pop()
            if n.type == "macro_definition":
                continue  # a macro_rules! body is a template, not code
            stack.extend((c, in_macro or n.type == "macro_invocation") for c in n.children)
            if in_macro and n.type == "identifier" and n.parent is not None and n.parent.type == "token_tree":
                if _macro_call(n):
                    line = n.start_point[0] + 1
                    caller = gold.outermost(units, line, FUNCTION_LIKE)
                    a.calls[data[n.start_byte : n.end_byte].decode()].append(_site(rel, line, n.start_point[1], caller))
            elif n.type in ("line_comment", "block_comment"):
                start, end = n.start_point, n.end_point
                comments[rel].append(((start[0] + 1, start[1]), (end[0] + 1, end[1])))
            elif n.type == "function_item":
                name = n.child_by_field_name("name")
                top = gold.outermost(units, name.start_point[0] + 1, FUNCTION_LIKE)
                if top is not None and top.head == n.start_point[0] + 1:
                    test = test_file or top.kind == "test-fn"
                    a.defs.append(Def(rel, top.qualname, name.start_point[0] + 1, name.start_point[1], test, top.head))
            elif n.type == "call_expression":
                ident = _callee(n.child_by_field_name("function"))
                if ident is not None:
                    line = ident.start_point[0] + 1
                    caller = gold.outermost(units, line, FUNCTION_LIKE)
                    called = data[ident.start_byte : ident.end_byte].decode()
                    a.calls[called].append(_site(rel, line, ident.start_point[1], caller))
            elif n.type == "use_declaration":
                uses[rel].append((n.start_point[0] + 1, n.end_point[0] + 1))
            elif n.type == "mod_item" and n.child_by_field_name("body") is None:
                name = n.child_by_field_name("name")
                word = data[name.start_byte : name.end_byte].decode()
                symbols = at.get((rel, name.start_point[0] + 1, name.start_point[1]), set())
                target = _mod_file(rel, word, root)
                if len(symbols) == 1 and target:
                    mods.append((next(iter(symbols)), target, word))

    def in_use(path: str, line: int) -> bool:
        return any(s <= line <= e for s, e in uses.get(path, []))

    for symbol, file, word in mods:
        found: dict[str, list[int]] = defaultdict(list)
        for path, line, _, _ in where.get(symbol, []):
            if path != file and in_use(path, line):
                found[path].append(line)
        a.modules.append(Module(file, f"the module defined in `{file}`", word, dict(found)))

    def identity(d: Def):
        symbols = at.get((d.path, d.line, d.col), set())
        return next(iter(symbols)) if len(symbols) == 1 else None

    def references(d: Def) -> set | None:
        """Where rust-analyzer saw d used, except its definition, `use` lines and comments (intra-doc links)."""
        symbol = identity(d)
        if symbol is None:
            return None
        return {
            (path, line, col)
            for path, line, col, is_def in where.get(symbol, [])
            if not is_def
            and not in_use(path, line)
            and not any(s <= (line, col) < e for s, e in comments.get(path, []))
        }

    def may_reach(site: Site, d: Def, got: set) -> bool:
        """Whether a call resolved elsewhere can reach d, a method of a trait implementation: a call through a
        generic or `dyn` resolves to the trait's own method (`Greet#hi().` for `impl#[Dog][Greet]hi().`)."""
        implemented = re.search(r"\]\[`?(\w+)[^\]]*\]" + re.escape(d.name) + r"\(\)\.$", identity(d) or "")
        if implemented is None:
            return False
        trait = re.compile(rf"(?:^|[ /]){implemented.group(1)}#{re.escape(d.name)}\(\)\.$")
        return any(trait.search(symbol) for symbol in got)

    a.resolve = lambda site: at.get((site.path, site.line, site.col)) or None
    a.identity = identity
    a.references = references
    a.may_reach = may_reach
    return a


# ---- questions ----


def _display(d: Def, lang: str) -> str:
    return d.qualname.replace(".", "::") if lang == "rust" else d.qualname


def _entries(sites: set[Site]) -> set[str]:
    return {f"{s.path}:{s.caller}" for s in sites}


def callers_question(a: Analysis, d: Def, lang: str) -> tuple[str, tuple[str, ...]] | None:
    sites = a.callers(d)
    if sites is None or not grep_confirms(a.root, d.name, {(s.path, s.line) for s in sites}):
        return None
    key = tuple(sorted(_entries(sites)))
    if not CALLERS_SIZE[0] <= len(key) <= CALLERS_SIZE[1]:
        return None
    return (
        f"Which functions call `{_display(d, lang)}`, defined in `{d.path}`? List every one, {EVERY}.",
        key,
    )


def two_hop_question(a: Analysis, d: Def, lang: str) -> tuple[str, tuple[str, ...]] | None:
    direct = a.callers(d)
    if direct is None or not grep_confirms(a.root, d.name, {(s.path, s.line) for s in direct}):
        return None
    key = _entries(direct)
    for s in direct:
        mid = a.def_of(s.path, s.caller, s.caller_head)
        up = a.callers(mid) if mid else None
        if up is None or not grep_confirms(a.root, mid.name, {(u.path, u.line) for u in up}):
            return None
        key |= _entries(up)
    if not TWO_HOP_SIZE[0] <= len(key) <= TWO_HOP_SIZE[1] or len(key) == len(_entries(direct)):
        return None
    return (
        f"Which functions call `{_display(d, lang)}`, defined in `{d.path}`, either directly or through one "
        f"intermediate function? List every one, {EVERY}.",
        tuple(sorted(key)),
    )


def importers_question(a: Analysis, m: Module, lang: str) -> tuple[str, tuple[str, ...]] | None:
    places = {(path, line) for path, lines in m.importers.items() for line in lines}
    if not IMPORTERS_SIZE[0] <= len(m.importers) <= IMPORTERS_SIZE[1] or not grep_confirms(a.root, m.grep, places):
        return None
    how = "or import names from it" if lang == "python" else "or import items from it, with a `use` declaration"
    return f"Which files import {m.label}, {how}? List every one, including test files.", tuple(sorted(m.importers))


def common_names(a: Analysis) -> set[str]:
    """Names defined more than once in the repo, whose callers grep over-reports."""
    count: dict[str, int] = defaultdict(int)
    for d in a.defs:
        count[d.name] += 1
    return {n for n, k in count.items() if k > 1}


def pick(
    a: Analysis,
    lang: str,
    make: Callable,
    common: int,
    unique: int,
    rng: random.Random,
    taken: set | None = None,
    asked: Collection[Def] = (),
) -> list[tuple]:
    """(def, question, key, is_common) for up to `common` common-name targets and `unique` others; a common one
    that can't be found is replaced by a unique one. A question whose key is in `taken`, or repeats one picked
    here, is passed over, and so is a target in `asked`: the repo has a question about it already."""
    taken = set(taken or ())
    names = common_names(a)
    seen: dict[tuple[str, str], int] = defaultdict(int)
    for d in a.defs:
        seen[(d.path, d.qualname)] += 1
    targets = sorted(
        (d for d in a.defs if not d.test and not d.name.startswith("__") and a.calls.get(d.name)),
        key=lambda d: (d.path, d.qualname, d.line),
    )
    rng.shuffle(targets)
    picked: list[tuple] = []
    for want_common, want in ((True, common), (False, None)):
        want = want if want is not None else unique + common - len(picked)
        for d in targets:
            if sum(1 for p in picked if p[3] == want_common) >= want:
                break
            if seen[(d.path, d.qualname)] > 1:
                continue  # two functions of that name in the file: a question about it would be ambiguous
            if (d.name in names) == want_common and d not in asked and all(p[0] != d for p in picked):
                made = make(a, d, lang)
                if made and made[1] not in taken:
                    taken.add(made[1])
                    picked.append((d, *made, want_common))
    return picked


def repo_tasks(
    pin: config.PinnedRepo, cache: Path, seed: int, callers: int = 2, two_hop: int = 2, importers: int = 1
) -> list[Task]:
    """Up to `callers` and `two_hop` questions (half about names defined more than once) and `importers` more.
    A question whose answer equals an earlier one's is dropped: the pilot's two requests two-hop questions had the
    same answer, which counts one finding twice."""
    root = workspace.worktree(pin.repo, pin.commit, "build", cache)
    if pin.lang == "python":
        import jedi

        a, tool = python_analysis(root), f"jedi {jedi.__version__}"
    else:
        a, tool = rust_analysis(root, scip_index(root, pin.repo, pin.commit, cache)), config.RUST_ANALYZER_VERSION
    source = f"{tool} on {pin.repo}@{pin.tag}"
    short = pin.repo.split("/")[1]
    rng = random.Random(f"{seed}:{pin.repo}")
    tasks = []

    answers: set[tuple[str, ...]] = set()
    asked: set[Def] = set()  # a target gets one question: its callers, or its callers two hops out

    def add(kind: str, label: str, question: str, key: tuple[str, ...], answer: str, stratum: str) -> bool:
        if key in answers:
            return False
        answers.add(key)
        tasks.append(
            Task(
                id=f"{short}-{kind}-{label}",
                kind="structural",
                lang=pin.lang,
                repo=pin.repo,
                commit=pin.commit,
                prompt=prompts.structural(question, answer, pin.lang),
                gold=key,
                source=source,
                answer=answer,
                stratum=stratum,
            )
        )
        return True

    for kind, make, n in (("callers", callers_question, callers), ("two-hop", two_hop_question, two_hop)):
        common = n // 2 + n % 2
        for d, question, key, is_common in pick(a, pin.lang, make, common, n - common, rng, answers, asked):
            if add(kind, d.qualname, question, key, "functions", f"{kind}:{'common' if is_common else 'unique'}"):
                asked.add(d)
    modules = sorted(a.modules, key=lambda m: m.file)
    rng.shuffle(modules)
    made_importers = 0
    for m in modules:
        if made_importers == importers:
            break
        made = importers_question(a, m, pin.lang)
        if made and add("importers", m.file.replace("/", "."), *made, "files", "importers"):
            made_importers += 1
    return tasks


def build(
    seed: int = config.SEED, profile: config.Profile = config.PROFILES["pilot"], cache: Path | None = None, log=print
) -> list[Task]:
    cache = cache or config.cache_dir()
    tasks = []
    for pin in profile.repos:
        made = repo_tasks(pin, cache, seed, *profile.questions)
        log(f"{pin.repo}@{pin.tag}: {len(made)} questions")
        tasks += made
    return tasks
