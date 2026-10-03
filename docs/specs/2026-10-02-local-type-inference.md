# Local type inference for receiver calls (Python)

## Goal

Calls on a receiver whose type duckgrep doesn't know (`obj.method()`) fall today to the `name` tier (up to 10 same-named candidates), to `ambiguous` (more candidates, or a method builtin types also have), or to false `name` candidates when the object is external (`out = StringIO(); out.getvalue()`). This spec infers a receiver's class from syntax alone, in the same per-file-facts-then-SQL shape as the rest of the resolver, so those calls get one confident edge, or are marked external.

Phase 1 covers Python only. TypeScript, Go and Rust follow in their own specs once the mechanism has been proven on Python, where jedi gives a reference to measure against.

## Evidence

A census of every Python receiver call without a confident edge, scored against jedi 0.20.0. It covered 113,628 calls on django (0ae93a0), 16,971 on freqtrade (f2ec745) and 1,088 on requests (611c616).

**Rules that need one binding per scope:**

| | django | freqtrade | requests |
|---|---:|---:|---:|
| Agreement with jedi, where the rules give an answer | 99.7% | 99.9% | 100% |
| Name and ambiguous calls with an in-repo jedi target that the rules resolve | 89.5% | 75.8% | 89.4% |
| All name and ambiguous calls the rules resolve | 36% | 61% | 58% |

- **Where the gain is,** in calls whose target becomes correct:

  | Pattern | django | freqtrade |
  |---|---:|---:|
  | Inherited `self.m()` | 10,581 | 206 |
  | `x = Foo()` | 5,054 | 873 |
  | `self.attr.m()` | 2,412 | 250 |
  | `super().m()` | 1,368 | 73 |
  | Annotated returns | — | 709 |
  | Imported module-level instances | 428 | — |
  | Class receivers | 377 | 108 |

- **False candidates removed:**
  - django: 998 of the 1,225 call sites jedi places outside the repo, carrying 4,440 false `name` edges;
  - requests: 25 of 26.
- **Mostly out of reach for syntax:**
  - django's ORM managers (`X.objects.filter()`, about 18,000 calls), which Django builds at runtime;
  - unannotated parameters;
  - loop variables;
  - `__getattr__` proxies;
  - mixins that call a method a sibling class provides.
- **The census sits in scratch scripts:** `census.py` scores every rule against jedi, using Python's `ast` with full scope analysis. It is the ceiling these rules can reach. The implementation plan moves it into `bench/`, so the numbers can be reproduced.

## Scope

### Phase 1 rules

In each rule the receiver's type is a class, the method is looked up on that class and then its in-repo bases, and the result is one `typed` edge.

1. **`self.m()` / `cls.m()` inherited from a base class.** Today the `self` tier matches only the same class in the same file. The walk extends it through bases resolved by import, in other files too.
2. **`super().m()`:** the bases of the enclosing class, skipping the class itself.
3. **Class receivers `Foo.m()`:** the existing `qualified` tier, extended through bases.
4. **`x = Foo(...)`,** where `Foo` resolves to a class (local, imported or dotted `mod.Foo`).
5. **Annotations:**
   - on parameters (`def f(p: Foo)`) and locals (`x: Foo = ...`);
   - unwrapping `Optional[Foo]`, `Foo | None`, `"Foo"` string annotations, `Self` and `type[Foo]` (`type[Foo]` makes `Foo.m()` a class receiver).
6. **`self.attr.m()`,** where the class or an in-repo base:
   - sets `self.attr = Foo(...)`;
   - sets `self.attr = p` for a parameter annotated `Foo`;
   - or declares `attr: Foo` at class level (dataclasses).
7. **One hop of return annotations:** `x = make(...)` or `x = obj.make(...)`, where `make` resolves to a function or method annotated `-> Foo`. Classmethod factories (`Foo.create()` returning `Self` or `"Foo"`) are included.
8. **Imported module-level instances:** `from m import cache` where `m` has `cache = Cache()` or `cache: Cache`. `cache.get()` then resolves to `Cache.get`.
9. **External types:** a receiver whose type is a class imported from outside the repo, a builtin or a literal gets `unresolved`, not `name` candidates.
   - Builtin-named methods (`get`, `append`) on such receivers stop being `ambiguous`.
   - A class whose method isn't found in the repo, with an external base in its ancestry, also gives `unresolved`.

### Not in Phase 1

Each of these is a later spec:
- attribute chains `a.b.m()` beyond `self.attr`;
- call results `f().m()`;
- tuple unpacking;
- several bindings that agree on one type;
- unannotated returns typed from `return` statements;
- pytest fixture parameters;
- container element types (`list[Foo]`);
- `with ... as`;
- TS, Go, Rust.

### Non-goals

