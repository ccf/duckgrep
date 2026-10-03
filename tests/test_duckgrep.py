import os
import subprocess

import duckdb
import pytest
from helpers import fresh_snapshot, rows, snapshot, write

from duckgrep import query as q


def test_symbols_all_languages(repo):
    got = {r[0] for r in rows(repo, "SELECT qualname || ':' || kind FROM symbols")}
    for want in [
        "Engine:class",
        "Engine.build:method",
        "Engine.step.inner:function",
        "MAX_RETRIES:constant",
        "Client.user:method",
        "makeClient:function",
        "User:interface",
        "Server:struct",
        "Server.Start:method",
        "Greet:function",
        "Circle:struct",
        "Circle.new:method",
        "area:function",
    ]:
        assert want in got, want


@pytest.mark.parametrize(
    "src_scope,name,dst_path,dst_qualname,resolution",
    [
        ("Base.run", "step", "pkg/core.py", "Base.step", "self"),
        ("Engine.__init__", "slug", "pkg/sub/helpers.py", "slugify", "import"),  # aliased import
        ("Engine.step.inner", "normalize", "pkg/sub/helpers.py", "normalize", "module"),  # module.attr
        ("make_engine", "build", "pkg/core.py", "Engine.build", "qualified"),
        ("Client.user", "get", "web/http.ts", "fetchJson", "import"),
        ("Client.ping", "fetchJson", "web/http.ts", "fetchJson", "module"),  # namespace import
        ("Server.Start", "Greet", "gosvc/util/util.go", "Greet", "module"),  # go.mod prefix
        ("Server.Start", "helper", "gosvc/other.go", "helper", "package"),  # same Go package
        ("total", "area", "rs/src/shapes.rs", "area", "import"),
        ("total", "new", "rs/src/shapes.rs", "Circle.new", "qualified"),
    ],
)
def test_edges(repo, src_scope, name, dst_path, dst_qualname, resolution):
    got = rows(
        repo,
        f"SELECT dst_path, dst_qualname, resolution FROM edges WHERE src_scope = '{src_scope}' AND name = '{name}'",
    )
    assert (dst_path, dst_qualname, resolution) in got


def test_inheritance_edge(repo):
    got = rows(repo, "SELECT ref_kind, dst_qualname FROM edges WHERE src_scope = 'Engine' AND name = 'Base'")
    assert ("inherit", "Base") in got


def test_macros(repo):
    assert rows(repo, "SELECT path, kind FROM defs('Engine')") == [("pkg/core.py", "class")]
    callers = rows(repo, "SELECT caller FROM callers('normalize')")
    assert sorted(c[0] for c in callers) == ["Engine.step.inner", "slugify"]
    g = rows(repo, "SELECT path, line, symbol FROM grep('self\\.name')")
    assert ("pkg/core.py", 29, "Engine.step.inner") in g
    assert [r[0] for r in rows(repo, "SELECT qualname FROM outline('helpers.py')")] == [
        "slugify",
        "normalize",
        "unused_helper",
    ]
    assert "def step(self):" in rows(repo, "SELECT text FROM source('Engine.step')")[0][0]


def test_incremental_modify_add_delete(repo):
    rows(repo, "SELECT 1")  # initial build
    helpers = os.path.join(repo, "pkg/sub/helpers.py")
    with open(helpers, "a") as f:
        f.write("\n\ndef brand_new():\n    return slugify('x')\n")
    with open(os.path.join(repo, "pkg/extra.py"), "w") as f:
        f.write("from .sub.helpers import brand_new\n\nbrand_new()\n")
    os.remove(os.path.join(repo, "web/http.ts"))

    res = q.run(repo, "SELECT qualname FROM symbols WHERE name = 'brand_new'")
    assert res.rows == [("brand_new",)]
    assert "+1 ~1 -1" in res.note
    assert rows(repo, "SELECT count(*) FROM files WHERE path = 'web/http.ts'") == [(0,)]
    assert rows(repo, "SELECT count(*) FROM lines WHERE path = 'web/http.ts'") == [(0,)]
    got = rows(repo, "SELECT src_path, resolution FROM edges WHERE dst_qualname = 'brand_new'")
    assert ("pkg/extra.py", "import") in got
    # the import from the deleted file now dangles
    assert rows(repo, "SELECT target_path FROM imports_resolved WHERE path = 'web/api.ts'") == [(None,), (None,)]
    # nothing changed -> no reindex
    assert q.run(repo, "SELECT 1").note == ""


def test_touch_without_change_is_not_reparsed(repo):
    rows(repo, "SELECT 1")
    p = os.path.join(repo, "pkg/core.py")
    os.utime(p, None)
    assert q.run(repo, "SELECT 1").note == ""


