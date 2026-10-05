"""DuckDB schema for duckgrep: base tables, resolution views and table macros.

Base tables are rewritten per file on every freshen. Everything that depends on
more than one file (import resolution, call edges) is a *view*, so it is always
consistent with the base tables and never needs a global rebuild.
"""

from . import builtin_names

SCHEMA_VERSION = 4

TABLES = """
CREATE TABLE IF NOT EXISTS meta (key VARCHAR PRIMARY KEY, value VARCHAR);

CREATE TABLE IF NOT EXISTS files (
    path      VARCHAR PRIMARY KEY,  -- repo-relative, forward slashes
    lang      VARCHAR,              -- python | typescript | tsx | javascript | go | rust | <ext> for plain text
    family    VARCHAR,              -- py | js | go | rs | NULL (not parsed)
    size      BIGINT,
    mtime_ns  BIGINT,
    sha       VARCHAR,              -- blake2b-128 of content
    n_lines   INTEGER,
    skipped   VARCHAR,              -- NULL, 'binary' or 'too_large'
    parse_errors INTEGER,           -- count of tree-sitter ERROR nodes
    indexed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS symbols (
    path       VARCHAR,
    lang       VARCHAR,
    name       VARCHAR,
    qualname   VARCHAR,             -- Class.method, outer.inner, Type.Method (Go), Type.fn (Rust impl)
    kind       VARCHAR,             -- function | method | class | interface | struct | enum | trait | type | module | constant | variable
    parent     VARCHAR,             -- qualname of enclosing scope, NULL at top level
    start_line INTEGER,
    end_line   INTEGER,
    signature  VARCHAR,
    doc        VARCHAR,             -- first line of docstring / leading comment
    exported   BOOLEAN,
    returns    VARCHAR              -- normalised return annotation (Python), NULL if none
);

CREATE TABLE IF NOT EXISTS refs (
    path        VARCHAR,
    lang        VARCHAR,
    name        VARCHAR,            -- the identifier text
    kind        VARCHAR,            -- call | attr | write | type | inherit | decorator | import | kwarg | name
    receiver    VARCHAR,            -- for obj.name / Obj::name: source text of obj
    line        INTEGER,
    col         INTEGER,
    scope       VARCHAR,            -- qualname of innermost enclosing symbol ('' = module level)
    scope_class VARCHAR             -- qualname of innermost enclosing class-like scope
);

CREATE TABLE IF NOT EXISTS bindings (  -- Python: what each name is bound to, per scope (see bindings.py)
    path      VARCHAR,
    scope     VARCHAR,            -- def/class qualname ('' = module); base and attr rows: the class
    name      VARCHAR,            -- the name, self.<attr> for attributes and aliases, '' for base rows, the receiver
                                  -- text for rcall, the function's name for return, the class's name for local_class
    kind      VARCHAR,            -- assign | annot | param | attr | base | global | import | alias | rcall | return
                                  -- | local_class
    type_text VARCHAR,            -- dotted class name, call:<callee>, super:<class>, var:<local>, builtin type of a
                                  -- literal, or NULL
    line      INTEGER,
    pos       INTEGER             -- base rows: position in the class's bases; rcall rows: the call ref's column
);

CREATE TABLE IF NOT EXISTS imports (
    path    VARCHAR,
    family  VARCHAR,
    module  VARCHAR,                -- as written: 'a.b', '.utils', './x', 'github.com/x/y', 'crate::foo'
    name    VARCHAR,                -- imported member, NULL for whole-module imports, '*' for star/namespace
    alias   VARCHAR,
    "local" VARCHAR,                -- name bound in this file
    line    INTEGER,
    key     VARCHAR,                -- normalised lookup key into modules.key
    subkey  VARCHAR                 -- key if `name` is itself a submodule (Python/Rust)
);

CREATE TABLE IF NOT EXISTS modules (   -- every key under which a file can be imported
    path   VARCHAR,
    family VARCHAR,
    key    VARCHAR,
    drop_n INTEGER                     -- leading path components dropped to form key (0 = full path)
);

CREATE TABLE IF NOT EXISTS lines (
    path VARCHAR,
    line INTEGER,
    text VARCHAR
);

-- Reference -> definition graph, maintained incrementally by freshen() (see EDGES_COMPUTE).
CREATE TABLE IF NOT EXISTS edges (
    src_path     VARCHAR,
    src_scope    VARCHAR,
    line         INTEGER,
    col          INTEGER,
    ref_kind     VARCHAR,
    name         VARCHAR,
    receiver     VARCHAR,
    dst_path     VARCHAR,
    dst_qualname VARCHAR,
    dst_kind     VARCHAR,
    dst_line     INTEGER,
    resolution   VARCHAR,
    n_candidates INTEGER
);

-- Pending edge recomputation (filled by freshen, drained lazily when a query needs edges).
CREATE TABLE IF NOT EXISTS edges_dirty (
    kind VARCHAR,   -- path: all refs in this file | name: refs with this name | bound: refs (path, name)
    path VARCHAR,
    name VARCHAR
);

CREATE TABLE IF NOT EXISTS commits (
    sha     VARCHAR PRIMARY KEY,
    author  VARCHAR,
    email   VARCHAR,
    ts      TIMESTAMP,
    subject VARCHAR
);

CREATE TABLE IF NOT EXISTS file_changes (
    sha     VARCHAR,
    path    VARCHAR,
    added   INTEGER,
    deleted INTEGER
);
"""

