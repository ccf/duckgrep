# Type inference, Phase 2 (Python)

## Goal

Phase 1 (`docs/specs/2026-10-02-local-type-inference.md`) gave Python receiver calls a confident `typed` edge when the receiver's class can be read from one binding. Confident coverage of in-repo calls, measured against jedi, reached 85–88% on django, 91–92% on freqtrade and 90% on requests, at 99.9–100% precision.

Phase 2 closes the largest measured causes of the remaining gap with the same design:
- per-file facts from the extractor;
- cross-file joins in `EDGES_COMPUTE`;
- refusal whenever syntax can't settle the answer.

It stays Python-only. TypeScript, Go and Rust keep their own later specs.

## Evidence

The jedi benchmark (`bench/accuracy.py`) was rerun on django (0ae93a0), freqtrade (f2ec745) and requests (611c616): 3,000, 3,000 and 300 sampled calls. It kept every call where jedi gives an in-repo target but duckgrep has no confident edge. That's 375 calls: 227 on django, 133 on freqtrade and 15 on requests.

Seven readers then examined 249 of them in the source, one reader per receiver shape. They grouped each call by why no `typed` edge was produced. The table scales each cause to its whole bucket.

| Cause | Calls (sampled) | Example |
|---|---:|---|
| **Star or multi-hop re-exports.** Only named re-exports are followed, one hop. | ~58 (49) | `from django.forms import FileField`, where `forms/__init__` does `from .fields import *`. Also `migrations.CreateModel`: a star import, then a named re-export. |
| **Class-body aliases.** `X = Foo` in a class body, then `self.X(...)`. | ~30 (30) | `form_class = BaseUserCreationForm`, then `form = self.form_class(data)` |
| **One-hop attribute chains** on a typed instance, a class or a module. | ~29 (29) | `freqtradebot.exchange.get_fee()`, `User.objects.create_user()`, `signals.post_save.connect()` |
| **Call-result receivers.** | ~20 (20) | `Foo(...).m()`, `super(Aggregate, c).resolve_expression()` |
| **Unannotated returns,** and method returns through a typed receiver. | ~17 (17) | `def make(): return Engine(...)`; `dk = FreqaiDataKitchen(c); return dk`; `x = session.request(...)` |
| **Classes defined inside the calling function.** Phase 1 refuses a name any nested def shares. | ~8 (8) | a test-local `class BandAdmin(admin.ModelAdmin)`, then `ma.check()` |
| **Deferred:** tuple unpacking, agreeing bindings, `None` as neutral, function-scope imports recorded as assignments | ~20 (16) | `a, _, m = helper()`; `x: Trade` plus `x = get_trade()` |
| **No sound rule; keep refusing** | ~35 (30) | the next list |

The causes with no sound rule:
- an external base ahead of the in-repo definer in the MRO (SQLAlchemy's `DeclarativeBase`);
- unannotated parameters typed only at their call sites;
- `try/except ImportError` shims;
- isinstance narrowing;
- `a or Foo()`;
- `with x() as y` through unannotated `__enter__` chains.

A prototype of the star re-export rule, run against the real `EDGES_COMPUTE`, gave exactly jedi's target on all 16 samples it was tried on. It adds 2,370 confident edges on django.
- Django has 13 star-importing files, exporting 367 names, with 0 names defined by two star sources.
- freqtrade and requests have no star imports.

## Scope

### Phase 2 rules

**1. Re-export closure.**
- `from m import N` and `m.N` resolve through up to 3 hops of re-exports, where each hop is either:
  - a named re-export: `from .x import N` in m;
  - a star re-export: `from .x import *` in m, where x defines or re-exports N at top level.
- A hop stops, with no star fallback, when the module itself defines, assigns or imports N by name.
- With a star hop, exactly one star source of the module may provide N; if several do, refuse.
- Aliased hops (`from .x import A as N`) are not followed. That keeps the known aliased re-export gap unchanged.
- The closure serves the typed tier's class and function lookup (today's `py_rx` arms) and the `module` tier's `rx`. So bases reached through a star stop being opaque.

**2. Class-body aliases.** A class-body assignment `X = <dotted name>`, whose value is a name, not a call, becomes a class-object binding.
- `self.X(...)` and `cls.X(...)`, found through the class's ancestry as for `self.attr`, type as instances of the class it names.
- `self.X.m()` is a call on the class object itself.

**3. One attribute hop.** Receiver `h.a`, where `h` is a bare name:
- **A typed instance** (stage 1 gives its class C): `a`'s type is the nearest `self.a` attribute binding in C's ancestry, with the same agreement rule as `self.attr`.
- **A class** (`h` resolves to a class, with no other binding in scope): `a`'s type is the class-level binding of `a` in that class or its ancestors.
- **A module** (`h` is bound only by a module import): `a`'s type is the binding of `a` at the top level of that module, found as for an imported module-level instance.
- `self.a.b` (two hops from `self`) is the same rule, with stage 1's `self.a` type as the head.
- There is no third hop.