- Full type checking.
- Typing unannotated parameters from their call sites.
- Unions over every assignment.
- Following runtime overrides.

## Design

### 1. Facts the extractor records (per file, pure)

**New table `bindings`, Python only in Phase 1:**

| column | meaning |
|---|---|
| `path`, `scope` | the file and the enclosing def's qualname (`''` for module level). Class-level declarations use the class qualname. |
| `name` | the bound name: `x`, a parameter name, or `self.attr` for attribute bindings. |
| `kind` | `assign`, `annot`, `param` or `attr` |
| `type_text` | the type evidence, normalised to a dotted name: `Foo`, `mod.Foo`, or for a call binding the callee's dotted text marked as a call (`call:mod.make`). It is NULL when the evidence is untypable (a literal, a subscript, an unannotated parameter, an expression). |
| `line` | the line, for ordering and tests |

- **Recorded for every binding of a name,** so SQL can tell one binding from many: assignments, annotated assignments, parameters (annotated or not), `for`/`with`/`except` targets, comprehension variables, walrus targets, `global`/`nonlocal` declarations, and `del`.
  - Bindings with no type (an unannotated parameter, a loop variable) are recorded with a NULL `type_text`. Their only job is to block inference.
- **Normalisation strips:**
  - `Optional[...]`, `X | None`, `Union[X, None]`;
  - quotes;
  - `Annotated[X, ...]`;
  - `typing.` and `typing_extensions.` prefixes.

  `Self` becomes the enclosing class. Anything else that isn't a plain dotted name is NULL: other unions, generics other than the ones listed, and subscripts.
- **New column `symbols.returns`:** the normalised return annotation of a function or method, the same normalisation, NULL when absent.
- **Where the facts live:** parameter names, `with`/`except ... as` targets and string annotations are recorded as `bindings` rows, not as new refs. Resolution needs only the facts, and this leaves the shared walker unchanged for every language. The one walker change is `symbols.returns`.
- **Not handled in Phase 1:** a call to a `@property` (`obj.prop()`) still resolves like a method call, because `symbols` doesn't record decorators. Such calls are rare, and usually a bug in the calling code.

### 2. Resolution in `EDGES_COMPUTE`: a new confident tier, `typed`

**1. Receiver binding.** The receiver must be one of:
- a bare name `x`;
- `self.attr` / `cls.attr`;
- `self`, `cls` or `super()`;
- a capitalised class name.

For a bare name, the binding is found in the closest enclosing scope that binds it, walking scope prefixes as the `local` tier does. A name bound at module level and not rebound in the function falls back to the module scope, and to an import when the module binds it by import.

**2. A single binding.**
- Inference applies only when every binding of that name in that scope has the same non-NULL `type_text`.
- With two or more distinct values, or any NULL, there's no inference, and today's tiers apply unchanged. That includes an unannotated parameter, a loop target, or a reassignment to something else.
- `global` and `nonlocal` names aren't inferred.

**3. Type to class.** The `type_text` is resolved in the binding's file through the existing machinery (same-file class, import, `mod.Foo` through a module import, one hop of re-exports) to a class symbol.
- A `call:` value resolves the callee:
  - a class means the type is that class;
  - a function or method with `returns` means that annotation, resolved in the callee's file;
  - anything else means no inference.
- One hop only: `returns` is never followed into another call.

**4. External.** If the type resolves to something bound by an import the repo can't resolve, a builtin, or a literal, the call is labelled `unresolved`.

**5. Method lookup.**
- The method is looked up on the class, then on its bases, resolved the same way from the class's `inherit` refs.
- The walk is depth-first, left to right, with the nearest definition winning, and capped at `INHERIT_DEPTH` = 8. It approximates the MRO the way the census did.
- `super()` starts at the bases.
- Not found:
  - with every base in the repo: no inference, and today's tiers apply;
  - with an external base in the chain: `unresolved`.

**6. Attribute bindings.** `self.attr` finds `attr` bindings in the class, then its bases, nearest first, under the same single-binding rule across the class's methods and its class body.

**Edges and labels:**
- A `typed` edge is confident, and it suppresses the `name` candidates for that ref, as the other confident tiers do.
- The confident labels' order gains `typed` after `qualified`. When two tiers reach the same target, the earlier label wins.
- Same-class `self` hits keep the `self` label.

**Batching:** full rebuilds compute edges in batches of refs and may read only base tables, never `edges`. So the class hierarchy and the binding lookups are computed from `symbols`, `refs`, `imports_resolved` and `bindings` within each batch. If profiling shows the per-batch hierarchy is the bottleneck, it may be materialised once per rebuild into a temporary table. That table lives inside the same transaction, is never read by queries, and needs no incremental maintenance.

### 3. Freshness: keeping incremental edges equal to a full rebuild

