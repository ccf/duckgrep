"""DuckDB schema for duckgrep: base tables, resolution views and table macros.

Base tables are rewritten per file on every freshen. Everything that depends on
more than one file (import resolution, call edges) is a *view*, so it is always
consistent with the base tables and never needs a global rebuild.
"""

SCHEMA_VERSION = 3

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
    exported   BOOLEAN
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

# Computes edges for the refs in {source} selected by {where} (a predicate on refs r).
# Must stay consistent with the imports_resolved view below.
# Tiers: 1 = confident (self, local, package, import, module, qualified)
#        2 = name match only, kept when <= NAME_CAP candidates ('name'),
#            otherwise one row with no target ('ambiguous')
#        calls with no candidate at all -> 'unresolved' (external / builtin)
EDGES_COMPUTE = """
INSERT INTO edges
WITH r AS (
    SELECT r.*, f.family, regexp_replace(r.path, '/[^/]*$', '') AS dir
    FROM {source} r JOIN files f USING (path)
    WHERE r.kind NOT IN ('write', 'kwarg', 'import') AND ({where})
),
imps AS (SELECT * FROM imports WHERE path IN (SELECT DISTINCT path FROM r)),
best_mod AS (
    SELECT family, key, path FROM modules
    WHERE key IN (SELECT key FROM imps UNION SELECT subkey FROM imps)
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
sym AS (
    SELECT s.*, regexp_replace(s.path, '/[^/]*$', '') AS dir, f.family
    FROM symbols s JOIN files f USING (path)
    WHERE s.name IN (SELECT name FROM r UNION SELECT name FROM imp UNION SELECT name FROM rx)
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
    SELECT r.*, s.path, s.qualname, s.kind, s.start_line, 'qualified'
    FROM r JOIN sym s ON s.name = r.name AND s.parent IS NOT NULL
         AND (s.parent = r.receiver OR ends_with(s.parent, '.' || r.receiver))
    WHERE r.receiver IS NOT NULL AND r.receiver NOT IN ('self', 'cls', 'this', 'Self')
),
t1d AS (
    SELECT DISTINCT ON (path, line, col, dst_path, dst_qualname) * FROM t1
),
t1refs AS (SELECT DISTINCT path, line, col FROM t1d),
nc AS (
    SELECT family, name,
           count(*) FILTER (WHERE kind <> 'method') AS n_bare,
           count(*) FILTER (WHERE parent IS NOT NULL OR family IN ('go', 'rs')) AS n_member
    FROM sym GROUP BY ALL
),
rest AS (
    SELECT r.*, CASE WHEN r.receiver IS NULL THEN nc.n_bare ELSE nc.n_member END AS n
    FROM r
    LEFT JOIN nc ON nc.family = r.family AND nc.name = r.name
    ANTI JOIN t1refs t ON t.path = r.path AND t.line = r.line AND t.col = r.col
),
out AS (
    SELECT path, scope, line, col, kind, name, receiver, dst_path, dst_qualname, dst_kind, dst_line, resolution,
           count(*) OVER (PARTITION BY path, line, col) AS n
    FROM t1d
  UNION ALL
    SELECT r.path, r.scope, r.line, r.col, r.kind, r.name, r.receiver,
           s.path, s.qualname, s.kind, s.start_line, 'name', r.n
    FROM rest r JOIN sym s ON s.name = r.name AND s.family = r.family
    WHERE r.kind <> 'name' AND r.n BETWEEN 1 AND {cap}
      AND CASE WHEN r.receiver IS NULL THEN s.kind <> 'method'
               ELSE s.parent IS NOT NULL OR s.family IN ('go', 'rs') END
  UNION ALL
    SELECT path, scope, line, col, kind, name, receiver, NULL, NULL, NULL, NULL, 'ambiguous', n
    FROM rest WHERE kind <> 'name' AND n > {cap}
  UNION ALL
    SELECT path, scope, line, col, kind, name, receiver, NULL, NULL, NULL, NULL, 'unresolved', 0
    FROM rest WHERE kind = 'call' AND coalesce(n, 0) = 0
)
SELECT * FROM out
"""