**4. Call-result receivers.**
- A receiver that is exactly one call of a dotted name, `Foo(...)` or `mod.Foo(...)`, types like a binding `call:Foo`. That covers constructors and annotated or inferred returns.
- `super(C, x)`, where C is the enclosing class's short name, types like `super()`.

**5. Inferred returns.** A function or method with no return annotation gets an inferred return type only when every value-returning `return` in its own body (nested defs excluded) agrees on one of:
- `return Foo(...)`, a call of the same dotted name;
- `return v`, where v is a local bound exactly once in the function to `Foo(...)`, and is not a parameter, not `global` or `nonlocal`, and not rebound by a loop, `with`, an augmented assignment or a walrus.

`return` and `return None` don't count against agreement. A function with no value-returning `return` gets nothing, and so does one that `yield`s. An inferred return is used exactly where an annotated one is, one hop. A method's return is also followed through a typed receiver: in `x = h.m(...)`, where stage 1 types `h` and the call has one `typed` target, the target's return types x.

**6. Classes defined inside a function.** A bare name N, used in function scope S, types as the class N defined in S or an enclosing function scope, when all of these hold:
- exactly one `class N` is defined under that parent;
- it's defined on an earlier line;
- no other binding of N exists from that parent down to S;
- no `global` or `nonlocal` for N.

Class-body scopes never count: their names aren't visible inside their methods. This replaces Phase 1's blanket refusal for this one shape. Every other nested-name refusal stays.

**7. The `qualified` tier's rebound class.** `Base = Other(); Base.run()` no longer resolves as a call to the class's method when the calling scope rebinds the capitalised name.

### Deferred (measured as small; a later spec)

- **Tuple unpacking.** It needs tuple-shaped inferred returns.
- **Several bindings that agree once resolved to a class** (`x: Trade` plus `x = get_trade()`), and `None` as a neutral binding.
- **Function-scope imports,** which are recorded today as plain assignments.
- **An inventory of what well-known external bases define,** so they stop blocking the MRO. Only the stdlib could be inventoried soundly.
- **Container element types** (`list[Foo]`, `for x in xs`).

### Non-goals

- Interprocedural argument typing.
- Flow sensitivity: which of several bindings reaches a use.
- Union types.
- More than two stages of derived types.

## Design

### 1. Facts the extractor records (`bindings.py`, per file, pure)

New `bindings.kind` values. Existing kinds are unchanged.

| kind | name | type_text | when |
|---|---|---|---|
| `alias` | `self.X` (scope: the class) | the dotted name | a class-body `X = <dotted name>`. Recorded in addition to today's untyped `attr` row, which the alias lookup ignores. |
| `rcall` | the receiver text, verbatim | `call:<dotted callee>` or `super:<C>` | a call ref whose receiver is exactly one call of a dotted name, or `super(C, x)`. `line`/`pos` are the call ref's. |
| `return` | the function's short name (scope: the function) | `call:<dotted>`, `var:<local>` or NULL | one row per value-returning `return`, in its own body. NULL when the value is anything else. |
| `local_class` | the class name (scope: the enclosing function) | the class qualname | a `class` statement whose parent is a function. |

- The `var:` returns are resolved inside the function from its own `assign` bindings, in SQL.
- The inferred return is only an SQL view over these rows. `symbols.returns` keeps annotations only.

### 2. Resolution in `EDGES_COMPUTE`

**New CTEs and changes to existing ones:**
- **`py_exp(mod_path, name, dpath, dqual, hops)`** is the re-export closure of rule 1, a recursive CTE capped at 3 hops. The guards: a name the module binds itself never takes a star hop, and a star hop needs `count(DISTINCT source) = 1`. `py_def` arms 2 and 4 read `py_exp` instead of `py_rx`. The `module` tier's `rx` gains the same closure for Python, and `imports_resolved` stays consistent with it.
- **`py_ret(path, qual, text)`** is the agreed inferred return of rule 5: annotated `returns` first, else the agreeing `return` rows, with `var:` rewritten to the local's single `call:` binding.
- **Stage 1:** `py_bind` and `py_type` as today, plus:
  - `rcall` receivers;
  - `super(C, x)`;
  - `local_class` lookups (rule 6);
  - `py_ret` in place of `symbols.returns` for the one return hop.