def test_touched_files_store_their_new_mtime(repo):
    rows(repo, "SELECT 1")
    touched = ["pkg/core.py", "pkg/__init__.py", "web/api.ts"]
    for i, rel in enumerate(touched):
        os.utime(os.path.join(repo, rel), ns=(1_700_000_000_000_000_000 + i, 1_700_000_000_000_000_000 + i))
    q.run(repo, "SELECT 1")
    got = dict(rows(repo, f"SELECT path, mtime_ns FROM files WHERE path IN {tuple(touched)}"))
    assert got == {rel: os.stat(os.path.join(repo, rel)).st_mtime_ns for rel in touched}


def test_read_only(repo):
    with pytest.raises(Exception, match="read-only"):
        q.run(repo, "DELETE FROM symbols")
    # a file that exists, so only the sandbox can refuse it
    path = os.path.join(repo, "pkg", "core.py")
    with pytest.raises(duckdb.PermissionException, match="disabled by configuration"):
        q.run(repo, f"SELECT * FROM read_text('{path}')")


def test_git_history(repo):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "a",
        "GIT_AUTHOR_EMAIL": "a@x",
        "GIT_COMMITTER_NAME": "a",
        "GIT_COMMITTER_EMAIL": "a@x",
    }

    def run(*a):
        return subprocess.run(["git", "-C", repo, *a], check=True, capture_output=True, env=env)

    run("init", "-q")
    run("add", "-A")
    run("commit", "-qm", "first")
    with open(os.path.join(repo, "pkg/core.py"), "a") as f:
        f.write("\nX = 1\n")
    run("commit", "-qam", "second")
    assert rows(repo, "SELECT n_commits FROM file_churn WHERE path = 'pkg/core.py'") == [(2,)]
    run("commit", "-q", "--allow-empty", "-m", "third")
    assert rows(repo, "SELECT count(*) FROM commits") == [(3,)]


def test_parse_errors_do_not_break_index(repo):
    with open(os.path.join(repo, "pkg/broken.py"), "w") as f:
        f.write("def ok():\n    return 1\n\ndef broken(:\n    pass\n")
    got = rows(repo, "SELECT parse_errors > 0 FROM files WHERE path = 'pkg/broken.py'")
    assert got == [(True,)]
    assert ("ok",) in rows(repo, "SELECT qualname FROM symbols WHERE path = 'pkg/broken.py'")


def test_incremental_edges_match_full_rebuild(repo, tmp_path):
    rows(repo, "SELECT 1")

    # rename a definition that other files call
    helpers = os.path.join(repo, "pkg/sub/helpers.py")
    write(repo, "pkg/sub/helpers.py", open(helpers).read().replace("def normalize", "def normalise"))
    rows(repo, "SELECT 1")
    assert snapshot(repo) == fresh_snapshot(repo, tmp_path / "a")
    # add a module that shadows an import key, and a new caller
    os.makedirs(os.path.join(repo, "pkg/sub2"), exist_ok=True)
    write(repo, "pkg/sub2/__init__.py", "def normalize(x):\n    return x\n")
    write(
        repo,
        "pkg/caller.py",
        "from .sub2 import normalize as n\nfrom .sub import helpers\n\n"
        "def go():\n    n(1)\n    helpers.normalise('a')\n",
    )
    rows(repo, "SELECT 1")
    assert snapshot(repo) == fresh_snapshot(repo, tmp_path / "b")
    got = rows(repo, "SELECT dst_path, resolution FROM edges WHERE src_scope = 'go' ORDER BY line")
    assert got == [("pkg/sub2/__init__.py", "import"), ("pkg/sub/helpers.py", "module")]
    # delete the target module: the aliased call must stop resolving to it
    os.remove(os.path.join(repo, "pkg/sub2/__init__.py"))
    rows(repo, "SELECT 1")
    assert snapshot(repo) == fresh_snapshot(repo, tmp_path / "c")
    assert ("pkg/sub2/__init__.py",) not in rows(repo, "SELECT dst_path FROM edges")


def test_reexports(repo, tmp_path):

    write(repo, "pkg/sub/__init__.py", "from .helpers import slugify\n")
    write(
        repo,
        "pkg/use.py",
        "from . import sub\nfrom .sub import slugify as s\n\ndef f():\n    sub.slugify('a')\n    s('b')\n",
    )
    write(repo, "web/index.ts", 'export { fetchJson as fj } from "./http";\n')
    write(repo, "web/use.ts", 'import { fj } from "./index";\nexport function g() { return fj("/"); }\n')
    got = rows(
        repo,
        "SELECT src_scope, name, dst_path, resolution FROM edges WHERE src_path IN ('pkg/use.py', 'web/use.ts') "
        "AND ref_kind = 'call' ORDER BY src_path, line",
    )
    assert got == [
        ("f", "slugify", "pkg/sub/helpers.py", "module"),
        ("f", "s", "pkg/sub/helpers.py", "import"),
        ("g", "fj", "web/http.ts", "import"),
    ]
    # changing only the re-export list must update edges incrementally
    write(repo, "pkg/sub/__init__.py", "from .helpers import normalize\n")
    rows(repo, "SELECT 1")
    assert snapshot(repo) == fresh_snapshot(repo, tmp_path / "x")