**Within one file:** a `typed` edge depends on its own file (the binding and the call). The `path` rule already re-resolves every ref in a changed file.

**Across files**, it also depends on signature-level facts elsewhere:
- the class the type names;
- that class's bases and their methods;
- `attr` bindings in those classes;
- the `returns` of a callee;
- module-level bindings behind an imported instance.

So when a file changes, add to the `name` dirty set, using the facts before and after the change:
- the method names of every class the file defines;
- the method names of the ancestors of those classes, following `inherit` refs by name with an over-approximating recursive walk to the same depth cap;
- the method names of the classes named by the file's `returns`, module-level `bindings` and `attr` bindings.

**Why this is sound:** a typed ref's name is the method name, so any change that could alter its target marks it dirty by name. The "before" facts are read before the file's rows are deleted, as `freshen()` already does for names.

**Test cases.** Each asserts `snapshot(root) == fresh_snapshot(root, tmp_path)` after the edit:
- a base class changes its base, or gains or loses a method, in another file;
- a return annotation changes;
- the class used in `x = Foo()` is renamed or deleted;
- an `attr` binding changes in a base class;
- a module-level instance changes type;
- a second binding makes a name ambiguous (inference must drop).

**Cost:** marking ancestor method names can be broad. For example, editing a file with a django model marks the method names of `Model` and its bases. The performance bar below caps this. If it's exceeded, the plan narrows the rule (for example, only names whose refs have a typed or typable receiver) before anything ships.

### 4. Versions and surfaces

- **Versions:**
  - `SCHEMA_VERSION` goes up (a new table and column), and the database is rebuilt;
  - `extractor_version` and `edges_version` change by themselves;
  - `INHERIT_DEPTH` joins `edges_version`'s key, like `NAME_CAP`.
- **`SCHEMA_DOC`:** the confident line gains `typed` with a short definition ("class inferred from syntax: constructor, annotation, base classes"). The tool description sits at 3,088 of 3,100 characters, so other wording is trimmed to stay under the limit. `bindings` stays undocumented in Phase 1, as an internal table.
- **README FAQ ("How does it resolve calls..."):**
  - gains the `typed` tier;
  - states the override rule: an edge goes to the method on the declared type, found through its bases, and a subclass override isn't followed, so `callers('Sub.m')` misses calls typed as the base;
  - gives the new accuracy numbers.

## Prerequisites (separate small PRs, first)

They make the baseline honest before measuring.

1. **The accuracy benchmark against jedi:** run jedi without its bundled Django stubs on django (`NO_DJANGO_STUBS=1`), and re-baseline django in `bench/RESULTS.md`. The stubs make jedi treat django's own calls as external.
2. **Standard-library modules resolving to repo files:** `import json` currently resolves to `django/utils/json.py` through dotted-suffix module keys. The same happens for `datetime`, `math`, `asyncio` and `sqlite3`, giving 420 false django sites. An absolute import of a top-level standard-library module name should not match a nested repo module by suffix.

Multi-hop re-export chains (961 unresolved django calls like `migrations.CreateModel`) are a separate roadmap item, not a prerequisite.

## Success criteria

**Accuracy,** with the jedi benchmark rerun after the prerequisites on django, freqtrade and requests:
- **Confident precision:** at least 99.5% on each repo. Every disagreement in the sample is classified by hand in RESULTS.md: a duckgrep error, a jedi error, or declared versus runtime type.
- **Confident coverage of in-repo calls:** up at least 10 points on django and freqtrade over the post-prerequisite baseline. Requests is reported.
- **False `name` edges on calls jedi places outside the repo:** down by at least half on django.

**Freshness:**
- every new snapshot test passes;
- the existing suite passes;
- the differential fuzzer shows no new divergence on Python repos.

**Performance,** measured with `bench/latency.py` on django and vscode against the RESULTS.md baselines:
- full index time within +25%;
- edge sync after a one-file edit within +50% on django's `query.py`;
- typical query latency unchanged.

**Agents:** no A/B run is required for this change. The next A/B repetition measures the agent-level effect.

## Risks

- **A wrong `typed` edge hides the right candidates,** because a confident row suppresses the `name` candidates. The single-binding rule and the precision bar are the guard. Any rule that misses the bar is dropped from Phase 1, not tuned until it passes.
- **Declared versus runtime type:** an in-repo subclass overrides the target method in 15% (django) and 7% (freqtrade) of the census's resolutions. The edge to the declared type's method is what jedi and pyright also give. It's documented, not "fixed".
- **Dirty-set breadth** on class-heavy repos. This is the performance bar's job.
- **Batch cost** of the hierarchy on very large repos (vscode has 6.5M refs, but Phase 1 only adds work for Python files). This is profiled in the plan.