- **Stage 2:** `py_bind2`/`py_type2` for:
  - attribute-hop receivers (rule 3);
  - `self.X(...)` aliases (rule 2);
  - method returns through a stage-1 typed receiver (rule 5).

  Stage 2 reads stage 1's types, never its own. Its results join `py_type` before `py_cand`, so the definer search, `py_diamond`, `py_blocker` and every Phase 1 refusal apply unchanged.

The label stays `typed`, and the tier order is unchanged.

**Batching:** as in Phase 1, full rebuilds compute in batches and read only base tables. Stage 2 recomputes the stage-1 types it needs inside the batch.

### 3. Freshness: incremental edges must equal a full rebuild

**New cross-file dependencies:**
- star sources and re-export chains;
- other classes' attribute and alias bindings;
- inferred returns;
- a module's top-level bindings, reached through `module.attr`.

**Dirty marking** (`TYPED_DIRTY_SEED`, `TYPED_DIRTY`, `_mark_edges_dirty`) adds, before and after the change:
- **Re-exports:** the names a changed file exports at top level, by name, as both a `name` and a `bound` key. Every file that star-imports a changed module, transitively up to 3 hops, is marked by `path`. Today only direct star-importers are.
- **Classes:** the attribute and alias names of classes the changed file defines, and the method names of the classes those bindings name, with their ancestors as today.
- **Functions:** the method names of the classes named by the changed file's inferred returns.

**A snapshot test per rule,** each asserting `snapshot(root) == fresh_snapshot(root, tmp_path)` after an edit in another file:
- a star source gains, loses or renames N;
- a second star source starts defining N, so the result must turn to a refusal;
- a re-export hop is inserted or removed;
- a class alias's target changes;
- an attribute binding in a base class changes type;
- a module-level binding behind `mod.attr` changes;
- an unannotated function's return changes, or stops agreeing;
- a method behind `x = h.m()` changes its return;
- a local class is renamed, or gains a second definition.

The Phase 1 known gap (a class turning external in another file) stays pinned by its strict xfails.

### 4. Versions and surfaces

- **Versions:**
  - `extractor_version` and `edges_version` change by themselves, through new binding kinds and new SQL;
  - `SCHEMA_VERSION` is unchanged, since there's no new table or column;
  - existing indexes re-parse and rebuild on first use.
- **`SCHEMA_DOC`:** wording unchanged, apart from tightening the `typed` description if it needs to mention re-exports.
- **README FAQ and `bench/RESULTS.md`:** the new accuracy and latency numbers.
- **`docs/roadmap.md`:**
  - Phase 2 moves to Done;
  - the deferred list becomes the next item;
  - multi-hop re-exports leave the known gaps, aliased chains stay.
- **`CHANGELOG.md`:** an Unreleased entry, and a release after merge.

## Prerequisites (a separate small PR, first)

**A deterministic benchmark sample.** `bench/accuracy.py` shuffles an unordered query result, so its sample changes between runs. Phase 1's numbers moved by a few points from this alone. The fix:
- order the edge query before seeding the shuffle;
- re-baseline the three repos in `bench/RESULTS.md` at main.

## Success criteria

**Accuracy,** with `bench/accuracy.py` (deterministic) on the same django, freqtrade and requests commits:
- **Confident precision:** at least 99.9% on each repo. Every disagreement is classified by hand in RESULTS.md.
- **Confident coverage of in-repo calls:** up at least 3 points on django and 2 on freqtrade over the re-baselined main. Requests is reported.
- **Per rule:** each rule's new edges are counted on the three repos, and a sample of 30 per rule (or all, if fewer) is checked against jedi. A rule below 99.5% agreement is dropped, not tuned until it passes.

**Freshness:**
- every new snapshot test passes;
- the existing suite passes;
- the differential fuzzer shows no new divergence on Python repos.

**Performance,** measured with `bench/latency.py` on django and vscode against the RESULTS.md baselines:
- full index time within +20%;
- edge sync after a one-file edit within +20% on django's `query.py`;
- no-op freshen and typical query latency unchanged;
- catch-up after 300 commits on django (the PR #28 measurement) within +20%.

## Risks

- **A wrong confident edge hides the `name` candidates.** The refusal guards and the per-rule precision gate are the defence.
- **Star re-exports are dynamic in principle** (`__all__`, conditional imports). Requiring one unambiguous source, and the module's own bindings winning, covers the measured cases. An `__all__` check is deferred unless the per-rule sample finds an error.
- **Dirty-set breadth:** transitive star-importers and attribute names could widen edge sync. The latency bar caps it. If it's exceeded, the plan narrows the seed (for example, only names with a typable receiver) before anything ships.
- **Stage-2 cost:** a second typing pass per batch. It's profiled in the plan. The fallback is materialising stage 1 once per batch, inside the transaction.
