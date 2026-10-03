# Local Type Inference (Python) Implementation Plan

**Goal:** Give Python receiver calls (`obj.method()`) one confident `typed` edge when the receiver's class can be read from syntax, and mark them `unresolved` when that class is outside the repo, instead of `name` or `ambiguous` guesses.

**Architecture:**
- The extractor records per-file, symbolic binding facts:
  - a new `bindings` table: what each name is bound to in each scope, plus class bases and `self.attr` types;
  - a new `symbols.returns` column.
- `EDGES_COMPUTE` resolves them in SQL with a new confident tier, `typed`: the type name goes through the existing import machinery, then a depth-capped walk over in-repo base classes.
- Dirty marking adds the method names of classes whose facts a changed file holds, so that an incremental index still equals a full rebuild.

**Tech stack:** Python 3.10+, tree-sitter (Python grammar), DuckDB SQL, pytest, uv.

**Spec:** `docs/specs/2026-10-02-local-type-inference.md`. Read it with this plan; the plan argues from it.

## Global Constraints

- **Language:** Phase 1 is Python only. Other languages' edges must be byte-identical before and after (the existing tests prove it).
- **No inference unless every binding agrees:** a receiver is inferred only when all its bindings in the closest binding scope share one non-NULL type. Anything else keeps today's tiers unchanged.
- **One hop of return annotations;** `returns` is never followed into another call.
- **`INHERIT_DEPTH` = 8.** It goes into `edges_version`'s key, as `NAME_CAP` does.
- **Label order:**
  - the confident label order becomes `self, local, package, import, module, qualified, typed`;
  - when two tiers reach one target, the earlier label wins;
  - a same-class `self` hit keeps `self`.
- **`SCHEMA_VERSION`** goes from 3 to 4.
- **The tool description** (`QUERY_DOC`) stays at or under 3,100 characters (tests/test_surface.py).
- **Success bars:**
  - confident precision at least 99.5% against jedi on django, freqtrade and requests;
  - confident coverage of in-repo calls up at least 10 points on django and freqtrade;
  - false `name` edges on jedi-external calls down by at least half on django.
- **Performance bars,** against the RESULTS.md baselines:
  - full index within +25%;
  - one-file edge sync within +50% (django `query.py`);
  - typical query latency unchanged.
- **Process:**
  - every PR is cut from an up-to-date `main` and committed through the hooks, never `--no-verify`;
  - wait for Bugbot and Greptile, and act on valid comments;
  - merge with `gh pr merge --merge --delete-branch`;
  - set `TMPDIR=/Volumes/research/tmp` for tests and commits.
- **Docs:**
  - specs go in `docs/specs/` and plans in `docs/plans/`;
  - keep tool, skill and plugin names out of docs.

## Review Focus

Inputs and conditions the spec implies but the feature tests below don't obviously cover. Each line names the test that pins it, in its owning task.

