"""Incremental indexing: detect changed files, re-extract them, swap their rows.

`freshen()` is cheap when nothing changed (one `git ls-files`, one stat per file,
one small query), so it runs before every query instead of relying on hooks.
"""

from __future__ import annotations

import hashlib
import os
import posixpath
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

import duckdb
import pyarrow as pa

from . import schema
from .extract import EXT_LANG, FAMILY, extract

DB_DIR = ".duckgrep"
DB_FILE = "index.duckdb"
MAX_BYTES = 1_000_000
MAX_LINE = 1000
PARALLEL_THRESHOLD = 48
DEFAULT_IGNORES = {
    ".git",
    ".duckgrep",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    "dist",
    "build",
    "target",
    ".tox",
    ".next",
}

SYMBOL_COLS = [
    "path",
    "lang",
    "name",
    "qualname",
    "kind",
    "parent",
    "start_line",
    "end_line",
    "signature",
    "doc",
    "exported",
]
REF_COLS = ["path", "lang", "name", "kind", "receiver", "line", "col", "scope", "scope_class"]
IMPORT_COLS = ["path", "family", "module", "name", "alias", "local", "line", "key", "subkey"]
MODULE_COLS = ["path", "family", "key", "drop_n"]
PER_FILE_TABLES = ["symbols", "refs", "imports", "modules", "lines"]


@dataclass
class FreshenStats:
    scanned: int = 0
    added: int = 0
    changed: int = 0
    deleted: int = 0
    touched: int = 0  # stat changed, content identical
    parsed: int = 0
    new_commits: int = 0
    edge_refs: int = 0  # refs whose edges were recomputed
    seconds: float = 0.0
    changed_paths: list = field(default_factory=list)

    @property
    def dirty(self) -> bool:
        return bool(self.added or self.changed or self.deleted)

    def summary(self) -> str:
        s = (
            f"scanned {self.scanned} files in {self.seconds * 1000:.0f} ms: "
            f"+{self.added} ~{self.changed} -{self.deleted}"
        )
        if self.new_commits:
            s += f", {self.new_commits} new commits"
        return s


def find_root(start: str | None = None) -> str:
    d = os.path.abspath(start or os.getcwd())
    cur = d
    while True:
        if os.path.isdir(os.path.join(cur, DB_DIR)) or os.path.exists(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return d
        cur = parent


def db_path(root: str) -> str:
    d = os.path.join(root, DB_DIR)
    if not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, ".gitignore"), "w") as f:
            f.write("*\n")
    return os.path.join(d, DB_FILE)


def connect(root: str, read_only: bool = False, retries: int = 40) -> duckdb.DuckDBPyConnection:
    """Open the index. Retries briefly if another duckgrep process holds the write lock."""
    path = db_path(root)
    last = None
    for _ in range(retries):
        try:
            if read_only:
                return duckdb.connect(path, read_only=True)
            con = duckdb.connect(
                path,
                config={"preserve_insertion_order": False, "memory_limit": os.environ.get("DUCKGREP_MEMORY", "2GB")},
            )
            if not ensure_schema(con):
                con.close()
                for suffix in ("", ".wal"):
                    if os.path.exists(path + suffix):
                        os.remove(path + suffix)
                con = duckdb.connect(path)
                ensure_schema(con)
            return con
        except duckdb.IOException as e:  # lock held by another process
            last = e
            time.sleep(0.05)
    raise last  # type: ignore[misc]


def ensure_schema(con) -> bool:
    """Create the schema if missing. Returns False if an incompatible old schema is present."""
    try:
        row = con.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    except duckdb.CatalogException:
        row = None
    if row:
        if int(row[0]) != schema.SCHEMA_VERSION:
            return False
        con.execute(schema.VIEWS)  # views and macros are cheap to refresh; keeps them in sync with the code
        return True
    con.execute(schema.TABLES)
    con.execute(schema.VIEWS)
    con.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", [str(schema.SCHEMA_VERSION)])
    return True


# ------------------------------------------------------------------ listing