NAME_CAP = 10
INHERIT_DEPTH = 8  # base classes walked for typed receivers

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
UNION SELECT name FROM imports WHERE path IN ({files}) AND family = 'py' AND name IS NOT NULL AND "local" <> name
UNION SELECT unnest([i."local", i.name]) FROM imports i  -- names the file re-exports, under both names
      JOIN (  -- how other files import the file's module: a name from it, or the whole module (name NULL)
          SELECT m.path, CASE WHEN j.name IS NULL OR m.key = j.subkey THEN NULL ELSE j.name END AS name
          FROM modules m JOIN imports j ON j.family = m.family AND j.key = m.key WHERE m.path IN ({files})
          UNION SELECT m.path, NULL FROM modules m JOIN imports j ON j.family = m.family AND j.subkey = m.key
          WHERE m.path IN ({files})
      ) x ON x.path = i.path AND (x.name IS NULL OR x.name = i."local")
      WHERE i.path IN ({files}) AND i.family = 'py' AND i."local" IS NOT NULL
UNION SELECT name FROM ({star_names})
"""

# Names reachable through the star imports of {files}, up to 3 hops (with named re-exports along the way): a change
# to any of those modules can change what a star-importer exposes under these names.
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
    SELECT unnest([regexp_extract(b.type_text, '[^.]*$'), i.name]), c.depth + 1
    FROM cls c JOIN bindings b ON b.kind = 'base' AND b.type_text IS NOT NULL
         AND regexp_extract(b.scope, '[^.]*$') = c.name
    LEFT JOIN imports i ON i.path = b.path AND i."local" = split_part(b.type_text, '.', 1) AND i.name IS NOT NULL
    WHERE c.depth < {depth}
)
SELECT DISTINCT 'name', NULL, s.name FROM symbols s
SEMI JOIN (SELECT DISTINCT name FROM cls) c ON regexp_extract(s.parent, '[^.]*$') = c.name
WHERE s.lang = 'python' AND s.kind IN ('method', 'class')
"""

# A Python key whose first part is a stdlib module name matches a repo module only when it is the module's full
# path (drop_n = 0) or its path under a top-level src/ (src layout): `import json` is the stdlib, not
# proj/utils/json.py reached by dotted suffix.
STDLIB_GUARD = (
    "NOT (family = 'py' AND drop_n > 0 AND split_part(key, '.', 1) IN (@PY_STDLIB@)"
    " AND NOT (drop_n = 1 AND starts_with(path, 'src/')))"
)

# Computes edges for the refs in {source} selected by {where} (a predicate on refs r).
# Must stay consistent with the imports_resolved view below.
# Tiers: 1 = confident (self, local, package, import, module, qualified, typed)
#        2 = name match only, kept when <= NAME_CAP candidates ('name'),
#            otherwise one row with no target ('ambiguous')
#        calls with no candidate at all -> 'unresolved' (external / builtin)
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

