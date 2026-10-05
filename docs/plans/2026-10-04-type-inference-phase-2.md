# Type Inference, Phase 2 (Python): Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the six largest measured causes of missed confident Python receiver edges: star and multi-hop re-exports, class-body aliases, one-hop attribute chains, call-result receivers, inferred unannotated returns, and function-local classes. Also stop the `qualified` tier reading a rebound class name as the class.

**Architecture:**
- **Per-file facts:** `bindings.py` records four new pure binding kinds: `alias`, `rcall`, `return` and `local_class`.
- **Resolution:** `EDGES_COMPUTE` gains a re-export closure (`py_exp`), an inferred-return view (`py_ret`) and a type-text resolver (`py_tclass`).
- **Typing in two stages:** the typing CTEs (`py_scoped`, `py_bind`, `py_type`) are generated twice from one SQL template. Stage 1 types receivers as today. Stage 2 types the heads of `h.a.m()` and of `x = h.m()`, then derives the receiver's class.
- **Unchanged:** every Phase 1 refusal applies after both stages, because both feed the same `py_cand` → `py_hit` search.
- **Freshness:** dirty marking extends to the new cross-file dependencies.

**Tech stack:** Python 3.10+, tree-sitter-python, DuckDB SQL, pytest, jedi (benchmark only).

**Spec:** `docs/specs/2026-10-04-type-inference-phase-2.md`. Its predecessor is `docs/specs/2026-10-02-local-type-inference.md`; its refusals still bind.

## Global Constraints

- Confident precision against jedi: at least 99.9% on django, freqtrade and requests. Every disagreement is classified by hand in `bench/RESULTS.md`.
- Confident coverage of in-repo calls: up at least 3 points on django and 2 on freqtrade over the re-baselined main. Requests is reported.
- Per rule: a sample of 30 new edges (or all, if fewer) is checked against jedi. A rule below 99.5% agreement is dropped, not tuned until it passes.
- Incremental edges must equal a full rebuild: `snapshot(root) == fresh_snapshot(root, tmp_path)`.
- Performance against the RESULTS.md baselines, each within +20%:
  - full index time;
  - edge sync after a one-file edit on django's `query.py`;
  - catch-up after 300 commits on django.

  No-op freshen and typical query latency stay unchanged.
- `SCHEMA_VERSION` is unchanged: no new table or column. `extractor_version` and `edges_version` change by themselves.
- Python only. Aliased re-export hops (`from .x import A as N`) are not followed.
- No more than two stages of derived types. One return hop per stage.
- Every PR adds a `CHANGELOG.md` line under Unreleased. A release follows each merge (`CLAUDE.md`, Workflow).
- Tests run with `TMPDIR=/Volumes/research/tmp`. Commits go through the hooks, never `--no-verify`.
- Spec and plan text names no tools, skills or plugins.

## Review Focus

1. **Cyclic star imports** (`a.py: from b import *`, `b.py: from a import *`). The closure must terminate, at its 3-hop cap, with no edge invented from the cycle. The test goes in Task 4.
2. **Private names through a star** (`_helper`). A star import doesn't expose them, so no edge may come through one. The test goes in Task 4.
3. **Self-recursive or mutually recursive unannotated functions** (`def f(): return f()`). There must be no inferred class and no loop. The test goes in Task 6.
4. **A generator, or a function mixing `return Foo()` with `return Bar()`.** It must get no inferred return. The test goes in Task 6.
5. **A local class used before its `class` line, or defined twice in one function.** There must be no typed edge. The test goes in Task 7.

## File structure

| File | Change | Responsibility |
|---|---|---|
| `bench/accuracy.py` | modify | deterministic sample (prerequisite) |
| `bench/typed_diff.py` | create | per-rule precision gate: confident targets that changed between two indexes, checked against jedi |
| `src/duckgrep/bindings.py` | modify | new binding kinds: `alias`, `rcall`, `return`, `local_class` |
| `src/duckgrep/schema.py` | modify | table comment; `py_exp`, `py_ret`, `py_tclass`, `py_lcls`, `py_alias`, the stage template, stage-2 CTEs, the `qualified` guard, dirty-marking SQL |
| `src/duckgrep/index.py` | modify | dirty marking: names exported through star chains, before and after |
| `tests/test_bindings.py` | modify | extractor facts |
| `tests/test_typed.py` | modify | resolution, one test per rule and each refusal |
| `tests/test_typed_freshness.py` | modify | incremental equals full, one case per rule |
| `tests/test_resolution.py` | modify | the `qualified` rebound-class case, plus star chains in the `import`/`module` tiers |
| `bench/RESULTS.md`, `README.md`, `docs/roadmap.md`, `CHANGELOG.md`, `CLAUDE.md` | modify | numbers and documentation |

## Branches and order

- **Task 1** is the prerequisite PR, on its own branch from `main`: `bench/deterministic-accuracy`. Merge it first, then rebase `feat/typed-phase-2` onto `main`.
- **Tasks 2–12** are commits on `feat/typed-phase-2`. The spec is already committed there, as `652d337`. One PR at the end.
- **Before each rule task (4–11):** run the "gate: before" step, so its precision gate has a before index.

## The per-rule gate (used by Tasks 4–11)

Each rule task ends with this gate, run on django and freqtrade. Requests is too small to sample. `bench/typed_diff.py` is created in Task 1.

**Gate, before.** Run at the task's start, before any code change, on the previous task's commit:

```bash
for r in django freqtrade; do
  uv run duckgrep -C /Volumes/research/scratch/$r index >/dev/null
  uv run duckgrep -C /Volumes/research/scratch/$r q "SELECT count(*) FROM edges" >/dev/null
  cp /Volumes/research/scratch/$r/.duckgrep/index.duckdb /Volumes/research/tmp/gate-$r-before.duckdb
done
```

**Gate, after.** Run once the task's tests are green:

```bash
for r in django freqtrade; do
  uv run duckgrep -C /Volumes/research/scratch/$r index >/dev/null
  uv run duckgrep -C /Volumes/research/scratch/$r q "SELECT count(*) FROM edges" >/dev/null
  uv run --group bench python bench/typed_diff.py /Volumes/research/scratch/$r \
      /Volumes/research/tmp/gate-$r-before.duckdb /Volumes/research/scratch/$r/.duckgrep/index.duckdb 30 \
      | tee /Volumes/research/tmp/gate-$r-task$TASK.txt
done
```

- **Expected:** each run prints `gained N, changed M, lost K` and `agreement: X/Y`. The task passes when X/Y ≥ 99.5% on each repo, or Y = 0.
- **If it fails:** classify every disagreement first. A disagreement can be jedi's error (for example, jedi following a runtime override). Only disagreements that are duckgrep's own error count against the gate.
- **Ledger it:** record the counts in the ledger, for Task 12's RESULTS table.
- **If a rule fails on its own errors:** revert that task's commits. Record the dropped rule as a ruling and move on.

---

### Task 1: Deterministic benchmark sample and the gate tool (prerequisite PR)

**Files:**
- Modify: `bench/accuracy.py:37-49`
- Create: `bench/typed_diff.py`
- Test: `tests/test_bench_tools.py` (create)

**Interfaces:**
- Produces: `bench/typed_diff.py <repo> <before.duckdb> <after.duckdb> [n]`. Run from Python, `typed_diff.changed_targets(before_db, after_db) -> list[tuple[path, line, col, name, before_targets, after_targets]]`.
- Produces: `bench/accuracy.py`'s `main(root, n, seed)`, which returns the same sample for the same index.

- [ ] **Step 1: Branch from main**

```bash
cd /Users/ccf/git/duckgrep && git stash list && git switch main && git pull --ff-only && git switch -c bench/deterministic-accuracy
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_bench_tools.py`:

```python
"""The benchmark helpers: a deterministic accuracy sample and the per-rule diff of confident targets."""

import os
import shutil
import sys

from helpers import make_repo, rows

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bench"))
import typed_diff  # noqa: E402


def test_changed_targets_lists_gained_changed_and_lost_confident_targets(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "a.py": "class A:\n    def m(self):\n        return 1\n",
            "b.py": "class B:\n    def m(self):\n        return 2\n",
            "use.py": "from a import A\n\n\ndef f():\n    x = A()\n    return x.m()\n",
        },
    )
    rows(root, "SELECT count(*) FROM edges")
    before = str(tmp_path / "before.duckdb")
    shutil.copy(os.path.join(root, ".duckgrep", "index.duckdb"), before)
    with open(os.path.join(root, "use.py"), "w") as f:
        f.write("from b import B\n\n\ndef f():\n    x = B()\n    return x.m()\n")
    rows(root, "SELECT count(*) FROM edges")
    after = os.path.join(root, ".duckgrep", "index.duckdb")
    got = {(p, n): (b, a) for p, _l, _c, n, b, a in typed_diff.changed_targets(before, after)}
    assert got[("use.py", "m")] == ([("a.py", "A.m")], [("b.py", "B.m")])


def test_accuracy_query_is_ordered():
    src = open(os.path.join(os.path.dirname(__file__), "..", "bench", "accuracy.py")).read()
    assert "ORDER BY src_path, line, col" in src
```

- [ ] **Step 3: Run it to verify it fails**

Run: `TMPDIR=/Volumes/research/tmp uv run pytest tests/test_bench_tools.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'typed_diff'`.

- [ ] **Step 4: Order the accuracy query**

In `bench/accuracy.py`, the edges query ends `GROUP BY ALL`. Make it end:

```python
        WHERE ref_kind = 'call' AND f.lang = 'python'
        GROUP BY ALL
        ORDER BY src_path, line, col, name
    """,
```

- [ ] **Step 5: Create `bench/typed_diff.py`**

```python
"""Per-rule precision gate: Python call refs whose confident targets differ between two indexes of one repo,
sampled and checked against jedi's goto-definition.

usage: python bench/typed_diff.py <repo> <before.duckdb> <after.duckdb> [n]
"""

import os
import random
import sys

import duckdb

CONFIDENT = "resolution NOT IN ('name', 'ambiguous', 'unresolved')"
QUERY = f"""
    SELECT e.src_path, e.line, e.col, e.name, list((e.dst_path, e.dst_qualname) ORDER BY e.dst_path, e.dst_qualname)
    FROM edges e JOIN files f ON f.path = e.src_path
    WHERE e.ref_kind = 'call' AND f.lang = 'python' AND {CONFIDENT}
    GROUP BY ALL
"""


def _targets(db):
    con = duckdb.connect(db, read_only=True)
    try:
        return {(p, ln, c, n): [tuple(t) for t in ts] for p, ln, c, n, ts in con.execute(QUERY).fetchall()}
    finally:
        con.close()


def changed_targets(before_db, after_db):
    """[(path, line, col, name, before targets, after targets)] for refs whose confident targets differ."""
    b, a = _targets(before_db), _targets(after_db)
    out = []
    for k in sorted(set(b) | set(a)):
        if b.get(k, []) != a.get(k, []):
            out.append((*k, b.get(k, []), a.get(k, [])))
    return out


def main(root, before_db, after_db, n=30, seed=0):
    import jedi

    sys.path.insert(0, os.path.dirname(__file__))
    from accuracy import to_repo_path

    root = os.path.abspath(root)
    diff = changed_targets(before_db, after_db)
    gained = sum(1 for d in diff if not d[4])
    lost = sum(1 for d in diff if not d[5])
    print(f"gained {gained}, changed {len(diff) - gained - lost}, lost {lost}")
    with_target = [d for d in diff if d[5]]
    random.Random(seed).shuffle(with_target)
    con = duckdb.connect(after_db, read_only=True)
    ranges = {}
    for p, nm, s, e, qn in con.execute("SELECT path, name, start_line, end_line, qualname FROM symbols").fetchall():
        ranges.setdefault((p, nm), []).append((s, e, qn))
    repo_files = {r[0] for r in con.execute("SELECT path FROM files").fetchall()}
    con.close()
    project = jedi.Project(root)
    agree = scored = 0
    for path, line, col, name, _before, after in with_target:
        if scored >= n:
            break
        try:
            gs = jedi.Script(path=os.path.join(root, path), project=project).goto(line, col, follow_imports=True)
        except Exception:
            continue
        truth = set()
        for g in gs:
            rel = to_repo_path(g.module_path, root, repo_files)
            for s, e, qn in ranges.get((rel, g.name), []) if rel and g.line else []:
                if s <= g.line <= e:
                    truth.add((rel, qn))
        if not truth:
            continue
        scored += 1
        ok = bool(truth & set(after))
        agree += ok
        if not ok:
            print(f"  DISAGREE {path}:{line} {name}: duckgrep {after} jedi {sorted(truth)}")
    print(f"agreement: {agree}/{scored}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 30)
```