VIEWS = r"""
-- Imports joined to the file they refer to (NULL target = external / unresolved).
CREATE OR REPLACE VIEW imports_resolved AS
WITH best AS (
    SELECT family, key, path FROM modules
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
-- A bare name also matches ambiguous / unresolved references with that name.
-- `targets` lists the candidate definitions when resolution is by name only.
CREATE OR REPLACE MACRO callers(q) AS TABLE
    SELECT src_path, line, nullif(src_scope, '') AS caller, ref_kind, left(receiver, 40) AS receiver, resolution,
           CASE WHEN count(dst_qualname) > 3 THEN count(dst_qualname) || ' candidates'
                ELSE string_agg(dst_qualname, ', ' ORDER BY dst_qualname) END AS targets
    FROM edges
    WHERE dst_qualname = q OR ends_with(dst_qualname, '.' || q)
       OR (name = q AND position('.' IN q) = 0 AND dst_qualname IS NULL)
    GROUP BY src_path, line, col, src_scope, ref_kind, receiver, resolution
    ORDER BY (resolution IN ('name', 'ambiguous', 'unresolved')), src_path, line;

-- what a function/method references (by qualname)
CREATE OR REPLACE MACRO callees(q) AS TABLE
    SELECT line, ref_kind, name, receiver, dst_path, dst_qualname, resolution, n_candidates
    FROM edges WHERE src_scope = q OR ends_with(src_scope, '.' || q)
    ORDER BY line, col;

-- file outline
CREATE OR REPLACE MACRO outline(p) AS TABLE
    SELECT start_line, end_line, kind, qualname, signature
    FROM symbols WHERE path = p OR ends_with(path, '/' || p)
    ORDER BY start_line;

-- regex search (RE2 syntax, prefix (?i) for case-insensitive) with the enclosing symbol
CREATE OR REPLACE MACRO grep(pat) AS TABLE
    SELECT l.path, l.line, s.qualname AS symbol, l.text
    FROM lines l
    LEFT JOIN symbols s ON s.path = l.path AND l.line BETWEEN s.start_line AND s.end_line
    WHERE regexp_matches(l.text, pat)
    QUALIFY row_number() OVER (PARTITION BY l.path, l.line ORDER BY s.start_line DESC NULLS LAST) = 1
    ORDER BY l.path, l.line;

-- source of a symbol (by qualname or name); first match
CREATE OR REPLACE MACRO source(q) AS TABLE
    WITH s AS (SELECT * FROM symbols WHERE qualname = q OR name = q ORDER BY (qualname = q) DESC, path LIMIT 1)
    SELECT l.line, l.text FROM lines l JOIN s ON l.path = s.path AND l.line BETWEEN s.start_line AND s.end_line
    ORDER BY l.line;
"""

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
    self | local | package | import | module | qualified   confident (import/scope analysis)
    name        matched by name only, <= 10 candidates (one row each; n_candidates = how many)
    ambiguous   > 10 same-named definitions; dst_* NULL (typical for obj.get(), obj.save() ...)
    unresolved  call to something not defined in the repo (stdlib, third-party); dst_* NULL

TABLE MACROS
  defs('name')          where is it defined          callers('name' | 'Class.method')   who uses it
  callees('qualname')   what it calls                outline('path/or/suffix.py')       symbols in a file
  grep('regex')         text search + enclosing symbol    source('qualname')           the code of a symbol

EXAMPLES
  SELECT * FROM defs('Session');
  SELECT * FROM callers('Session.request') WHERE resolution <> 'name';
  SELECT * FROM grep('(?i)retry') WHERE path LIKE 'src/%';
  -- functions nothing references (by edge; aliases and module.attr calls count)
  SELECT s.path, s.qualname FROM symbols s WHERE s.kind = 'function'
    AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.dst_path = s.path AND e.dst_qualname = s.qualname);
  -- transitive callers (2 hops)
  WITH RECURSIVE up(q, d) AS (SELECT 'Session.send', 0 UNION
    SELECT e.src_scope, d+1 FROM edges e JOIN up ON e.dst_qualname = up.q WHERE d < 2 AND e.resolution <> 'name' AND e.src_scope <> '')
  SELECT * FROM up;
  -- hottest files by churn that define classes
  SELECT c.* FROM file_churn c WHERE path IN (SELECT path FROM symbols WHERE kind='class') ORDER BY n_commits DESC LIMIT 10;
"""
