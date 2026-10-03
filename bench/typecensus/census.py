"""Step 2 of the receiver-gap census: classify how each gap call's receiver is bound, simulate
syntax-only type rules, and score them against jedi (from jedi_truth.py).

usage (from /Users/ccf/git/duckgrep):
  uv run python <scratch>/ti/census.py <repo> <truth.jsonl> [--examples out.txt] [--json out.json]

Two rule sets are simulated:
  strict   - a receiver name needs exactly one binding in its scope; types come only from
             constructors (Foo(...)), annotations (params, AnnAssign, class-level fields, return
             annotations of functions/methods/properties), imports and class bases.
  lenient  - strict, plus: bindings to None are ignored and several bindings may agree; an
             unannotated function/classmethod is typed by its return statements when they agree;
             a pytest-fixture parameter is typed by the (unique) fixture's return.
A method is looked up on the inferred class and then on its in-repo bases (approximate MRO).
A receiver typed as external (external import, builtin, literal, external constructor/factory)
is marked "not in repo".
"""

import argparse
import ast
import builtins
import collections
import json
import os
import sys

from duckgrep import query as q

BUILTIN_CLASSES = {n for n in dir(builtins) if isinstance(getattr(builtins, n), type)}
BUILTIN_FUNCS = {n for n in dir(builtins) if callable(getattr(builtins, n))} - BUILTIN_CLASSES
# callables whose result type depends on their arguments: never conclude "external" from them
GENERIC = {
    "getattr",
    "next",
    "max",
    "min",
    "copy",
    "deepcopy",
    "reduce",
    "choice",
    "sample",
    "loads",
    "load",
    "import_module",
    "get",
    "pop",
    "setdefault",
    "field",
    "partial",
    "wraps",
    "run",
    "gather",
    "wait_for",
    "run_until_complete",
    "to_thread",
    "run_in_executor",
    "proxy",
    "ref",
    "cache",
    "lru_cache",
    "cached_property",
    "property",
    "staticmethod",
    "classmethod",
    "super",
    "type",
    "vars",
    "cast",
    "replace",
    "lazy",
    "SimpleLazyObject",
    "import_string",
    "locate",
    "eval",
    "exec",
    "__import__",
    "Mock",
    "MagicMock",
    "AsyncMock",
    "patch",
    "create_autospec",
    "PropertyMock",
    "NonCallableMock",
    "spec",
    "object",
    "resolve",
    "apply",
    "dumps",
}
WRAPPERS = {"Optional", "Annotated", "ClassVar", "Final", "Required", "NotRequired", "ReadOnly", "InitVar"}
LITERALS = (
    ast.Constant,
    ast.JoinedStr,
    ast.List,
    ast.Dict,
    ast.Set,
    ast.Tuple,
    ast.ListComp,
    ast.DictComp,
    ast.SetComp,
    ast.GeneratorExp,
)
SCOPES = (
    ast.Module,
    ast.ClassDef,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.Lambda,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)
FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)

EXT_INST = ("ext", "inst")
EXT_CLASS = ("ext", "class")
EXT_FUNC = ("ext", "func")
EXT_MOD = ("ext", "mod")


# ---------------------------------------------------------------- repo model


class Mod:
    def __init__(self, path, src):
        self.path = path
        self.tree = ast.parse(src)
        self.calls = {}  # (line, col of attr name) -> (Call, scopes)
        self.parent = {}  # scope node -> parent scope list
        self._index()

    def _index(self):
        def visit(node, scopes):
            for ch in ast.iter_child_nodes(node):
                if isinstance(ch, ast.Call) and isinstance(ch.func, ast.Attribute):
                    f = ch.func
                    self.calls[(f.end_lineno, f.end_col_offset - len(f.attr.encode()))] = (ch, scopes)
                if isinstance(ch, SCOPES):
                    self.parent[id(ch)] = scopes
                    visit(ch, scopes + (ch,))
                else:
                    visit(ch, scopes)

        self.parent[id(self.tree)] = ()
        visit(self.tree, (self.tree,))