- [ ] **Step 6: Run the tests**

Run: `TMPDIR=/Volumes/research/tmp uv run pytest tests/test_bench_tools.py -v`
Expected: 2 passed.

If `edges` has no `ref_kind` column, read the table definition (`grep -n "CREATE TABLE IF NOT EXISTS edges" -A16 src/duckgrep/schema.py`) and use the column `accuracy.py` uses. It reads `ref_kind`, so this is a check, not an expected change.

- [ ] **Step 7: Re-baseline with the deterministic sample**

```bash
for r in django freqtrade; do uv run --group bench python bench/accuracy.py /Volumes/research/scratch/$r 3000 > /Volumes/research/tmp/acc-$r-base.txt; done
uv run --group bench python bench/accuracy.py /Volumes/research/scratch/requests 300 > /Volumes/research/tmp/acc-requests-base.txt
grep "confident tiers" /Volumes/research/tmp/acc-*-base.txt
```

Expected: one `confident tiers: X% of in-repo calls, precision Y%` line per repo. Run django twice; the two outputs must be identical.

- [ ] **Step 8: Record the baseline**

Add this section to `bench/RESULTS.md`, under the accuracy section, with the measured values:

```markdown
### Deterministic baseline (main, 2026-10-04)

`bench/accuracy.py` now orders its query before sampling, so a rerun on the same index scores the same calls.

| repo | sample | confident coverage of in-repo calls | precision |
|---|---:|---:|---:|
| django (0ae93a0) | 3,000 | X% | Y% |
| freqtrade (f2ec745) | 3,000 | X% | Y% |
| requests (611c616) | 300 | X% | Y% |
```

Also add `- **Benchmarks:** the jedi accuracy sample is deterministic, and \`bench/typed_diff.py\` checks the confident edges that change between two indexes.` under `## [Unreleased]` → `### Changed` in `CHANGELOG.md`. Create the subsection if it's missing.

- [ ] **Step 9: Commit, PR, merge**

```bash
git add bench/accuracy.py bench/typed_diff.py tests/test_bench_tools.py bench/RESULTS.md CHANGELOG.md
git commit -F- <<'EOF'
bench: deterministic jedi sample, and a per-rule diff of confident targets

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
git push -u origin bench/deterministic-accuracy && gh pr create --title "bench: deterministic jedi sample and a per-rule gate" --body "Orders bench/accuracy.py's query before sampling, so reruns on one index score the same calls, and re-baselines django, freqtrade and requests. Adds bench/typed_diff.py, which lists the confident targets that change between two indexes and checks a sample against jedi: the per-rule gate for type inference Phase 2 (docs/specs/2026-10-04-type-inference-phase-2.md)."
```

Follow `CLAUDE.md`, Workflow: wait for Bugbot and Greptile, address the comments, and merge with `gh pr merge --merge --delete-branch`. This changes only bench tooling, which users don't see. So the CHANGELOG line can stay under Unreleased, with no release. Then:

```bash
git switch main && git pull --ff-only && git switch feat/typed-phase-2 && git rebase main
```

---

### Task 2: New binding facts in the extractor

**Files:**
- Modify: `src/duckgrep/bindings.py` (the `walk_class` and `walk` functions inside `extract_bindings`)
- Modify: `src/duckgrep/schema.py:55-63`, the `bindings` table comment only
- Test: `tests/test_bindings.py`

**Interfaces:**
- Produces these `bindings` rows. The columns are `(path, scope, name, kind, type_text, line, pos)`.
  - **`alias`:** scope is the class qualname, name `self.<X>`, type_text the dotted RHS. One per class-body `X = <dotted name>`. The untyped `attr` row stays too.
  - **`rcall`:** scope is the enclosing scope, name the receiver text (whitespace collapsed). type_text is `call:<dotted callee>` or `super:<C>`. line and pos are the line and 0-based column of the called method's identifier, which equal `refs.line`/`refs.col`.
  - **`return`:** scope is the function qualname, name its short name. type_text is `call:<dotted>`, `var:<local>` or NULL. Emitted only for functions without a return annotation:
    - one row per value-returning `return` that isn't `return None`;
    - a generator gets one NULL row.
  - **`local_class`:** scope is the enclosing function's qualname, name the class's short name, type_text the class qualname. One per `class` statement directly in a function body, at any block depth.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_bindings.py`:

```python
def test_class_body_alias_of_a_dotted_name():
    rows = bind("class T:\n    form_class = forms.Base\n    other = Foo\n    n = 3\n    made = Foo()\n")
    assert ("m.py", "T", "self.form_class", "alias", "forms.Base", 2, 0) in rows
    assert ("m.py", "T", "self.other", "alias", "Foo", 3, 0) in rows
    assert ("m.py", "T", "self.form_class", "attr", None, 2, 0) in rows  # today's row stays
    assert not [r for r in rows if r[3] == "alias" and r[2] in ("self.n", "self.made")]


def test_receiver_that_is_one_call():
    rows = bind(
        "class C(B):\n"
        "    def m(self):\n"
        "        Foo(1, x=2).run()\n"
        "        mod.Foo('a').run()\n"
        "        super(C, self).run()\n"
        "        self.k(1).run()\n"
        "        Foo()[0].run()\n"
        "        f()().run()\n"
    )
    rc = sorted(r for r in rows if r[3] == "rcall")
    assert rc == [
        ("m.py", "C.m", "Foo(1, x=2)", "rcall", "call:Foo", 3, 20),
        ("m.py", "C.m", "mod.Foo('a')", "rcall", "call:mod.Foo", 4, 21),
        ("m.py", "C.m", "self.k(1)", "rcall", "call:self.k", 6, 18),
        ("m.py", "C.m", "super(C, self)", "rcall", "super:C", 5, 23),
    ]


def test_returns_of_unannotated_functions():
    rows = bind(
        "def a():\n    return Foo()\n\n"
        "def b():\n    x = Foo()\n    return x\n\n"
        "def c():\n    if p:\n        return None\n    return\n\n"
        "def d() -> Foo:\n    return make()\n\n"
        "def e():\n    yield Foo()\n    return Foo()\n\n"
        "def g():\n    def inner():\n        return Bar()\n    return 1 + 2\n"
    )
    ret = sorted(r[1:6] for r in rows if r[3] == "return")
    assert ret == [
        ("a", "a", "return", "call:Foo", 2),
        ("b", "b", "return", "var:x", 6),
        ("e", "e", "return", None, 17),
        ("g", "g", "return", None, 23),
        ("g.inner", "inner", "return", "call:Bar", 22),
    ]


def test_classes_defined_in_functions():
    rows = bind(
        "def t():\n    class Local(Base):\n        pass\n    if x:\n        class Two:\n            pass\n\n"
        "class Outer:\n    class Inner:\n        pass\n"
    )
    lc = sorted(r[1:5] for r in rows if r[3] == "local_class")
    assert lc == [("t", "Local", "local_class", "t.Local"), ("t", "Two", "local_class", "t.Two")]
```

The generator row's line is the line where the function's body block starts (17, the `yield`). If tree-sitter reports another line, take the line the extractor gives and ledger it; only the row's existence and its NULL matter. The same goes for the `rcall` columns: they must equal what the extractor reports for the call ref. Check them against `refs` with `uv run duckgrep -C <scratch repo> q "SELECT line, col, name, receiver FROM refs WHERE kind='call'"` on a scratch copy of this source, as was done when writing this plan: `Foo(1, x=2).m()` at 8 spaces gave col 20.

- [ ] **Step 2: Run them to verify they fail**

Run: `TMPDIR=/Volumes/research/tmp uv run pytest tests/test_bindings.py -v -k "alias or one_call or returns_of or defined_in_functions"`
Expected: 4 FAIL, with empty lists or a missing alias row.

- [ ] **Step 3: Implement**

In `extract_bindings`, add `fscopes: set[str] = set()` next to `out`.

In `walk_class`, replace the identifier branch:

```python
            if left is not None and left.type == "identifier":
                tt = normalize_type(text(ann), qual) if ann is not None else value_type(right, {})
                add(qual, "self." + text(left), "attr", tt, left)
                rt = " ".join(text(right).split()) if right is not None else ""
                if ann is None and right is not None and right.type in ("identifier", "attribute") and DOTTED.match(rt):
                    add(qual, "self." + text(left), "alias", rt, left)  # X = Foo: self.X(...) makes a Foo
```

Add a helper inside `extract_bindings`, after `rebound`:

```python
    def returns(fn_body) -> list[tuple[str | None, object]]:
        """(type text, node) per value-returning `return` of one function body (nested defs excluded); a
        generator gives one untypable row, so no return is inferred for it"""
        rows, stack, gen = [], [fn_body], False
        while stack:
            c = stack.pop()
            if c.type in ("function_definition", "class_definition", "lambda"):
                continue
            if c.type == "yield":
                gen = True
            if c.type == "return_statement" and c.named_child_count:
                v = c.named_children[0]
                if v.type != "none":
                    if v.type == "call":
                        rows.append((value_type(v, {}), c))
                    elif v.type == "identifier":
                        rows.append(("var:" + text(v), c))
                    else:
                        rows.append((None, c))
            stack.extend(c.named_children)
        return [(None, fn_body)] if gen else rows
```

In `walk`, `class_definition` branch, after computing `short` and `qual`:

```python
            if scope in fscopes:
                add(scope, short, "local_class", qual, nm)
```

In `walk`, `function_definition` branch, replace the body handling:

```python
            body = n.child_by_field_name("body")
            if body is not None:
                fscopes.add(qual)
                if n.child_by_field_name("return_type") is None:
                    for tt, node in returns(body):
                        add(qual, text(nm), "return", tt, node)
                again = rebound(body)  # an annotation no longer types a parameter that is reassigned
                walk(body, qual, cls, {k: v for k, v in ps.items() if k not in again})
            return
```

In `walk`, add a branch to the `elif` chain (before `elif t == "lambda":`):

```python
        elif t == "call":  # Foo(...).m(), super(C, x).m(): the receiver is one call
            fn = n.child_by_field_name("function")
            if fn is not None and fn.type == "attribute":
                obj, attr = fn.child_by_field_name("object"), fn.child_by_field_name("attribute")
                if obj is not None and attr is not None and obj.type == "call":
                    ofn = obj.child_by_field_name("function")
                    ot = " ".join(text(ofn).split()) if ofn is not None else ""
                    tt = None
                    if ot == "super":
                        args = obj.child_by_field_name("arguments")
                        a = list(args.named_children) if args is not None else []
                        if len(a) == 2 and a[0].type == "identifier":
                            tt = "super:" + text(a[0])
                    elif DOTTED.match(ot):
                        tt = "call:" + ot
                    if tt:
                        add(scope, " ".join(text(obj).split()), "rcall", tt, attr, attr.start_point[1])
```

In `src/duckgrep/schema.py`, update the `bindings` comments:

```sql
    name      VARCHAR,            -- the name, self.<attr> for attributes and aliases, '' for base rows, the receiver
                                  -- text for rcall, the function's name for return, the class's name for local_class
    kind      VARCHAR,            -- assign | annot | param | attr | base | global | import | alias | rcall | return
                                  -- | local_class
    type_text VARCHAR,            -- dotted class name, call:<callee>, super:<class>, var:<local>, builtin type of a
                                  -- literal, or NULL
    line      INTEGER,
    pos       INTEGER             -- base rows: position in the class's bases; rcall rows: the call ref's column