def list_files(root: str) -> list[str]:
    if os.path.exists(os.path.join(root, ".git")):
        try:
            out = subprocess.run(
                ["git", "-C", root, "ls-files", "-z", "-co", "--exclude-standard"], capture_output=True, check=True
            ).stdout
            paths = [p for p in out.decode("utf-8", "surrogateescape").split("\0") if p]
            return [p for p in paths if not p.startswith(DB_DIR + "/")]
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
    paths = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in DEFAULT_IGNORES and not d.startswith(".")]
        rel = os.path.relpath(dirpath, root)
        for fn in filenames:
            p = fn if rel == "." else f"{rel}/{fn}"
            paths.append(p.replace(os.sep, "/"))
    return paths


def lang_of(path: str) -> str:
    ext = posixpath.splitext(path)[1].lower()
    return EXT_LANG.get(ext) or (ext[1:] if ext else "text")


def _go_modules(root: str, paths: list[str]) -> list[tuple[str, str]]:
    mods = []
    for p in paths:
        if p == "go.mod" or p.endswith("/go.mod"):
            try:
                with open(os.path.join(root, p), encoding="utf-8", errors="replace") as f:
                    for ln in f:
                        if ln.startswith("module "):
                            mods.append((posixpath.dirname(p), ln.split()[1].strip()))
                            break
            except OSError:
                pass
    return sorted(mods, key=lambda m: -len(m[1]))


def extractor_version() -> str:
    """Hash of the extraction code: any change forces a full re-parse."""
    from . import extract as _e

    with open(_e.__file__, "rb") as f:
        return hashlib.blake2b(f.read(), digest_size=8).hexdigest()


CHUNK = 256


def _work(args):
    """Read one file, split it into lines, and parse it if it is a supported language."""
    root, path, lang, parse, ctx = args
    try:
        with open(os.path.join(root, path), "rb") as f:
            data = f.read()
    except OSError:
        return path, None, None
    text = data.decode("utf-8", "replace")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    lines = [ln.rstrip("\r")[:MAX_LINE] for ln in lines]
    return path, lines, (extract(path, lang, data, ctx) if parse else None)


# ------------------------------------------------------------------ freshen