1. **Conditional rebinding** (`if c: x = A()` / `else: x = B()`, or a reassignment in a loop): no `typed` edge. That's `test_branch_rebinding_blocks_inference` in Task 5.
2. **A receiver shadowed in an inner scope** (a nested function's parameter or a comprehension variable with the same name): no edge to the outer binding's class. That's `test_inner_scope_shadowing_blocks_inference` in Task 5.
3. **A dynamic or unnameable base** (`class C(namedtuple("P", "a"))`, `class C(make_base())`): no `typed` edge, and today's tiers stay. That's `test_opaque_base_keeps_todays_tiers` in Task 5.
4. **Cyclic or very deep class hierarchies** (a by-name cycle across files, or chains longer than 8): the recursion terminates, with no crash and no edge beyond the cap. That's `test_cyclic_and_deep_hierarchies_terminate` in Task 5.
5. **A file with syntax errors:** bindings still extract from the parts that parse, and indexing doesn't fail. That's `test_bindings_survive_parse_errors` in Task 3.

---

## PR A: the jedi benchmark without Django's bundled stubs

### Task 1: jedi resolves django's own source; `typed` counts as confident

**Files:**
- Modify: `bench/accuracy.py` (the `jedi.Project` setup near line 52, and the confident-tier tuple near line 115)
- Modify: `bench/RESULTS.md` (the django rows of "Call-graph accuracy vs jedi" and of "freqtrade and django")

**Interfaces:**
- Produces: `bench/accuracy.py` output lines `confident tiers: X% of in-repo calls, precision Y%`, with `typed` counted as confident. Task 9 reads these.

- [ ] **Step 1:** create the branch.

  ```bash
  cd /Users/ccf/git/duckgrep && git checkout main && git pull --ff-only && git checkout -b bench/jedi-no-django-stubs
  ```

- [ ] **Step 2:** turn off jedi's bundled Django stubs. jedi serves them for any `django` import, including the repo's own django source, which makes django's in-repo calls look external. In `bench/accuracy.py`, directly before `project = jedi.Project(root, added_sys_path=extra)`, add:

  ```python
      # jedi bundles django-stubs and answers with them for the repo's own django: the reference must be the
      # repo's source, so point the stub path at nothing (harmless for repos that only use django)
      import pathlib

      import jedi.inference.gradual.typeshed as typeshed

      typeshed.DJANGO_INIT_PATH = pathlib.Path("/nonexistent/django-stubs/__init__.pyi")
  ```

- [ ] **Step 3:** count `typed` as confident. Replace the tier tuple:

  ```python
          if tier in ("self", "local", "import", "module", "qualified", "package", "typed"):
  ```

- [ ] **Step 4:** re-measure. Each run takes a few minutes, and the checkouts already exist:

  ```bash
  uv run --group bench python bench/accuracy.py /Volumes/research/scratch/django 3000 > /Volumes/research/scratch/acc-django-nostubs.txt
  uv run --group bench python bench/accuracy.py /Volumes/research/scratch/freqtrade 3000 > /Volumes/research/scratch/acc-freqtrade-nostubs.txt
  ```

  Expected: django's `in-repo target` count rises above the previous 1,411 of 3,000. Freqtrade's numbers barely move.

- [ ] **Step 5:** in `bench/RESULTS.md`, replace the django and freqtrade accuracy rows with the new outputs: calls with an in-repo target, confident share and precision. Add one sentence saying jedi now runs without its bundled Django stubs, and why. Update the same three numbers in README.md's "How accurate is the call graph?" table, and in `site/index.html`'s FAQ table, so `tests/test_site.py::test_prose_numbers_match_readme` keeps passing. Update its figures tuple if a number it lists changes.

- [ ] **Step 6:** run the suite, commit, open the PR.

  ```bash
  TMPDIR=/Volumes/research/tmp uv run pytest -q
  git add bench/accuracy.py bench/RESULTS.md README.md site/index.html tests/test_site.py
  git commit -m "bench: score against django's source, not jedi's bundled stubs"
  git push -u origin bench/jedi-no-django-stubs && gh pr create --fill
  ```

  Expected: all tests pass. Then follow the review loop and merge when it's clean.

## PR B: standard-library imports don't resolve to nested repo modules

### Task 2: a stdlib module name matches a repo module only at the top (or under `src/`)

**Files:**
- Modify: `src/duckgrep/builtin_names.py` (add `PY_STDLIB`)
- Modify: `src/duckgrep/schema.py` (`best_mod` in `_EDGES_TEMPLATE`, `best` in the `imports_resolved` view, and the `@PY_STDLIB@` substitution)
- Test: `tests/test_resolution.py`

**Interfaces:**
- Produces: `builtin_names.PY_STDLIB: frozenset[str]` and the SQL predicate fragment `STDLIB_GUARD`, which both module-key lookups use.

- [ ] **Step 1:** create the branch.

  ```bash
  git checkout main && git pull --ff-only && git checkout -b fix/stdlib-suffix-keys
  ```

- [ ] **Step 2:** write the failing tests. Append to `tests/test_resolution.py`:

  ```python
  def test_stdlib_imports_do_not_resolve_to_nested_repo_modules(tmp_path):
      root = make_repo(
          tmp_path / "r",
          {
              "proj/utils/json.py": "def dumps(x):\n    return x\n",
              "proj/app.py": "import json\n\n\ndef f():\n    return json.dumps(1)\n",
          },
      )
      assert edges_at(root, "proj/app.py", "dumps") == [(None, None, "unresolved")]


  def test_stdlib_names_still_resolve_to_top_level_and_src_modules(tmp_path):
      root = make_repo(
          tmp_path / "r",
          {
              "json.py": "def dumps(x):\n    return x\n",
              "app.py": "import json\n\n\ndef f():\n    return json.dumps(1)\n",
              "src/math.py": "def tau():\n    return 6\n",
              "src/use.py": "import math\n\n\ndef g():\n    return math.tau()\n",
          },
      )
      assert edges_at(root, "app.py", "dumps") == [("json.py", "dumps", "module")]
      assert edges_at(root, "src/use.py", "tau") == [("src/math.py", "tau", "module")]
  ```

- [ ] **Step 3:** run them.

  ```bash
  TMPDIR=/Volumes/research/tmp uv run pytest tests/test_resolution.py -k stdlib -q
  ```

  Expected: the first test FAILS (today the edge is `('proj/utils/json.py', 'dumps', 'module')`). The second passes.

- [ ] **Step 4:** add the standard-library list. In `src/duckgrep/builtin_names.py`, add the names of `sys.stdlib_module_names`, generated once and pasted as a constant, so the index doesn't depend on the Python version running it:

  ```bash
  uv run python -c "import sys; print(' '.join(sorted(n for n in sys.stdlib_module_names if not n.startswith('_'))))"
  ```

  Paste the output into:

  ```python
  # top-level standard-library module names (sys.stdlib_module_names, CPython 3.13, private ones dropped):
  # an absolute import of one of these never means a nested repo module that happens to share the name
  _PY_STDLIB = """<paste>"""
  PY_STDLIB = frozenset(_PY_STDLIB.split())
  ```

- [ ] **Step 5:** guard both module-key lookups. In `src/duckgrep/schema.py`, add above `_EDGES_TEMPLATE`:

  ```python
  # A Python key whose first part is a stdlib module name matches a repo module only when it is the module's full
  # path (drop_n = 0) or its path under a top-level src/ (src layout): `import json` is the stdlib, not
  # proj/utils/json.py reached by dotted suffix.
  STDLIB_GUARD = (
      "NOT (family = 'py' AND drop_n > 0 AND split_part(key, '.', 1) IN (@PY_STDLIB@)"
      " AND NOT (drop_n = 1 AND starts_with(path, 'src/')))"
  )
  ```

  In `_EDGES_TEMPLATE`, change `best_mod` to:

  ```sql
  best_mod AS (
      SELECT family, key, path FROM modules
      WHERE key IN (SELECT key FROM imps UNION SELECT subkey FROM imps) AND @STDLIB_GUARD@
      QUALIFY row_number() OVER (PARTITION BY family, key ORDER BY drop_n, length(path), path) = 1
  ),
  ```

  In the `imports_resolved` view, change `best` to:

  ```sql
  WITH best AS (
      SELECT family, key, path FROM modules
      WHERE @STDLIB_GUARD@
      QUALIFY row_number() OVER (PARTITION BY family, key ORDER BY drop_n, length(path), path) = 1
  )
  ```

  Where `EDGES_COMPUTE` is built, and where `VIEWS` is built, substitute the guard and the list. Add `_names`, and leave `_values` (which already renders `(family, name)` pairs) unchanged:

  ```python
  def _names(words) -> str:
      return ", ".join(f"'{w}'" for w in sorted(words))


  _GUARD = STDLIB_GUARD.replace("@PY_STDLIB@", _names(builtin_names.PY_STDLIB))
  EDGES_COMPUTE = (
      _EDGES_TEMPLATE.replace("@BUILTIN_METHODS@", _values(builtin_names.METHODS))
      .replace("@BUILTIN_GLOBALS@", _values(builtin_names.GLOBALS))
      .replace("@STDLIB_GUARD@", _GUARD)
  )
  ```

  In `VIEWS`, do `.replace("@STDLIB_GUARD@", _GUARD)` on the views string where it's defined. If `VIEWS` is a plain literal, wrap the literal and apply the replace right after it.

- [ ] **Step 6:** run the tests.

  ```bash
  TMPDIR=/Volumes/research/tmp uv run pytest tests/test_resolution.py -q && TMPDIR=/Volumes/research/tmp uv run pytest -q
  ```

  Expected: all pass. `edges_version` changes, because `EDGES_COMPUTE` and `VIEWS` changed, so existing indexes rebuild their edges by themselves.

- [ ] **Step 7:** commit and open the PR. The PR body should give the django effect, measured with `uv run duckgrep -C /Volumes/research/scratch/django q "SELECT count(*) FROM edges WHERE resolution = 'module' AND dst_path IN ('django/utils/json.py')"`, before and after.

  ```bash
  git add src/duckgrep/builtin_names.py src/duckgrep/schema.py tests/test_resolution.py
  git commit -m "fix: stdlib imports no longer resolve to nested repo modules by dotted suffix"
  git push -u origin fix/stdlib-suffix-keys && gh pr create --fill
  ```

## PR C: the `typed` tier (branch `feat/typed-tier`, cut after PRs A and B merge)

### Task 3: binding facts from the Python tree (pure, per file)

**Files:**
- Create: `src/duckgrep/bindings.py`
- Test: `tests/test_bindings.py`

**Interfaces:**
- Produces:
  - `normalize_type(text: str, cls: str | None = None, generic_head: bool = False) -> str | None`
  - `extract_bindings(root: tree_sitter.Node, src: bytes, path: str) -> list[tuple]`. Each row is `(path, scope, name, kind, type_text, line, pos)`:
    - `kind` is one of `assign`, `annot`, `param`, `attr`, `base` or `global`;
    - `type_text` is a dotted name, `call:<dotted callee>`, a builtin type name for literals, or `None`.
  - `BINDING_COLS = ["path", "scope", "name", "kind", "type_text", "line", "pos"]`
- Base rows use `scope` = the class qualname, `name` = `''`, and `pos` = the base's position. `object`, `Generic`, `Protocol`, `ABC` and `abc.ABC` bases are skipped, because they add no methods.
- Attribute rows use `scope` = the class qualname and `name` = `self.<attr>`.

- [ ] **Step 1:** create the branch.

  ```bash
  git checkout main && git pull --ff-only && git checkout -b feat/typed-tier
  ```

- [ ] **Step 2:** write the failing tests in `tests/test_bindings.py`.

  ```python
  """Binding facts for type inference (Python): normalisation and extraction, per file."""

  import pytest

  from duckgrep.bindings import extract_bindings, normalize_type
  from duckgrep.extract import get_parser


  @pytest.mark.parametrize(
      "text, cls, want",
      [
          ("Foo", None, "Foo"),
          ("mod.Foo", None, "mod.Foo"),
          ('"Foo"', None, "Foo"),
          ("Optional[Foo]", None, "Foo"),
          ("typing.Optional['Foo']", None, "Foo"),
          ("Foo | None", None, "Foo"),
          ("None | Foo", None, "Foo"),
          ("Union[Foo, None]", None, "Foo"),
          ("Union[Foo, Bar]", None, None),
          ("Foo | Bar", None, None),
          ("Annotated[Foo, 'x']", None, "Foo"),
          ("type[Foo]", None, "Foo"),
          ("ClassVar[Foo]", None, "Foo"),
          ("list[Foo]", None, None),
          ("Self", "Engine", "Engine"),
          ("Self", None, None),
          ("None", None, None),
          ("Callable[[int], Foo]", None, None),
      ],
  )
  def test_normalize_type(text, cls, want):
      assert normalize_type(text, cls) == want


  def test_generic_heads_name_the_base():
      assert normalize_type("Base[T]", generic_head=True) == "Base"
      assert normalize_type("Base[T]") is None


  def bind(src, path="m.py"):
      tree = get_parser("python").parse(src.encode())
      return sorted(extract_bindings(tree.root_node, src.encode(), path))


  def test_locals_params_and_module_level():
      rows = bind(
          "x = Foo()\n"
          "def f(a, b: 'Bar', *args, c: int = 1, **kw) -> None:\n"
          "    y = mod.make(a)\n"
          "    z: Baz = get()\n"
          "    w = []\n"
          "    v = y\n"
      )
      assert ("m.py", "", "x", "assign", "call:Foo", 1, 0) in rows
      assert ("m.py", "f", "a", "param", None, 2, 0) in rows
      assert ("m.py", "f", "b", "param", "Bar", 2, 0) in rows
      assert ("m.py", "f", "args", "param", None, 2, 0) in rows
      assert ("m.py", "f", "c", "param", "int", 2, 0) in rows
      assert ("m.py", "f", "kw", "param", None, 2, 0) in rows
      assert ("m.py", "f", "y", "assign", "call:mod.make", 3, 0) in rows
      assert ("m.py", "f", "z", "annot", "Baz", 4, 0) in rows
      assert ("m.py", "f", "w", "assign", "list", 5, 0) in rows
      assert ("m.py", "f", "v", "assign", None, 6, 0) in rows


  def test_classes_bases_and_attributes():
      rows = bind(
          "class Svc(Base, mod.Mixin, Generic[T], metaclass=Meta):\n"
          "    conn: Conn\n"
          "    pool = Pool()\n"
          "    def __init__(self, c: Conn, n):\n"
          "        self.c = c\n"
          "        self.e = Engine()\n"
          "        self.n = n\n"
          "        self.a, self.b = 1, 2\n"
          "    def make(self) -> 'Self':\n"
          "        return self\n"
      )
      assert ("m.py", "Svc", "", "base", "Base", 1, 0) in rows
      assert ("m.py", "Svc", "", "base", "mod.Mixin", 1, 1) in rows
      assert not [r for r in rows if r[3] == "base" and r[4] in ("Generic", "Meta")]
      assert ("m.py", "Svc", "self.conn", "attr", "Conn", 2, 0) in rows
      assert ("m.py", "Svc", "self.pool", "attr", "call:Pool", 3, 0) in rows
      assert ("m.py", "Svc", "self.c", "attr", "Conn", 5, 0) in rows
      assert ("m.py", "Svc", "self.e", "attr", "call:Engine", 6, 0) in rows
      assert ("m.py", "Svc", "self.n", "attr", None, 7, 0) in rows
      assert ("m.py", "Svc", "self.a", "attr", None, 8, 0) in rows
      assert ("m.py", "Svc.__init__", "c", "param", "Conn", 4, 0) in rows


  def test_untyped_bindings_are_recorded_to_block_inference():
      rows = bind(
          "def f(items):\n"
          "    for x in items:\n"
          "        pass\n"
          "    with open('p') as fh, lock as (a, b):\n"
          "        pass\n"
          "    try:\n"
          "        pass\n"
          "    except ValueError as e:\n"
          "        pass\n"
          "    if (n := len(items)):\n"
          "        pass\n"
          "    ys = [y for y in items]\n"
          "    x += 1\n"
          "    import os\n"
          "    global G\n"
      )
      names = {(r[2], r[4]) for r in rows if r[1] == "f"}
      for nm in ("x", "fh", "a", "b", "e", "n", "y", "os", "G"):
          assert (nm, None) in names, nm
      assert ("G", None) in names and any(r[2] == "G" and r[3] == "global" for r in rows)


  def test_nested_scopes_use_walker_qualnames():
      rows = bind("class A:\n    class B:\n        def m(self):\n            q = Q()\n")
      assert ("m.py", "A.B.m", "q", "assign", "call:Q", 4, 0) in rows


  def test_bindings_survive_parse_errors():
      rows = bind("def f(:\n    pass\n\ndef g():\n    x = Foo()\n")
      assert ("m.py", "g", "x", "assign", "call:Foo", 5, 0) in rows
  ```

- [ ] **Step 3:** run them.

  ```bash
  TMPDIR=/Volumes/research/tmp uv run pytest tests/test_bindings.py -q
  ```

  Expected: FAIL with `ModuleNotFoundError: No module named 'duckgrep.bindings'`.

- [ ] **Step 4:** implement `src/duckgrep/bindings.py`.

  ```python
  """Binding facts for type inference: what each name is bound to, per scope (Python; pure, per file).

  extract.py records these rows; EDGES_COMPUTE's `typed` tier resolves them. Every binding of a name is
  recorded, typed or not, so the resolver can tell one binding from many: an untyped binding (an unannotated
  parameter, a loop variable) has a NULL type and only blocks inference.
  """

  from __future__ import annotations

  import re

  BINDING_COLS = ["path", "scope", "name", "kind", "type_text", "line", "pos"]

  DOTTED = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\Z")
  _TYPING = ("typing.", "typing_extensions.")
  _UNWRAP = {"Optional", "Final", "ClassVar", "Annotated", "type", "Type", "Required", "NotRequired", "ReadOnly"}
  _NO_METHODS = {"object", "Generic", "Protocol", "ABC", "abc.ABC"}  # bases that add no methods worth walking
  _LITERALS = {
      "string": "str",
      "concatenated_string": "str",
      "integer": "int",
      "float": "float",
      "true": "bool",
      "false": "bool",
      "list": "list",
      "list_comprehension": "list",
      "dictionary": "dict",
      "dictionary_comprehension": "dict",
      "set": "set",
      "set_comprehension": "set",
      "tuple": "tuple",
  }
  _TARGETS = {
      "pattern_list",
      "tuple_pattern",
      "list_pattern",
      "parenthesized_expression",
      "tuple",
      "list",
      "list_splat_pattern",
      "as_pattern_target",
  }


  def _split_top(s: str, sep: str) -> list[str]:
      parts, depth, cur = [], 0, []
      for ch in s:
          if ch in "[(":
              depth += 1
          elif ch in "])":
              depth -= 1
          if ch == sep and depth == 0:
              parts.append("".join(cur).strip())
              cur = []
          else:
              cur.append(ch)
      parts.append("".join(cur).strip())
      return parts


  def _strip_typing(t: str) -> str:
      for p in _TYPING:
          if t.startswith(p):
              return t[len(p) :]
      return t


  def normalize_type(text: str, cls: str | None = None, generic_head: bool = False) -> str | None:
      """An annotation as one dotted class name, or None: unwraps Optional/X | None/quotes/Annotated/type[...],
      maps Self to `cls`. With generic_head, Base[T] names Base (for base classes); otherwise a subscript
      (a container or generic) is None."""
      t = " ".join(text.split())
      if len(t) >= 2 and t[0] == t[-1] and t[0] in "'\"":
          return normalize_type(t[1:-1], cls, generic_head)
      t = _strip_typing(t)
      union = [p for p in _split_top(t, "|") if p != "None"]
      if len(union) != 1:
          return None
      t = union[0]
      if t.endswith("]") and "[" in t:
          head = _strip_typing(t[: t.index("[")].strip())
          args = _split_top(t[t.index("[") + 1 : -1], ",")
          if head in _UNWRAP:
              return normalize_type(args[0], cls, generic_head)
          if head == "Union":
              rest = [a for a in args if a != "None"]
              return normalize_type(rest[0], cls, generic_head) if len(rest) == 1 else None
          return normalize_type(head, cls) if generic_head else None
      if t == "Self":
          return cls
      if t == "None":
          return None
      return t if DOTTED.match(t) else None


  def extract_bindings(root, src: bytes, path: str) -> list[tuple]:
      out: list[tuple] = []

      def text(n) -> str:
          return src[n.start_byte : n.end_byte].decode("utf-8", "replace")

      def add(scope, name, kind, type_text, node, pos=0):
          out.append((path, scope, name, kind, type_text, node.start_point[0] + 1, pos))

      def idents(n):
          """identifier nodes a target binds (attributes and subscripts bind no name here)"""
          if n.type == "identifier":
              yield n
          elif n.type in _TARGETS:
              for c in n.named_children:
                  yield from idents(c)

      def self_attrs(n):
          """self.x / cls.x targets inside a (possibly tuple) target"""
          if n.type == "attribute":
              obj = n.child_by_field_name("object")
              if obj is not None and obj.type == "identifier" and text(obj) in ("self", "cls"):
                  yield n
          elif n.type in _TARGETS:
              for c in n.named_children:
                  yield from self_attrs(c)

      def value_type(v, params) -> str | None:
          if v is None:
              return None
          if v.type == "assignment":  # a = b = Foo()
              return value_type(v.child_by_field_name("right"), params)
          if v.type == "call":
              fn = v.child_by_field_name("function")
              ft = " ".join(text(fn).split()) if fn is not None else ""
              return "call:" + ft if DOTTED.match(ft) else None
          if v.type == "identifier":
              return params.get(text(v))
          return _LITERALS.get(v.type)

      def param(p):
          """(name, annotation node) for one parameter node"""
          t = p.type
          if t == "identifier":
              return text(p), None
          if t in ("list_splat_pattern", "dictionary_splat_pattern"):
              i = next((c for c in p.named_children if c.type == "identifier"), None)
              return (text(i), None) if i is not None else (None, None)
          if t == "typed_parameter":
              first = p.named_children[0] if p.named_child_count else None
              if first is None:
                  return None, None
              if first.type != "identifier":  # *args: T / **kw: T bind a tuple / dict, not T
                  return param(first)[0], None
              return text(first), p.child_by_field_name("type")
          if t in ("default_parameter", "typed_default_parameter"):
              n = p.child_by_field_name("name")
              return (text(n) if n is not None else None), p.child_by_field_name("type")
          return None, None

      def walk_class(c, qual, short):
          if c.type == "expression_statement" and c.named_child_count and c.named_children[0].type == "assignment":
              a = c.named_children[0]
              left, right, ann = (a.child_by_field_name(f) for f in ("left", "right", "type"))
              if left is not None and left.type == "identifier":
                  tt = normalize_type(text(ann), short) if ann is not None else value_type(right, {})
                  add(qual, "self." + text(left), "attr", tt, left)
              return
          walk(c, qual, (qual, short), {})

      def walk(n, scope, cls, params):
          t = n.type
          if t == "class_definition":
              nm = n.child_by_field_name("name")
              if nm is None:
                  return
              short = text(nm)
              qual = f"{scope}.{short}" if scope else short
              sup = n.child_by_field_name("superclasses")
              pos = 0
              for b in sup.named_children if sup is not None else []:
                  if b.type == "keyword_argument":
                      continue
                  bt = normalize_type(text(b), short, generic_head=True)
                  if bt not in _NO_METHODS:
                      add(qual, "", "base", bt, b, pos)
                  pos += 1
              body = n.child_by_field_name("body")
              for c in body.named_children if body is not None else []:
                  walk_class(c, qual, short)
              return
          if t == "function_definition":
              nm = n.child_by_field_name("name")
              if nm is None:
                  return
              qual = f"{scope}.{text(nm)}" if scope else text(nm)
              ps: dict[str, str] = {}
              plist = n.child_by_field_name("parameters")
              for p in plist.named_children if plist is not None else []:
                  pname, ann = param(p)
                  if pname is None:
                      continue
                  tt = normalize_type(text(ann), cls[1] if cls else None) if ann is not None else None
                  add(qual, pname, "param", tt, p)
                  if tt:
                      ps[pname] = tt
              body = n.child_by_field_name("body")
              if body is not None:
                  walk(body, qual, cls, ps)
              return
          if t == "assignment":
              left, right, ann = (n.child_by_field_name(f) for f in ("left", "right", "type"))
              short = cls[1] if cls else None
              tt = normalize_type(text(ann), short) if ann is not None else value_type(right, params)
              if left is not None and left.type == "identifier":
                  add(scope, text(left), "annot" if ann is not None else "assign", tt, left)
              elif left is not None and left.type == "attribute":
                  for a in self_attrs(left):
                      if cls:
                          add(cls[0], "self." + text(a.child_by_field_name("attribute")), "attr", tt, a)
              elif left is not None:
                  for i in idents(left):
                      add(scope, text(i), "assign", None, i)
                  for a in self_attrs(left):
                      if cls:
                          add(cls[0], "self." + text(a.child_by_field_name("attribute")), "attr", None, a)
              if right is not None:
                  walk(right, scope, cls, params)
              return
          if t == "augmented_assignment":
              left = n.child_by_field_name("left")
              if left is not None:
                  for i in idents(left):
                      add(scope, text(i), "assign", None, i)
                  for a in self_attrs(left):
                      if cls:
                          add(cls[0], "self." + text(a.child_by_field_name("attribute")), "attr", None, a)
          elif t in ("for_statement", "for_in_clause"):
              left = n.child_by_field_name("left")
              if left is not None:
                  for i in idents(left):
                      add(scope, text(i), "assign", None, i)
          elif t == "as_pattern":
              alias = n.child_by_field_name("alias")
              if alias is not None:
                  for i in idents(alias):
                      add(scope, text(i), "assign", None, i)
          elif t == "named_expression":
              nm = n.child_by_field_name("name")
              if nm is not None:
                  add(scope, text(nm), "assign", None, nm)
          elif t in ("global_statement", "nonlocal_statement"):
              for c in n.named_children:
                  if c.type == "identifier":
                      add(scope, text(c), "global", None, c)
          elif t in ("import_statement", "import_from_statement") and scope:
              for c in n.children_by_field_name("name"):
                  if c.type == "aliased_import":
                      a = c.child_by_field_name("alias")
                      add(scope, text(a), "assign", None, a)
                  else:
                      add(scope, text(c).split(".")[0], "assign", None, c)
          for c in n.named_children:
              walk(c, scope, cls, params)

      walk(root, "", None, {})
      return out
  ```

- [ ] **Step 5:** run the tests.

  ```bash
  TMPDIR=/Volumes/research/tmp uv run pytest tests/test_bindings.py -q
  ```

  Expected: PASS. If the `except ... as e` case fails because this grammar version puts `e` directly under `except_clause` instead of inside an `as_pattern`, add this branch to `walk` before the final loop, then re-run:

  ```python
          elif t == "except_clause":
              kids = n.children
              for k, c in enumerate(kids):
                  if c.type == "as" and k + 1 < len(kids) and kids[k + 1].type == "identifier":
                      add(scope, text(kids[k + 1]), "assign", None, kids[k + 1])
  ```

- [ ] **Step 6:** commit.

  ```bash
  git add src/duckgrep/bindings.py tests/test_bindings.py
  git commit -m "feat(extract): Python binding facts for type inference"
  ```

### Task 4: store `bindings` and `symbols.returns`

**Files:**
- Modify: `src/duckgrep/schema.py` (`SCHEMA_VERSION` = 4; the `symbols.returns` column; the `bindings` table)
- Modify: `src/duckgrep/extract.py` (symbols gain `returns`; `extract()` returns `bindings`)
- Modify: `src/duckgrep/index.py` (`SYMBOL_COLS`, `BINDING_COLS` import, `PER_FILE_TABLES`, the per-chunk `rows` dict and `_insert`)
- Modify: `tests/helpers.py` (add `"bindings": "*"` to `COLUMNS`)
- Test: `tests/test_bindings.py` (append)

**Interfaces:**
- Consumes: `bindings.extract_bindings`, `bindings.normalize_type` and `bindings.BINDING_COLS` (Task 3).
- Produces:
  - the table `bindings(path, scope, name, kind, type_text, line, pos)`;
  - the column `symbols.returns VARCHAR` (a normalised return annotation; NULL for non-Python or none);
  - the key `"bindings"` in `extract()`'s result dict.

- [ ] **Step 1:** write the failing test. Append to `tests/test_bindings.py`:

  ```python
  from helpers import fresh_snapshot, make_repo, rows, snapshot, write


  def test_bindings_and_returns_are_stored_and_refreshed(tmp_path):
      root = make_repo(
          tmp_path / "r",
          {"a.py": "class Foo:\n    def make(self) -> 'Self':\n        return self\n\n\ndef f():\n    x = Foo()\n"},
      )
      assert rows(root, "SELECT scope, name, kind, type_text FROM bindings ORDER BY ALL") == [
          ("Foo.make", "self", "param", None),
          ("f", "x", "assign", "call:Foo"),
      ]
      assert rows(root, "SELECT qualname, returns FROM symbols WHERE returns IS NOT NULL") == [("Foo.make", "Foo")]
      write(root, "a.py", "def f():\n    x = Bar()\n")
      assert rows(root, "SELECT name, type_text FROM bindings") == [("x", "call:Bar")]
      tables = ("symbols", "bindings", "edges")
      assert snapshot(root, tables) == fresh_snapshot(root, tmp_path, tables)
  ```

- [ ] **Step 2:** run it.

  ```bash
  TMPDIR=/Volumes/research/tmp uv run pytest tests/test_bindings.py -k stored -q
  ```

  Expected: FAIL with `Catalog Error: Table with name bindings does not exist`.

- [ ] **Step 3:** schema. In `src/duckgrep/schema.py`:
  - set `SCHEMA_VERSION = 4`;
  - add `returns VARCHAR -- normalised return annotation (Python), NULL if none` after `exported BOOLEAN` in `symbols`;
  - add to `TABLES` after `refs`:

  ```sql
  CREATE TABLE IF NOT EXISTS bindings (  -- Python: what each name is bound to, per scope (see bindings.py)
      path      VARCHAR,
      scope     VARCHAR,            -- def/class qualname ('' = module); base and attr rows: the class
      name      VARCHAR,            -- the name, self.<attr> for attributes, '' for base rows
      kind      VARCHAR,            -- assign | annot | param | attr | base | global
      type_text VARCHAR,            -- dotted class name, call:<callee>, builtin type of a literal, or NULL
      line      INTEGER,
      pos       INTEGER             -- base rows: position in the class's bases
  );
  ```

- [ ] **Step 4:** extract. In `src/duckgrep/extract.py`:
  - import `from .bindings import extract_bindings, normalize_type`;
  - in the walker where symbols are appended, compute and append `returns` as the 12th element:

  ```python
                  returns = None
                  if lang == "python" and t == "function_definition":
                      rt = node.child_by_field_name("return_type")
                      if rt is not None:
                          cls_q = classes[-1] if classes else None
                          returns = normalize_type(_text(src, rt), cls_q.rsplit(".", 1)[-1] if cls_q else None)
  ```

  Then add `returns,` after the `_exported(...)` element of the tuple. Change the final return to:

  ```python
      binds = extract_bindings(tree.root_node, src, path) if lang == "python" else []
      return {"symbols": symbols, "refs": refs, "imports": imports, "modules": mods, "bindings": binds,
              "parse_errors": errors}
  ```

- [ ] **Step 5:** index. In `src/duckgrep/index.py`:
  - `SYMBOL_COLS` gains `"returns"` at the end;
  - add `from .bindings import BINDING_COLS`;
  - `PER_FILE_TABLES = ["symbols", "refs", "imports", "modules", "lines", "bindings"]`;
  - in the chunk loop, `rows = {"symbols": [], "refs": [], "imports": [], "modules": [], "bindings": []}`;
  - after `_insert(con, "modules", ...)`, add `_insert(con, "bindings", BINDING_COLS, rows["bindings"])`.

  The `for t in rows: rows[t].extend(res[t])` loop picks the new key up as is.

- [ ] **Step 6:** helpers. In `tests/helpers.py`'s `COLUMNS`, add `"bindings": "*",`.

- [ ] **Step 7:** run the test and the suite.

  ```bash
  TMPDIR=/Volumes/research/tmp uv run pytest tests/test_bindings.py -q && TMPDIR=/Volumes/research/tmp uv run pytest -q
  ```

  Expected: all pass. Fix any test that lists `symbols` columns positionally by adding `returns`. Search with `grep -rn "SELECT \* FROM symbols" tests`.

- [ ] **Step 8:** commit.

  ```bash
  git add -A src/duckgrep tests
  git commit -m "feat(index): store Python bindings and return annotations (schema v4)"
  ```

### Task 5: the `typed` tier

**Files:**
- Modify: `src/duckgrep/schema.py` (`INHERIT_DEPTH`, `WITH RECURSIVE`, the typed CTEs, a `t1` branch, `t1d` order, the `rest` external flag)
- Modify: `src/duckgrep/index.py` (`_compute_edges` passes `depth=schema.INHERIT_DEPTH`; `edges_version` key adds it)
- Test: `tests/test_typed.py`

**Interfaces:**
- Consumes: `bindings`, `symbols.returns` (Task 4), and the `imports_resolved` view.
- Produces:
  - edges with `resolution = 'typed'`;
  - the CTEs `py_def`, `py_ext`, `py_base`, `py_anc`, `py_open`, `py_type`, `py_hit` and `py_typed_ext`. Task 6 extends `py_typed_ext`, and Task 7's dirty marking mirrors `py_anc`'s walk by name.
  - `schema.INHERIT_DEPTH = 8`.

- [ ] **Step 1:** write the failing tests in `tests/test_typed.py`.

  ```python
  """The typed tier: receiver calls resolved through the receiver's class, inferred from syntax (Python)."""

  from helpers import make_repo, rows


  def edges_at(root, path, name):
      return rows(
          root,
          f"SELECT dst_path, dst_qualname, resolution FROM edges WHERE src_path = '{path}' AND name = '{name}' "
          "ORDER BY ALL",
      )


  BASE = "class Base:\n    def run(self):\n        return 1\n\n    def get(self):\n        return 2\n"
  OTHER = "class Other:\n    def run(self):\n        return 3\n\n    def get(self):\n        return 4\n"


  def test_inherited_self_and_builtin_named_methods(tmp_path):
      root = make_repo(
          tmp_path / "r",
          {
              "base.py": BASE,
              "other.py": OTHER,
              "child.py": "from base import Base\n\n\nclass Child(Base):\n    def go(self):\n"
              "        self.run()\n        return self.get()\n",
          },
      )
      assert edges_at(root, "child.py", "run") == [("base.py", "Base.run", "typed")]
      assert edges_at(root, "child.py", "get") == [("base.py", "Base.get", "typed")]


  def test_super_skips_the_class_itself(tmp_path):
      root = make_repo(
          tmp_path / "r",
          {
              "base.py": BASE,
              "child.py": "from base import Base\n\n\nclass Child(Base):\n    def run(self):\n"
              "        return super().run()\n",
          },
      )
      assert edges_at(root, "child.py", "run") == [("base.py", "Base.run", "typed")]


  def test_constructor_and_annotations(tmp_path):
      root = make_repo(
          tmp_path / "r",
          {
              "base.py": BASE,
              "other.py": OTHER,
              "use.py": "from typing import Optional\n\nfrom base import Base\nimport other\n\n\n"
              "def f():\n    x = Base()\n    return x.run()\n\n\n"
              "def g(p: 'Optional[Base]'):\n    return p.get()\n\n\n"
              "def h():\n    o = other.Other()\n    return o.run()\n",
          },
      )
      assert edges_at(root, "use.py", "run") == [("base.py", "Base.run", "typed"), ("other.py", "Other.run", "typed")]
      assert edges_at(root, "use.py", "get") == [("base.py", "Base.get", "typed")]


  def test_self_attr_from_param_constructor_and_class_annotation(tmp_path):
      root = make_repo(
          tmp_path / "r",
          {
              "conn.py": "class Conn:\n    def send(self):\n        return 1\n\n    def close(self):\n        return 2\n",
              "noise.py": "class Noise:\n    def send(self):\n        return 0\n\n    def close(self):\n        return 0\n",
              "svc.py": "from conn import Conn\n\n\nclass Svc:\n    pool: Conn\n\n"
              "    def __init__(self, c: Conn):\n        self.c = c\n        self.d = Conn()\n\n"
              "    def go(self):\n        self.c.send()\n        self.d.send()\n        return self.pool.close()\n",
          },
      )
      assert edges_at(root, "svc.py", "send") == [("conn.py", "Conn.send", "typed")] * 2
      assert edges_at(root, "svc.py", "close") == [("conn.py", "Conn.close", "typed")]


  def test_return_annotation_one_hop_and_classmethod_factory(tmp_path):
      root = make_repo(
          tmp_path / "r",
          {
              "base.py": BASE + "\n    @classmethod\n    def create(cls) -> 'Self':\n        return cls()\n",
              "make.py": "from base import Base\n\n\ndef make() -> Base:\n    return Base()\n",
              "use.py": "from base import Base\nfrom make import make\n\n\n"
              "def f():\n    x = make()\n    y = Base.create()\n    x.run()\n    return y.get()\n",
          },
      )
      assert edges_at(root, "use.py", "run") == [("base.py", "Base.run", "typed")]
      assert edges_at(root, "use.py", "get") == [("base.py", "Base.get", "typed")]


  def test_imported_module_level_instance(tmp_path):
      root = make_repo(
          tmp_path / "r",
          {
              "base.py": BASE + "\n\nshared = Base()\n",
              "other.py": OTHER,
              "use.py": "from base import shared\n\n\ndef f():\n    return shared.run()\n",
          },
      )
      assert edges_at(root, "use.py", "run") == [("base.py", "Base.run", "typed")]


  def test_class_receiver_reaches_inherited_methods(tmp_path):
      root = make_repo(
          tmp_path / "r",
          {
              "base.py": "class Base:\n    @staticmethod\n    def helper():\n        return 1\n",
              "child.py": "from base import Base\n\n\nclass Child(Base):\n    pass\n\n\ndef f():\n"
              "    return Child.helper()\n",
          },
      )
      assert edges_at(root, "child.py", "helper") == [("base.py", "Base.helper", "typed")]


  def test_nearest_ancestor_wins(tmp_path):
      root = make_repo(
          tmp_path / "r",
          {"m.py": "class A:\n    def m(self):\n        return 1\n\n\nclass B(A):\n    def m(self):\n        return 2\n\n\n"
           "class C(B):\n    pass\n\n\ndef g():\n    c = C()\n    return c.m()\n"},
      )
      assert edges_at(root, "m.py", "m") == [("m.py", "B.m", "typed")]


  def test_closures_and_module_bindings(tmp_path):
      root = make_repo(
          tmp_path / "r",
          {
              "base.py": BASE,
              "use.py": "from base import Base\n\nx = Base()\n\n\ndef f():\n    return x.run()\n\n\n"
              "def g():\n    y = Base()\n\n    def inner():\n        return y.get()\n\n    return inner\n",
          },
      )
      assert edges_at(root, "use.py", "run") == [("base.py", "Base.run", "typed")]
      assert edges_at(root, "use.py", "get") == [("base.py", "Base.get", "typed")]


  def test_two_bindings_block_inference(tmp_path):
      root = make_repo(
          tmp_path / "r",
          {"base.py": BASE, "other.py": OTHER,
           "use.py": "from base import Base\nfrom other import Other\n\n\ndef f():\n    x = Base()\n    x = Other()\n"
           "    return x.run()\n"},
      )
      assert {r[2] for r in edges_at(root, "use.py", "run")} == {"name"}


  def test_branch_rebinding_blocks_inference(tmp_path):
      root = make_repo(
          tmp_path / "r",
          {"base.py": BASE, "other.py": OTHER,
           "use.py": "from base import Base\nfrom other import Other\n\n\ndef f(c):\n    if c:\n        x = Base()\n"
           "    else:\n        x = Other()\n    for _ in range(2):\n        x.run()\n"},
      )
      assert {r[2] for r in edges_at(root, "use.py", "run")} == {"name"}


  def test_unannotated_param_and_inner_scope_shadowing_block_inference(tmp_path):
      root = make_repo(
          tmp_path / "r",
          {"base.py": BASE, "other.py": OTHER,
           "use.py": "from base import Base\n\nx = Base()\n\n\ndef f(x):\n    return x.run()\n"},
      )
      assert {r[2] for r in edges_at(root, "use.py", "run")} == {"name"}


  def test_inner_scope_shadowing_blocks_inference(tmp_path):
      root = make_repo(
          tmp_path / "r",
          {"base.py": BASE, "other.py": OTHER,
           "use.py": "from base import Base\n\n\ndef f(items):\n    x = Base()\n\n    def g(x):\n        return x.run()\n\n"
           "    return [x.get() for x in items]\n"},
      )
      assert {r[2] for r in edges_at(root, "use.py", "run")} == {"name"}
      assert {r[2] for r in edges_at(root, "use.py", "get")} == {"ambiguous"}


  def test_imports_decide_between_same_named_classes(tmp_path):
      root = make_repo(
          tmp_path / "r",
          {"a/m.py": "class Foo:\n    def run(self):\n        return 1\n",
           "b/m.py": "class Foo:\n    def run(self):\n        return 2\n",
           "use.py": "from b.m import Foo\n\n\ndef f():\n    return Foo().run()\n\n\ndef g():\n    x = Foo()\n"
           "    return x.run()\n"},
      )
      assert edges_at(root, "use.py", "run")[-1] == ("b/m.py", "Foo.run", "typed")
      assert ("a/m.py", "Foo.run", "typed") not in edges_at(root, "use.py", "run")


  def test_metaclass_keyword_is_not_a_base(tmp_path):
      root = make_repo(
          tmp_path / "r",
          {"m.py": "class Meta(type):\n    def run(cls):\n        return 1\n\n\nclass C(metaclass=Meta):\n    def go(self):\n"
           "        return self.run()\n"},
      )
      assert ("m.py", "Meta.run", "typed") not in edges_at(root, "m.py", "run")


  def test_opaque_base_keeps_todays_tiers(tmp_path):
      root = make_repo(
          tmp_path / "r",
          {"base.py": BASE, "m.py": "from collections import namedtuple\n\n\nclass C(namedtuple('P', 'a')):\n"
           "    def go(self):\n        return self.run()\n\n\ndef make_base():\n    return object\n\n\n"
           "class D(make_base()):\n    def go(self):\n        return self.run()\n"},
      )
      assert {r[2] for r in edges_at(root, "m.py", "run")} == {"name"}


  def test_cyclic_and_deep_hierarchies_terminate(tmp_path):
      chain = "".join(f"class K{i}(K{i + 1}):\n    pass\n\n\n" for i in range(12)) + "class K12:\n    def deep(self):\n        return 1\n\n\n"
      root = make_repo(
          tmp_path / "r",
          {"a.py": "from b import B\n\n\nclass A(B):\n    def go(self):\n        return self.nope()\n",
           "b.py": "from a import A\n\n\nclass B(A):\n    pass\n",
           "k.py": chain + "def f():\n    k = K0()\n    return k.deep()\n"},
      )
      assert {r[2] for r in edges_at(root, "a.py", "nope")} <= {"unresolved", "name"}
      assert ("k.py", "K12.deep", "typed") not in edges_at(root, "k.py", "deep")
  ```

- [ ] **Step 2:** run them.

  ```bash
  TMPDIR=/Volumes/research/tmp uv run pytest tests/test_typed.py -q
  ```

  Expected: most FAIL, because no `typed` rows exist. The blocking tests (`two_bindings`, `branch`, `shadowing`, `opaque`) may already pass, which is fine.

- [ ] **Step 3:** add the constant and a recursive `WITH`. In `src/duckgrep/schema.py`:
  - add `INHERIT_DEPTH = 8  # base classes walked for typed receivers` after `NAME_CAP`;
  - change the template's opening line from `WITH r AS (` to `WITH RECURSIVE r AS (`;
  - in the tier comment, list `typed`.

  In `index.py`:
  - `_compute_edges` becomes `con.execute(schema.EDGES_COMPUTE.format(source=source, where=where, cap=schema.NAME_CAP, depth=schema.INHERIT_DEPTH))`;
  - `edges_version`'s key becomes `f"{schema.EDGES_COMPUTE}\0{schema.VIEWS}\0{schema.NAME_CAP}\0{schema.INHERIT_DEPTH}"`.

- [ ] **Step 4:** insert the typed CTEs into `_EDGES_TEMPLATE`, directly after the `sym AS (...)` CTE and before `t1 AS (`.

  ```sql
  -- ---- typed tier (Python): the receiver's class, inferred from per-file facts (bindings, symbols.returns)
  py_imp AS (SELECT * FROM imports_resolved WHERE family = 'py'),
  py_rx AS (  -- names a module imports from another: one re-export hop
      SELECT path AS mod_path, "local", name, target_path FROM py_imp
      WHERE target_path IS NOT NULL AND NOT target_is_module AND "local" IS NOT NULL AND name IS NOT NULL
  ),
  py_tx AS (  -- dotted names to resolve, per file: binding types, callees, return annotations, class receivers
      SELECT DISTINCT path, text, split_part(text, '.', 1) AS head,
             CASE WHEN contains(text, '.') THEN regexp_replace(text, '[.][^.]*$', '') END AS prefix,
             regexp_extract(text, '[^.]*$') AS tail
      FROM (
          SELECT path, CASE WHEN starts_with(type_text, 'call:') THEN substr(type_text, 6) ELSE type_text END AS text
          FROM bindings WHERE type_text IS NOT NULL
        UNION SELECT path, returns FROM symbols WHERE returns IS NOT NULL
        UNION SELECT path, receiver FROM r
          WHERE family = 'py' AND regexp_matches(receiver, '^[A-Za-z_][A-Za-z0-9_]*([.][A-Za-z_][A-Za-z0-9_]*)*$')
            AND regexp_matches(recv_last, '^[A-Z]')
      )
  ),
  py_def AS (  -- a dotted name in a file -> the definition it names (class, function, Class.method): local first
      SELECT * FROM (
          SELECT t.path, t.text, s.path AS dpath, s.qualname AS dqual, s.kind AS dkind, s.returns, 0 AS p
          FROM py_tx t JOIN symbols s ON s.path = t.path AND s.qualname = t.text
        UNION ALL  -- from m import Foo  (Foo, Foo.create)
          SELECT t.path, t.text, s.path, s.qualname, s.kind, s.returns, 1
          FROM py_tx t
          JOIN py_imp i ON i.path = t.path AND i."local" = t.head AND i.name IS NOT NULL AND NOT i.target_is_module
          JOIN symbols s ON s.path = i.target_path AND s.qualname = i.name || substr(t.text, length(t.head) + 1)
        UNION ALL  -- ... re-exported by m
          SELECT t.path, t.text, s.path, s.qualname, s.kind, s.returns, 2
          FROM py_tx t
          JOIN py_imp i ON i.path = t.path AND i."local" = t.head AND i.name IS NOT NULL AND NOT i.target_is_module
          JOIN py_rx x ON x.mod_path = i.target_path AND x."local" = i.name
          JOIN symbols s ON s.path = x.target_path AND s.qualname = x.name || substr(t.text, length(t.head) + 1)
        UNION ALL  -- mod.Foo, a.b.Foo through a module import
          SELECT t.path, t.text, s.path, s.qualname, s.kind, s.returns, 3
          FROM py_tx t
          JOIN py_imp i ON i.path = t.path AND t.prefix IS NOT NULL AND i.target_path IS NOT NULL
               AND ((i."local" = t.prefix AND (i.name IS NULL OR i.target_is_module))
                    OR (i.name IS NULL AND i.alias IS NULL AND i.module = t.prefix))
          JOIN symbols s ON s.path = i.target_path AND s.parent IS NULL AND s.name = t.tail
        UNION ALL  -- ... re-exported by that module
          SELECT t.path, t.text, s.path, s.qualname, s.kind, s.returns, 4
          FROM py_tx t
          JOIN py_imp i ON i.path = t.path AND t.prefix IS NOT NULL AND i.target_path IS NOT NULL
               AND ((i."local" = t.prefix AND (i.name IS NULL OR i.target_is_module))
                    OR (i.name IS NULL AND i.alias IS NULL AND i.module = t.prefix))
          JOIN py_rx x ON x.mod_path = i.target_path AND x."local" = t.tail
          JOIN symbols s ON s.path = x.target_path AND s.parent IS NULL AND s.name = x.name
      )
      QUALIFY row_number() OVER (PARTITION BY path, text ORDER BY p, dpath, dqual) = 1
  ),
  py_ext AS (  -- a dotted name in a file that names something outside the repo: an unresolved import, a builtin
      SELECT t.path, t.text FROM py_tx t
      ANTI JOIN py_def d ON d.path = t.path AND d.text = t.text
      WHERE EXISTS (SELECT 1 FROM py_imp i WHERE i.path = t.path AND i."local" = t.head AND i.target_path IS NULL)
         OR (t.text IN (SELECT name FROM builtin_globals WHERE family = 'py')
             AND NOT EXISTS (SELECT 1 FROM py_imp i WHERE i.path = t.path AND i."local" = t.head)
             AND NOT EXISTS (SELECT 1 FROM symbols s WHERE s.path = t.path AND s.name = t.head AND s.parent IS NULL))
  ),
  py_base AS (  -- class -> its bases in order; ext: outside the repo; opaque: can't be named
      SELECT b.path, b.scope AS qual, b.pos, d.dpath AS bpath, d.dqual AS bqual,
             x.text IS NOT NULL AS ext, d.dpath IS NULL AND x.text IS NULL AS opaque
      FROM bindings b
      LEFT JOIN (SELECT * FROM py_def WHERE dkind = 'class') d ON d.path = b.path AND d.text = b.type_text
      LEFT JOIN py_ext x ON x.path = b.path AND x.text = b.type_text
      WHERE b.kind = 'base'
  ),
  py_anc AS (  -- class -> itself and its in-repo ancestors (depth-first, left to right), at most {depth} deep
      SELECT path, qualname AS qual, path AS apath, qualname AS aqual, 0 AS depth, []::INTEGER[] AS ord
      FROM symbols WHERE lang = 'python' AND kind = 'class'
    UNION ALL
      SELECT a.path, a.qual, b.bpath, b.bqual, a.depth + 1, list_append(a.ord, b.pos)
      FROM py_anc a JOIN py_base b ON b.path = a.apath AND b.qual = a.aqual
      WHERE b.bpath IS NOT NULL AND a.depth < {depth}
  ),
  py_open AS (  -- classes with a base outside the repo, or one that can't be named, anywhere in their ancestry
      SELECT a.path, a.qual, bool_or(b.ext) AS ext, bool_or(b.opaque) AS opaque
      FROM py_anc a JOIN py_base b ON b.path = a.apath AND b.qual = a.aqual
      GROUP BY ALL
  ),
  py_r AS (SELECT * FROM r WHERE family = 'py' AND kind = 'call' AND receiver IS NOT NULL),
  py_scoped AS (  -- bare-name receivers: the bindings of each enclosing scope that binds the name
      SELECT r.path, r.line, r.col, b.scope,
             count(*) FILTER (WHERE b.type_text IS NULL OR b.kind = 'global') AS n_untyped,
             count(DISTINCT b.type_text) AS n_types, min(b.type_text) AS type_text
      FROM py_r r
      JOIN bindings b ON b.path = r.path AND b.name = r.receiver AND b.kind IN ('assign', 'annot', 'param', 'global')
           AND (b.scope = r.scope OR b.scope = '' OR starts_with(r.scope, b.scope || '.'))
      WHERE r.receiver NOT IN ('self', 'cls') AND regexp_matches(r.receiver, '^[A-Za-z_][A-Za-z0-9_]*$')
      GROUP BY ALL
  ),
  py_bind AS (  -- receiver -> (file to resolve its type in, type text), when every binding agrees
      SELECT path, line, col, path AS tpath, type_text FROM (
          SELECT * FROM py_scoped
          QUALIFY row_number() OVER (PARTITION BY path, line, col ORDER BY length(scope) DESC) = 1
      ) WHERE n_untyped = 0 AND n_types = 1
    UNION ALL  -- `from m import cache`, with cache bound once at the top of m
      SELECT r.path, r.line, r.col, i.target_path, min(b.type_text)
      FROM py_r r
      JOIN py_imp i ON i.path = r.path AND i."local" = r.receiver AND i.name IS NOT NULL AND NOT i.target_is_module
      JOIN bindings b ON b.path = i.target_path AND b.scope = '' AND b.name = i.name
           AND b.kind IN ('assign', 'annot', 'global')
      ANTI JOIN py_scoped s ON s.path = r.path AND s.line = r.line AND s.col = r.col
      GROUP BY ALL
      HAVING count(*) FILTER (WHERE b.type_text IS NULL OR b.kind = 'global') = 0 AND count(DISTINCT b.type_text) = 1
    UNION ALL  -- self.attr / cls.attr: the nearest class in the ancestry that binds the attribute
      SELECT path, line, col, apath, type_text FROM (
          SELECT r.path, r.line, r.col, a.apath,
                 count(*) FILTER (WHERE b.type_text IS NULL) AS n_untyped,
                 count(DISTINCT b.type_text) AS n_types, min(b.type_text) AS type_text
          FROM py_r r
          JOIN py_anc a ON a.path = r.path AND a.qual = r.scope_class
          JOIN bindings b ON b.path = a.apath AND b.scope = a.aqual AND b.kind = 'attr'
               AND b.name = 'self.' || regexp_extract(r.receiver, '^(?:self|cls)[.]([A-Za-z_][A-Za-z0-9_]*)$', 1)
          WHERE regexp_matches(r.receiver, '^(self|cls)[.][A-Za-z_][A-Za-z0-9_]*$')
          GROUP BY r.path, r.line, r.col, a.apath, a.depth, a.ord
          QUALIFY row_number() OVER (PARTITION BY r.path, r.line, r.col ORDER BY a.depth, a.ord) = 1
      ) WHERE n_untyped = 0 AND n_types = 1
  ),
  py_type AS (  -- typed receiver -> its class (cpath, cqual) and where the method search starts, or ext
      SELECT b.path, b.line, b.col, d.dpath AS cpath, d.dqual AS cqual, 0 AS from_depth, FALSE AS ext
      FROM py_bind b JOIN py_def d ON d.path = b.tpath AND d.text = b.type_text AND d.dkind = 'class'
    UNION ALL  -- x = Foo(...)
      SELECT b.path, b.line, b.col, d.dpath, d.dqual, 0, FALSE
      FROM py_bind b JOIN py_def d ON d.path = b.tpath AND d.text = substr(b.type_text, 6) AND d.dkind = 'class'
      WHERE starts_with(b.type_text, 'call:')
    UNION ALL  -- x = make(...) / Foo.create(...): one hop of the callee's return annotation, in the callee's file
      SELECT b.path, b.line, b.col, c.dpath, c.dqual, 0, FALSE
      FROM py_bind b
      JOIN py_def d ON d.path = b.tpath AND d.text = substr(b.type_text, 6) AND d.dkind IN ('function', 'method')
      JOIN py_def c ON c.path = d.dpath AND c.text = d.returns AND c.dkind = 'class'
      WHERE starts_with(b.type_text, 'call:')
    UNION ALL  -- self / cls: the enclosing class; super(): its bases
      SELECT path, line, col, path, scope_class, CASE WHEN receiver = 'super()' THEN 1 ELSE 0 END, FALSE
      FROM py_r WHERE receiver IN ('self', 'cls', 'super()') AND scope_class IS NOT NULL
    UNION ALL  -- Foo.m(), mod.Foo.m(): a class receiver (qualified covers Foo's own methods; this adds its bases)
      SELECT r.path, r.line, r.col, d.dpath, d.dqual, 0, FALSE
      FROM py_r r JOIN py_def d ON d.path = r.path AND d.text = r.receiver AND d.dkind = 'class'
  ),
  py_hit AS (  -- the method on that class or its nearest in-repo ancestor
      SELECT t.path, t.line, t.col, s.path AS dst_path, s.qualname AS dst_qualname, s.kind AS dst_kind,
             s.start_line AS dst_line
      FROM py_type t
      JOIN py_r r ON r.path = t.path AND r.line = t.line AND r.col = t.col
      JOIN py_anc a ON a.path = t.cpath AND a.qual = t.cqual AND a.depth >= t.from_depth
      JOIN symbols s ON s.path = a.apath AND s.parent = a.aqual AND s.name = r.name AND s.kind IN ('method', 'class')
      WHERE NOT t.ext
      QUALIFY row_number() OVER (PARTITION BY t.path, t.line, t.col ORDER BY a.depth, a.ord, s.start_line) = 1
  ),
  py_typed_ext AS (  -- typed receivers whose method can only be outside the repo (extended in Task 6)
      SELECT t.path, t.line, t.col FROM py_type t
      JOIN py_open o ON o.path = t.cpath AND o.qual = t.cqual AND o.ext AND NOT o.opaque
      ANTI JOIN py_hit h ON h.path = t.path AND h.line = t.line AND h.col = t.col
      WHERE NOT t.ext
  ),
  ```

- [ ] **Step 5:** wire the tier in.
  - In `t1`, add a last branch before the closing `)` of `t1 AS (...)`:

    ```sql
      UNION ALL
        -- the receiver's class, inferred from syntax (constructor, annotation, base classes)
        SELECT r.*, h.dst_path, h.dst_qualname, h.dst_kind, h.dst_line, 'typed'
        FROM r JOIN py_hit h ON h.path = r.path AND h.line = r.line AND h.col = r.col
    ```

  - In `t1d`, change the label list to `['self', 'local', 'package', 'import', 'module', 'qualified', 'typed']`.
  - In `rest`, add `LEFT JOIN py_typed_ext te ON te.path = r.path AND te.line = r.line AND te.col = r.col` next to the other joins. Change `external` to `(x.path IS NOT NULL OR g.path IS NOT NULL OR te.path IS NOT NULL) AS external`.

- [ ] **Step 6:** run the tests.

  ```bash
  TMPDIR=/Volumes/research/tmp uv run pytest tests/test_typed.py -q
  ```

  Expected: PASS. If one fails, inspect the intermediate CTE with a throwaway query. For example, `uv run duckgrep -C <tmp repo> q "SELECT * FROM bindings"`, or run the template with `INSERT INTO edges` replaced by a `SELECT` from the CTE in question. Fix the SQL, not the test, unless the test contradicts the spec.

- [ ] **Step 7:** run the whole suite. Other languages must not change.

  ```bash
  TMPDIR=/Volumes/research/tmp uv run pytest -q
  ```

  Expected: PASS. A failure in an existing Python resolution test where `typed` now precedes `name` is expected only if the test's receiver is typable. In that case, update the expectation to `typed` and check the target by hand.

- [ ] **Step 8:** commit.

  ```bash
  git add src/duckgrep/schema.py src/duckgrep/index.py tests/test_typed.py
  git commit -m "feat(resolve): typed tier: receiver class from constructors, annotations and base classes"
  ```

### Task 6: external types become `unresolved`

**Files:**
- Modify: `src/duckgrep/schema.py` (`py_typed_ext`)
- Test: `tests/test_typed.py` (append)

**Interfaces:**
- Consumes: `py_type.ext`, `py_bind`, `py_def`, `py_ext` (Task 5).
- Produces: typed receivers of external types count as `external` in `rest`. That gives them one `unresolved` row and no `name` or `ambiguous` rows.

- [ ] **Step 1:** write the failing tests. Append:

  ```python
  def test_external_and_literal_types_are_unresolved(tmp_path):
      root = make_repo(
          tmp_path / "r",
          {"buf.py": "class Buf:\n    def getvalue(self):\n        return 1\n\n    def append(self, x):\n        return x\n",
           "use.py": "import io\n\n\ndef f():\n    out = io.StringIO()\n    return out.getvalue()\n\n\n"
           "def g():\n    xs = []\n    xs.append(1)\n    return xs\n"},
      )
      assert edges_at(root, "use.py", "getvalue") == [(None, None, "unresolved")]
      assert edges_at(root, "use.py", "append") == [(None, None, "unresolved")]


  def test_methods_missing_from_a_class_with_an_external_base_are_unresolved(tmp_path):
      root = make_repo(
          tmp_path / "r",
          {"other.py": "class Other:\n    def assertEqual(self, a, b):\n        return a == b\n",
           "t.py": "import unittest\n\n\nclass T(unittest.TestCase):\n    def test(self):\n"
           "        self.assertEqual(1, 1)\n"},
      )
      assert edges_at(root, "t.py", "assertEqual") == [(None, None, "unresolved")]
  ```

- [ ] **Step 2:** run them.

  ```bash
  TMPDIR=/Volumes/research/tmp uv run pytest tests/test_typed.py -k "external or literal" -q
  ```

  Expected: the first test FAILS (today `getvalue` gets a `name` edge to `Buf.getvalue`). The second may already pass through Task 5's ancestry rule.

- [ ] **Step 3:** extend `py_typed_ext` to the bindings whose type is external. That means:
  - an annotation or a callee that names an external import or builtin;
  - a literal's builtin type;
  - a repo callee whose `returns` names one.

  Replace the CTE with:

  ```sql
  py_type_ext AS (  -- receivers whose bound type is outside the repo
      SELECT b.path, b.line, b.col FROM py_bind b
      JOIN py_ext x ON x.path = b.tpath
           AND x.text = CASE WHEN starts_with(b.type_text, 'call:') THEN substr(b.type_text, 6) ELSE b.type_text END
    UNION
      SELECT b.path, b.line, b.col FROM py_bind b
      JOIN py_def d ON d.path = b.tpath AND d.text = substr(b.type_text, 6) AND d.dkind IN ('function', 'method')
      JOIN py_ext x ON x.path = d.dpath AND x.text = d.returns
      WHERE starts_with(b.type_text, 'call:')
  ),
  py_typed_ext AS (  -- typed receivers whose method can only be outside the repo
      SELECT path, line, col FROM py_type_ext
      ANTI JOIN py_type t USING (path, line, col)
    UNION
      SELECT t.path, t.line, t.col FROM py_type t
      JOIN py_open o ON o.path = t.cpath AND o.qual = t.cqual AND o.ext AND NOT o.opaque
      ANTI JOIN py_hit h ON h.path = t.path AND h.line = t.line AND h.col = t.col
  ),
  ```

  The `ANTI JOIN py_type` keeps a receiver that also has an in-repo class from being called external. Literals reach `py_ext` because their type text (`list`, `str`) is a Python builtin global and isn't rebound in the file.

- [ ] **Step 4:** run the tests and the suite.

  ```bash
  TMPDIR=/Volumes/research/tmp uv run pytest tests/test_typed.py -q && TMPDIR=/Volumes/research/tmp uv run pytest -q
  ```

  Expected: PASS.

- [ ] **Step 5:** commit.

  ```bash
  git add src/duckgrep/schema.py tests/test_typed.py
  git commit -m "feat(resolve): receivers of external types are unresolved, not name candidates"
  ```

### Task 7: keep incremental edges equal to a full rebuild

**Files:**
- Modify: `src/duckgrep/schema.py` (add `TYPED_DIRTY_SEED`, `TYPED_DIRTY`)
- Modify: `src/duckgrep/index.py` (capture `_aff_types` before the delete; call the typed marking from `_mark_edges_dirty`; drop the temp table)
- Test: `tests/test_typed_freshness.py`

**Interfaces:**
- Consumes: `bindings`, `symbols.returns`, `INHERIT_DEPTH`.
- Produces:
  - `schema.TYPED_DIRTY_SEED`: SQL taking `{files}` (a subquery of paths), returning one column `name` of class-ish short names;
  - `schema.TYPED_DIRTY`: an `INSERT INTO edges_dirty` statement that reads `_aff_types` and the current tables.

- [ ] **Step 1:** write the failing tests in `tests/test_typed_freshness.py`.

  ```python
  """Incremental edges equal a full rebuild after edits that change what a typed receiver resolves to."""

  import random

  from helpers import fresh_snapshot, make_repo, rows, snapshot, write

  ALL = ("files", "symbols", "refs", "imports", "modules", "lines", "bindings", "edges")
  BASE = "class Base:\n    def run(self):\n        return 1\n"
  GRAND = "class Grand:\n    def deep(self):\n        return 1\n"
  OTHER = "class Other:\n    def deep(self):\n        return 2\n\n    def run(self):\n        return 3\n"
  USE = "from child import Child\n\n\ndef f():\n    x = Child()\n    x.run()\n    return x.deep()\n"


  def same(root, tmp_path):
      rows(root, "SELECT count(*) FROM edges")  # freshen + sync
      assert snapshot(root, ALL) == fresh_snapshot(root, tmp_path, ALL)


  def repo(tmp_path):
      return make_repo(
          tmp_path / "r",
          {"grand.py": GRAND, "other.py": OTHER,
           "base.py": "from grand import Grand\n\n\n" + BASE.replace("class Base:", "class Base(Grand):"),
           "child.py": "from base import Base\n\n\nclass Child(Base):\n    pass\n", "use.py": USE},
      )


  def test_base_changes_its_base_in_another_file(tmp_path):
      root = repo(tmp_path)
      same(root, tmp_path)
      write(root, "base.py", "from other import Other\n\n\n" + BASE.replace("class Base:", "class Base(Other):"))
      same(root, tmp_path)


  def test_base_gains_and_loses_a_method(tmp_path):
      root = repo(tmp_path)
      same(root, tmp_path)
      write(root, "base.py", "from grand import Grand\n\n\nclass Base(Grand):\n    def deep(self):\n        return 9\n")
      same(root, tmp_path)


  def test_return_annotation_changes(tmp_path):
      root = make_repo(
          tmp_path / "r",
          {"grand.py": GRAND, "other.py": OTHER,
           "make.py": "from grand import Grand\n\n\ndef make() -> Grand:\n    return Grand()\n",
           "use.py": "from make import make\n\n\ndef f():\n    y = make()\n    return y.deep()\n"},
      )
      same(root, tmp_path)
      write(root, "make.py", "from other import Other\n\n\ndef make() -> Other:\n    return Other()\n")
      same(root, tmp_path)


  def test_constructor_class_renamed_or_deleted(tmp_path):
      root = repo(tmp_path)
      same(root, tmp_path)
      write(root, "child.py", "from base import Base\n\n\nclass Kid(Base):\n    pass\n")
      same(root, tmp_path)


  def test_attr_binding_changes_in_a_base_class(tmp_path):
      root = make_repo(
          tmp_path / "r",
          {"grand.py": GRAND, "other.py": OTHER,
           "base.py": "from grand import Grand\n\n\nclass Base:\n    def __init__(self):\n        self.h = Grand()\n",
           "child.py": "from base import Base\n\n\nclass Child(Base):\n    def go(self):\n        return self.h.deep()\n"},
      )
      same(root, tmp_path)
      write(root, "base.py", "from other import Other\n\n\nclass Base:\n    def __init__(self):\n        self.h = Other()\n")
      same(root, tmp_path)


  def test_module_level_instance_changes_type(tmp_path):
      root = make_repo(
          tmp_path / "r",
          {"grand.py": GRAND, "other.py": OTHER, "inst.py": "from grand import Grand\n\nshared = Grand()\n",
           "use.py": "from inst import shared\n\n\ndef f():\n    return shared.deep()\n"},
      )
      same(root, tmp_path)
      write(root, "inst.py", "from other import Other\n\nshared = Other()\n")
      same(root, tmp_path)


  def test_second_binding_drops_inference(tmp_path):
      root = repo(tmp_path)
      same(root, tmp_path)
      write(root, "use.py", USE.replace("    x.run()\n", "    x = object()\n    x.run()\n"))
      same(root, tmp_path)


  POOL = {
      "base.py": [
          "from grand import Grand\n\n\nclass Base(Grand):\n    def run(self):\n        return 1\n",
          "from other import Other\n\n\nclass Base(Other):\n    pass\n",
          "class Base:\n    def deep(self):\n        return 0\n",
      ],
      "child.py": [
          "from base import Base\n\n\nclass Child(Base):\n    pass\n",
          "from other import Other\n\n\nclass Child(Other):\n    def run(self):\n        return 5\n",
          "from base import Base\n\n\nclass Child(Base):\n    def __init__(self):\n        self.o = Base()\n",
      ],
      "inst.py": ["from child import Child\n\nshared = Child()\n", "from other import Other\n\nshared = Other()\n", ""],
      "make.py": [
          "from child import Child\n\n\ndef make() -> Child:\n    return Child()\n",
          "from grand import Grand\n\n\ndef make() -> 'Grand':\n    return Grand()\n",
      ],
  }


  def test_random_edit_sequences(tmp_path):
      rng = random.Random(20261003)
      files = {"grand.py": GRAND, "other.py": OTHER, **{k: v[0] for k, v in POOL.items()},
               "use.py": "from child import Child\nfrom inst import shared\nfrom make import make\n\n\n"
               "def f():\n    x = Child()\n    y = make()\n    x.run()\n    y.deep()\n    shared.run()\n"
               "    return x.deep()\n"}
      root = make_repo(tmp_path / "r", files)
      same(root, tmp_path)
      for _ in range(12):
          path = rng.choice(sorted(POOL))
          write(root, path, rng.choice(POOL[path]))
          same(root, tmp_path)
  ```

- [ ] **Step 2:** run them.

  ```bash
  TMPDIR=/Volumes/research/tmp uv run pytest tests/test_typed_freshness.py -q
  ```

  Expected: some FAIL with an edges mismatch: the incremental index keeps a stale `typed` edge.

- [ ] **Step 3:** add the marking SQL. In `src/duckgrep/schema.py`:

  ```python
  # Typed edges depend on other files' signature-level facts: the class a type names, its bases and their
  # methods, attr bindings, a callee's return annotation, a module-level instance's type. When files change,
  # the names those facts mention (before and after) are expanded to their ancestors by name, and every method
  # of those classes is marked dirty by name. Over-approximate on purpose; {files} is a subquery of paths.
  TYPED_DIRTY_SEED = """
  SELECT name FROM symbols WHERE path IN ({files}) AND lang = 'python' AND kind = 'class'
  UNION SELECT regexp_extract(CASE WHEN starts_with(type_text, 'call:') THEN substr(type_text, 6) ELSE type_text END,
                              '[^.]*$')
        FROM bindings WHERE path IN ({files}) AND type_text IS NOT NULL AND (kind IN ('base', 'attr') OR scope = '')
  UNION SELECT regexp_extract(returns, '[^.]*$') FROM symbols WHERE path IN ({files}) AND returns IS NOT NULL
  UNION SELECT "local" FROM imports WHERE path IN ({files}) AND family = 'py' AND "local" IS NOT NULL
  """

  TYPED_DIRTY = """
  INSERT INTO edges_dirty
  WITH RECURSIVE seed(name) AS (
      SELECT name FROM _aff_types
      UNION {seed_after}
  ),
  start(name) AS (
      SELECT name FROM seed
      UNION SELECT regexp_extract(s.returns, '[^.]*$') FROM symbols s SEMI JOIN seed ON seed.name = s.name
      WHERE s.returns IS NOT NULL
  ),
  cls(name, depth) AS (
      SELECT name, 0 FROM start
    UNION
      SELECT regexp_extract(b.type_text, '[^.]*$'), c.depth + 1
      FROM cls c JOIN bindings b ON b.kind = 'base' AND b.type_text IS NOT NULL
           AND regexp_extract(b.scope, '[^.]*$') = c.name
      WHERE c.depth < {depth}
  )
  SELECT DISTINCT 'name', NULL, s.name FROM symbols s
  SEMI JOIN (SELECT DISTINCT name FROM cls) c ON regexp_extract(s.parent, '[^.]*$') = c.name
  WHERE s.lang = 'python' AND s.kind IN ('method', 'class')
  """
  ```

- [ ] **Step 4:** call it. In `index.py`'s freshen, right after `_aff_keys` is created (before the `DELETE` loop), add:

  ```python
              con.execute(
                  "CREATE OR REPLACE TEMP TABLE _aff_types AS "
                  + schema.TYPED_DIRTY_SEED.format(files="SELECT path FROM _chg")
              )
  ```

  At the end of `_mark_edges_dirty`, after the star-import insert and before `DROP TABLE IF EXISTS _new_keys`, add the call below. Files marked `path`-dirty include `_chg`, importers of moved keys and star importers; their facts count as "after" facts too.

  ```python
      con.execute(
          schema.TYPED_DIRTY.format(
              seed_after=schema.TYPED_DIRTY_SEED.format(files="SELECT path FROM edges_dirty WHERE kind = 'path'"),
              depth=schema.INHERIT_DEPTH,
          )
      )
  ```

  In the cleanup loop, change `("_chg", "_aff_names", "_aff_keys")` to `("_chg", "_aff_names", "_aff_keys", "_aff_types")`.

- [ ] **Step 5:** run the tests.

  ```bash
  TMPDIR=/Volumes/research/tmp uv run pytest tests/test_typed_freshness.py -q && TMPDIR=/Volumes/research/tmp uv run pytest -q
  ```

  Expected: PASS. If one test still diverges, print the differing edges:

  ```python
  a, b = snapshot(root, ("edges",)), fresh_snapshot(root, tmp_path, ("edges",))
  print(set(a["edges"]) ^ set(b["edges"]))
  ```

  Find which fact the stale edge used (its class's file, a base, an attr, a return) and make sure that fact's name reaches `_aff_types` or the after-seed. Fix the seed, not the test.

- [ ] **Step 6:** commit.

  ```bash
  git add src/duckgrep/schema.py src/duckgrep/index.py tests/test_typed_freshness.py
  git commit -m "feat(freshen): typed edges stay equal to a full rebuild across cross-file edits"
  ```

### Task 8: the tool description, README and roadmap

**Files:**
- Modify: `src/duckgrep/schema.py` (`SCHEMA_DOC` resolution lines)
- Modify: `README.md` (the FAQ "How does it resolve calls without a type checker?")
- Modify: `docs/roadmap.md` (item 1 moves from Next to done; Phase 2 is listed)
- Modify: `CLAUDE.md` (the resolution tiers bullet: add `typed`, `bindings`, `INHERIT_DEPTH`)
- Modify: `site/index.html` (the FAQ's resolution list), so the site matches the README
- Test: `tests/test_surface.py`

- [ ] **Step 1:** write the failing test. Append to `tests/test_surface.py`:

  ```python
  def test_the_tool_description_names_the_typed_tier():
      from duckgrep.schema import SCHEMA_DOC

      assert "typed" in SCHEMA_DOC
  ```

- [ ] **Step 2:** run it.

  ```bash
  TMPDIR=/Volumes/research/tmp uv run pytest tests/test_surface.py -q
  ```

  Expected: the new test FAILS.

- [ ] **Step 3:** edit `SCHEMA_DOC`'s resolution block to:

  ```
      self | local | package | import | module | qualified | typed   confident (scope and import analysis; typed:
                  the receiver's class read from a constructor, annotation or base class)
      name        obj.method() matched by method name only, <= 10 candidates (one row each; n_candidates = how many)
      ambiguous   > 10 candidates, or a method builtin types also have (get, append, push ...); dst_* NULL
      unresolved  no in-repo target (stdlib, third party, or an import it can't follow); dst_* NULL
  ```

  Then run `uv run pytest tests/test_surface.py -q`. If `len(QUERY_DOC) > 3100`, shorten in this order until it fits:
  1. `(one row each; n_candidates = how many)` becomes `(one row each)`.
  2. `outline, callees and source fill their first column(s) only when the argument matches several files or symbols` becomes `outline, callees, source fill their first column(s) only when the argument is ambiguous`.

- [ ] **Step 4:** in README.md's FAQ, add after the **Confident** bullet:

  ```markdown
  - **`typed`:** `obj.method()` where the receiver's class can be read from syntax, Python only for now: `x = Foo()`, an annotation (`p: Foo`, `-> Foo` one call away), `self.attr` set or annotated in the class, an imported module-level instance, or `self`/`super()` through base classes in other files. A name bound more than once, or to something untypable, gets no inference. The edge goes to the method on that class or its nearest in-repo base; a subclass that overrides it isn't followed, so `callers('Sub.m')` misses calls typed as the base. A receiver whose class is outside the repo (`io.StringIO()`, a literal) is `unresolved`, not a `name` guess.
  ```

  Mirror it in `site/index.html`'s FAQ list as a `<li>` with the same words. Keep `<code>` around identifiers.

- [ ] **Step 5:** update the roadmap.
  - In `docs/roadmap.md`, replace "Next" item 1 with: "Type inference, Phase 2: attribute chains `a.b.m()`, call results `f().m()`, tuple unpacking, agreeing multiple bindings; then TypeScript, Go and Rust (separate specs)."
  - Add under a new `## Done` heading: "Python receiver types (the `typed` tier): spec `docs/specs/2026-10-02-local-type-inference.md`."
  - In CLAUDE.md's tiers bullet, add `typed` to the confident list with one line: "the receiver's class from `bindings` (per-file facts) and `symbols.returns`, walked through in-repo bases up to `INHERIT_DEPTH` = 8".

- [ ] **Step 6:** run the suite and commit.

  ```bash
  TMPDIR=/Volumes/research/tmp uv run pytest -q
  git add src/duckgrep/schema.py README.md docs/roadmap.md CLAUDE.md site/index.html tests/test_surface.py
  git commit -m "docs: the typed tier in the tool description, README, roadmap and CLAUDE.md"
  ```

### Task 9: measure against the bars and record

**Files:**
- Create: `bench/typecensus/` (copies of the census scripts: `census.py`, `jedi_truth.py`, `summarize.py`, `overrides.py`, plus a `README.md` with the reproduction steps)
- Modify: `bench/RESULTS.md` (a "Typed receivers (2026-10-xx)" section), `README.md` (accuracy table), `site/index.html` (accuracy table)

- [ ] **Step 1:** copy the census scripts.

  ```bash
  mkdir -p bench/typecensus
  cp /private/tmp/claude-501/-Users-ccf-git-duckgrep/942c1d79-b7dc-4ffe-92a4-a352d55aef56/scratchpad/ti/{census.py,jedi_truth.py,summarize.py,overrides.py} bench/typecensus/
  uv run ruff check --fix bench/typecensus && uv run ruff format bench/typecensus
  ```

  Write `bench/typecensus/README.md` with these reproduction steps, run from the repo root, and fix any remaining lint by hand:
  1. `uv run --group bench python bench/typecensus/jedi_truth.py <repo> <venv> truth-<r>.jsonl 0 14` writes jedi's answer for every gap call. `<venv>` is a virtualenv with the repo's dependencies installed. For django, set `NO_DJANGO_STUBS=1`.
  2. `uv run python bench/typecensus/census.py <repo> truth-<r>.jsonl --examples ex-<r>.txt --json census-<r>.json` classifies each call's binding and scores the strict and lenient rules.
  3. `python3 bench/typecensus/summarize.py ex-*.rows.jsonl` merges the per-repo tables.
  4. `uv run python bench/typecensus/overrides.py <repo> ex-<r>.txt.rows.jsonl` gives the cross-file and override shares.

- [ ] **Step 2:** check accuracy against jedi on each repo. Index each with the branch's code first, which happens by itself on first query.

  ```bash
  for r in django freqtrade; do uv run --group bench python bench/accuracy.py /Volumes/research/scratch/$r 3000 > /Volumes/research/scratch/acc-$r-typed.txt; done
  git clone -q https://github.com/psf/requests /Volumes/research/scratch/requests 2>/dev/null; git -C /Volumes/research/scratch/requests checkout -q 611c616
  uv run --group bench python bench/accuracy.py /Volumes/research/scratch/requests 300 > /Volumes/research/scratch/acc-requests-typed.txt
  ```

  Gate:
  - each file's `confident tiers: ... precision` is at least 99.5%;
  - the coverage on django and freqtrade is at least 10 points above PR A's numbers.

  If precision misses, list the `confident misses` the script prints and classify each: a duckgrep error, a jedi error, or declared versus runtime type. Drop the rule responsible from the tier (spec Risks: "dropped from Phase 1, not tuned") and re-run.

- [ ] **Step 3:** count false `name` edges on jedi-external calls (django). Compare the script's `calls jedi resolves outside the repo -> duckgrep: ... name N%` line with PR A's. Gate: the `name` share is at least halved.

- [ ] **Step 4:** measure performance.

  ```bash
  uv run python bench/latency.py /Volumes/research/scratch/django django/db/models/query.py get_or_create QuerySet.get_or_create
  ```

  Run it on vscode as well, if the checkout used for RESULTS.md's latency table still exists. Its path and symbols are in RESULTS.md's latency section, so use the same arguments. Gates:
  - full index within +25%;
  - one-file edge sync within +50% on django;
  - typical queries unchanged, within run-to-run noise.

  If the sync gate misses, narrow `TYPED_DIRTY` to names that have a typed or typable receiver ref (`SEMI JOIN refs r ON r.name = s.name AND r.receiver IS NOT NULL AND r.lang = 'python'`), re-run Task 7's tests, then re-measure.

- [ ] **Step 5:** record the results.
  - In `bench/RESULTS.md`, add a section with:
    - the commit;
    - the three accuracy outputs (confident share, precision, tier table);
    - the false-`name` change;
    - the latency rows;
    - a hand-classified list of every confident miss.
  - Update README.md's and `site/index.html`'s accuracy tables. Update `tests/test_site.py`'s figures tuple if a listed figure changes.

- [ ] **Step 6:** run the suite and commit.

  ```bash
  TMPDIR=/Volumes/research/tmp uv run pytest -q
  git add bench README.md site/index.html tests/test_site.py
  git commit -m "bench: typed tier accuracy and latency on django, freqtrade and requests"
  ```

### Task 10: the PR

- [ ] **Step 1:** push and open the PR. The body summarises the spec, the measured results against each bar, and the hand-classified misses.

  ```bash
  git push -u origin feat/typed-tier
  gh pr create --title "Typed receivers: infer Python receiver classes from syntax" --body-file <body.md>
  ```

- [ ] **Step 2:** run one independent review of the whole branch (read-only, at the highest effort), checking:
  - SQL correctness;
  - the dirty-marking soundness argument;
  - that no other language's edges changed.

  Wait for Bugbot and Greptile. Fix the valid findings on the branch and reply with evidence to the rest. Merge with `gh pr merge --merge --delete-branch` when clean.