```

- [ ] **Step 4: Run the tests**

Run: `TMPDIR=/Volumes/research/tmp uv run pytest tests/test_bindings.py -v`
Expected: all pass.

Then run the whole suite: `TMPDIR=/Volumes/research/tmp uv run pytest -q`. Expected: the same counts as main, plus 4. The new kinds are not in `py_scoped`'s kind list or `py_multi`'s, so no resolution changes yet.

- [ ] **Step 5: Commit**

```bash
git add src/duckgrep/bindings.py src/duckgrep/schema.py tests/test_bindings.py
git commit -F- <<'EOF'
feat(bindings): record class aliases, call receivers, returns and function-local classes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 3: One typing template, and a type-text resolver (no behaviour change)

**Files:**
- Modify: `src/duckgrep/schema.py`: `py_tx`, `py_scoped`, `py_bind`, `py_type`, `py_type_ext`, and `EDGES_COMPUTE` assembly (around lines 241–470 and 600–615)
- Test: the whole existing suite, plus `tests/test_typed.py::test_template_generates_both_stages`

**Interfaces:**
- Consumes: Task 2's binding kinds. `py_tx` must skip `super:`, `var:` and `NULL` texts.
- Produces these CTEs, which later tasks rely on (`<n>` is the stage, 1 or 2):
  - `py_subj<n>(path, line, col, scope, scope_class, subj)`: the subjects to type, keyed by the call ref.
  - `py_scoped<n>(path, line, col, scope, n_untyped, n_types, type_text)`.
  - `py_bind<n>(path, line, col, tpath, type_text, bscope)`, where `bscope` is the binding's scope: the winning scope for local bindings, `''` for imported instances, NULL for `self.attr`.
  - `py_type<n>(path, line, col, cpath, cqual, from_depth, ext)`.
  - `py_tclass(path, text, cpath, cqual, hop)`: a type text, as written in `path`, mapped to the class it means. `hop` is 0 for `Foo` and `call:Foo`, and 1 for `call:f` through f's return.
  - `py_type`: the union every later stage feeds. In this task it is `py_type1`.
- Produces this Python name in `schema.py`: `_PY_TYPING`, a template string using `@N@` for the stage. It's expanded with `.replace("@N@", "1")` before `.format()` runs, because `{}` belongs to `_compute_edges`'s `.format()`.

- [ ] **Step 1: Write the test**

Append to `tests/test_typed.py`:

```python
def test_template_generates_both_stages():
    from duckgrep import schema

    assert "py_scoped1 AS" in schema.EDGES_COMPUTE and "py_bind1 AS" in schema.EDGES_COMPUTE
    assert "@N@" not in schema.EDGES_COMPUTE
```

- [ ] **Step 2: Run it to verify it fails**

Run: `TMPDIR=/Volumes/research/tmp uv run pytest tests/test_typed.py::test_template_generates_both_stages -v`
Expected: FAIL, because `py_scoped1 AS` is not in `EDGES_COMPUTE`.

- [ ] **Step 3: Implement**

Move the current `py_scoped`, `py_bind` and `py_type` CTEs out of `_EDGES_TEMPLATE` into a new module-level string `_PY_TYPING`. Rename them `py_scoped@N@`, `py_bind@N@` and `py_type@N@`. Read subjects from `py_subj@N@` (column `subj`) instead of `py_r` (column `receiver`). Then:

```python
_PY_TYPING = """
py_scoped@N@ AS (  -- bare-name subjects: the bindings of each enclosing scope that binds the name
    SELECT r.path, r.line, r.col, b.scope,
           count(*) FILTER (WHERE b.type_text IS NULL OR b.kind = 'global') AS n_untyped,
           count(DISTINCT b.type_text) AS n_types, min(b.type_text) AS type_text
    FROM py_subj@N@ r
    JOIN bindings b ON b.path = r.path AND b.name = r.subj
         AND b.kind IN ('assign', 'annot', 'param', 'global', 'import')
         AND (b.scope = r.scope OR b.scope = '' OR starts_with(r.scope, b.scope || '.'))
    WHERE r.subj NOT IN ('self', 'cls')
      -- a class body's names aren't visible inside its methods (only in the class body itself)
      AND NOT (b.scope <> r.scope AND b.scope <> ''
               AND EXISTS (SELECT 1 FROM symbols c WHERE c.path = b.path AND c.qualname = b.scope AND c.kind = 'class'))
      AND regexp_matches(r.subj, '^[A-Za-z_][A-Za-z0-9_]*$')
    GROUP BY ALL
    HAVING count(*) FILTER (WHERE b.kind <> 'import') > 0  -- a name only imported is resolved through the import
),
py_bind@N@ AS (  -- subject -> (file to resolve its type in, type text, binding scope), when every binding agrees
    SELECT path, line, col, path AS tpath, type_text, scope AS bscope FROM (
        SELECT s.* FROM py_scoped@N@ s
        JOIN py_subj@N@ r ON r.path = s.path AND r.line = s.line AND r.col = s.col
        ANTI JOIN py_global g ON g.path = r.path AND g.name = r.subj
        QUALIFY row_number() OVER (PARTITION BY s.path, s.line, s.col ORDER BY length(s.scope) DESC) = 1
    ) WHERE n_untyped = 0 AND n_types = 1
  UNION ALL  -- `from m import cache`, with cache bound once at the top of m
    SELECT r.path, r.line, r.col, i.target_path, min(b.type_text), ''
    FROM py_subj@N@ r
    JOIN py_imp i ON i.path = r.path AND i."local" = r.subj AND i.name IS NOT NULL AND NOT i.target_is_module
    JOIN bindings b ON b.path = i.target_path AND b.scope = '' AND b.name = i.name
         AND b.kind IN ('assign', 'annot', 'global', 'import')
    ANTI JOIN py_scoped@N@ s ON s.path = r.path AND s.line = r.line AND s.col = r.col
    ANTI JOIN py_global g ON g.path = i.target_path AND g.name = i.name
    GROUP BY ALL
    HAVING count(*) FILTER (WHERE b.type_text IS NULL OR b.kind = 'global') = 0 AND count(DISTINCT b.type_text) = 1
  UNION ALL  -- self.attr / cls.attr: the nearest class in the ancestry that binds the attribute
    SELECT path, line, col, apath, type_text, NULL FROM (
        SELECT r.path, r.line, r.col, a.apath,
               count(*) FILTER (WHERE b.type_text IS NULL) AS n_untyped,
               count(DISTINCT b.type_text) AS n_types, min(b.type_text) AS type_text
        FROM py_subj@N@ r
        JOIN py_mro a ON a.path = r.path AND a.qual = r.scope_class
        JOIN bindings b ON b.path = a.apath AND b.scope = a.aqual AND b.kind = 'attr'
             AND b.name = 'self.' || regexp_extract(r.subj, '^(?:self|cls)[.]([A-Za-z_][A-Za-z0-9_]*)$', 1)
        WHERE regexp_matches(r.subj, '^(self|cls)[.][A-Za-z_][A-Za-z0-9_]*$')
        GROUP BY r.path, r.line, r.col, a.apath, a.ord
        QUALIFY row_number() OVER (PARTITION BY r.path, r.line, r.col ORDER BY a.ord) = 1
    ) WHERE n_untyped = 0 AND n_types = 1
),
py_type@N@ AS (  -- typed subject -> its class (cpath, cqual) and where the method search starts
    SELECT b.path, b.line, b.col, c.cpath, c.cqual, 0 AS from_depth, FALSE AS ext
    FROM py_bind@N@ b JOIN py_tclass c ON c.path = b.tpath AND c.text = b.type_text
  UNION ALL  -- self / cls: the enclosing class; super(): its bases
    SELECT path, line, col, path, scope_class, CASE WHEN subj = 'super()' THEN 1 ELSE 0 END, FALSE
    FROM py_subj@N@ WHERE subj IN ('self', 'cls', 'super()') AND scope_class IS NOT NULL
  UNION ALL  -- Foo.m(), mod.Foo.m(): a class subject (qualified covers Foo's own methods; this adds its bases)
    SELECT r.path, r.line, r.col, d.dpath, d.dqual, 0, FALSE
    FROM py_subj@N@ r JOIN py_def d ON d.path = r.path AND d.text = r.subj AND d.dkind = 'class'
    ANTI JOIN py_scoped@N@ s ON s.path = r.path AND s.line = r.line AND s.col = r.col
),
"""
```

In `_EDGES_TEMPLATE`, where `py_scoped` used to start (after `py_r`), put:

```sql
py_global AS (  -- names a global/nonlocal statement rebinds somewhere in the file: never inferred there
    SELECT DISTINCT path, name FROM bindings WHERE kind = 'global'
),
py_tclass AS (  -- a type text as written in a file -> the class it means; hop 1: through a callee's return
    SELECT d.path, d.text, d.dpath AS cpath, d.dqual AS cqual, 0 AS hop FROM py_def d WHERE d.dkind = 'class'
  UNION ALL
    SELECT d.path, 'call:' || d.text, d.dpath, d.dqual, 0 FROM py_def d WHERE d.dkind = 'class'
  UNION ALL  -- x = make(...) / Foo.create(...): one hop of the callee's return annotation, in the callee's file
    SELECT d.path, 'call:' || d.text, c.dpath, c.dqual, 1
    FROM py_def d JOIN py_def c ON c.path = d.dpath AND c.text = d.returns AND c.dkind = 'class'
    WHERE d.dkind IN ('function', 'method')
),
py_subj1 AS (SELECT path, line, col, scope, scope_class, receiver AS subj FROM py_r),
@PY_TYPING_1@
py_type AS (SELECT * FROM py_type1),
```

- **`py_global`:** remove its old definition, the one between `py_scoped` and `py_bind`, so it is defined once, before the template.
- **`py_type_ext`:** both of its branches read `py_bind`. Make them read `py_bind1`.
- **`py_cand`:** it reads `py_type`, which stays as is.

Assemble `EDGES_COMPUTE`:

```python
EDGES_COMPUTE = (
    _EDGES_TEMPLATE.replace("@PY_TYPING_1@", _PY_TYPING.replace("@N@", "1"))
    .replace("@BUILTIN_METHODS@", _values(builtin_names.METHODS))
    .replace("@BUILTIN_GLOBALS@", _values(builtin_names.GLOBALS))
    .replace("@STDLIB_GUARD@", _GUARD)
)
```

In `py_tx`, the bindings branch must skip the new non-class texts:

```sql
        SELECT path, CASE WHEN starts_with(type_text, 'call:') THEN substr(type_text, 6) ELSE type_text END AS text
        FROM bindings WHERE type_text IS NOT NULL AND NOT regexp_matches(type_text, '^(super|var):')
          AND kind <> 'local_class'
```

- [ ] **Step 4: Run the whole suite**

Run: `TMPDIR=/Volumes/research/tmp uv run pytest -q`
Expected: everything passes. The counts are main's plus Task 2's 4 tests plus this one. No resolution test may change.

- [ ] **Step 5: Check behaviour is unchanged on a real repo**

