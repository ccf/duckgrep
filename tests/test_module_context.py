"""go.mod and Cargo.toml changes re-key the imports of the files that depend on them."""

from helpers import fresh_snapshot, make_repo, rows, snapshot, write

from duckgrep import index
from duckgrep.index import connect, freshen

ALL = ("files", "symbols", "refs", "imports", "modules", "lines", "edges")

GO = {
    "go.mod": "module example.com/old\n\ngo 1.22\n",
    "store/store.go": "package store\n\nfunc Open() {}\n",
    "app/app.go": 'package app\n\nimport "example.com/new/store"\n\nfunc Run() { store.Open() }\n',
}

RS = {
    "Cargo.toml": '[workspace]\nmembers = ["a", "c"]\n',
    "a/Cargo.toml": '[package]\nname = "a"\nversion = "0.1.0"\n',
    "a/src/lib.rs": "pub fn go() {}\n",
    "c/Cargo.toml": '[package]\nname = "c"\nversion = "0.1.0"\n',
    "c/src/main.rs": "use core_a::go;\n\nfn main() {\n    go();\n}\n",
}


def test_go_module_rename_rekeys_imports(tmp_path):
    root = make_repo(tmp_path / "go", GO)
    assert rows(root, "SELECT target_path FROM imports_resolved WHERE path = 'app/app.go'") == [(None,)]
    write(root, "go.mod", "module example.com/new\n\ngo 1.22\n")
    assert rows(root, "SELECT target_path FROM imports_resolved WHERE path = 'app/app.go'") == [("store/store.go",)]
    assert rows(root, "SELECT resolution FROM edges WHERE src_path = 'app/app.go' AND name = 'Open'") == [("module",)]
    assert snapshot(root, ALL) == fresh_snapshot(root, tmp_path, ALL)


def test_crate_rename_rekeys_imports(tmp_path):
    root = make_repo(tmp_path / "rs", RS)
    assert rows(root, "SELECT target_path FROM imports_resolved WHERE path = 'c/src/main.rs'") == [(None,)]
    write(root, "a/Cargo.toml", '[package]\nname = "core-a"\nversion = "0.1.0"\n')
    assert rows(root, "SELECT target_path FROM imports_resolved WHERE path = 'c/src/main.rs'") == [("a/src/lib.rs",)]
    assert snapshot(root, ALL) == fresh_snapshot(root, tmp_path, ALL)


def test_dependency_edit_does_not_reparse(tmp_path):
    root = make_repo(tmp_path / "rs", RS)
    rows(root, "SELECT 1")
    write(root, "a/Cargo.toml", '[package]\nname = "a"\nversion = "0.1.0"\n\n[dependencies]\nserde = "1"\n')
    con = connect(root)
    try:
        st = freshen(con, root)
    finally:
        con.close()
    assert (st.changed, st.parsed) == (1, 0)


def test_array_of_tables_names_are_not_the_lib_name(tmp_path):
    root = make_repo(
        tmp_path / "rs",
        {
            "a/Cargo.toml": '[package]\nname = "a"\n\n[lib]\nbench = false\n\n[[bench]]\nname = "speed"\n',
            "a/src/lib.rs": "pub mod auth;\n",
            "a/src/auth.rs": "pub fn sign() {}\n",
            "c/Cargo.toml": '[package]\nname = "c"\n',
            "c/src/main.rs": "use a::auth::sign;\n\nfn main() {\n    sign();\n}\n",
        },
    )
    assert index._rust_crates(root, ["a/Cargo.toml"]) == [("a", "a")]
    assert rows(root, "SELECT target_path FROM imports_resolved WHERE path = 'c/src/main.rs'") == [("a/src/auth.rs",)]