_EDGES_TEMPLATE = """
INSERT INTO edges
WITH RECURSIVE r AS (
    SELECT r.*, f.family, regexp_replace(r.path, '/[^/]*$', '') AS dir,
           regexp_extract(r.receiver, '^[A-Za-z_$][A-Za-z0-9_$]*') AS recv_root,
           regexp_extract(r.receiver, '[A-Za-z_$][A-Za-z0-9_$]*$') AS recv_last
    FROM {source} r JOIN files f USING (path)
    WHERE r.kind NOT IN ('write', 'kwarg', 'import') AND ({where})
),
imps AS (SELECT * FROM imports WHERE path IN (SELECT DISTINCT path FROM r)),
best_mod AS (
    SELECT family, key, path FROM modules
    WHERE key IN (SELECT key FROM imps UNION SELECT subkey FROM imps) AND @STDLIB_GUARD@
    QUALIFY row_number() OVER (PARTITION BY family, key ORDER BY drop_n, length(path), path) = 1
),
imp AS (
    SELECT i.*, coalesce(sub.path, m.path) AS target_path, sub.path IS NOT NULL AS target_is_module
    FROM imps i
    LEFT JOIN best_mod m   ON m.family = i.family AND m.key = i.key
    LEFT JOIN best_mod sub ON sub.family = i.family AND sub.key = i.subkey
    WHERE coalesce(sub.path, m.path) IS NOT NULL
),
rx AS (  -- one hop of re-exports: names that imported modules themselves import (from .api import get)
    SELECT j.path AS mod_path, j."local", j.name, j.target_path
    FROM imports_resolved j
    WHERE j.target_path IS NOT NULL AND NOT j.target_is_module
      AND j.path IN (SELECT target_path FROM imp)
      AND (j."local" IS NOT NULL OR j.name = '*')
),
ext AS (  -- local names bound by imports that don't resolve inside the repo (stdlib, third party)
    SELECT DISTINCT i.path, i."local" FROM imps i
    WHERE i."local" IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM imp WHERE imp.path = i.path AND imp."local" = i."local")
),
builtin_methods(family, name) AS (VALUES @BUILTIN_METHODS@),
builtin_globals(family, name) AS (VALUES @BUILTIN_GLOBALS@),
bglob AS (  -- builtin receivers (Date, Math, str, Vec ...) the file doesn't rebind
    SELECT DISTINCT r.path, r.recv_root AS name FROM r
    JOIN builtin_globals g ON g.family = r.family AND g.name = r.recv_root
    WHERE NOT EXISTS (SELECT 1 FROM imps i WHERE i.path = r.path AND i."local" = r.recv_root)
      AND NOT EXISTS (SELECT 1 FROM symbols s WHERE s.path = r.path AND s.name = r.recv_root AND s.parent IS NULL)
),
sym AS (
    SELECT s.*, regexp_replace(s.path, '/[^/]*$', '') AS dir, f.family
    FROM symbols s JOIN files f USING (path)
    WHERE s.name IN (SELECT name FROM r UNION SELECT name FROM imp UNION SELECT name FROM rx)
),
-- ---- typed tier (Python): the receiver's class, inferred from per-file facts (bindings, symbols.returns)
py_imp AS (SELECT * FROM imports_resolved WHERE family = 'py'),
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
py_tx AS (  -- dotted names to resolve, per file: binding types, callees, return annotations, class receivers
    SELECT DISTINCT path, text, split_part(text, '.', 1) AS head,
           CASE WHEN contains(text, '.') THEN regexp_replace(text, '[.][^.]*$', '') END AS prefix,
           regexp_extract(text, '[^.]*$') AS tail
    FROM (
        SELECT path, CASE WHEN starts_with(type_text, 'call:') THEN substr(type_text, 6) ELSE type_text END AS text
        FROM bindings WHERE type_text IS NOT NULL AND NOT regexp_matches(type_text, '^(super|var):')
          AND kind <> 'local_class'
      UNION SELECT path, returns FROM symbols WHERE returns IS NOT NULL
      UNION SELECT path, receiver FROM r
        WHERE family = 'py' AND regexp_matches(receiver, '^[A-Za-z_][A-Za-z0-9_]*([.][A-Za-z_][A-Za-z0-9_]*)*$')
          AND regexp_matches(recv_last, '^[A-Z]')
    )
),
py_multi AS (  -- names bound on more than one line of a file (imports at any depth, module-level definitions and
              -- assignments, assignments in any scope): which binding a use means depends on where it is, so no guess.
              -- Plain `import a.b` / `import a.c` lines all bind the same package and don't count.
    SELECT path, name FROM (
        SELECT path, "local" AS name, line FROM py_imp WHERE "local" IS NOT NULL AND NOT (name IS NULL AND alias IS NULL)
      UNION SELECT path, name, start_line FROM symbols
        WHERE lang = 'python' AND parent IS NULL AND kind IN ('class', 'function', 'variable', 'constant')
      UNION SELECT path, name, line FROM bindings WHERE kind IN ('assign', 'annot')
    )
    GROUP BY ALL HAVING count(DISTINCT line) > 1
),
py_def AS (  -- a dotted name in a file -> the definition it names (class, function, Class.method); bline: where
            -- the referencing file binds the name, so of several bindings the latest wins, as in Python
    SELECT * FROM (
        SELECT t.path, t.text, t.head, s.path AS dpath, s.qualname AS dqual, s.kind AS dkind, s.returns,
               s.start_line AS dline, s.start_line AS bline, 0 AS p
        FROM py_tx t JOIN symbols s ON s.path = t.path AND s.qualname = t.text
      UNION ALL  -- from m import Foo  (Foo, Foo.create)
        SELECT t.path, t.text, t.head, s.path, s.qualname, s.kind, s.returns, s.start_line, i.line, 1
        FROM py_tx t
        JOIN py_imp i ON i.path = t.path AND i."local" = t.head AND i.name IS NOT NULL AND NOT i.target_is_module
        JOIN symbols s ON s.path = i.target_path AND s.qualname = i.name || substr(t.text, length(t.head) + 1)
      UNION ALL  -- ... re-exported by m (named or star hops, at most 3)
        SELECT t.path, t.text, t.head, s.path, s.qualname, s.kind, s.returns, s.start_line, i.line, 2
        FROM py_tx t
        JOIN py_imp i ON i.path = t.path AND i."local" = t.head AND i.name IS NOT NULL AND NOT i.target_is_module
        JOIN py_exp1 x ON x.mod_path = i.target_path AND x.name = i.name
        JOIN symbols s ON s.path = x.dpath AND s.qualname = x.dqual || substr(t.text, length(t.head) + 1)
      UNION ALL  -- mod.Foo, a.b.Foo through a module import
        SELECT t.path, t.text, t.head, s.path, s.qualname, s.kind, s.returns, s.start_line, i.line, 3
        FROM py_tx t
        JOIN py_imp i ON i.path = t.path AND t.prefix IS NOT NULL AND i.target_path IS NOT NULL
             AND ((i."local" = t.prefix AND (i.name IS NULL OR i.target_is_module))
                  OR (i.name IS NULL AND i.alias IS NULL AND i.module = t.prefix))
        JOIN symbols s ON s.path = i.target_path AND s.parent IS NULL AND s.name = t.tail
      UNION ALL  -- ... re-exported by that module (named or star hops, at most 3)
        SELECT t.path, t.text, t.head, s.path, s.qualname, s.kind, s.returns, s.start_line, i.line, 4
        FROM py_tx t
        JOIN py_imp i ON i.path = t.path AND t.prefix IS NOT NULL AND i.target_path IS NOT NULL
             AND ((i."local" = t.prefix AND (i.name IS NULL OR i.target_is_module))
                  OR (i.name IS NULL AND i.alias IS NULL AND i.module = t.prefix))
        JOIN py_exp1 x ON x.mod_path = i.target_path AND x.name = t.tail
        JOIN symbols s ON s.path = x.dpath AND s.qualname = x.dqual
    ) c
    WHERE NOT EXISTS (  -- `class Foo` then `Foo = Other` (or an import of Foo): the name no longer means the class
        SELECT 1 FROM bindings bb WHERE bb.path = c.dpath AND bb.scope = '' AND bb.name = split_part(c.dqual, '.', 1)
          AND bb.kind IN ('assign', 'annot', 'import', 'global') AND bb.line > c.dline)
      -- a name a nested def or class in this file also defines may mean that local one: don't guess at all
      AND NOT EXISTS (SELECT 1 FROM symbols n WHERE n.path = c.path AND n.name = c.head AND n.parent IS NOT NULL
                        AND n.kind IN ('class', 'function'))
      AND NOT EXISTS (SELECT 1 FROM py_multi m WHERE m.path = c.path AND m.name = c.head)
    QUALIFY row_number() OVER (PARTITION BY c.path, c.text ORDER BY c.bline DESC, c.p, c.dpath, c.dqual) = 1
),
py_ext AS (  -- a dotted name in a file that names something outside the repo: an unresolved import, a builtin
    SELECT t.path, t.text FROM py_tx t
    ANTI JOIN py_def d ON d.path = t.path AND d.text = t.text
    -- a name refused as ambiguous (bound more than once, or by a nested def) is unknown, not external
    ANTI JOIN py_multi m ON m.path = t.path AND m.name = t.head
    ANTI JOIN (SELECT DISTINCT path, name FROM symbols WHERE parent IS NOT NULL AND kind IN ('class', 'function')) n
         ON n.path = t.path AND n.name = t.head
    WHERE EXISTS (SELECT 1 FROM py_imp i WHERE i.path = t.path AND i."local" = t.head AND i.target_path IS NULL)
       OR (t.text IN (SELECT name FROM builtin_globals WHERE family = 'py')
           AND NOT EXISTS (SELECT 1 FROM py_imp i WHERE i.path = t.path AND i."local" = t.head)
           AND NOT EXISTS (SELECT 1 FROM symbols s WHERE s.path = t.path AND s.name = t.head AND s.parent IS NULL))
),
py_base AS (  -- class -> its bases in order; ext: outside the repo; opaque: can't be named
    SELECT b.path, b.scope AS qual, b.pos,
           coalesce(ii.module || ':' || coalesce(ii.name, '') || substr(b.type_text, length(split_part(b.type_text, '.', 1)) + 1),
                    b.type_text) AS btext,  -- what the base is, not what it's called here
           d.dpath AS bpath, d.dqual AS bqual,
           x.text IS NOT NULL AS ext, d.dpath IS NULL AND x.text IS NULL AS opaque
    FROM bindings b
    LEFT JOIN (SELECT * FROM py_def WHERE dkind = 'class') d ON d.path = b.path AND d.text = b.type_text
    LEFT JOIN py_ext x ON x.path = b.path AND x.text = b.type_text
    LEFT JOIN py_imp ii ON ii.path = b.path AND ii."local" = split_part(b.type_text, '.', 1) AND x.text IS NOT NULL
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
py_mro AS (  -- each class's ancestors in resolution order: depth-first, left to right, each at its last occurrence
    SELECT path, qual, apath, aqual, max(ord) AS ord, min(depth) AS depth FROM py_anc GROUP BY ALL
),
py_open AS (  -- classes with a base outside the repo, or one that can't be named, anywhere in their ancestry
    SELECT a.path, a.qual, bool_or(b.ext) AS ext, bool_or(b.opaque) AS opaque
    FROM py_anc a JOIN py_base b ON b.path = a.apath AND b.qual = a.aqual
    GROUP BY ALL
),
py_r AS (SELECT * FROM r WHERE family = 'py' AND kind = 'call' AND receiver IS NOT NULL),
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
py_cand AS MATERIALIZED (  -- each typed receiver's classes to search, in resolution order (staged: joining the
                          -- ancestry to every method first was the tier's main cost)
    SELECT t.path, t.line, t.col, t.cpath, t.cqual, r.name, a.apath, a.aqual, a.ord
    FROM py_type t
    JOIN py_r r ON r.path = t.path AND r.line = t.line AND r.col = t.col
    JOIN py_mro a ON a.path = t.cpath AND a.qual = t.cqual AND a.depth >= t.from_depth
    WHERE NOT t.ext
),
py_defs AS (  -- the in-repo ancestors that define the called method
    SELECT c.path, c.line, c.col, c.cpath, c.cqual, c.ord, s.path AS dst_path, s.qualname AS dst_qualname,
           s.kind AS dst_kind, s.start_line AS dst_line
    FROM py_cand c
    JOIN symbols s ON s.path = c.apath AND s.parent = c.aqual AND s.name = c.name AND s.kind IN ('method', 'class')
),
py_diamond AS (  -- classes reaching an ancestor by two paths: there the last-occurrence order may not be Python's
    SELECT DISTINCT path, qual FROM (SELECT path, qual FROM py_anc GROUP BY path, qual, apath, aqual HAVING count(*) > 1)
),
py_blocker AS (  -- bases outside the repo or unnameable, each at its last depth-first position in a class's ancestry
    SELECT a.path, a.qual, max(list_append(a.ord, b.pos)) AS ord
    FROM py_anc a JOIN py_base b ON b.path = a.apath AND b.qual = a.aqual
    WHERE b.ext OR b.opaque
    GROUP BY a.path, a.qual, coalesce(b.btext, b.path || ':' || b.qual || ':' || b.pos)
),
py_hit AS (  -- the first definer in resolution order, unless the order is uncertain or an unknown base comes first
    SELECT path, line, col, dst_path, dst_qualname, dst_kind, dst_line FROM (
        SELECT d.*, count(*) OVER (PARTITION BY d.path, d.line, d.col) AS n_def,
               row_number() OVER (PARTITION BY d.path, d.line, d.col ORDER BY d.ord, d.dst_line) AS rk
        FROM py_defs d
    ) h
    WHERE rk = 1
      AND NOT (n_def > 1 AND len(h.ord) > 0
               AND EXISTS (SELECT 1 FROM py_diamond x WHERE x.path = h.cpath AND x.qual = h.cqual))
      AND NOT EXISTS (SELECT 1 FROM py_blocker k WHERE k.path = h.cpath AND k.qual = h.cqual AND k.ord < h.ord)
),
py_type_ext AS (  -- receivers whose bound type is outside the repo
    SELECT b.path, b.line, b.col FROM py_bind1 b
    JOIN py_ext x ON x.path = b.tpath
         AND x.text = CASE WHEN starts_with(b.type_text, 'call:') THEN substr(b.type_text, 6) ELSE b.type_text END
  UNION
    SELECT b.path, b.line, b.col FROM py_bind1 b
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
    ANTI JOIN py_defs h ON h.path = t.path AND h.line = t.line AND h.col = t.col  -- an in-repo definer: not external
),
t1 AS (
    SELECT r.*, s.path AS dst_path, s.qualname AS dst_qualname, s.kind AS dst_kind, s.start_line AS dst_line,
           'self' AS resolution
    FROM r JOIN sym s ON s.name = r.name AND s.path = r.path AND s.parent = r.scope_class
    WHERE r.receiver IN ('self', 'cls', 'this', 'Self', 'self::')
  UNION ALL
    SELECT r.*, s.path, s.qualname, s.kind, s.start_line, 'local'
    FROM r JOIN sym s ON s.name = r.name AND s.path = r.path
    WHERE r.receiver IS NULL AND s.kind <> 'method'
      AND (s.parent IS NULL OR r.scope = s.parent OR starts_with(r.scope, s.parent || '.'))
  UNION ALL
    SELECT r.*, s.path, s.qualname, s.kind, s.start_line, 'package'
    FROM r JOIN sym s ON s.name = r.name AND s.dir = r.dir AND s.path <> r.path AND s.family = 'go'
    WHERE r.family = 'go' AND r.receiver IS NULL AND s.parent IS NULL
  UNION ALL
    SELECT r.*, s.path, s.qualname, s.kind, s.start_line, 'import'
    FROM r
    JOIN imp i ON i.path = r.path AND i."local" = r.name AND NOT i.target_is_module
    JOIN sym s ON s.path = i.target_path AND s.name = coalesce(nullif(i.name, 'default'), r.name)
              AND s.parent IS NULL
    WHERE r.receiver IS NULL
  UNION ALL
    SELECT r.*, s.path, s.qualname, s.kind, s.start_line, 'module'
    FROM r
    JOIN imp i ON i.path = r.path AND (i."local" = r.receiver OR i.module = r.receiver)
              AND (i.target_is_module OR i.name IS NULL OR i.name = '*')
    JOIN sym s ON s.path = i.target_path AND s.name = r.name AND s.parent IS NULL
    WHERE r.receiver IS NOT NULL
  UNION ALL
    -- `from pkg import get` where pkg/__init__ re-exports get from pkg/api
    SELECT r.*, s.path, s.qualname, s.kind, s.start_line, 'import'
    FROM r
    JOIN imp i ON i.path = r.path AND i."local" = r.name AND NOT i.target_is_module
    JOIN rx ON rx.mod_path = i.target_path
           AND (rx."local" = coalesce(nullif(i.name, 'default'), r.name) OR (rx.name = '*' AND rx."local" IS NULL))
    JOIN sym s ON s.path = rx.target_path AND s.parent IS NULL
           AND s.name = CASE WHEN rx.name = '*' THEN coalesce(nullif(i.name, 'default'), r.name)
                             ELSE coalesce(nullif(rx.name, 'default'), rx."local") END
    WHERE r.receiver IS NULL
  UNION ALL
    -- `pkg.get()` where pkg re-exports get
    SELECT r.*, s.path, s.qualname, s.kind, s.start_line, 'module'
    FROM r
    JOIN imp i ON i.path = r.path AND (i."local" = r.receiver OR i.module = r.receiver)
              AND (i.target_is_module OR i.name IS NULL OR i.name = '*')
    JOIN rx ON rx.mod_path = i.target_path AND (rx."local" = r.name OR (rx.name = '*' AND rx."local" IS NULL))
    JOIN sym s ON s.path = rx.target_path AND s.parent IS NULL
           AND s.name = CASE WHEN rx.name = '*' THEN r.name ELSE coalesce(nullif(rx.name, 'default'), rx."local") END
    WHERE r.receiver IS NOT NULL
  UNION ALL
    -- `from m import *` / `use m::*`: a bare name defined at the top of the star-imported module
    SELECT r.*, s.path, s.qualname, s.kind, s.start_line, 'import'
    FROM r
    JOIN imp i ON i.path = r.path AND i.name = '*' AND i."local" IS NULL
    JOIN sym s ON s.path = i.target_path AND s.name = r.name AND s.parent IS NULL
    WHERE r.receiver IS NULL AND r.family IN ('py', 'rs')
  UNION ALL
    -- ... or re-exported by it
    SELECT r.*, s.path, s.qualname, s.kind, s.start_line, 'import'
    FROM r
    JOIN imp i ON i.path = r.path AND i.name = '*' AND i."local" IS NULL
    JOIN rx ON rx.mod_path = i.target_path AND (rx."local" = r.name OR (rx.name = '*' AND rx."local" IS NULL))
    JOIN sym s ON s.path = rx.target_path AND s.parent IS NULL
           AND s.name = CASE WHEN rx.name = '*' THEN r.name ELSE coalesce(nullif(rx.name, 'default'), rx."local") END
    WHERE r.receiver IS NULL AND r.family IN ('py', 'rs')
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
  UNION ALL
    -- Class.method / Type::method, with the class bound in this file: defined here, imported by name
    -- (directly or through one re-export), or reached through an imported module (mod.Class.method)
    SELECT r.*, s.path, s.qualname, s.kind, s.start_line, 'qualified'
    FROM r JOIN sym s ON s.name = r.name AND s.family = r.family AND s.parent IS NOT NULL
         AND (s.parent = r.recv_last OR ends_with(s.parent, '.' || r.recv_last))
    WHERE r.receiver IS NOT NULL AND r.receiver NOT IN ('self', 'cls', 'this', 'Self')
      AND regexp_matches(r.recv_last, '^[A-Z]')
      AND (s.path = r.path
           OR EXISTS (SELECT 1 FROM imp i WHERE i.path = r.path AND i.target_path = s.path
                        AND i."local" IN (r.recv_last, r.recv_root))
           OR EXISTS (SELECT 1 FROM imp i JOIN rx ON rx.mod_path = i.target_path
                      WHERE i.path = r.path AND i."local" = r.recv_last
                        AND rx."local" = coalesce(nullif(i.name, 'default'), r.recv_last)
                        AND rx.target_path = s.path))
  UNION ALL
    -- the receiver's class, inferred from syntax (constructor, annotation, base classes)
    SELECT r.*, h.dst_path, h.dst_qualname, h.dst_kind, h.dst_line, 'typed'
    FROM r JOIN py_hit h ON h.path = r.path AND h.line = r.line AND h.col = r.col
),
t1d AS (  -- one row per (ref, target): the strongest tier, then the first definition
    SELECT DISTINCT ON (path, line, col, dst_path, dst_qualname) * FROM t1
    ORDER BY path, line, col, dst_path, dst_qualname,
             list_position(['self', 'local', 'package', 'import', 'module', 'qualified', 'typed'], resolution), dst_line
),
t1refs AS (SELECT DISTINCT path, line, col FROM t1d),
nc AS (
    SELECT family, name,
           count(*) FILTER (WHERE kind = 'macro' OR ends_with(path, '.d.ts')) AS n_bare,
           count(*) FILTER (WHERE parent IS NOT NULL OR family IN ('go', 'rs')) AS n_member
    FROM sym GROUP BY ALL
),
rest AS (
    SELECT r.*, CASE WHEN r.receiver IS NULL THEN nc.n_bare ELSE nc.n_member END AS n,
           (x.path IS NOT NULL OR g.path IS NOT NULL OR te.path IS NOT NULL) AS external,
           bm.name IS NOT NULL AS builtin_method
    FROM r
    LEFT JOIN nc ON nc.family = r.family AND nc.name = r.name
    LEFT JOIN ext x ON r.receiver IS NOT NULL AND x.path = r.path AND x."local" = r.recv_root
    LEFT JOIN bglob g ON r.receiver IS NOT NULL AND g.path = r.path AND g.name = r.recv_root
    LEFT JOIN builtin_methods bm ON r.receiver IS NOT NULL AND bm.family = r.family AND bm.name = r.name
    LEFT JOIN py_typed_ext te ON te.path = r.path AND te.line = r.line AND te.col = r.col
    ANTI JOIN t1refs t ON t.path = r.path AND t.line = r.line AND t.col = r.col
),
out AS (
    SELECT path, scope, line, col, kind, name, receiver, dst_path, dst_qualname, dst_kind, dst_line, resolution,
           count(*) OVER (PARTITION BY path, line, col) AS n
    FROM t1d
  UNION ALL
    -- by name alone: receiver calls of unknown type. A bare name needs an import to reach another file,
    -- except Rust macros and TypeScript ambient (.d.ts) declarations.
    SELECT r.path, r.scope, r.line, r.col, r.kind, r.name, r.receiver,
           s.path, s.qualname, s.kind, s.start_line, 'name', r.n
    FROM rest r JOIN sym s ON s.name = r.name AND s.family = r.family
    WHERE r.kind <> 'name' AND r.n BETWEEN 1 AND {cap} AND NOT r.external AND NOT r.builtin_method
      AND CASE WHEN r.receiver IS NULL THEN s.kind = 'macro' OR ends_with(s.path, '.d.ts')
               ELSE s.parent IS NOT NULL OR s.family IN ('go', 'rs') END
  UNION ALL
    SELECT path, scope, line, col, kind, name, receiver, NULL, NULL, NULL, NULL, 'ambiguous', n
    FROM rest WHERE kind <> 'name' AND NOT external AND (n > {cap} OR (builtin_method AND n > 0))
  UNION ALL
    SELECT path, scope, line, col, kind, name, receiver, NULL, NULL, NULL, NULL, 'unresolved', 0
    FROM rest WHERE kind = 'call' AND (external OR coalesce(n, 0) = 0)
)
SELECT * FROM out
"""