Run the "gate: before" step (on the Task 2 commit) and the "gate: after" step (on this task's working tree) for django only. Expected: `gained 0, changed 0, lost 0`.

- [ ] **Step 6: Commit**

```bash
git add src/duckgrep/schema.py tests/test_typed.py
git commit -F- <<'EOF'
refactor(typed): one typing template per stage, and a type-text resolver

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 4: The re-export closure (named and star, up to 3 hops)

**Files:**
- Modify: `src/duckgrep/schema.py`: `py_def` arms 2 and 4; new `py_own`, `py_hop`, `py_exp`, `py_exp1`; new Python arms in `t1`; `TYPED_DIRTY_SEED`; new `STAR_NAMES`
- Modify: `src/duckgrep/index.py`: `_aff_names` (before) and `_mark_edges_dirty` (after)
- Test: `tests/test_typed.py`, `tests/test_resolution.py`, `tests/test_typed_freshness.py`

**Interfaces:**
- Consumes: `py_imp` (existing), `symbols`, `bindings`.
- Produces: `py_exp1(mod_path, name, dpath, dqual)`, the unique top-level definition that module file `mod_path` exposes as `name`, through at most 3 hops.
- Produces: `schema.STAR_NAMES`, a SQL string with a `{files}` placeholder. It selects one column, `name`: the names reachable through the star imports of `{files}`, up to 3 hops.

- [ ] **Step 0: Gate, before** (see "The per-rule gate"). Set `TASK=4`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_typed.py`:

```python
STAR = {
    "pkg/fields.py": "class FileField:\n    def clean(self):\n        return 1\n\n\ndef _private():\n    return 0\n",
    "pkg/__init__.py": "from pkg.fields import *\n",
    "ops/models.py": "class CreateModel:\n    def state_forwards(self):\n        return 1\n",
    "ops/__init__.py": "from ops.models import CreateModel\n",
    "mig/__init__.py": "from ops import *\n",
}


def test_star_and_multi_hop_reexports(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            **STAR,
            "use.py": "from pkg import FileField\nimport mig\n\n\n"
            "def f():\n    x = FileField()\n    x.clean()\n    op = mig.CreateModel()\n    return op.state_forwards()\n",
        },
    )
    assert edges_at(root, "use.py", "clean") == [("pkg/fields.py", "FileField.clean", "typed")]
    assert edges_at(root, "use.py", "state_forwards") == [("ops/models.py", "CreateModel.state_forwards", "typed")]


def test_star_reexports_refuse_two_sources_and_private_names(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "a.py": "class Foo:\n    def m(self):\n        return 1\n",
            "b.py": "class Foo:\n    def m(self):\n        return 2\n",
            "two/__init__.py": "from a import *\nfrom b import *\n",
            "p.py": "class _Hidden:\n    def go(self):\n        return 1\n",
            "q/__init__.py": "from p import *\n",
            "use.py": "from two import Foo\nfrom q import _Hidden\n\n\n"
            "def f():\n    x = Foo()\n    h = _Hidden()\n    h.go()\n    return x.m()\n",
        },
    )
    assert "typed" not in {r[2] for r in edges_at(root, "use.py", "m")}
    assert "typed" not in {r[2] for r in edges_at(root, "use.py", "go")}  # a star doesn't export _names


def test_cyclic_star_imports_terminate(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "a/__init__.py": "from b import *\n\n\nclass A:\n    def m(self):\n        return 1\n",
            "b/__init__.py": "from a import *\n",
            "use.py": "from b import A\n\n\ndef f():\n    x = A()\n    return x.m()\n",
        },
    )
    assert edges_at(root, "use.py", "m") == [("a/__init__.py", "A.m", "typed")]
```

Append to `tests/test_resolution.py`, using that file's existing helpers. If it has none, import `make_repo` and `rows` from `helpers`.

```python
def test_bare_and_module_calls_through_star_chains(tmp_path):
    from helpers import make_repo, rows

    root = make_repo(
        tmp_path / "r",
        {
            "ops/models.py": "def make():\n    return 1\n",
            "ops/__init__.py": "from ops.models import make\n",
            "mig/__init__.py": "from ops import *\n",
            "use.py": "import mig\nfrom mig import make\n\n\ndef f():\n    make()\n    return mig.make()\n",
        },
    )
    got = rows(root, "SELECT line, dst_path, dst_qualname, resolution FROM edges WHERE src_path = 'use.py' "
                     "AND name = 'make' AND ref_kind = 'call' ORDER BY ALL")
    assert got == [(6, "ops/models.py", "make", "import"), (7, "ops/models.py", "make", "module")]
```

Append to `CROSS_FILE` in `tests/test_typed_freshness.py`:

```python
    "star source gains, loses or renames the name": (
        {
            "src.py": "class Foo:\n    def m(self):\n        return 1\n",
            "other.py": "class Foo:\n    def m(self):\n        return 2\n",
            "pkg/__init__.py": "from src import *\n",
            "use.py": "from pkg import Foo\n\n\ndef f():\n    x = Foo()\n    return x.m()\n",
        },
        {"src.py": "class Bar:\n    def m(self):\n        return 1\n"},
    ),
    "a second star source starts defining the name": (
        {
            "src.py": "class Foo:\n    def m(self):\n        return 1\n",
            "two.py": "class Two:\n    def m(self):\n        return 2\n",
            "pkg/__init__.py": "from src import *\nfrom two import *\n",
            "use.py": "from pkg import Foo\n\n\ndef f():\n    x = Foo()\n    return x.m()\n",
        },
        {"two.py": "class Foo:\n    def m(self):\n        return 2\n"},
    ),
    "a re-export hop is inserted": (
        {
            "src.py": "class Foo:\n    def m(self):\n        return 1\n",
            "mid/__init__.py": "",
            "pkg/__init__.py": "from mid import *\n",
            "use.py": "from pkg import Foo\n\n\ndef f():\n    x = Foo()\n    return x.m()\n",
        },
        {"mid/__init__.py": "from src import Foo\n"},
    ),
```

- [ ] **Step 2: Run them to verify they fail**

Run: `TMPDIR=/Volumes/research/tmp uv run pytest tests/test_typed.py tests/test_resolution.py tests/test_typed_freshness.py -q -k "star or reexport or hop"`
Expected:
- the star and multi-hop tests fail, with `[]` or `name` rows;
- the freshness cases may pass or fail. They must pass after Step 4.

- [ ] **Step 3: Implement the closure**

In `_EDGES_TEMPLATE`, replace `py_rx` with:

```sql
py_own AS (  -- names a module binds itself at the top (a definition, assignment or named import): a star never
             -- overrides them there
    SELECT path, name FROM symbols WHERE lang = 'python' AND parent IS NULL
  UNION SELECT path, name FROM bindings WHERE scope = '' AND kind IN ('assign', 'annot', 'import', 'global')
),
py_hop AS (  -- one re-export step: module mod_path exposes, from file src, the name name_in (NULL: every name, a star)
    SELECT path AS mod_path, target_path AS src, name AS name_in FROM py_imp
    WHERE name IS NOT NULL AND name <> '*' AND "local" = name AND target_path IS NOT NULL AND NOT target_is_module
  UNION ALL
    SELECT path, target_path, NULL FROM py_imp
    WHERE name = '*' AND "local" IS NULL AND target_path IS NOT NULL AND NOT target_is_module
),
py_exp AS (  -- (module, name) -> the top-level definition it exposes, through at most 3 named or star hops
    SELECT path AS mod_path, name, path AS dpath, qualname AS dqual, 0 AS hops
    FROM symbols WHERE lang = 'python' AND parent IS NULL
  UNION
    SELECT h.mod_path, e.name, e.dpath, e.dqual, e.hops + 1
    FROM py_exp e JOIN py_hop h ON h.src = e.mod_path AND coalesce(h.name_in, e.name) = e.name
    WHERE e.hops < 3
      AND (h.name_in IS NOT NULL
           OR (NOT starts_with(e.name, '_')
               AND NOT EXISTS (SELECT 1 FROM py_own o WHERE o.path = h.mod_path AND o.name = e.name)))
),
py_exp1 AS (  -- ... when every path agrees on one definition (two star sources of one name: refuse)
    SELECT mod_path, name, min(dpath) AS dpath, min(dqual) AS dqual FROM py_exp
    GROUP BY mod_path, name HAVING count(DISTINCT (dpath, dqual)) = 1
),
py_rx AS (  -- names a module imports from another: one re-export hop (kept for the qualified/module arms' tests)
    SELECT path AS mod_path, "local", name, target_path FROM py_imp
    WHERE target_path IS NOT NULL AND NOT target_is_module AND "local" IS NOT NULL AND name IS NOT NULL
),
```

If DuckDB rejects the correlated `NOT EXISTS` inside the recursive branch, replace it with a precomputed anti-join source. Precompute `py_star_ok(mod_path, name)`: the top-level names of star sources that `py_own` doesn't bind in `mod_path`. Then join to it. Ledger the ruling.

In `py_def`, replace arm 2 (`-- ... re-exported by m`):

```sql
      UNION ALL  -- ... re-exported by m (named or star hops, at most 3)
        SELECT t.path, t.text, t.head, s.path, s.qualname, s.kind, s.returns, s.start_line, i.line, 2
        FROM py_tx t
        JOIN py_imp i ON i.path = t.path AND i."local" = t.head AND i.name IS NOT NULL AND NOT i.target_is_module
        JOIN py_exp1 x ON x.mod_path = i.target_path AND x.name = i.name
        JOIN symbols s ON s.path = x.dpath AND s.qualname = x.dqual || substr(t.text, length(t.head) + 1)
```

And arm 4 (`-- ... re-exported by that module`):

```sql
      UNION ALL  -- ... re-exported by that module (named or star hops, at most 3)
        SELECT t.path, t.text, t.head, s.path, s.qualname, s.kind, s.returns, s.start_line, i.line, 4
        FROM py_tx t
        JOIN py_imp i ON i.path = t.path AND t.prefix IS NOT NULL AND i.target_path IS NOT NULL
             AND ((i."local" = t.prefix AND (i.name IS NULL OR i.target_is_module))
                  OR (i.name IS NULL AND i.alias IS NULL AND i.module = t.prefix))
        JOIN py_exp1 x ON x.mod_path = i.target_path AND x.name = t.tail
        JOIN symbols s ON s.path = x.dpath AND s.qualname = x.dqual
```

In `t1`, before the `qualified` arm, add:

```sql
  UNION ALL
    -- Python: `from pkg import get` through re-export chains (named and star, up to 3 hops)
    SELECT r.*, s.path, s.qualname, s.kind, s.start_line, 'import'
    FROM r
    JOIN imp i ON i.path = r.path AND i."local" = r.name AND NOT i.target_is_module
              AND i.name IS NOT NULL AND i.name <> '*'
    JOIN py_exp1 x ON x.mod_path = i.target_path AND x.name = i.name
    JOIN symbols s ON s.path = x.dpath AND s.qualname = x.dqual
    WHERE r.receiver IS NULL AND r.family = 'py'
  UNION ALL
    -- Python: `pkg.get()` through re-export chains
    SELECT r.*, s.path, s.qualname, s.kind, s.start_line, 'module'
    FROM r
    JOIN imp i ON i.path = r.path AND (i."local" = r.receiver OR i.module = r.receiver)
              AND (i.target_is_module OR i.name IS NULL)
    JOIN py_exp1 x ON x.mod_path = i.target_path AND x.name = r.name
    JOIN symbols s ON s.path = x.dpath AND s.qualname = x.dqual
    WHERE r.receiver IS NOT NULL AND r.family = 'py'
```

`py_def` keeps its guards: a later rebinding in the defining file, nested namesakes, and `py_multi`. They apply to the definitions `py_exp1` finds.

- [ ] **Step 4: Dirty marking for star chains**

In `schema.py`, add:

```python
STAR_NAMES = """
SELECT name FROM (
    WITH RECURSIVE st(tgt, d) AS (
        SELECT target_path, 1 FROM imports_resolved
        WHERE path IN ({files}) AND family = 'py' AND name = '*' AND target_path IS NOT NULL
      UNION
        SELECT i.target_path, st.d + 1 FROM st JOIN imports_resolved i ON i.path = st.tgt
        WHERE i.family = 'py' AND i.target_path IS NOT NULL AND (i.name = '*' OR i."local" = i.name) AND st.d < 3
    )
    SELECT s.name FROM st JOIN symbols s ON s.path = st.tgt AND s.parent IS NULL
    UNION SELECT i."local" FROM st JOIN imports i ON i.path = st.tgt AND i."local" IS NOT NULL
)
"""
```

Append to `TYPED_DIRTY_SEED`, before its closing `"""`:

```sql
UNION SELECT name FROM ({star_names})
```

`TYPED_DIRTY_SEED.format(files=...)` is called in two places. Change both to pass `star_names=STAR_NAMES.format(files=<same files expression>)`. One is in `index.py`, where `_aff_types` is created. The other is inside `_mark_edges_dirty`, as `seed_after=`. The `{files}` inside `STAR_NAMES` is filled first, so the outer `.format` must not see braces. Build it as:

```python
schema.TYPED_DIRTY_SEED.format(files=F, star_names=schema.STAR_NAMES.format(files=F))
```

In `index.py`, extend `_aff_names` (the before state) with:

```python
                con.execute(
                    "CREATE OR REPLACE TEMP TABLE _aff_names AS "
                    "SELECT name FROM symbols WHERE path IN (SELECT path FROM _chg) "
                    'UNION SELECT "local" FROM imports WHERE path IN (SELECT path FROM _chg) '
                    'AND "local" IS NOT NULL '
                    "UNION " + schema.STAR_NAMES.format(files="SELECT path FROM _chg")
                )
```

And in `_mark_edges_dirty`, the `'name'` insert (the after state), add:

```python
        "UNION SELECT 'name', NULL, name FROM (" + schema.STAR_NAMES.format(files="SELECT path FROM _chg") + ") "
```

Put it before the closing quote of that statement's string.

- [ ] **Step 5: Run the tests**

Run: `TMPDIR=/Volumes/research/tmp uv run pytest -q`
Expected: all pass, including the 4 new resolution tests and the 3 new freshness cases.

- [ ] **Step 6: Gate, after** (see "The per-rule gate"). Expected:
- django: a large gain, on the order of thousands, in `module`/`import`/`typed` edges. Agreement ≥ 99.5%.
- freqtrade: about `gained 0`, since it has no star imports.

- [ ] **Step 7: Commit**

```bash
git add src/duckgrep/schema.py src/duckgrep/index.py tests/test_typed.py tests/test_resolution.py tests/test_typed_freshness.py
git commit -F- <<'EOF'
feat(typed): follow named and star re-exports up to three hops

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 5: Call-result receivers and `super(C, x)`

**Files:**
- Modify: `src/duckgrep/schema.py`: `_PY_TYPING` (`py_bind@N@`, `py_type@N@`)
- Test: `tests/test_typed.py`, `tests/test_typed_freshness.py`

**Interfaces:**
- Consumes: the `rcall` bindings from Task 2; `py_tclass` from Task 3.
- Produces: `py_bind@N@` rows for `rcall` receivers, with `bscope` = the ref's scope.

- [ ] **Step 0: Gate, before.** Set `TASK=5`.

- [ ] **Step 1: Write the failing tests**

```python
def test_call_result_receivers_and_two_argument_super(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": BASE,
            "other.py": OTHER,
            "ext_use.py": "from extlib import Ext\n\n\ndef g():\n    return Ext().run()\n",
            "use.py": "import other\nfrom base import Base\n\n\n"
            "class Child(Base):\n    def get(self):\n        return super(Child, self).get()\n\n\n"
            "def f():\n    Base().run()\n    return other.Other(1).get()\n",
        },
    )
    assert edges_at(root, "use.py", "run") == [("base.py", "Base.run", "typed")]
    assert edges_at(root, "use.py", "get") == [("base.py", "Base.get", "typed"), ("other.py", "Other.get", "typed")]
    assert [r[2] for r in edges_at(root, "ext_use.py", "run")] == ["unresolved"]