class Repo:
    def __init__(self, root, paths):
        self.root = root
        self.mods = {}
        for p in paths:
            try:
                self.mods[p] = Mod(p, open(os.path.join(root, p), "rb").read())
            except (SyntaxError, ValueError, OSError):
                pass
        self.byname = {}
        self.modname = {}
        for p in sorted(self.mods):
            n = self._modname(p)
            self.modname[p] = n
            self.byname.setdefault(n, p)
        self.tops = {n.split(".")[0] for n in self.byname}
        self.fixtures = collections.defaultdict(list)
        for m in self.mods.values():
            for node in ast.walk(m.tree):
                if isinstance(node, FUNCS) and any("fixture" in ast.unparse(d) for d in node.decorator_list):
                    self.fixtures[node.name].append((m, node))
        self._bind = {}

    def _modname(self, p):
        parts = p[:-3].split("/")
        if parts[-1] == "__init__":
            parts = parts[:-1]
        d = os.path.dirname(p)
        start = len(p.split("/")) - 1  # index of the file component
        # climb while the directory is a package
        comps = p.split("/")[:-1]
        i = len(comps)
        while i > 0 and os.path.exists(os.path.join(self.root, *comps[:i], "__init__.py")):
            i -= 1
        del d, start
        return ".".join(parts[i:]) or parts[-1]

    def package(self, mod):
        n = self.modname[mod.path]
        return n if mod.path.endswith("__init__.py") else n.rpartition(".")[0]

    # bindings of a scope: name -> [(kind, node, extra)]
    def bindings(self, scope):
        k = id(scope)
        if k in self._bind:
            return self._bind[k]
        b = collections.defaultdict(list)

        def target(t, kind, node, extra=None):
            if isinstance(t, ast.Name):
                b[t.id].append((kind, node, extra))
            elif isinstance(t, (ast.Tuple, ast.List)):
                for e in t.elts:
                    target(e, "unpack" if kind == "assign" else kind, node, extra)
            elif isinstance(t, ast.Starred):
                target(t.value, "unpack" if kind == "assign" else kind, node, extra)

        def walk(n):
            for ch in ast.iter_child_nodes(n):
                if isinstance(ch, ast.Assign):
                    for t in ch.targets:
                        target(t, "assign", ch, ch.value)
                elif isinstance(ch, ast.AnnAssign):
                    target(ch.target, "annassign", ch, ch.value)
                elif isinstance(ch, ast.AugAssign):
                    target(ch.target, "aug", ch)
                elif isinstance(ch, (ast.For, ast.AsyncFor)):
                    target(ch.target, "for", ch)
                elif isinstance(ch, (ast.With, ast.AsyncWith)):
                    for it in ch.items:
                        if it.optional_vars is not None:
                            target(it.optional_vars, "with", ch, it.context_expr)
                elif isinstance(ch, ast.ExceptHandler) and ch.name:
                    b[ch.name].append(("except", ch, ch.type))
                elif isinstance(ch, ast.NamedExpr):
                    target(ch.target, "assign", ch, ch.value)
                elif isinstance(ch, ast.Import):
                    for a in ch.names:
                        b[a.asname or a.name.split(".")[0]].append(("import", ch, a))
                elif isinstance(ch, ast.ImportFrom):
                    for a in ch.names:
                        if a.name != "*":
                            b[a.asname or a.name].append(("importfrom", ch, a))
                        else:
                            b["*"].append(("star", ch, a))
                elif isinstance(ch, (ast.Global, ast.Nonlocal)):
                    for nm in ch.names:
                        b[nm].append(("global" if isinstance(ch, ast.Global) else "nonlocal", ch, None))
                elif isinstance(ch, (ast.MatchAs, ast.MatchStar)) and ch.name:
                    b[ch.name].append(("match", ch, None))
                if isinstance(ch, FUNCS):
                    b[ch.name].append(("def", ch, None))
                    continue
                if isinstance(ch, ast.ClassDef):
                    b[ch.name].append(("class", ch, None))
                    continue
                if isinstance(ch, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                    continue
                walk(ch)

        if isinstance(scope, (*FUNCS, ast.Lambda)):
            a = scope.args
            for i, arg in enumerate(a.posonlyargs + a.args):
                b[arg.arg].append(("param", arg, i))
            for arg in a.kwonlyargs:
                b[arg.arg].append(("param", arg, None))
            if a.vararg:
                b[a.vararg.arg].append(("vararg", a.vararg, None))
            if a.kwarg:
                b[a.kwarg.arg].append(("vararg", a.kwarg, None))
            if isinstance(scope, ast.Lambda):
                walk(ast.Expr(scope.body))
            else:
                walk(scope)
        elif isinstance(scope, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            for g in scope.generators:
                target(g.target, "for", g)
        else:
            walk(scope)
        self._bind[k] = b
        return b


# ---------------------------------------------------------------- inference


class Ctx:
    __slots__ = ("mod", "scopes")

    def __init__(self, mod, scopes):
        self.mod, self.scopes = mod, tuple(scopes)


class Infer:
    def __init__(self, repo, lenient):
        self.r = repo
        self.lenient = lenient
        self.memo = {}
        self.busy = set()

    # -- helpers
    def enclosing_method(self, scopes):
        """(FunctionDef, ClassDef) of the innermost function directly inside a class."""
        for i in range(len(scopes) - 1, 0, -1):
            if isinstance(scopes[i], FUNCS) and isinstance(scopes[i - 1], ast.ClassDef):
                return scopes[i], scopes[i - 1], i
        return None, None, None

    def class_ctx(self, mod, cls):
        return Ctx(mod, mod.parent[id(cls)])

    def def_ctx(self, mod, fn):
        return Ctx(mod, mod.parent[id(fn)])

    def lookup(self, name, ctx):
        sc = ctx.scopes
        for i in range(len(sc) - 1, -1, -1):
            s = sc[i]
            if isinstance(s, ast.ClassDef) and i != len(sc) - 1:
                continue
            bs = self.r.bindings(s).get(name)
            if bs:
                kinds = {b[0] for b in bs}
                if "global" in kinds:
                    return 0, self.r.bindings(sc[0]).get(name) or []
                if kinds == {"nonlocal"}:
                    continue
                return i, [b for b in bs if b[0] not in ("global", "nonlocal")]
        return None, None

    # -- modules / imports
    def resolve_modname(self, name):
        if name in self.r.byname:
            return ("mod", self.r.mods[self.r.byname[name]])
        if name.split(".")[0] in self.r.tops:
            return None  # an in-repo package, but a module we can't see
        return EXT_MOD

    def import_binding(self, kind, node, alias, mod):
        if kind == "import":
            name = alias.name if alias.asname else alias.name.split(".")[0]
            return self.resolve_modname(name)
        base = node.module or ""
        if node.level:
            pkg = self.r.package(mod).split(".") if self.r.package(mod) else []
            up = node.level - 1
            pkg = pkg[: len(pkg) - up] if up else pkg
            base = ".".join(pkg + ([node.module] if node.module else []))
        full = f"{base}.{alias.name}" if base else alias.name
        if full in self.r.byname:
            return ("mod", self.r.mods[self.r.byname[full]])
        bm = self.resolve_modname(base) if base else None
        if bm is None:
            return None
        if bm[0] == "ext":
            return EXT_CLASS if alias.name[:1].isupper() else EXT_FUNC
        return self.module_member(bm[1], alias.name)

    def module_member(self, mod, name, depth=0):
        if depth > 8:
            return None
        bs = self.r.bindings(mod.tree).get(name)
        ctx = Ctx(mod, (mod.tree,))
        if bs:
            return self.binding_type(bs, ctx, 0, module_level=True)
        for _, node, _a in self.r.bindings(mod.tree).get("*", []):
            t = self.import_binding("importfrom", node, ast.alias(name=name), mod)
            if t is not None:
                return t
        return None

    # -- bindings
    def binding_type(self, bs, ctx, idx, module_level=False):
        scope_ctx = Ctx(ctx.mod, ctx.scopes[: idx + 1])
        if module_level and len(bs) > 1:
            # module level: imports/defs inside try/except or TYPE_CHECKING; take the defining ones
            defs = [b for b in bs if b[0] in ("class", "def", "import", "importfrom")]
            if defs and all(b[0] in ("class", "def", "import", "importfrom") or self._is_none(b) for b in bs):
                ts = {self._key(self.one_binding(b, scope_ctx)) for b in defs}
                if len(ts) == 1:
                    return self.one_binding(defs[0], scope_ctx)
        if len(bs) != 1:
            if not self.lenient:
                return None
            live = [b for b in bs if not self._is_none(b) and b[0] != "aug"]
            if not live:
                return None
            ts = [self.one_binding(b, scope_ctx) for b in live]
            ks = {self._key(t) for t in ts}
            if len(ks) == 1 and ts[0] is not None:
                return ts[0]
            return None
        return self.one_binding(bs[0], scope_ctx)

    @staticmethod
    def _is_none(b):
        return (
            b[0] in ("assign", "annassign")
            and isinstance(b[2], ast.Constant)
            and b[2].value is None
            and b[0] == "assign"
        )

    @staticmethod
    def _key(t):
        if t is None:
            return None
        return tuple(id(x) if isinstance(x, (ast.AST, Mod)) else x for x in t[:2])

    def one_binding(self, b, ctx):
        kind, node, extra = b
        key = (id(node), kind, id(extra) if isinstance(extra, ast.AST) else extra, self.lenient)
        if key in self.memo:
            return self.memo[key]
        if key in self.busy:
            return None
        self.busy.add(key)
        try:
            t = self._one_binding(kind, node, extra, ctx)
        finally:
            self.busy.discard(key)
        self.memo[key] = t
        return t

    def _one_binding(self, kind, node, extra, ctx):
        mod = ctx.mod
        if kind == "param":
            fn = ctx.scopes[-1]
            if (
                extra == 0
                and len(ctx.scopes) > 1
                and isinstance(ctx.scopes[-2], ast.ClassDef)
                and isinstance(fn, FUNCS)
            ):
                decos = {ast.unparse(d) for d in fn.decorator_list}
                if "staticmethod" in decos:
                    pass
                elif "classmethod" in decos:
                    return ("classobj", ctx.scopes[-2], mod)
                else:
                    return ("cls", ctx.scopes[-2], mod)
            if node.annotation is not None:
                return self.annotation(node.annotation, Ctx(mod, ctx.scopes[:-1]))
            if self.lenient:
                fx = self.r.fixtures.get(node.arg)
                if fx and len(fx) == 1:
                    fm, fnode = fx[0]
                    return self.func_return(fnode, fm, owner=None)
            return None
        if kind == "assign":
            return self.expr(extra, ctx)
        if kind == "annassign":
            return self.annotation(node.annotation, ctx)
        if kind in ("import", "importfrom"):
            return self.import_binding(kind, node, extra, mod)
        if kind == "class":
            return ("classobj", node, mod)
        if kind == "def":
            return ("func", node, mod)
        if kind == "except" and extra is not None and not isinstance(extra, ast.Tuple):
            t = self.expr(extra, ctx)
            return self.instance_of(t)
        return None

    def instance_of(self, t):
        if t is None:
            return None
        if t[0] == "classobj":
            return ("cls", t[1], t[2])
        if t[0] == "ext" and t[1] == "class":
            return EXT_INST
        return None

    # -- annotations
    def annotation(self, a, ctx, depth=0):
        if a is None or depth > 6:
            return None
        if isinstance(a, ast.Constant):
            if isinstance(a.value, str):
                try:
                    return self.annotation(ast.parse(a.value, mode="eval").body, ctx, depth + 1)
                except SyntaxError:
                    return None
            return None
        if isinstance(a, ast.BinOp) and isinstance(a.op, ast.BitOr):
            return self._union([a.left, a.right], ctx, depth)
        if isinstance(a, ast.Subscript):
            head = a.value.attr if isinstance(a.value, ast.Attribute) else getattr(a.value, "id", None)
            sl = a.slice
            if head in WRAPPERS:
                first = sl.elts[0] if isinstance(sl, ast.Tuple) else sl
                return self.annotation(first, ctx, depth + 1)
            if head == "Union":
                return self._union(sl.elts if isinstance(sl, ast.Tuple) else [sl], ctx, depth)
            if head in ("Type", "type"):
                t = self.annotation(sl, ctx, depth + 1)
                return ("classobj", t[1], t[2]) if t and t[0] == "cls" else (EXT_CLASS if t == EXT_INST else None)
            return self.annotation(a.value, ctx, depth + 1)
        if isinstance(a, ast.Name):
            if a.id in ("Any", "object", "Self") and a.id != "Self":
                return None
            if a.id == "Self":
                _, cls, _ = self.enclosing_method(ctx.scopes + (None,)) if False else (None, None, None)
                for s in reversed(ctx.scopes):
                    if isinstance(s, ast.ClassDef):
                        return ("cls", s, ctx.mod)
                return None
            i, bs = self.lookup(a.id, ctx)
            if bs and len(bs) == 1 and bs[0][0] == "assign" and isinstance(bs[0][2], ast.Call):
                fn = bs[0][2].func
                if (getattr(fn, "id", None) or getattr(fn, "attr", None)) in ("TypeVar", "NewType", "ParamSpec"):
                    return None
            if bs and bs[0][0] in ("assign", "annassign") and not isinstance(bs[0][2], ast.Call):
                return None  # a type alias (X = Union[...]) or a constant
            t = self.expr(a, ctx)
            return self._ann_value(t)
        if isinstance(a, ast.Attribute):
            return self._ann_value(self.expr(a, ctx))
        return None

    def _ann_value(self, t):
        if t is None:
            return None
        if t[0] == "classobj":
            return ("cls", t[1], t[2])
        if t[0] == "ext" and t[1] in ("class", "func"):
            return EXT_INST
        return None

    def _union(self, elts, ctx, depth):
        ts = []
        for e in elts:
            if isinstance(e, ast.Constant) and e.value is None or getattr(e, "id", None) == "None":
                continue
            ts.append(self.annotation(e, ctx, depth + 1))
        ks = {self._key(t) for t in ts}
        return ts[0] if len(ks) == 1 else None

    # -- expressions
    def expr(self, e, ctx, depth=0):
        if depth > 20 or e is None:
            return None
        if isinstance(e, ast.Name):
            i, bs = self.lookup(e.id, ctx)
            if bs is None:
                if e.id in BUILTIN_CLASSES:
                    return EXT_CLASS
                if e.id in BUILTIN_FUNCS:
                    return EXT_FUNC
                return None
            return self.binding_type(bs, ctx, i, module_level=(i == 0))
        if isinstance(e, ast.Attribute):
            return self.attr(self.expr(e.value, ctx, depth + 1), e.attr)
        if isinstance(e, ast.Call):
            return self.call(e, ctx, depth)
        if isinstance(e, LITERALS):
            return EXT_INST
        if isinstance(e, ast.BinOp) and isinstance(e.left, (ast.Constant, ast.JoinedStr)):
            return EXT_INST  # "..." % x, "..." + x
        return None

    def call(self, c, ctx, depth):
        f = c.func
        fname = getattr(f, "id", None) or getattr(f, "attr", None)
        if isinstance(f, ast.Name) and f.id == "super":
            cls = None
            if len(c.args) >= 1:
                t = self.expr(c.args[0], ctx, depth + 1)
                if t and t[0] == "classobj":
                    return ("super", t[1], t[2])
                return None
            for s in reversed(ctx.scopes):
                if isinstance(s, ast.ClassDef):
                    cls = s
                    break
            return ("super", cls, ctx.mod) if cls is not None else None
        if fname == "cast" and c.args:
            return self.annotation(c.args[0], ctx)
        if isinstance(f, ast.Name) and f.id == "type" and len(c.args) == 1:
            t = self.expr(c.args[0], ctx, depth + 1)
            return ("classobj", t[1], t[2]) if t and t[0] == "cls" else None
        ft = self.expr(f, ctx, depth + 1)
        if ft is None:
            return None
        if ft[0] == "classobj":
            return ("cls", ft[1], ft[2])
        if ft[0] == "ext":
            if ft[1] == "class":
                return None if fname in GENERIC else EXT_INST
            if ft[1] == "func":
                return None if fname in GENERIC else EXT_INST
            return None  # method of an external instance, or a module: result unknown
        if ft[0] == "func":
            return self.func_return(ft[1], ft[2], owner=None)
        if ft[0] == "method":
            return self.func_return(ft[1], ft[2], owner=ft[3])
        return None

    def func_return(self, fn, mod, owner):
        if fn.returns is not None:
            ctx = self.def_ctx(mod, fn)
            r = fn.returns
            nm = getattr(r, "id", None) or (r.value if isinstance(r, ast.Constant) else None)
            if nm == "Self" and owner is not None:
                return ("cls", owner[0], owner[1])
            t = self.annotation(r, ctx)
            if (
                t
                and t[0] == "cls"
                and owner is not None
                and isinstance(r, ast.Constant)
                and isinstance(ctx.scopes[-1], ast.ClassDef)
                and t[1] is ctx.scopes[-1]
            ):
                return ("cls", owner[0], owner[1])
            if t is None and isinstance(r, ast.Subscript):
                # Generator[X, ...] / Iterator[X] fixtures, Awaitable: leave unknown
                return None
            return t
        if not self.lenient:
            return None
        # lenient: unannotated -> the type its return/yield statements agree on
        decos = {ast.unparse(d) for d in fn.decorator_list}
        rets = []

        def walk(n):
            for ch in ast.iter_child_nodes(n):
                if isinstance(ch, (*FUNCS, ast.ClassDef, ast.Lambda)):
                    continue
                if isinstance(ch, ast.Return) and ch.value is not None:
                    rets.append(ch.value)
                if isinstance(ch, ast.Yield) and ch.value is not None:
                    rets.append(ch.value)
                walk(ch)

        walk(fn)
        if not rets:
            return None
        ctx = Ctx(mod, mod.parent[id(fn)] + (fn,))
        ts = []
        for rv in rets:
            if (
                isinstance(rv, ast.Call)
                and isinstance(rv.func, ast.Name)
                and "classmethod" in decos
                and fn.args.args
                and rv.func.id == fn.args.args[0].arg
                and owner is not None
            ):
                ts.append(("cls", owner[0], owner[1]))
            elif isinstance(rv, ast.Constant) and rv.value is None:
                continue
            else:
                ts.append(self.expr(rv, ctx))
        ks = {self._key(t) for t in ts}
        return ts[0] if ts and len(ks) == 1 else None

    # -- classes
    def bases(self, cls, mod):
        key = ("bases", id(cls))
        if key in self.memo:
            return self.memo[key]
        self.memo[key] = []
        out = []
        ctx = self.class_ctx(mod, cls)
        for b in cls.bases:
            if isinstance(b, ast.Subscript):
                b = b.value
            t = self.expr(b, ctx)
            if t and t[0] == "classobj":
                out.append((t[1], t[2]))
            elif t and t[0] == "ext":
                if getattr(b, "id", None) not in ("object", "Generic", "Protocol"):
                    out.append("EXT")
            else:
                out.append("UNKNOWN")
        self.memo[key] = out
        return out

    def mro(self, cls, mod):
        key = ("mro", id(cls))
        if key in self.memo:
            return self.memo[key]
        self.memo[key] = [(cls, mod)]
        seq = []

        def dfs(c, m, depth):
            seq.append((c, m))
            if depth > 30:
                return
            for b in self.bases(c, m):
                if isinstance(b, str):
                    seq.append(b)
                else:
                    dfs(b[0], b[1], depth + 1)

        dfs(cls, mod, 0)
        # keep the last occurrence of each class (approximates C3 for diamonds)
        seen, out = set(), []
        for x in reversed(seq):
            k = x if isinstance(x, str) else id(x[0])
            if isinstance(x, str) or k not in seen:
                seen.add(k)
                out.append(x)
        out.reverse()
        self.memo[key] = out
        return out

    def class_member(self, cls, mod, name, skip_self=False):
        """('found', cls, mod, binding) | 'EXT' | 'UNKNOWN' | None"""
        m = self.mro(cls, mod)
        if skip_self:
            m = m[1:]
        opaque = None
        for x in m:
            if isinstance(x, str):
                opaque = "UNKNOWN" if x == "UNKNOWN" or opaque == "UNKNOWN" else "EXT"
                continue
            c, cm = x
            bs = self.r.bindings(c).get(name)
            if bs:
                return ("found", c, cm, bs)
        return opaque

    def self_attrs(self, cls):
        key = ("selfattrs", id(cls))
        if key in self.memo:
            return self.memo[key]
        out = collections.defaultdict(list)
        for fn in cls.body:
            if not isinstance(fn, FUNCS):
                continue
            a = fn.args.posonlyargs + fn.args.args
            if not a:
                continue
            me = a[0].arg
            for n in ast.walk(fn):
                tgts = []
                if isinstance(n, ast.Assign):
                    tgts = [(t, n.value, None) for t in n.targets]
                elif isinstance(n, ast.AnnAssign):
                    tgts = [(n.target, n.value, n.annotation)]
                for t, v, ann in tgts:
                    if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == me:
                        out[t.attr].append((fn, v, ann))
        self.memo[key] = out
        return out

    def instance_attr(self, cls, mod, name):
        mem = self.class_member(cls, mod, name)
        if isinstance(mem, tuple):
            _, c, cm, bs = mem
            kinds = [b[0] for b in bs]
            if "def" in kinds:
                fn = [b for b in bs if b[0] == "def"][-1][1]
                decos = {ast.unparse(d).split(".")[-1] for d in fn.decorator_list}
                if decos & {"property", "cached_property"}:
                    return self.func_return(fn, cm, owner=(cls, mod))
                return ("method", fn, cm, (cls, mod))
            if "class" in kinds:
                return ("classobj", [b for b in bs if b[0] == "class"][-1][1], cm)
            ann = [b for b in bs if b[0] == "annassign"]
            if ann:
                return self.annotation(
                    ann[0][1].annotation, self.class_ctx(cm, c) if False else Ctx(cm, cm.parent[id(c)] + (c,))
                )
        # assignments to self.<name> in the methods of every in-repo class of the MRO
        sites = []
        for x in self.mro(cls, mod):
            if isinstance(x, str):
                continue
            for fn, v, ann in self.self_attrs(x[0]).get(name, []):
                sites.append((x[1], fn, v, ann))
        class_level = mem if isinstance(mem, tuple) else None
        for cm, fn, _v, ann in sites:
            if ann is not None:
                return self.annotation(ann, Ctx(cm, cm.parent[id(fn)] + (fn,)))
        n_class = len(class_level[3]) if class_level else 0
        if class_level and not sites and n_class == 1 and class_level[3][0][0] == "assign":
            c, cm = class_level[1], class_level[2]
            return self.expr(class_level[3][0][2], Ctx(cm, cm.parent[id(c)] + (c,)))
        if len(sites) == 1 and n_class == 0:
            cm, fn, v, _ = sites[0]
            return self.expr(v, Ctx(cm, cm.parent[id(fn)] + (fn,)))
        if self.lenient and sites:
            live = [s for s in sites if not (isinstance(s[2], ast.Constant) and s[2].value is None)]
            if class_level:
                live_cls = [
                    b
                    for b in class_level[3]
                    if not (b[0] == "assign" and isinstance(b[2], ast.Constant) and b[2].value is None)
                ]
                if live_cls:
                    return None
            ts = [self.expr(v, Ctx(cm, cm.parent[id(fn)] + (fn,))) for cm, fn, v, _ in live]
            ks = {self._key(t) for t in ts}
            if ts and len(ks) == 1:
                return ts[0]
            return None
        if not sites and not class_level:
            if mem in ("EXT",):
                return EXT_INST
        return None

    def attr(self, t, name):
        if t is None:
            return None
        k = t[0]
        if k == "ext":
            if t[1] == "mod":
                return EXT_CLASS if name[:1].isupper() else EXT_FUNC
            return EXT_INST if t[1] == "inst" else None
        if k == "mod":
            mod = t[1]
            sub = self.r.modname[mod.path] + "." + name
            if sub in self.r.byname and name not in self.r.bindings(mod.tree):
                return ("mod", self.r.mods[self.r.byname[sub]])
            return self.module_member(mod, name)
        if k == "cls":
            return self.instance_attr(t[1], t[2], name)
        if k == "classobj":
            mem = self.class_member(t[1], t[2], name)
            if isinstance(mem, tuple):
                _, c, cm, bs = mem
                if len(bs) == 1 or self.lenient:
                    b = bs[-1]
                    if b[0] == "def":
                        return ("method", b[1], cm, (t[1], t[2]))
                    if b[0] == "class":
                        return ("classobj", b[1], cm)
                    if b[0] == "annassign":
                        return self.annotation(b[1].annotation, Ctx(cm, cm.parent[id(c)] + (c,)))
                    if b[0] == "assign" and len(bs) == 1:
                        return self.expr(b[2], Ctx(cm, cm.parent[id(c)] + (c,)))
                return None
            return EXT_INST if mem == "EXT" else None
        if k == "super":
            if t[1] is None:
                return None
            mem = self.class_member(t[1], t[2], name, skip_self=True)
            if isinstance(mem, tuple):
                _, c, cm, bs = mem
                b = [x for x in bs if x[0] == "def"]
                if b:
                    return ("method", b[-1][1], cm, None)
                return None
            return EXT_INST if mem == "EXT" else None
        return None

    # -- the call target
    def target(self, call, ctx):
        """('in', path, line, name) | 'ext' | None, plus the receiver type."""
        rt = self.expr(call.func.value, ctx)
        name = call.func.attr
        if rt is None:
            return None, rt
        k = rt[0]
        if k == "ext":
            return "ext", rt
        if k in ("cls", "classobj", "super"):
            if k == "super":
                if rt[1] is None:
                    return None, rt
                mem = self.class_member(rt[1], rt[2], name, skip_self=True)
            else:
                mem = self.class_member(rt[1], rt[2], name)
            if isinstance(mem, tuple):
                _, c, cm, bs = mem
                defs = [b for b in bs if b[0] in ("def", "class")]
                if defs:
                    n = defs[-1][1]
                    return ("in", cm.path, n.lineno, n.name), rt
                return None, rt  # a callable class attribute
            if mem == "EXT":
                return "ext", rt
            return None, rt
        if k == "mod":
            mem = self.module_member(rt[1], name)
            if mem and mem[0] in ("func", "classobj"):
                return ("in", mem[2].path, mem[1].lineno, mem[1].name), rt
            if mem and mem[0] == "ext":
                return "ext", rt
            return None, rt
        return None, rt


# ---------------------------------------------------------------- classification (syntax only)


def is_self_param(inf, name, ctx):
    i, bs = inf.lookup(name, ctx)
    if not bs or len(bs) != 1 or bs[0][0] != "param" or bs[0][2] != 0:
        return False
    sc = ctx.scopes
    return i is not None and i > 0 and isinstance(sc[i - 1], ast.ClassDef)


def classify(inf, call, ctx):
    recv = call.func.value
    if isinstance(recv, ast.Name):
        if is_self_param(inf, recv.id, ctx):
            return "self.m() (inherited/external base)"
        i, bs = inf.lookup(recv.id, ctx)
        if not bs:
            if recv.id in BUILTIN_CLASSES or recv.id in BUILTIN_FUNCS:
                return "builtin name receiver"
            return "unbound name (star import/dynamic)"
        mod_level = i == 0
        pre = "module-level " if mod_level else ""
        kind, node, extra = bs[0]
        kinds = {b[0] for b in bs}
        if kinds <= {"import", "importfrom"}:
            t = inf.import_binding(kind, node, extra, ctx.mod)
            if t is None:
                return "imported name (unresolvable in-repo)"
            if t[0] == "ext":
                return "external import receiver"
            if t[0] == "mod":
                return "imported module receiver"
            if t[0] == "classobj":
                return "class receiver (Foo.m())"
            if t[0] == "func":
                return "imported function receiver"
            return "imported module-level instance"
        if kind == "param":
            if node.annotation is not None:
                return "param annotated"
            if recv.id in inf.r.fixtures:
                return "param unannotated (pytest fixture)"
            return "param unannotated"
        if kind == "vararg":
            return "param unannotated"
        if kind in ("assign",) and extra is not None:
            v = extra
            if isinstance(v, ast.Call):
                ft = inf.expr(v.func, Ctx(ctx.mod, ctx.scopes[: i + 1]))
                fv = v.func
                if ft and ft[0] == "classobj":
                    return pre + "x = Foo()"
                if ft and ft[0] == "method" and isinstance(fv, ast.Attribute):
                    bt = inf.expr(fv.value, Ctx(ctx.mod, ctx.scopes[: i + 1]))
                    ann = "annotated" if ft[1].returns is not None else "unannotated"
                    if bt and bt[0] == "classobj":
                        return pre + f"x = Foo.factory() [{ann}]"
                    return pre + f"x = obj.method() [{ann}]"
                if ft and ft[0] == "func":
                    return pre + (
                        "x = f() [annotated return]" if ft[1].returns is not None else "x = f() [unannotated]"
                    )
                if ft and ft[0] == "ext":
                    if ft[1] in ("class", "func"):
                        return pre + "x = external call"
                    return pre + "x = ext_obj.method()"
                if isinstance(fv, ast.Name) and fv.id == "super":
                    return pre + "x = other call"
                return pre + "x = call of unknown callee"
            if isinstance(v, LITERALS):
                return pre + "x = literal"
            return pre + "x = other expr"
        if kind == "annassign":
            return pre + "x: T annotated local"
        if kind in ("for", "with", "match"):
            return "loop/with/comprehension var"
        if kind == "except":
            return "except var"
        if kind == "unpack":
            return pre + "x unpacked (a, x = ...)"
        if kind == "class":
            return "class receiver (Foo.m())"
        if kind == "def":
            return "function receiver"
        return "other name binding"
    if isinstance(recv, ast.Call):
        if isinstance(recv.func, ast.Name) and recv.func.id == "super":
            return "super().m()"
        return "call result f().m()"
    if isinstance(recv, ast.Attribute):
        b = recv.value
        if isinstance(b, ast.Name) and is_self_param(inf, b.id, ctx):
            return "self.attr.m()"
        return "attribute chain a.b.m()"
    if isinstance(recv, LITERALS) or isinstance(recv, ast.BinOp):
        return "literal receiver"
    if isinstance(recv, ast.Subscript):
        return "subscript x[i].m()"
    return "other"


# ---------------------------------------------------------------- scoring


def to_repo_path(module_path, root, repo_files):
    if module_path is None:
        return None
    mp = str(module_path)
    if mp.startswith(root + os.sep):
        return os.path.relpath(mp, root)
    if "site-packages/" in mp:
        tail = mp.split("site-packages/", 1)[1]
        for f in repo_files:
            if f == tail or f.endswith("/" + tail):
                return f
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("truth")
    ap.add_argument("--examples")
    ap.add_argument("--json")
    args = ap.parse_args()
    root = os.path.realpath(os.path.abspath(args.root))
    root_raw = os.path.abspath(args.root)
    sym_rows = q.run(
        root, "SELECT path, name, start_line, end_line, qualname FROM symbols", max_rows=10_000_000, fresh=False
    ).rows
    ranges = collections.defaultdict(list)
    for p, nm, s, e, qn in sym_rows:
        ranges[(p, nm)].append((s, e, qn))
    py = [
        r[0] for r in q.run(root, "SELECT path FROM files WHERE lang='python'", max_rows=10_000_000, fresh=False).rows
    ]
    repo_files = set(py)
    repo = Repo(root, py)
    strict, lenient = Infer(repo, False), Infer(repo, True)

    def qn_of(path, line, name):
        best = None
        for s, e, qn in ranges.get((path, name), []):
            if s <= line <= e and (best is None or e - s < best[0]):
                best = (e - s, qn)
        return best[1] if best else None

    rows = []
    for ln in open(args.truth):
        x = json.loads(ln)
        mod = repo.mods.get(x["path"])
        # jedi truth
        truth, ext, inrepo_unmapped = set(), False, False
        for g in x.get("jedi") or []:
            mp = g["mp"]
            rel = None
            if mp:
                for rt in (root, root_raw):
                    rel = to_repo_path(mp, rt, repo_files)
                    if rel:
                        break
            if rel is None or g["line"] is None:
                ext = True
                continue
            qn = qn_of(rel, g["line"], g["name"])
            if qn:
                truth.add((rel, qn))
            else:
                inrepo_unmapped = True
        if truth:
            js = "in"
        elif ext:
            js = "ext"
        elif inrepo_unmapped:
            js = "in-unmapped"
        else:
            js = "none"
        rec = dict(
            path=x["path"],
            line=x["line"],
            name=x["name"],
            receiver=x["receiver"],
            tier="+".join(x["res"]),
            n_cand=x["n_cand"],
            jedi=js,
            truth=sorted(truth),
        )
        hit = mod.calls.get((x["line"], x["col"])) if mod else None
        if hit is None:
            rec["cls"] = "(no ast match)"
            rec["strict"] = rec["lenient"] = None
            rows.append(rec)
            continue
        call, scopes = hit
        ctx = Ctx(mod, scopes)
        rec["cls"] = classify(strict, call, ctx)
        for label, inf in (("strict", strict), ("lenient", lenient)):
            try:
                t, rt = inf.target(call, ctx)
            except RecursionError:
                t, rt = None, None
            if rt and rt[0] in ("cls", "classobj", "super") and rt[1] is not None and label == "strict":
                rec["rtype"] = [rt[0], qn_of(rt[2].path, rt[1].lineno, rt[1].name)]
            if t is None:
                rec[label] = None
            elif t == "ext":
                rec[label] = "ext"
            else:
                rec[label] = ["in", t[1], qn_of(t[1], t[2], t[3]) or f"?{t[3]}@{t[2]}"]
        rows.append(rec)

    report(rows, args, root)


def score(r, label):
    """'ok' | 'wrong' | 'ext-ok' | 'ext-wrong' | 'unverified' | 'unverified-ext' | None"""
    v = r[label]
    if v is None:
        return None
    if r["jedi"] in ("none", "in-unmapped"):
        return "unverified-ext" if v == "ext" else "unverified"
    if v == "ext":
        return "ext-ok" if r["jedi"] == "ext" else "ext-wrong"
    if r["jedi"] == "ext":
        return "wrong"
    return "ok" if (v[1], v[2]) in {tuple(t) for t in r["truth"]} else "wrong"


def table(rows, title, N_all):
    by = collections.defaultdict(list)
    for r in rows:
        by[r["cls"]].append(r)
    print(f"\n## {title}: {len(rows)} sites")
    print(
        f"{'class':40s} {'n':>6s} {'share':>6s} {'j.in':>6s} {'j.ext':>6s} {'j.unk':>6s} {'name':>6s} {'amb':>6s} "
        f"{'unres':>6s} | {'S.res':>6s} {'S.ok':>6s} {'S.bad':>5s} {'S.unv':>5s} {'S.prec':>6s} {'S.rec':>6s} "
        f"{'S.ext':>6s} {'xok':>6s} {'xbad':>4s} {'FC.rm':>6s} {'FCe.rm':>6s} | {'L.res':>6s} {'L.ok':>6s} "
        f"{'L.bad':>5s} {'L.prec':>6s} {'L.rec':>6s} {'L.ext':>6s} {'L.xok':>6s} {'L.FCrm':>6s}"
    )
    out = {}
    tot = collections.Counter()
    for cls, rs in sorted(by.items(), key=lambda kv: -len(kv[1])):
        c = collections.Counter()
        c["n"] = len(rs)
        for r in rs:
            c["j_in"] += r["jedi"] == "in"
            c["j_ext"] += r["jedi"] == "ext"
            t = r["tier"]
            c["t_name"] += t == "name"
            c["t_amb"] += t == "ambiguous"
            c["t_unres"] += t == "unresolved"
            c["fc"] += r["jedi"] == "ext" and t == "name"
            c["fc_edges"] += (r["n_cand"] or 0) if (r["jedi"] == "ext" and t == "name") else 0
            for L in ("strict", "lenient"):
                s = score(r, L)
                p = L[0].upper()
                if s is None:
                    continue
                if r[L] != "ext":
                    c[p + "res"] += 1
                else:
                    c[p + "ext"] += 1
                c[p + s] += 1
                if s == "ext-ok" and t == "name":
                    c[p + "fc_rm"] += 1
                    c[p + "fce_rm"] += r["n_cand"] or 0
                if s == "ext-ok" and t == "ambiguous":
                    c[p + "amb_rm"] += 1
        tot.update(c)
        out[cls] = dict(c)
        print(_line(cls, c, len(rows)))
    print(_line("TOTAL", tot, len(rows)))
    out["TOTAL"] = dict(tot)
    return out


def report(rows, args, root):
    N = len(rows)
    n_in = sum(r["jedi"] == "in" for r in rows)
    n_ext = sum(r["jedi"] == "ext" for r in rows)
    print(
        f"repo {root}: {N} gap receiver calls (tier name/ambiguous/unresolved); jedi: in-repo {n_in}, "
        f"external {n_ext}, unknown {N - n_in - n_ext}"
    )
    tiers = collections.Counter(r["tier"] for r in rows)
    print("duckgrep tiers:", dict(tiers.most_common()))
    false_c = [r for r in rows if r["jedi"] == "ext" and r["tier"] == "name"]
    print(
        f"false 'name' candidates today: {len(false_c)} jedi-external sites carry "
        f"{sum(r['n_cand'] or 0 for r in false_c)} name-tier edges; ambiguous jedi-external sites: "
        f"{sum(r['jedi'] == 'ext' and r['tier'] == 'ambiguous' for r in rows)}"
    )
    out = {
        "all": table(rows, "all gap tiers", N),
        "name+ambiguous": table([r for r in rows if r["tier"] in ("name", "ambiguous")], "name + ambiguous only", N),
    }
    print(
        "\ncolumns: j.in/j.ext/j.unk = jedi target in repo / external / no answer; name/amb/unres = duckgrep tier today;"
        "\n S.res = sites the strict rule resolves to an in-repo target; S.ok / S.bad = equal to / differs from jedi"
        " (jedi external counts as bad); S.unv = resolved where jedi has no answer;"
        "\n S.prec = S.ok / (S.ok + S.bad); S.rec = S.ok / j.in; S.ext = sites marked not-in-repo; xok / xbad = of"
        " those, jedi external / jedi in-repo;"
        "\n FC.rm = jedi-external 'name'-tier sites the rule marks external; FCe.rm = the name edges they carry."
        "\n L.* = the same for the lenient rule set."
    )
    if args.json:
        json.dump(out, open(args.json, "w"), indent=1)
    if args.examples:
        with open(args.examples, "w") as f:
            for L in ("strict", "lenient"):
                f.write(f"==== {L}: wrong resolutions / wrong ext\n")
                cnt = collections.Counter()
                for r in rows:
                    s = score(r, L)
                    if s in ("wrong", "ext-wrong") and cnt[(r["cls"], s)] < 6:
                        cnt[(r["cls"], s)] += 1
                        f.write(
                            f"[{r['cls']}] {s} {r['path']}:{r['line']} {r['receiver']}.{r['name']} "
                            f"rule={r[L]} jedi={r['jedi']} {r['truth'][:2]}\n"
                        )
            f.write("==== strict misses (jedi in-repo, rule no answer), by class\n")
            cnt = collections.Counter()
            for r in rows:
                if r["jedi"] == "in" and r["strict"] is None and cnt[r["cls"]] < 8:
                    cnt[r["cls"]] += 1
                    f.write(f"[{r['cls']}] {r['path']}:{r['line']} {r['receiver']}.{r['name']} -> {r['truth'][:1]}\n")
        with open(args.examples + ".rows.jsonl", "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")


def _pct(a, b):
    return f"{a / b:6.1%}" if b else f"{'-':>6s}"


def _line(cls, c, N):
    s_ver = c["Sok"] + c["Swrong"]
    l_ver = c["Lok"] + c["Lwrong"]
    return (
        f"{cls[:40]:40s} {c['n']:6d} {_pct(c['n'], N)} {c['j_in']:6d} {c['j_ext']:6d} "
        f"{c['n'] - c['j_in'] - c['j_ext']:6d} {c['t_name']:6d} {c['t_amb']:6d} {c['t_unres']:6d} | "
        f"{c['Sres']:6d} {c['Sok']:6d} {c['Swrong']:5d} {c['Sunverified'] - 0:5d} {_pct(c['Sok'], s_ver)} "
        f"{_pct(c['Sok'], c['j_in'])} {c['Sext']:6d} {c['Sext-ok']:6d} {c['Sext-wrong']:4d} {c['Sfc_rm']:6d} "
        f"{c['Sfce_rm']:6d} | {c['Lres']:6d} {c['Lok']:6d} {c['Lwrong']:5d} {_pct(c['Lok'], l_ver)} "
        f"{_pct(c['Lok'], c['j_in'])} {c['Lext']:6d} {c['Lext-ok']:6d} {c['Lfc_rm']:6d}"
    )


if __name__ == "__main__":
    sys.setrecursionlimit(20000)
    main()