def _values(pairs) -> str:
    return ", ".join(f"('{f}', '{n}')" for f, n in sorted(pairs))


def _names(words) -> str:
    return ", ".join(f"'{w}'" for w in sorted(words))


_GUARD = STDLIB_GUARD.replace("@PY_STDLIB@", _names(builtin_names.PY_STDLIB))
EDGES_COMPUTE = (
    _EDGES_TEMPLATE.replace("@PY_TYPING_1@", _PY_TYPING.replace("@N@", "1"))
    .replace("@BUILTIN_METHODS@", _values(builtin_names.METHODS))
    .replace("@BUILTIN_GLOBALS@", _values(builtin_names.GLOBALS))
    .replace("@STDLIB_GUARD@", _GUARD)
)

VIEWS = r"""
-- Imports joined to the file they refer to (NULL target = external / unresolved).
CREATE OR REPLACE VIEW imports_resolved AS
WITH best AS (
    SELECT family, key, path FROM modules
    WHERE @STDLIB_GUARD@
    QUALIFY row_number() OVER (PARTITION BY family, key ORDER BY drop_n, length(path), path) = 1
)
SELECT i.*,
       coalesce(sub.path, m.path)  AS target_path,
       sub.path IS NOT NULL        AS target_is_module
FROM imports i
LEFT JOIN best m   ON m.family = i.family AND m.key = i.key
LEFT JOIN best sub ON sub.family = i.family AND sub.key = i.subkey;

CREATE OR REPLACE VIEW file_churn AS
SELECT fc.path,
       count(DISTINCT fc.sha)     AS n_commits,
       count(DISTINCT c.email)    AS n_authors,
       max(c.ts)                  AS last_change,
       sum(fc.added)              AS lines_added,
       sum(fc.deleted)            AS lines_deleted
FROM file_changes fc JOIN commits c USING (sha)
GROUP BY fc.path;

-- ---------- table macros: the "canned" questions ----------

-- definitions by name or qualified name
CREATE OR REPLACE MACRO defs(q) AS TABLE
    SELECT path, start_line, end_line, kind, qualname, signature, doc
    FROM symbols WHERE name = q OR qualname = q OR ends_with(qualname, '.' || q)
    ORDER BY path, start_line;

-- who references a symbol (name or qualname): one row per reference site, confident first.
-- A bare name also matches ambiguous / unresolved references with that name; a qualified one ends with a summary
-- row counting the calls of that method name on receivers of unknown type, in the language that defines it.
-- `targets` lists the candidate definitions when resolution is by name only.
CREATE OR REPLACE MACRO callers(q) AS TABLE
    SELECT src_path, line, caller, ref_kind, receiver, resolution, targets FROM (
        SELECT 0 AS ord, src_path, line, nullif(src_scope, '') AS caller, ref_kind, left(receiver, 40) AS receiver,
               resolution,
               CASE WHEN count(dst_qualname) > 3 THEN count(dst_qualname) || ' candidates'
                    ELSE string_agg(dst_qualname, ', ' ORDER BY dst_qualname) END AS targets
        FROM edges
        WHERE dst_qualname = q OR ends_with(dst_qualname, '.' || q)
           OR (name = q AND position('.' IN q) = 0 AND dst_qualname IS NULL)
        GROUP BY src_path, line, col, src_scope, ref_kind, receiver, resolution
      UNION ALL
        SELECT 1, NULL, NULL, NULL, 'call', NULL, 'ambiguous',
               n || ' call(s) of .' || split_part(q, '.', -1) || '() on receivers of unknown type are not listed; callers('''
               || split_part(q, '.', -1) || ''') shows them'
        FROM (SELECT count(*) AS n FROM edges e JOIN files f ON f.path = e.src_path
              WHERE e.resolution = 'ambiguous' AND e.name = split_part(q, '.', -1)
                AND f.family IN (SELECT DISTINCT sf.family FROM symbols s JOIN files sf USING (path)
                                 WHERE s.qualname = q OR ends_with(s.qualname, '.' || q)))
        WHERE n > 0 AND position('.' IN q) > 0
    )
    ORDER BY ord, (resolution IN ('name', 'ambiguous', 'unresolved')), src_path, line;

-- what a function references; `file` and `caller` are filled only when the name matches more than one function
CREATE OR REPLACE MACRO callees(q) AS TABLE
    WITH m AS (
        SELECT * FROM edges WHERE src_scope = q
        UNION ALL
        SELECT * FROM edges WHERE ends_with(src_scope, '.' || q) AND NOT EXISTS (SELECT 1 FROM edges WHERE src_scope = q)
    ), k AS (SELECT count(DISTINCT (src_path, src_scope)) AS n FROM m)
    SELECT CASE WHEN k.n > 1 THEN m.src_path END AS file, CASE WHEN k.n > 1 THEN m.src_scope END AS caller,
           line, ref_kind, name, receiver, dst_path, dst_qualname, resolution, n_candidates
    FROM m, k
    ORDER BY m.src_path, m.src_scope, line, col;

-- symbols in a file; `file` is filled only when the argument matches more than one file
CREATE OR REPLACE MACRO outline(p) AS TABLE
    WITH m AS (
        SELECT * FROM symbols WHERE path = p
        UNION ALL
        SELECT * FROM symbols WHERE ends_with(path, '/' || p) AND NOT EXISTS (SELECT 1 FROM files WHERE path = p)
    ), k AS (SELECT count(DISTINCT path) AS n FROM m)
    SELECT CASE WHEN k.n > 1 THEN m.path END AS file, start_line, end_line, kind, qualname, signature
    FROM m, k
    ORDER BY m.path, start_line;

-- regex search (RE2 syntax, prefix (?i) for case-insensitive) with the enclosing symbol
CREATE OR REPLACE MACRO grep(pat) AS TABLE
    SELECT l.path, l.line, s.qualname AS symbol, l.text
    FROM lines l
    LEFT JOIN symbols s ON s.path = l.path AND l.line BETWEEN s.start_line AND s.end_line
    WHERE regexp_matches(l.text, pat)
    QUALIFY row_number() OVER (PARTITION BY l.path, l.line ORDER BY s.start_line DESC NULLS LAST) = 1
    ORDER BY l.path, l.line;

-- the code of a symbol (qualname or name); with several matches, the first row's `file` names the one shown
CREATE OR REPLACE MACRO source(q) AS TABLE
    WITH c AS (SELECT * FROM symbols WHERE qualname = q OR name = q),
         s AS (SELECT * FROM c ORDER BY (qualname = q) DESC, path, start_line LIMIT 1),
         k AS (SELECT count(*) AS n FROM c)
    SELECT CASE WHEN k.n > 1 AND l.line = s.start_line THEN s.path || ' (1 of ' || k.n || ' matches)' END AS file,
           l.line, l.text
    FROM s JOIN lines l ON l.path = s.path AND l.line BETWEEN s.start_line AND s.end_line, k
    ORDER BY l.line;
""".replace("@STDLIB_GUARD@", _GUARD)