```

Append to `CROSS_FILE`:

```python
    "the class behind Foo().m() changes": (
        {
            "src.py": "class Foo:\n    def m(self):\n        return 1\n",
            "use.py": "from src import Foo\n\n\ndef f():\n    return Foo().m()\n",
        },
        {"src.py": "class Foo:\n    def n(self):\n        return 1\n"},
    ),
```

- [ ] **Step 2: Run them to verify they fail**

Run: `TMPDIR=/Volumes/research/tmp uv run pytest tests/test_typed.py::test_call_result_receivers_and_two_argument_super -v`
Expected: FAIL. `run`/`get` come back as `name` rows, and `Ext().run()` is not `unresolved`.

- [ ] **Step 3: Implement**

In `_PY_TYPING`, add a fourth arm to `py_bind@N@`:

```sql
  UNION ALL  -- Foo(...).m(), mod.Foo(...).m(): the receiver is one call, typed like `x = Foo(...)`
    SELECT r.path, r.line, r.col, r.path, b.type_text, r.scope
    FROM py_subj@N@ r
    JOIN bindings b ON b.path = r.path AND b.kind = 'rcall' AND b.line = r.line AND b.pos = r.col
    WHERE starts_with(b.type_text, 'call:')
```

Add an arm to `py_type@N@`:

```sql
  UNION ALL  -- super(C, x): like super(), when C is the enclosing class
    SELECT r.path, r.line, r.col, r.path, r.scope_class, 1, FALSE
    FROM py_subj@N@ r
    JOIN bindings b ON b.path = r.path AND b.kind = 'rcall' AND b.line = r.line AND b.pos = r.col
    WHERE r.scope_class IS NOT NULL AND b.type_text = 'super:' || regexp_extract(r.scope_class, '[^.]*$')
```

`py_type_ext` already reads `py_bind1`, so `Ext().run()` becomes `unresolved` through its first branch. No change is needed there.

- [ ] **Step 4: Run the tests.** `TMPDIR=/Volumes/research/tmp uv run pytest -q`. Expected: all pass.

- [ ] **Step 5: Gate, after.** Expected: a gain on both repos, with agreement ≥ 99.5%.

- [ ] **Step 6: Commit**

```bash
git add src/duckgrep/schema.py tests/test_typed.py tests/test_typed_freshness.py
git commit -F- <<'EOF'
feat(typed): type Foo(...).m() and super(C, x).m()

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 6: Inferred returns of unannotated functions

**Files:**
- Modify: `src/duckgrep/schema.py`: new `py_retvar` and `py_ret`; `py_tclass` arm 3; `py_type_ext` arm 2; `TYPED_DIRTY` (`start`) and `TYPED_DIRTY_SEED`
- Test: `tests/test_typed.py`, `tests/test_typed_freshness.py`

**Interfaces:**
- Consumes: `return` bindings (Task 2).
- Produces: `py_ret(path, qual, text)`, a function's return type text, at most one per function. It is the annotation (`Foo`) if there is one. Otherwise it's the agreed inferred text (`call:Foo`), and `var:` locals are rewritten to their `call:` binding.

- [ ] **Step 0: Gate, before.** Set `TASK=6`.

- [ ] **Step 1: Write the failing tests**

```python
def test_inferred_returns(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": BASE,
            "other.py": OTHER,
            "mk.py": "from base import Base\nfrom other import Other\n\n\n"
            "def make():\n    return Base()\n\n\n"
            "def via_local(c):\n    dk = Base()\n    if c:\n        return None\n    return dk\n\n\n"
            "def mixed(c):\n    if c:\n        return Base()\n    return Other()\n\n\n"
            "def gen():\n    yield Base()\n\n\n"
            "def rec():\n    return rec()\n\n\n"
            "def rebound():\n    x = Base()\n    x = Other()\n    return x\n",
            "use.py": "from mk import make, via_local, mixed, gen, rec, rebound\n\n\n"
            "def f():\n    a = make()\n    a.run()\n    b = via_local(1)\n    b.get()\n"
            "    c = mixed(1)\n    c.run()\n    d = gen()\n    d.get()\n    e = rec()\n    e.run()\n"
            "    g = rebound()\n    return g.get()\n",
        },
    )
    typed = {(r[0], r[1]) for n in ("run", "get") for r in edges_at(root, "use.py", n) if r[2] == "typed"}
    assert typed == {("base.py", "Base.run"), ("base.py", "Base.get")}
    lines = rows(root, "SELECT line FROM edges WHERE src_path = 'use.py' AND resolution = 'typed' ORDER BY line")
    assert lines == [(6,), (8,)]
```

Add `rows` to the `helpers` import at the top of `test_typed.py`, if it's not already imported.

Append to `CROSS_FILE`:

```python
    "an unannotated function's return changes": (
        {
            "a.py": "class A:\n    def m(self):\n        return 1\n",
            "b.py": "class B:\n    def m(self):\n        return 2\n",
            "mk.py": "from a import A\nfrom b import B\n\n\ndef make():\n    return A()\n",
            "use.py": "from mk import make\n\n\ndef f():\n    x = make()\n    return x.m()\n",
        },
        {"mk.py": "from a import A\nfrom b import B\n\n\ndef make():\n    return B()\n"},
    ),
    "an unannotated function's returns stop agreeing": (
        {
            "a.py": "class A:\n    def m(self):\n        return 1\n",
            "b.py": "class B:\n    def m(self):\n        return 2\n",
            "mk.py": "from a import A\nfrom b import B\n\n\ndef make(c):\n    return A()\n",
            "use.py": "from mk import make\n\n\ndef f():\n    x = make(1)\n    return x.m()\n",
        },
        {"mk.py": "from a import A\nfrom b import B\n\n\ndef make(c):\n    if c:\n        return B()\n    return A()\n"},
    ),
```

- [ ] **Step 2: Run them to verify they fail**

Run: `TMPDIR=/Volumes/research/tmp uv run pytest tests/test_typed.py::test_inferred_returns -v`
Expected: FAIL, with the typed set empty.

- [ ] **Step 3: Implement**

Add to `_EDGES_TEMPLATE`, before `py_tclass`:

```sql
py_retvar AS (  -- `return v`: v bound exactly once in the function, by an assignment of a call
    SELECT path, scope, name, min(type_text) AS type_text FROM bindings
    WHERE kind IN ('assign', 'annot', 'param', 'global', 'import')
    GROUP BY path, scope, name
    HAVING count(*) = 1 AND min(kind) = 'assign' AND starts_with(min(type_text), 'call:')
),
py_nonlocal AS (SELECT DISTINCT path, scope, name FROM bindings WHERE kind = 'global'),
py_ret AS (  -- a function's return type text: its annotation, else what every value-returning `return` agrees on
    SELECT path, qualname AS qual, returns AS text FROM symbols WHERE lang = 'python' AND returns IS NOT NULL
  UNION ALL
    SELECT path, scope, min(t) FROM (
        SELECT b.path, b.scope,
               CASE WHEN starts_with(b.type_text, 'call:') THEN b.type_text
                    WHEN starts_with(b.type_text, 'var:') AND g.name IS NULL THEN v.type_text END AS t
        FROM bindings b
        LEFT JOIN py_retvar v ON v.path = b.path AND v.scope = b.scope AND 'var:' || v.name = b.type_text
        LEFT JOIN py_nonlocal g ON g.path = b.path AND 'var:' || g.name = b.type_text
             AND starts_with(g.scope, b.scope || '.')  -- nonlocal v in a nested def rebinds it
        WHERE b.kind = 'return'
        -- one row per return statement; a nonlocal match (g.name not NULL) sorts first, so it makes t NULL
        QUALIFY row_number() OVER (PARTITION BY b.path, b.scope, b.line, b.type_text ORDER BY (g.name IS NULL)) = 1
    )
    GROUP BY path, scope
    HAVING count(*) = count(t) AND count(DISTINCT t) = 1
),
```

Change `py_tclass` arm 3 to read `py_ret` rather than `d.returns`. The text can be `Foo` (annotated) or `call:Foo` (inferred), so match both through the class-only arms:

```sql
  UNION ALL  -- x = make(...) / Foo.create(...): one hop of the callee's return (annotated or inferred)
    SELECT d.path, 'call:' || d.text, c.dpath, c.dqual, 1
    FROM py_def d
    JOIN py_ret pr ON pr.path = d.dpath AND pr.qual = d.dqual
    JOIN py_def c ON c.path = d.dpath AND c.dkind = 'class'
         AND c.text = CASE WHEN starts_with(pr.text, 'call:') THEN substr(pr.text, 6) ELSE pr.text END
    WHERE d.dkind IN ('function', 'method')
```

In `py_type_ext`, its second branch (`JOIN py_ext x ON x.path = d.dpath AND x.text = d.returns`) becomes:

```sql
    SELECT b.path, b.line, b.col FROM py_bind1 b
    JOIN py_def d ON d.path = b.tpath AND d.text = substr(b.type_text, 6) AND d.dkind IN ('function', 'method')
    JOIN py_ret pr ON pr.path = d.dpath AND pr.qual = d.dqual
    JOIN py_ext x ON x.path = d.dpath
         AND x.text = CASE WHEN starts_with(pr.text, 'call:') THEN substr(pr.text, 6) ELSE pr.text END
    WHERE starts_with(b.type_text, 'call:')
```

