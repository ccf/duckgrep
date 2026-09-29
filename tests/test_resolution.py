"""Resolution precision: builtin and external names, star imports, deterministic labels, bound classes."""

from helpers import fresh_snapshot, make_repo, rows, snapshot, write

from duckgrep import schema
from duckgrep.index import connect

ALL = ("files", "symbols", "refs", "imports", "modules", "lines", "edges")
CARGO = '[package]\nname = "k"\nversion = "0.1.0"\n'


def edges_at(root, path, name):
    return rows(
        root,
        f"SELECT dst_path, dst_qualname, resolution FROM edges WHERE src_path = '{path}' AND name = '{name}' "
        "ORDER BY ALL",
    )


def test_builtin_method_names_do_not_match_by_name(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "cache.py": "class Cache:\n    def get(self, key):\n        return key\n",
            "use.py": "def lookup(d):\n    return d.get('x')\n",
        },
    )
    assert edges_at(root, "use.py", "get") == [(None, None, "ambiguous")]
    assert rows(root, "SELECT * FROM callers('Cache.get')") == []


def test_calls_on_external_modules_are_unresolved(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "store.py": "class Store:\n    def dumps(self):\n        return ''\n",
            "ext.py": "import json\n\n\ndef save(x):\n    return json.dumps(x)\n",
        },
    )
    assert edges_at(root, "ext.py", "dumps") == [(None, None, "unresolved")]


def test_calls_on_js_globals_are_unresolved(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "clock.ts": "export class Clock {\n  now() { return 1; }\n}\n",
            "use.ts": "export function stamp() { return Date.now(); }\n",
        },
    )
    assert edges_at(root, "use.ts", "now") == [(None, None, "unresolved")]


def test_rust_prelude_and_std_receivers_are_unresolved(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "Cargo.toml": CARGO,
            "src/lib.rs": "pub mod stack;\npub mod use_it;\n",
            "src/stack.rs": "pub struct Stack;\n\nimpl Stack {\n    pub fn new() -> Self {\n        Stack\n    }\n}\n",
            "src/use_it.rs": (
                "use std::collections::HashMap;\n\npub fn f() {\n"
                "    let _v: Vec<u8> = Vec::new();\n    let _m: HashMap<u8, u8> = HashMap::new();\n}\n"
            ),
        },
    )
    assert edges_at(root, "src/use_it.rs", "new") == [(None, None, "unresolved"), (None, None, "unresolved")]


def test_bare_names_do_not_match_other_files_by_name(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "numberformat.py": "def format(number):\n    return str(number)\n",
            "use.py": "def show(x):\n    return format(x, '.2f')\n",
        },
    )
    assert edges_at(root, "use.py", "format") == [(None, None, "unresolved")]


def test_star_imports_resolve_bare_names(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "pkg/__init__.py": "",
            "pkg/consts.py": "def helper():\n    return 1\n",
            "pkg/api.py": "from .consts import helper\n",
            "pkg/use.py": "from .consts import *\n\n\ndef f():\n    return helper()\n",
            "pkg/use2.py": "from .api import *\n\n\ndef g():\n    return helper()\n",
        },
    )
    assert edges_at(root, "pkg/use.py", "helper") == [("pkg/consts.py", "helper", "import")]
    assert edges_at(root, "pkg/use2.py", "helper") == [("pkg/consts.py", "helper", "import")]


def test_rust_macros_still_match_by_name(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "Cargo.toml": CARGO,
            "src/lib.rs": "#[macro_use]\nmod macros;\nmod user;\n",
            "src/macros.rs": "macro_rules! my_mac {\n    () => {};\n}\n",
            "src/user.rs": "pub fn f() {\n    my_mac!();\n}\n",
        },
    )
    assert edges_at(root, "src/user.rs", "my_mac") == [("src/macros.rs", "my_mac", "name")]


def test_confident_label_is_deterministic(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "lib/__init__.py": "from .core import helper\n",
            "lib/core.py": "from lib import helper\n\n\ndef helper():\n    return 1\n\n\ndef use():\n    return helper()\n",
        },
    )
    rows(root, "SELECT 1")
    con = connect(root)
    try:
        labels = set()
        for _ in range(40):
            con.execute("DELETE FROM edges")
            con.execute(schema.EDGES_COMPUTE.format(source="refs", where="TRUE", cap=schema.NAME_CAP))
            q = "SELECT resolution FROM edges WHERE src_path = 'lib/core.py' AND name = 'helper' AND line = 9"
            labels |= set(con.execute(q).fetchall())
    finally:
        con.close()
    assert labels == {("local",)}


def test_qualified_calls_reach_only_the_bound_class(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "pkg/__init__.py": "",
            "pkg/mysql/__init__.py": "",
            "pkg/mysql/client.py": "class Client:\n    def settings(self):\n        return 1\n",
            "pkg/pg/__init__.py": "",
            "pkg/pg/client.py": "class Client:\n    def settings(self):\n        return 2\n",
            "web/client.ts": "export class Client {\n  static settings() { return 3; }\n}\n",
            "use.py": "from pkg.pg.client import Client\n\n\ndef f():\n    return Client.settings()\n",
        },
    )
    assert edges_at(root, "use.py", "settings") == [("pkg/pg/client.py", "Client.settings", "qualified")]


def test_unbound_class_calls_fall_back_to_name(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "a.py": "class Client:\n    def settings(self):\n        return 1\n",
            "b.py": "class Client:\n    def settings(self):\n        return 2\n",
            "use.py": "def f(Client):\n    return Client.settings()\n",
        },
    )
    assert edges_at(root, "use.py", "settings") == [
        ("a.py", "Client.settings", "name"),
        ("b.py", "Client.settings", "name"),
    ]


def test_lowercase_receivers_are_not_classes(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "svc.py": "class Service:\n    def client(self):\n        def fetch():\n            return 1\n        return fetch\n",
            "use.py": "def f(client):\n    return client.fetch()\n",
        },
    )
    assert "qualified" not in {r[2] for r in edges_at(root, "use.py", "fetch")}


def test_rust_type_calls_prefer_the_type_in_scope(tmp_path):
    impl = "pub struct Backoff;\n\nimpl Backoff {\n    pub fn new() -> Self {\n        Backoff\n    }\n}\n"
    root = make_repo(
        tmp_path / "r",
        {
            "Cargo.toml": CARGO,
            "src/lib.rs": "pub mod a;\npub mod b;\n",
            "src/a.rs": impl,
            "src/b.rs": impl + "\npub fn go() {\n    let _b = Backoff::new();\n}\n",
        },
    )
    assert edges_at(root, "src/b.rs", "new") == [("src/b.rs", "Backoff.new", "qualified")]


def test_reexport_change_updates_class_calls(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "pkg/__init__.py": "from .models import Client\n",
            "pkg/models.py": "class Client:\n    def settings(self):\n        return 1\n",
            "pkg/other.py": "class Client:\n    def settings(self):\n        return 2\n",
            "use.py": "from pkg import Client\n\n\ndef f():\n    return Client.settings()\n",
        },
    )
    assert edges_at(root, "use.py", "settings") == [("pkg/models.py", "Client.settings", "qualified")]
    write(root, "pkg/__init__.py", "from .other import Client\n")
    assert edges_at(root, "use.py", "settings") == [("pkg/other.py", "Client.settings", "qualified")]
    assert snapshot(root, ALL) == fresh_snapshot(root, tmp_path, ALL)