def freshen(
    con,
    root: str,
    full: bool = False,
    workers: int | None = None,
    git_history: bool = True,
    max_commits: int = 5000,
    edges: bool = True,
) -> FreshenStats:
    """Bring the index in line with the working tree.

    edges=False leaves call-graph maintenance pending (recorded in edges_dirty) so that
    queries which don't touch `edges` never pay for it; sync_edges() settles it later.
    """
    t0 = time.perf_counter()
    st = FreshenStats()
    ver = extractor_version()
    row = con.execute("SELECT value FROM meta WHERE key = 'extractor_version'").fetchone()
    if not row or row[0] != ver:
        full = True
    paths = list_files(root)
    st.scanned = len(paths)

    known = {r[0]: (r[1], r[2], r[3]) for r in con.execute("SELECT path, size, mtime_ns, sha FROM files").fetchall()}
    listed = set()
    candidates = []
    for p in paths:
        try:
            s = os.lstat(os.path.join(root, p))
        except OSError:
            continue
        if not (s.st_mode & 0o170000 == 0o100000):  # regular files only
            continue
        listed.add(p)
        k = known.get(p)
        if full or k is None or k[0] != s.st_size or k[1] != s.st_mtime_ns:
            candidates.append((p, s.st_size, s.st_mtime_ns))
    deleted = [p for p in known if p not in listed]

    # ---- pass 1: hash candidates to find real content changes (cheap; nothing kept in memory)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    file_rows, jobs, touched = {}, [], []
    for p, size, mtime in candidates:
        lang = lang_of(p)
        fam = FAMILY.get(lang)
        if size > MAX_BYTES:
            file_rows[p] = [p, lang, None, size, mtime, None, None, "too_large", None, now]
            continue
        try:
            with open(os.path.join(root, p), "rb") as f:
                data = f.read()
        except OSError:
            continue
        sha = hashlib.blake2b(data, digest_size=16).hexdigest()
        k = known.get(p)
        if k is not None and k[2] == sha and not full:
            touched.append((size, mtime, p))
            continue
        if b"\0" in data[:8192]:
            file_rows[p] = [p, lang, None, size, mtime, sha, None, "binary", None, now]
            continue
        file_rows[p] = [p, lang, fam, size, mtime, sha, None, None, None, now]
        jobs.append((p, lang, fam is not None))
        if k is None:
            st.added += 1
        else:
            st.changed += 1
        st.changed_paths.append(p)
    st.deleted = len(deleted)
    st.touched = len(touched)

    ctx = {"gomods": _go_modules(root, paths)} if any(lang == "go" for _, lang, parse in jobs if parse) else {}
    replaced = list(file_rows) + deleted
    rebuild_edges = full or len(replaced) > 0.3 * max(1, len(listed))

    # ---- write: one transaction; parse + insert in bounded chunks
    con.execute("BEGIN")
    try:
        if replaced:
            con.register("_chg_src", pa.table({"path": replaced}))
            con.execute("CREATE OR REPLACE TEMP TABLE _chg AS SELECT DISTINCT path FROM _chg_src")
            con.unregister("_chg_src")
            # what the old versions defined / exported (needed to find edges that point at them)
            con.execute(
                "CREATE OR REPLACE TEMP TABLE _aff_names AS "
                "SELECT name FROM symbols WHERE path IN (SELECT path FROM _chg) "
                'UNION SELECT "local" FROM imports WHERE path IN (SELECT path FROM _chg) '
                'AND "local" IS NOT NULL'
            )
            con.execute(
                "CREATE OR REPLACE TEMP TABLE _aff_keys AS "
                "SELECT DISTINCT family, key FROM modules WHERE path IN (SELECT path FROM _chg)"
            )
            for t in PER_FILE_TABLES + ["files"]:
                con.execute(f"DELETE FROM {t} WHERE path IN (SELECT path FROM _chg)")

        use_pool = len(jobs) >= PARALLEL_THRESHOLD and (workers or os.cpu_count() or 1) > 1
        ex = ProcessPoolExecutor(max_workers=workers) if use_pool else None
        try:
            for i in range(0, len(jobs), CHUNK):
                chunk = [(root, p, lang, parse, ctx) for p, lang, parse in jobs[i : i + CHUNK]]
                results = ex.map(_work, chunk, chunksize=8) if ex else map(_work, chunk)
                rows = {"symbols": [], "refs": [], "imports": [], "modules": []}
                lines = {"path": [], "line": [], "text": []}
                for p, file_lines, res in results:
                    if file_lines is None:
                        continue
                    file_rows[p][6] = len(file_lines)
                    lines["path"].extend([p] * len(file_lines))
                    lines["line"].extend(range(1, len(file_lines) + 1))
                    lines["text"].extend(file_lines)
                    if res is not None:
                        st.parsed += 1
                        file_rows[p][8] = res["parse_errors"]
                        for t in rows:
                            rows[t].extend(res[t])
                _insert(con, "symbols", SYMBOL_COLS, rows["symbols"])
                _insert(con, "refs", REF_COLS, rows["refs"])
                _insert(con, "imports", IMPORT_COLS, rows["imports"])
                _insert(con, "modules", MODULE_COLS, rows["modules"])
                if lines["path"]:
                    con.register("_lines", pa.table(lines))
                    con.execute("INSERT INTO lines BY NAME SELECT * FROM _lines")
                    con.unregister("_lines")
                if rebuild_edges:
                    # Big rebuild: commit per chunk to bound memory. Not atomic, but the extractor
                    # version is only recorded at the end, so an interrupted rebuild is redone.
                    con.execute("COMMIT")
                    con.execute("BEGIN")
        finally:
            if ex:
                ex.shutdown()
        _insert(
            con,
            "files",
            ["path", "lang", "family", "size", "mtime_ns", "sha", "n_lines", "skipped", "parse_errors", "indexed_at"],
            [tuple(r) for r in file_rows.values()],
        )
        if touched:
            con.executemany("UPDATE files SET size = ?, mtime_ns = ? WHERE path = ?", touched)
        if rebuild_edges:
            _rebuild_edges(con)
        elif replaced:
            _mark_edges_dirty(con)
        if replaced:
            for t in ("_chg", "_aff_names", "_aff_keys"):
                con.execute(f"DROP TABLE IF EXISTS {t}")
        if full:
            con.execute("INSERT OR REPLACE INTO meta VALUES ('extractor_version', ?)", [ver])
        if git_history:
            st.new_commits = _update_git(con, root, max_commits)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    if edges:
        st.edge_refs = sync_edges(con)
    st.seconds = time.perf_counter() - t0
    return st