The `CASE` key must be in `py_tx`, so the referencing file's `py_def` and `py_ext` see it. The `return` rows' `call:` texts already reach `py_tx` through Task 3's bindings branch, with `call:` stripped. `var:` texts are excluded there, and the locals' `call:` assignments are already in it.

Dirty marking, in `TYPED_DIRTY_SEED`: add the changed files' return texts and the call types of their returned locals:

```sql
UNION SELECT regexp_extract(substr(b.type_text, 6), '[^.]*$') FROM bindings b
      WHERE b.path IN ({files}) AND b.kind = 'return' AND starts_with(b.type_text, 'call:')
UNION SELECT regexp_extract(substr(a.type_text, 6), '[^.]*$') FROM bindings a
      JOIN bindings b ON b.path = a.path AND b.scope = a.scope AND b.kind = 'return' AND b.type_text = 'var:' || a.name
      WHERE a.path IN ({files}) AND a.kind = 'assign' AND starts_with(a.type_text, 'call:')
```

In `TYPED_DIRTY`'s `start` CTE, add inferred returns of functions named in the seed:

```sql
    UNION SELECT regexp_extract(substr(b.type_text, 6), '[^.]*$') FROM bindings b
          SEMI JOIN seed ON seed.name = regexp_extract(b.scope, '[^.]*$')
          WHERE b.kind = 'return' AND starts_with(b.type_text, 'call:')
```

- [ ] **Step 4: Run the tests.** `TMPDIR=/Volumes/research/tmp uv run pytest -q`. Expected: all pass.

- [ ] **Step 5: Gate, after.** Expected: a gain on both repos, with agreement ≥ 99.5%.

- [ ] **Step 6: Commit** with the message `feat(typed): infer the return of an unannotated function when every return agrees`, plus the trailer.

---

### Task 7: Classes defined inside the calling function

**Files:**
- Modify: `src/duckgrep/schema.py`: new `py_lcls`; two arms in `_PY_TYPING`'s `py_type@N@`
- Test: `tests/test_typed.py`, `tests/test_typed_freshness.py`

**Interfaces:**
- Consumes: `local_class` bindings (Task 2); `py_bind@N@.bscope` (Task 3).
- Produces: `py_lcls(path, scope, name, qual, line)`, function-local classes. Each is defined once under its parent function, and no other binding of its name exists in that function or below it.

- [ ] **Step 0: Gate, before.** Set `TASK=7`.

- [ ] **Step 1: Write the failing tests**

```python
def test_classes_defined_in_the_calling_function(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": BASE,
            "t.py": "from base import Base\n\n\n"
            "def test_a():\n    class Admin(Base):\n        pass\n\n    ma = Admin()\n    ma.run()\n    return Admin.get(ma)\n\n\n"
            "def test_b():\n    class Admin(Base):\n        pass\n\n    return Admin().get()\n\n\n"
            "def test_twice(c):\n    if c:\n        class Two(Base):\n            pass\n    else:\n"
            "        class Two(Base):\n            pass\n    return Two().run()\n\n\n"
            "def test_early():\n    x = Late()\n    x.run()\n\n    class Late(Base):\n        pass\n",
        },
    )
    got = rows(root, "SELECT line, dst_qualname FROM edges WHERE src_path = 't.py' AND resolution = 'typed' "
                     "ORDER BY line")
    assert got == [(9, "Base.run"), (10, "Base.get"), (17, "Base.get")]
```

Append to `CROSS_FILE`:

```python
    "a local class's base changes in another file": (
        {
            "a.py": "class A:\n    def m(self):\n        return 1\n",
            "b.py": "class B:\n    def m(self):\n        return 2\n",
            "base.py": "from a import A\n\n\nclass Base(A):\n    pass\n",
            "t.py": "from base import Base\n\n\ndef t():\n    class L(Base):\n        pass\n\n    return L().m()\n",
        },
        {"base.py": "from b import B\n\n\nclass Base(B):\n    pass\n"},
    ),
```

- [ ] **Step 2: Run them to verify they fail.** Expected: FAIL, because Phase 1 refuses the nested names.

- [ ] **Step 3: Implement**

Add to `_EDGES_TEMPLATE`, before `py_subj1`:

```sql
py_lcls AS (  -- classes defined in a function: once there, and no other binding of the name there or below
    SELECT l.path, l.scope, l.name, min(l.type_text) AS qual, min(l.line) AS line
    FROM bindings l
    WHERE l.kind = 'local_class'
      AND NOT EXISTS (SELECT 1 FROM bindings o WHERE o.path = l.path AND o.name = l.name
                        AND o.kind IN ('assign', 'annot', 'param', 'global', 'import')
                        AND (o.scope = l.scope OR starts_with(o.scope, l.scope || '.')))
    GROUP BY l.path, l.scope, l.name
    HAVING count(*) = 1
),
```

Add two arms to `py_type@N@` in `_PY_TYPING`:

```sql
  UNION ALL  -- x = Local(...): a class defined in this function or an enclosing one, before the use
    SELECT path, line, col, path, qual, 0, FALSE FROM (
        SELECT b.path, b.line, b.col, lc.qual,
               row_number() OVER (PARTITION BY b.path, b.line, b.col ORDER BY length(lc.scope) DESC) AS rk
        FROM py_bind@N@ b
        JOIN py_lcls lc ON lc.path = b.path AND b.tpath = b.path AND lc.name = regexp_replace(b.type_text, '^call:', '')
             AND (lc.scope = b.bscope OR starts_with(b.bscope, lc.scope || '.')) AND lc.line < b.line
    ) WHERE rk = 1
  UNION ALL  -- Local.m(): the local class itself as the receiver
    SELECT path, line, col, path, qual, 0, FALSE FROM (
        SELECT r.path, r.line, r.col, lc.qual,
               row_number() OVER (PARTITION BY r.path, r.line, r.col ORDER BY length(lc.scope) DESC) AS rk
        FROM py_subj@N@ r
        JOIN py_lcls lc ON lc.path = r.path AND lc.name = r.subj
             AND (lc.scope = r.scope OR starts_with(r.scope, lc.scope || '.')) AND lc.line < r.line
        ANTI JOIN py_scoped@N@ s ON s.path = r.path AND s.line = r.line AND s.col = r.col
    ) WHERE rk = 1
```

`py_bind@N@`'s first arm requires one agreeing binding: `ma = Admin()` gives `call:Admin`. `py_tclass` has no row for it, because `py_def` refuses nested names. So the only `py_type` row comes from this arm.

- [ ] **Step 4: Run the tests.** Expected: all pass.

If `(17, "Base.get")` comes back as two rows, one from the `qualified` tier and one `typed`, keep the test's `resolution = 'typed'` filter. The `t1d` order labels same-target rows by tier.

- [ ] **Step 5: Gate, after.** Expected: a gain on django's test files, with agreement ≥ 99.5%.

- [ ] **Step 6: Commit** with the message `feat(typed): classes defined in the calling function`, plus the trailer.

---

### Task 8: Stage 2, one attribute hop (`h.a.m()`, `self.a.b.m()`, `Cls.attr.m()`, `mod.attr.m()`)

**Files:**
- Modify: `src/duckgrep/schema.py`: `py_tx` (heads); new `py_subj2`, `@PY_TYPING_2@`, `py_attr2`, `py_type2x`; `py_type` becomes the union
- Test: `tests/test_typed.py`, `tests/test_typed_freshness.py`

**Interfaces:**
- Consumes: `_PY_TYPING` (Task 3) and `py_tclass`.
- Produces:
  - `py_subj2(path, line, col, scope, scope_class, subj, attr, how)`. `how` is `'attr'` here; Task 10 adds `'ret'`.
  - `py_type2x(path, line, col, cpath, cqual, from_depth, ext)`.
  - `py_type`, which becomes `py_type1` plus the `py_type2x` rows for refs `py_type1` doesn't type.

- [ ] **Step 0: Gate, before.** Set `TASK=8`.

- [ ] **Step 1: Write the failing tests**

```python
def test_one_attribute_hop(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": BASE,
            "other.py": OTHER,
            "bot.py": "from base import Base\nfrom other import Other\n\n\n"
            "class Bot:\n    objects = Other()\n\n    def __init__(self):\n        self.exchange = Base()\n",
            "sig.py": "from base import Base\n\npost_save = Base()\n",
            "use.py": "import sig\nfrom bot import Bot\n\n\n"
            "class Svc:\n    def __init__(self):\n        self.bot = Bot()\n\n"
            "    def go(self):\n        return self.bot.exchange.run()\n\n\n"
            "def f():\n    b = Bot()\n    b.exchange.get()\n    Bot.objects.run()\n    return sig.post_save.get()\n",
        },
    )
    got = rows(root, "SELECT line, dst_qualname FROM edges WHERE src_path = 'use.py' AND resolution = 'typed' "
                     "ORDER BY line")
    assert got == [(10, "Base.run"), (15, "Base.get"), (16, "Other.run"), (17, "Base.get")]
```

Append to `CROSS_FILE`:

```python
    "an attribute's type changes in the head's class": (
        {
            "a.py": "class A:\n    def m(self):\n        return 1\n",
            "b.py": "class B:\n    def m(self):\n        return 2\n",
            "bot.py": "from a import A\nfrom b import B\n\n\nclass Bot:\n    def __init__(self):\n        self.x = A()\n",
            "use.py": "from bot import Bot\n\n\ndef f():\n    b = Bot()\n    return b.x.m()\n",
        },
        {"bot.py": "from a import A\nfrom b import B\n\n\nclass Bot:\n    def __init__(self):\n        self.x = B()\n"},
    ),
    "a module-level binding behind mod.attr changes": (
        {
            "a.py": "class A:\n    def m(self):\n        return 1\n",
            "b.py": "class B:\n    def m(self):\n        return 2\n",
            "sig.py": "from a import A\nfrom b import B\n\nhook = A()\n",
            "use.py": "import sig\n\n\ndef f():\n    return sig.hook.m()\n",
        },
        {"sig.py": "from a import A\nfrom b import B\n\nhook = B()\n"},
    ),
```

- [ ] **Step 2: Run them to verify they fail.** Expected: FAIL, with no typed rows.

- [ ] **Step 3: Implement**

Extend `py_tx`'s receivers branch so class heads (`User` in `User.objects.m()`) are resolvable:

```sql
      UNION SELECT path, regexp_extract(receiver, '^([A-Za-z_][A-Za-z0-9_]*)[.]', 1) FROM r
        WHERE family = 'py' AND regexp_matches(receiver, '^[A-Za-z_][A-Za-z0-9_]*[.][A-Za-z_][A-Za-z0-9_]*$')
          AND regexp_matches(receiver, '^[A-Z]')
```

After `py_type1` in `_EDGES_TEMPLATE`, add:

```sql
py_subj2 AS (  -- stage 2 subjects: the head of `h.a.m()` (h a name or self.x), for receivers stage 1 didn't type
    SELECT r.path, r.line, r.col, r.scope, r.scope_class,
           regexp_extract(r.receiver, '^(.*)[.][A-Za-z_][A-Za-z0-9_]*$', 1) AS subj,
           regexp_extract(r.receiver, '[A-Za-z_][A-Za-z0-9_]*$') AS attr, 'attr' AS how
    FROM py_r r
    ANTI JOIN py_type1 t ON t.path = r.path AND t.line = r.line AND t.col = r.col
    WHERE regexp_matches(r.receiver, '^[A-Za-z_][A-Za-z0-9_]*([.][A-Za-z_][A-Za-z0-9_]*)?[.][A-Za-z_][A-Za-z0-9_]*$')
      AND NOT regexp_matches(r.receiver, '^(self|cls)[.][A-Za-z_][A-Za-z0-9_]*$')
),
@PY_TYPING_2@
py_attr2 AS (  -- h.a: a's type text and the file it's written in
    -- an instance or class head: the nearest class in its ancestry that binds self.a, when its bindings agree
    SELECT path, line, col, apath, type_text FROM (
        SELECT t.path, t.line, t.col, a.apath,
               count(*) FILTER (WHERE b.type_text IS NULL) AS n_untyped,
               count(DISTINCT b.type_text) AS n_types, min(b.type_text) AS type_text
        FROM py_type2 t
        JOIN py_subj2 s ON s.path = t.path AND s.line = t.line AND s.col = t.col AND s.how = 'attr'
        JOIN py_mro a ON a.path = t.cpath AND a.qual = t.cqual AND a.depth >= t.from_depth
        JOIN bindings b ON b.path = a.apath AND b.scope = a.aqual AND b.kind = 'attr' AND b.name = 'self.' || s.attr
        WHERE NOT t.ext
        GROUP BY t.path, t.line, t.col, a.apath, a.ord
        QUALIFY row_number() OVER (PARTITION BY t.path, t.line, t.col ORDER BY a.ord) = 1
    ) WHERE n_untyped = 0 AND n_types = 1
  UNION ALL  -- a module head (bound only by an import): a's binding at the top of that module, bound once
    SELECT s.path, s.line, s.col, i.target_path, min(b.type_text)
    FROM py_subj2 s
    JOIN py_imp i ON i.path = s.path AND i.target_path IS NOT NULL
         AND ((i."local" = s.subj AND (i.name IS NULL OR i.target_is_module))
              OR (i.name IS NULL AND i.alias IS NULL AND i.module = s.subj))
    JOIN bindings b ON b.path = i.target_path AND b.scope = '' AND b.name = s.attr
         AND b.kind IN ('assign', 'annot', 'global', 'import')
    ANTI JOIN py_scoped2 x ON x.path = s.path AND x.line = s.line AND x.col = s.col
    ANTI JOIN py_global g ON g.path = i.target_path AND g.name = s.attr
    WHERE s.how = 'attr'
    GROUP BY ALL
    HAVING count(*) FILTER (WHERE b.type_text IS NULL OR b.kind = 'global') = 0 AND count(DISTINCT b.type_text) = 1
),
py_type2x AS (  -- stage 2 types: the attribute's type text, read in the file that binds it (no return hop)
    SELECT a.path, a.line, a.col, c.cpath, c.cqual, 0 AS from_depth, FALSE AS ext
    FROM py_attr2 a JOIN py_tclass c ON c.path = a.apath AND c.text = a.type_text AND c.hop = 0
),
```

Replace `py_type AS (SELECT * FROM py_type1),` with:

```sql
py_type AS (
    SELECT * FROM py_type1
  UNION ALL
    SELECT x.* FROM py_type2x x ANTI JOIN py_type1 t ON t.path = x.path AND t.line = x.line AND t.col = x.col
),
```

Add `.replace("@PY_TYPING_2@", _PY_TYPING.replace("@N@", "2"))` to the `EDGES_COMPUTE` assembly. Extend `test_template_generates_both_stages` with `assert "py_scoped2 AS" in schema.EDGES_COMPUTE`.

Dirty marking: attribute bindings (`kind = 'attr'`) and module-level bindings (`scope = ''`) are already in `TYPED_DIRTY_SEED`'s bindings branch. The two new freshness cases verify that this is enough.

- [ ] **Step 4: Run the tests.** Expected: all pass.

- [ ] **Step 5: Gate, after.** Expected: a gain on both repos, with agreement ≥ 99.5%.

- [ ] **Step 6: Commit** with the message `feat(typed): one attribute hop on a typed instance, class or module`, plus the trailer.

---

### Task 9: Class-body aliases (`self.X(...)` where `X = Foo`)

**Files:**
- Modify: `src/duckgrep/schema.py`: new `py_alias_need` and `py_alias`; two arms in `_PY_TYPING`'s `py_type@N@`; `TYPED_DIRTY_SEED`
- Test: `tests/test_typed.py`, `tests/test_typed_freshness.py`

**Interfaces:**
- Consumes: `alias` bindings (Task 2); `rcall` receivers (Task 5); `py_mro`.
- Produces: `py_alias(path, qual, x, apath, alias)`. For class `qual`, it gives the nearest class (`apath`) in its ancestry whose body binds `X = <alias>`, when that is the only binding of `self.X` there.

- [ ] **Step 0: Gate, before.** Set `TASK=9`.

- [ ] **Step 1: Write the failing tests**

```python
def test_class_body_aliases(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": BASE,
            "other.py": OTHER,
            "views.py": "from base import Base\nfrom other import Other\n\n\n"
            "class View:\n    form_class = Base\n    k_class = Other\n\n"
            "    def __init__(self):\n        self.k_class = None\n\n"
            "    def go(self):\n        form = self.form_class()\n        form.run()\n"
            "        self.form_class().get()\n        self.form_class.get(form)\n"
            "        return self.k_class().run()\n\n\n"
            "class Sub(View):\n    def more(self):\n        return self.form_class().run()\n",
        },
    )
    got = rows(root, "SELECT line, dst_qualname FROM edges WHERE src_path = 'views.py' AND resolution = 'typed' "
                     "ORDER BY line")
    assert got == [(14, "Base.run"), (15, "Base.get"), (16, "Base.get"), (22, "Base.run")]
```

`k_class` is also assigned in `__init__`, so it is refused, and line 17 gets no typed edge.

Append to `CROSS_FILE`:

```python
    "a class alias's target changes in a base class": (
        {
            "a.py": "class A:\n    def m(self):\n        return 1\n",
            "b.py": "class B:\n    def m(self):\n        return 2\n",
            "view.py": "from a import A\nfrom b import B\n\n\nclass View:\n    form_class = A\n",
            "sub.py": "from view import View\n\n\nclass Sub(View):\n    def go(self):\n        return self.form_class().m()\n",
        },
        {"view.py": "from a import A\nfrom b import B\n\n\nclass View:\n    form_class = B\n"},
    ),
```

- [ ] **Step 2: Run them to verify they fail.** Expected: FAIL, with no typed rows.

- [ ] **Step 3: Implement**

Add to `_EDGES_TEMPLATE`, after `py_mro`:

```sql
py_alias_need AS (  -- attribute names called as self.X(...) / cls.X(...) or used as self.X.m()
    SELECT DISTINCT regexp_extract(type_text, '^call:(?:self|cls)[.]([A-Za-z_][A-Za-z0-9_]*)$', 1) AS x FROM bindings
    WHERE regexp_matches(type_text, '^call:(self|cls)[.][A-Za-z_][A-Za-z0-9_]*$')
  UNION SELECT regexp_extract(receiver, '^(?:self|cls)[.]([A-Za-z_][A-Za-z0-9_]*)$', 1) FROM py_r
    WHERE regexp_matches(receiver, '^(self|cls)[.][A-Za-z_][A-Za-z0-9_]*$')
),
py_alias AS (  -- class -> the alias X = Foo nearest in its ancestry, when that body binds self.X only there
    SELECT path, qual, x, apath, alias FROM (
        SELECT m.path, m.qual, substr(b.name, 6) AS x, m.apath,
               count(*) FILTER (WHERE b.kind = 'alias') AS n_alias,
               count(*) FILTER (WHERE b.kind = 'attr') AS n_attr,
               count(DISTINCT b.line) AS n_lines, min(b.type_text) FILTER (WHERE b.kind = 'alias') AS alias
        FROM py_mro m
        JOIN bindings b ON b.path = m.apath AND b.scope = m.aqual AND b.kind IN ('attr', 'alias')
             AND substr(b.name, 6) IN (SELECT x FROM py_alias_need)
        GROUP BY m.path, m.qual, b.name, m.apath, m.ord
        QUALIFY row_number() OVER (PARTITION BY m.path, m.qual, b.name ORDER BY m.ord) = 1
    ) WHERE n_alias = 1 AND n_attr = 1 AND n_lines = 1
),
```

Add two arms to `py_type@N@` in `_PY_TYPING`:

```sql
  UNION ALL  -- form = self.form_class(...) / self.form_class(...).m(): an instance of the alias's class
    SELECT b.path, b.line, b.col, c.cpath, c.cqual, 0, FALSE
    FROM py_bind@N@ b
    JOIN py_subj@N@ r ON r.path = b.path AND r.line = b.line AND r.col = b.col
    JOIN py_alias al ON al.path = r.path AND al.qual = r.scope_class
         AND al.x = regexp_extract(b.type_text, '^call:(?:self|cls)[.]([A-Za-z_][A-Za-z0-9_]*)$', 1)
    JOIN py_tclass c ON c.path = al.apath AND c.text = al.alias AND c.hop = 0
    WHERE b.tpath = b.path
  UNION ALL  -- self.form_class.m(): the alias's class itself as the receiver
    SELECT r.path, r.line, r.col, c.cpath, c.cqual, 0, FALSE
    FROM py_subj@N@ r
    JOIN py_alias al ON al.path = r.path AND al.qual = r.scope_class
         AND al.x = regexp_extract(r.subj, '^(?:self|cls)[.]([A-Za-z_][A-Za-z0-9_]*)$', 1)
    JOIN py_tclass c ON c.path = al.apath AND c.text = al.alias AND c.hop = 0
```

`py_bind@N@`'s first arm gives `form`'s binding text `call:self.form_class`. Its `rcall` arm (Task 5) gives the same text for `self.form_class().m()`. The alias's own text (`Base`) is in `py_tx`, because it is a binding `type_text`.

Dirty marking: add alias texts to `TYPED_DIRTY_SEED`'s bindings branch. It reads `kind IN ('base', 'attr')`; make it `kind IN ('base', 'attr', 'alias')`.

- [ ] **Step 4: Run the tests.** Expected: all pass.

- [ ] **Step 5: Gate, after.** Expected: a gain, mostly on django, with agreement ≥ 99.5%.

- [ ] **Step 6: Commit** with the message `feat(typed): class-body aliases called through self`, plus the trailer.

---

### Task 10: Stage 2, a method's return through a typed receiver (`x = h.m()`)

**Files:**
- Modify: `src/duckgrep/schema.py`: `py_subj2` (a `'ret'` branch); new `py_ret2`; `py_type2x`
- Test: `tests/test_typed.py`, `tests/test_typed_freshness.py`

**Interfaces:**
- Consumes: `py_bind1.bscope` (Task 3); `py_ret` (Task 6); `py_subj2`/`py_type2` (Task 8); `py_diamond`, `py_blocker`, `py_mro` (existing).

- [ ] **Step 0: Gate, before.** Set `TASK=10`.

- [ ] **Step 1: Write the failing tests**

```python
def test_method_return_through_a_typed_receiver(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": BASE,
            "other.py": OTHER,
            "eng.py": "from other import Other\n\n\nclass Engine:\n    def make(self) -> Other:\n        return Other()\n\n"
            "    def plain(self):\n        return Other()\n",
            "use.py": "from eng import Engine\n\n\nclass T:\n    def go(self):\n        e = Engine()\n"
            "        t = e.make()\n        t.run()\n        p = e.plain()\n        return p.get()\n",
        },
    )
    got = rows(root, "SELECT line, dst_qualname FROM edges WHERE src_path = 'use.py' AND resolution = 'typed' "
                     "ORDER BY line")
    assert (8, "Other.run") in got and (10, "Other.get") in got


def test_self_method_return_and_the_stage_cap(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": BASE,
            "eng.py": "from base import Base\n\n\nclass Engine:\n    def make(self):\n        return Base()\n",
            "use.py": "from eng import Engine\n\n\nclass T:\n    def _engine(self):\n        return Engine()\n\n"
            "    def go(self):\n        e = self._engine()\n        e.make()\n        t = e.make()\n        return t.run()\n",
        },
    )
    got = rows(root, "SELECT line, dst_qualname FROM edges WHERE src_path = 'use.py' AND resolution = 'typed' "
                     "ORDER BY line")
    assert got == [(10, "Engine.make"), (11, "Engine.make")]  # t.run() would need a third stage: none
```

- **First test:** `t` and `p` are typed in stage 2. Their head `e` is a direct `Engine()` binding; `make` is annotated and `plain` inferred.
- **Second test:** `e = self._engine()` is typed in stage 2. Its head is `self`, and `_engine`'s inferred return is `Engine`. `t = e.make()` would need `e`'s stage-2 type as a head, which is a third stage, so `t.run()` gets no typed edge. `self._engine()` itself keeps its `self` tier label, so it isn't in the typed rows.