# Shown to agents in the MCP tool description and by `duckgrep schema`.
SCHEMA_DOC = """\
duckgrep: a DuckDB index of this repo (tree-sitter parse + git history). Refreshed automatically before every query.

TABLES
  files(path, lang, size, n_lines, sha, skipped)          one row per tracked text file
  symbols(path, name, qualname, kind, parent, start_line, end_line, signature, doc, exported)
      kind: function|method|class|interface|struct|enum|trait|type|module|constant|variable
      qualname: Class.method, outer.inner, GoType.Method, RustType.fn
  refs(path, name, kind, receiver, line, col, scope, scope_class)   every identifier use (not definitions)
      kind: call|attr|write|type|inherit|decorator|import|kwarg|name ; receiver = text of `obj` in obj.name
      scope = qualname of enclosing symbol ('' = module level)
  imports(path, module, name, alias, local, line)   + view imports_resolved(..., target_path)
  lines(path, line, text)                           full text of every file
  commits(sha, author, email, ts, subject), file_changes(sha, path, added, deleted), view file_churn(path, n_commits, n_authors, last_change, ...)

edges(src_path, src_scope, line, ref_kind, name, receiver, dst_path, dst_qualname, dst_kind, dst_line, resolution, n_candidates)
  reference -> definition (the call graph). resolution:
    self | local | package | import | module | qualified | typed   confident (typed: receiver class from a
                constructor, annotation or base class)
    name        obj.method() matched by method name only, <= 10 candidates (one row each)
    ambiguous   > 10 candidates, or a method builtin types also have (get, append, push ...); dst_* NULL
    unresolved  no in-repo target found (stdlib, builtins, third party, or an import duckgrep can't follow); dst_* NULL
  A bare name resolves through its file's scope and imports only (Rust macros and .d.ts declarations excepted).

TABLE MACROS
  defs('name')          where is it defined          callers('name' | 'Class.method')   who uses it
  callees('qualname')   what it calls                outline('path/or/suffix.py')       symbols in a file
  grep('regex')         text search + enclosing symbol    source('qualname')           the code of a symbol
  outline, callees, source fill their first column(s) only when the argument is ambiguous
  callers('Class.method') ends with one row (src_path NULL) counting its calls on receivers of unknown type

EXAMPLES
  SELECT * FROM defs('Session');
  SELECT * FROM callers('Session.request') WHERE resolution <> 'name';
  SELECT * FROM grep('(?i)retry') WHERE path LIKE 'src/%';
  two hops: join edges on (dst_path, dst_qualname) = a first hop's (src_path, src_scope);
    callers('qualname') also takes same-named functions in other files
"""