EDGE_BATCH_REFS = 400_000


def _rebuild_edges(con) -> None:
    """Recompute all edges, in batches of files so memory stays bounded on big repos."""
    con.execute("DELETE FROM edges")
    con.execute("DELETE FROM edges_dirty")
    n_refs = con.execute("SELECT count(*) FROM refs").fetchone()[0]
    batches = max(1, -(-n_refs // EDGE_BATCH_REFS))
    for b in range(batches):
        where = "TRUE" if batches == 1 else f"hash(r.path) % {batches} = {b}"
        con.execute(schema.EDGES_COMPUTE.format(source="refs", where=where, cap=schema.NAME_CAP))
        if batches > 1:
            con.execute("COMMIT")
            con.execute("BEGIN")


def _mark_edges_dirty(con) -> None:
    """Record which refs a batch of file changes can affect; `sync_edges` recomputes them later.

    A reference's candidate definitions depend on (a) its own file's imports and
    scopes, (b) definitions with the same name anywhere, and (c) which file each
    import key resolves to. So the affected refs are:
      - every ref in a changed file,
      - refs whose name a changed file defined (before or after the change),
      - refs bound by an import of a changed file (catches `import x as y`),
      - every ref in files importing a module key that appeared or disappeared
        (file added / deleted), since their import resolution may have moved.
    Expects temp tables _chg (paths), _aff_names and _aff_keys (pre-change state).
    """
    con.execute(
        "CREATE OR REPLACE TEMP TABLE _new_keys AS "
        "SELECT DISTINCT family, key FROM modules WHERE path IN (SELECT path FROM _chg)"
    )
    # names defined or re-exported (imported) by the changed files, before and after
    con.execute(
        "INSERT INTO edges_dirty SELECT 'name', NULL, name FROM _aff_names "
        "UNION SELECT 'name', NULL, name FROM symbols WHERE path IN (SELECT path FROM _chg) "
        "UNION SELECT 'name', NULL, \"local\" FROM imports WHERE path IN (SELECT path FROM _chg) "
        'AND "local" IS NOT NULL'
    )
    con.execute("""
        INSERT INTO edges_dirty
        WITH moved AS (
            (SELECT * FROM _aff_keys EXCEPT SELECT * FROM _new_keys)
            UNION (SELECT * FROM _new_keys EXCEPT SELECT * FROM _aff_keys)
        )
        SELECT 'path', path, NULL FROM _chg
        UNION
        SELECT 'path', i.path, NULL FROM imports i JOIN moved k
          ON k.family = i.family AND (k.key = i.key OR k.key = i.subkey)
    """)
    con.execute("""
        INSERT INTO edges_dirty
        SELECT DISTINCT 'bound', i.path, i."local" FROM imports i
        JOIN (SELECT * FROM _aff_keys UNION SELECT * FROM _new_keys) k
          ON k.family = i.family AND (k.key = i.key OR k.key = i.subkey)
        WHERE i."local" IS NOT NULL
    """)
    con.execute("DROP TABLE IF EXISTS _new_keys")


def sync_edges(con) -> int:
    """Recompute edges for everything marked dirty. Returns the number of refs recomputed."""
    if con.execute("SELECT count(*) FROM edges_dirty").fetchone()[0] == 0:
        return 0
    con.execute("BEGIN")
    try:
        con.execute("""
            CREATE OR REPLACE TEMP TABLE _r AS
            SELECT * FROM refs WHERE rowid IN (
                SELECT rowid FROM refs WHERE path IN (SELECT path FROM edges_dirty WHERE kind = 'path')
                UNION SELECT rowid FROM refs WHERE name IN (SELECT name FROM edges_dirty WHERE kind = 'name')
                -- refs bound through an import: by name (f()) or through their receiver (m.f(), Class.m())
                UNION SELECT r.rowid FROM refs r
                      SEMI JOIN (SELECT path, name FROM edges_dirty WHERE kind = 'bound') b
                      ON b.path = r.path
                         AND b.name IN (r.name, regexp_extract(r.receiver, '^[A-Za-z_$][A-Za-z0-9_$]*'),
                                        regexp_extract(r.receiver, '[A-Za-z_$][A-Za-z0-9_$]*$')))
        """)
        n = con.execute("SELECT count(*) FROM _r").fetchone()[0]
        con.execute("""
            DELETE FROM edges WHERE rowid IN (
                SELECT rowid FROM edges WHERE src_path IN (SELECT path FROM edges_dirty WHERE kind = 'path')
                UNION
                SELECT e.rowid FROM edges e SEMI JOIN _r ON _r.path = e.src_path AND _r.line = e.line
                                                       AND _r.col = e.col AND _r.name = e.name)
        """)
        con.execute(schema.EDGES_COMPUTE.format(source="_r", where="TRUE", cap=schema.NAME_CAP))
        con.execute("DELETE FROM edges_dirty")
        con.execute("DROP TABLE IF EXISTS _r")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return n


def _insert(con, table: str, cols: list[str], rows: list[tuple], or_ignore: bool = False) -> None:
    if not rows:
        return
    data = {c: [r[i] for r in rows] for i, c in enumerate(cols)}
    con.register("_ins", pa.table(data))
    verb = "INSERT OR IGNORE" if or_ignore else "INSERT"
    con.execute(f"{verb} INTO {table} BY NAME SELECT * FROM _ins")
    con.unregister("_ins")


# ------------------------------------------------------------------ git history


def _git(root, *args) -> str | None:
    try:
        return subprocess.run(["git", "-C", root, *args], capture_output=True, check=True).stdout.decode(
            "utf-8", "replace"
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _update_git(con, root: str, max_commits: int) -> int:
    if not os.path.exists(os.path.join(root, ".git")):
        return 0
    head = (_git(root, "rev-parse", "HEAD") or "").strip()
    if not head:
        return 0
    row = con.execute("SELECT value FROM meta WHERE key = 'git_head'").fetchone()
    last = row[0] if row else None
    if last == head:
        return 0
    rng = [f"{last}..{head}"] if last and _git(root, "merge-base", "--is-ancestor", last, head) is not None else [head]
    if rng == [head] and last:  # history rewritten: rebuild
        con.execute("DELETE FROM file_changes")
        con.execute("DELETE FROM commits")
    out = _git(
        root,
        "log",
        f"--max-count={max_commits}",
        "--no-renames",
        "--numstat",
        "--format=%x1e%H%x1f%an%x1f%ae%x1f%at%x1f%s",
        *rng,
    )
    commits, changes = [], []
    for block in (out or "").split("\x1e")[1:]:
        header, _, body = block.partition("\n")
        parts = header.split("\x1f")
        if len(parts) < 5:
            continue
        sha, an, ae, at, subj = parts[:5]
        commits.append((sha, an, ae, datetime.fromtimestamp(int(at), timezone.utc).replace(tzinfo=None), subj))
        for ln in body.splitlines():
            f = ln.split("\t")
            if len(f) == 3:
                a = int(f[0]) if f[0].isdigit() else None
                d = int(f[1]) if f[1].isdigit() else None
                changes.append((sha, f[2], a, d))
    if commits:
        _insert(con, "commits", ["sha", "author", "email", "ts", "subject"], commits, or_ignore=True)
    _insert(con, "file_changes", ["sha", "path", "added", "deleted"], changes)
    con.execute("INSERT OR REPLACE INTO meta VALUES ('git_head', ?)", [head])
    return len(commits)