Append to `CROSS_FILE`:

```python
    "a method behind x = h.m() changes its return": (
        {
            "a.py": "class A:\n    def m(self):\n        return 1\n",
            "b.py": "class B:\n    def m(self):\n        return 2\n",
            "eng.py": "from a import A\nfrom b import B\n\n\nclass Engine:\n    def make(self):\n        return A()\n",
            "use.py": "from eng import Engine\n\n\ndef f():\n    e = Engine()\n    t = e.make()\n    return t.m()\n",
        },
        {"eng.py": "from a import A\nfrom b import B\n\n\nclass Engine:\n    def make(self):\n        return B()\n"},
    ),
    "a subclass starts overriding the method behind x = h.m()": (
        {
            "a.py": "class A:\n    def m(self):\n        return 1\n",
            "b.py": "class B:\n    def m(self):\n        return 2\n",
            "base.py": "from a import A\n\n\nclass Base:\n    def make(self):\n        return A()\n",
            "eng.py": "from base import Base\n\n\nclass Engine(Base):\n    pass\n",
            "use.py": "from eng import Engine\n\n\ndef f():\n    e = Engine()\n    t = e.make()\n    return t.m()\n",
        },
        {"eng.py": "from base import Base\nfrom b import B\n\n\nclass Engine(Base):\n    def make(self):\n        return B()\n"},
    ),
```

- [ ] **Step 2: Run them to verify they fail.** Expected: FAIL.

- [ ] **Step 3: Implement**

Add a branch to `py_subj2`. The subject is the head `h` of the receiver's binding `call:h.m`, in the binding's scope:

```sql
  UNION ALL  -- x = h.m(...): the head h, in the scope that binds x, for the method's return
    SELECT b.path, b.line, b.col, b.bscope, r.scope_class,
           regexp_extract(b.type_text, '^call:([A-Za-z_][A-Za-z0-9_]*)[.]', 1),
           regexp_extract(b.type_text, '[A-Za-z_][A-Za-z0-9_]*$'), 'ret'
    FROM py_bind1 b
    JOIN py_r r ON r.path = b.path AND r.line = b.line AND r.col = b.col
    ANTI JOIN py_type1 t ON t.path = b.path AND t.line = b.line AND t.col = b.col
    WHERE b.tpath = b.path AND b.bscope IS NOT NULL
      AND regexp_matches(b.type_text, '^call:[A-Za-z_][A-Za-z0-9_]*[.][A-Za-z_][A-Za-z0-9_]*$')
```

Its `scope` column is `b.bscope`, so `py_scoped2` reads `h`'s bindings where `x` is bound. A `self` head is typed by `py_type2`'s `self` arm, through `scope_class`.

After `py_attr2`, add:

```sql
py_ret2 AS (  -- x = h.m(...): the first definer of m in h's class, unless the order is uncertain
    SELECT path, line, col, dst_path, dst_qual FROM (
        SELECT t.path, t.line, t.col, t.cpath, t.cqual, a.ord, s.path AS dst_path, s.qualname AS dst_qual,
               count(*) OVER (PARTITION BY t.path, t.line, t.col) AS n_def,
               row_number() OVER (PARTITION BY t.path, t.line, t.col ORDER BY a.ord, s.start_line) AS rk
        FROM py_type2 t
        JOIN py_subj2 j ON j.path = t.path AND j.line = t.line AND j.col = t.col AND j.how = 'ret'
        JOIN py_mro a ON a.path = t.cpath AND a.qual = t.cqual AND a.depth >= t.from_depth
        JOIN symbols s ON s.path = a.apath AND s.parent = a.aqual AND s.name = j.attr AND s.kind = 'method'
        WHERE NOT t.ext
    ) h
    WHERE rk = 1
      AND NOT (n_def > 1 AND len(h.ord) > 0
               AND EXISTS (SELECT 1 FROM py_diamond x WHERE x.path = h.cpath AND x.qual = h.cqual))
      AND NOT EXISTS (SELECT 1 FROM py_blocker k WHERE k.path = h.cpath AND k.qual = h.cqual AND k.ord < h.ord)
),
```

Add to `py_type2x`:

```sql
  UNION ALL  -- ... and the class that method returns (annotated or inferred), in the method's file
    SELECT r.path, r.line, r.col, c.cpath, c.cqual, 0, FALSE
    FROM py_ret2 r
    JOIN py_ret pr ON pr.path = r.dst_path AND pr.qual = r.dst_qual
    JOIN py_tclass c ON c.path = r.dst_path AND c.hop = 0 AND c.text = pr.text  -- Foo and call:Foo are both keys
```

`py_attr2` must now filter `s.how = 'attr'` in both its branches; it already does.

- [ ] **Step 4: Run the tests.** Expected: all pass, including the two new freshness cases. The second relies on the class names `TYPED_DIRTY_SEED` takes from `eng.py` (`Engine`) and their method return texts (`B`, through Task 6's seed). If it fails, add the changed file's method return texts to the seed. They are already there for annotated returns (`symbols.returns`) and for inferred ones (Task 6). So then debug before widening.

- [ ] **Step 5: Gate, after.** Expected: a gain, with agreement ≥ 99.5%.

- [ ] **Step 6: Commit** with the message `feat(typed): a method's return through a typed receiver`, plus the trailer.

---

### Task 11: The `qualified` tier skips a locally rebound class name

**Files:**
- Modify: `src/duckgrep/schema.py`: new `py_rebound`; the `qualified` arm of `t1`
- Test: `tests/test_resolution.py`

- [ ] **Step 0: Gate, before.** Set `TASK=11`.

- [ ] **Step 1: Write the failing test**

```python
def test_qualified_skips_a_rebound_class_name(tmp_path):
    from helpers import make_repo, rows

    root = make_repo(
        tmp_path / "r",
        {
            "base.py": "class Base:\n    def run(self):\n        return 1\n",
            "use.py": "from base import Base\n\n\ndef f(other):\n    Base = other\n    return Base.run()\n\n\n"
            "def g():\n    return Base.run(None)\n",
        },
    )
    got = rows(root, "SELECT line, resolution FROM edges WHERE src_path = 'use.py' AND name = 'run' ORDER BY ALL")
    assert (6, "qualified") not in got and (10, "qualified") in got
```

- [ ] **Step 2: Run it to verify it fails.** Expected: FAIL, because `(6, 'qualified')` is present.

- [ ] **Step 3: Implement**

Add to `_EDGES_TEMPLATE`, before `t1`:

```sql
py_rebound AS (  -- capitalised names a function assigns or takes as a parameter: there they aren't the class
    SELECT DISTINCT path, scope, name FROM bindings
    WHERE kind IN ('assign', 'annot', 'param') AND scope <> '' AND regexp_matches(name, '^[A-Z]')
),
```

In the `qualified` arm, add to its `WHERE`:

```sql
      AND NOT (r.family = 'py' AND EXISTS (
          SELECT 1 FROM py_rebound b WHERE b.path = r.path AND b.name = r.recv_root
            AND (b.scope = r.scope OR starts_with(r.scope, b.scope || '.'))))
```

- [ ] **Step 4: Run the tests.** `TMPDIR=/Volumes/research/tmp uv run pytest -q`. Expected: all pass.

- [ ] **Step 5: Gate, after.** Expected: a small loss of `qualified` rows (`lost K`). Check each lost row by hand: it must be a rebound name, not a lost true edge.

- [ ] **Step 6: Commit** with the message `fix(qualified): a locally rebound class name is not the class`, plus the trailer.

---

### Task 12: Measure, document, review, ship

**Files:**
- Modify: `bench/RESULTS.md`, `README.md` (FAQ resolution tiers), `docs/roadmap.md`, `CHANGELOG.md`, `CLAUDE.md` (Resolution tiers → `typed`), `src/duckgrep/schema.py` (`SCHEMA_DOC`, only if the `typed` line needs to change)

- [ ] **Step 1: Accuracy against the Task 1 baseline**

```bash
for r in django freqtrade; do uv run --group bench python bench/accuracy.py /Volumes/research/scratch/$r 3000 > /Volumes/research/tmp/acc-$r-p2.txt; done
uv run --group bench python bench/accuracy.py /Volumes/research/scratch/requests 300 > /Volumes/research/tmp/acc-requests-p2.txt
grep "confident tiers" /Volumes/research/tmp/acc-*-p2.txt
```

Expected:
- coverage up at least 3 points on django and 2 on freqtrade over Task 1's baseline;
- precision of 99.9% or better on each repo.

List every `confident misses:` line and classify it by hand.

- [ ] **Step 2: Latency**

Build a main worktree for the "before" numbers:

```bash
git worktree add /Volumes/research/tmp/dg-main main
for tree in /Volumes/research/tmp/dg-main /Users/ccf/git/duckgrep; do
  (cd $tree && uv run python bench/latency.py /Volumes/research/scratch/django django/db/models/query.py get_or_create QuerySet.get_or_create)
done
```

Also time the 300-commit catch-up. Use the same procedure as `bench/RESULTS.md`'s "Catching up after many changes" section, on both trees.

Expected:
- full index within +20%;
- edge sync after a one-file edit within +20%;
- the 300-commit catch-up within +20%;
- no-op freshen unchanged.

If vscode is cloned at `/Volumes/research/scratch/vscode`, run `bench/latency.py` there too (`src/vs/base/common/strings.ts createDecorator createDecorator`). If it isn't, ledger that.

**If a bar fails, profile first:** time each statement, as in the PR #28 investigation. Then apply the spec's fallbacks:
- stage 2 cost: materialise stage 1 once per batch;
- dirty breadth: narrow the seed.

- [ ] **Step 3: Final review.** Dispatch one independent reviewer of the whole branch (`git merge-base main HEAD`..HEAD), on the most capable model at xhigh effort. That is the `live-reviewer` agent type, which is read-only.

Its prompt covers:
- the spec and this plan;
- the Review Focus list;
- the ledger's rulings;
- the gate files under `/Volumes/research/tmp/gate-*`.

Fix the Critical and Important findings test-first, in one pass. Minor ones go under "Deferred minors".

- [ ] **Step 4: Documentation**
- **`bench/RESULTS.md`:** a "Type inference, Phase 2" section. It has the accuracy table against the Task 1 baseline, the per-rule gate table (gained/changed/lost and agreement per task and repo), the latency table, and the classified disagreements.
- **`README.md` FAQ:** the `typed` tier's description gains star and multi-hop re-exports, class aliases, one attribute hop, call-result receivers, inferred returns and local classes. The accuracy numbers are updated.
- **`docs/roadmap.md`:**
  - Phase 2 moves to Done;
  - the Next item 1 becomes the deferred list (tuple unpacking, agreeing bindings, `None` as neutral, function-scope imports, external-base inventories, container element types), then TS/Go/Rust;
  - the multi-hop re-export gap is removed from Known gaps; aliased chains stay.
- **`CLAUDE.md`, Resolution tiers → `typed`:** one sentence on what Phase 2 adds, and "one hop of re-exports" becomes "up to three hops of named and star re-exports".
- **`CHANGELOG.md`, under Unreleased → Added:** a bold-lead line for Phase 2, with the accuracy deltas.
- **`SCHEMA_DOC`:** check that `uv run duckgrep schema` still describes `typed` accurately. Change it only if it's wrong, and keep it compact.

- [ ] **Step 5: Full suite and hooks.** `TMPDIR=/Volumes/research/tmp uv run pre-commit run --all-files`. Expected: all pass.

- [ ] **Step 6: Commit, PR, merge, release**

```bash
git add -A bench/RESULTS.md README.md docs/roadmap.md CHANGELOG.md CLAUDE.md src/duckgrep/schema.py
git commit -F- <<'EOF'
docs: Phase 2 numbers, tiers and roadmap

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
git push -u origin feat/typed-phase-2
gh pr create --title "feat(typed): type inference Phase 2 (Python)" --body "<summary, accuracy and latency tables, per-rule gate table>"
```

Follow `CLAUDE.md`, Workflow, through to merge. Then cut the minor release, `release/0.3.0`. The steps are in `CLAUDE.md`'s release paragraph.
