"""Rust module resolution across a Cargo workspace."""

from helpers import make_repo, rows

WORKSPACE = {
    "Cargo.toml": '[workspace]\nmembers = ["crates/*"]\n',
    "crates/a/Cargo.toml": '[package]\nname = "a"\nversion = "0.1.0"\n',
    "crates/a/src/lib.rs": "pub mod auth;\n",
    "crates/a/src/auth.rs": "pub struct Signer;\n\nimpl Signer {\n    pub fn new() -> Self {\n        Signer\n    }\n}\n",
    "crates/b/Cargo.toml": '[package]\nname = "b"\nversion = "0.1.0"\n',
    "crates/b/src/lib.rs": "pub mod auth;\npub mod rest;\n",
    "crates/b/src/auth.rs": "pub struct Signer;\n\nimpl Signer {\n    pub fn new() -> Self {\n        Signer\n    }\n}\n",
    "crates/b/src/rest.rs": (
        "use crate::auth::Signer;\n"  # 1
        "\n"
        "pub fn sign() -> Signer {\n"  # 3
        "    Signer::new()\n"
        "}\n"
        "\n"
        "pub fn helper() {}\n"  # 7
        "\n"
        "mod tests {\n"
        "    use super::helper;\n"  # 10
        "\n"
        "    fn t() {\n"
        "        helper();\n"
        "    }\n"
        "}\n"
    ),
    "crates/c-cli/Cargo.toml": '[package]\nname = "c-cli"\nversion = "0.1.0"\n',
    "crates/c-cli/src/main.rs": "use a::auth::Signer;\n\nfn main() {\n    let _s = Signer::new();\n}\n",
}


def workspace(tmp_path, **extra):
    return make_repo(tmp_path / "ws", {**WORKSPACE, **extra})


def target(root, path, line):
    return rows(root, f"SELECT target_path FROM imports_resolved WHERE path = '{path}' AND line = {line}")


def test_module_keys_are_qualified_by_crate(tmp_path):
    root = workspace(tmp_path)
    got = dict(rows(root, "SELECT path, key FROM modules WHERE family = 'rs'"))
    assert got["crates/a/src/auth.rs"] == "a::auth"
    assert got["crates/b/src/auth.rs"] == "b::auth"
    assert got["crates/b/src/lib.rs"] == "b"
    assert got["crates/c-cli/src/main.rs"] == "c_cli"


def test_crate_path_stays_in_own_crate(tmp_path):
    root = workspace(tmp_path)
    assert target(root, "crates/b/src/rest.rs", 1) == [("crates/b/src/auth.rs",)]
    got = rows(
        root,
        "SELECT dst_path, resolution FROM edges WHERE src_path = 'crates/b/src/rest.rs' AND line = 3 AND name = 'Signer'",
    )
    assert got == [("crates/b/src/auth.rs", "import")]


def test_other_crate_path_resolves(tmp_path):
    root = workspace(tmp_path)
    assert target(root, "crates/c-cli/src/main.rs", 1) == [("crates/a/src/auth.rs",)]
    got = rows(
        root,
        "SELECT dst_path, resolution FROM edges WHERE src_path = 'crates/c-cli/src/main.rs' AND line = 4 "
        "AND name = 'Signer'",
    )
    assert got == [("crates/a/src/auth.rs", "import")]


def test_lib_name_override(tmp_path):
    root = workspace(
        tmp_path,
        **{
            "crates/a/Cargo.toml": '[package]\nname = "a"\nversion = "0.1.0"\n\n[lib]\nname = "a_core"\n',
            "crates/c-cli/src/main.rs": "use a_core::auth::Signer;\n\nfn main() {\n    let _s = Signer::new();\n}\n",
        },
    )
    assert target(root, "crates/c-cli/src/main.rs", 1) == [("crates/a/src/auth.rs",)]


def test_uniform_path_use_of_child_module(tmp_path):
    root = workspace(tmp_path, **{"crates/b/src/lib.rs": "pub mod auth;\npub mod rest;\nuse auth::Signer;\n"})
    assert target(root, "crates/b/src/lib.rs", 3) == [("crates/b/src/auth.rs",)]


def test_external_crates_stay_unresolved(tmp_path):
    root = workspace(tmp_path, **{"crates/b/src/io.rs": "use std::collections::HashMap;\nuse serde::Serialize;\n"})
    got = rows(root, "SELECT target_path FROM imports_resolved WHERE path = 'crates/b/src/io.rs' ORDER BY line")
    assert got == [(None,), (None,)]


def test_loose_rust_without_cargo_keeps_crate_keys(repo):
    # tests/fixture/rs has no Cargo.toml
    assert ("rs/src/shapes.rs", "crate::shapes") in rows(repo, "SELECT path, key FROM modules WHERE family = 'rs'")


def test_super_in_inline_mod_is_the_enclosing_file(tmp_path):
    root = workspace(tmp_path)
    assert target(root, "crates/b/src/rest.rs", 10) == [("crates/b/src/rest.rs",)]


def test_nested_inline_mods(tmp_path):
    deep = "pub fn top() {}\n\nmod x {\n    mod y {\n        use super::super::top;\n    }\n}\n"
    root = workspace(tmp_path, **{"crates/b/src/deep.rs": deep})
    assert target(root, "crates/b/src/deep.rs", 5) == [("crates/b/src/deep.rs",)]


def _crate_root_repo(tmp_path, files):
    return make_repo(
        tmp_path / "k",
        {"Cargo.toml": '[package]\nname = "k"\nversion = "0.1.0"\n', "src/lib.rs": "pub mod cli;\n", **files},
    )


def test_integration_test_root_sees_its_sibling_module(tmp_path):
    root = _crate_root_repo(
        tmp_path,
        {
            "tests/it.rs": "mod common;\nuse common::helper;\n\nfn t() {\n    helper();\n}\n",
            "tests/common/mod.rs": "pub fn helper() {}\n",
        },
    )
    assert target(root, "tests/it.rs", 2) == [("tests/common/mod.rs",)]
    got = rows(root, "SELECT dst_path, resolution FROM edges WHERE src_path = 'tests/it.rs' AND name = 'helper'")
    assert got == [("tests/common/mod.rs", "import")]
    keys = dict(rows(root, "SELECT path, key FROM modules WHERE family = 'rs'"))
    assert keys["tests/it.rs"] == "k::tests::it"
    assert keys["tests/common/mod.rs"] == "k::tests::common"


def test_bin_crate_path_is_not_the_library(tmp_path):
    root = _crate_root_repo(
        tmp_path,
        {"src/cli.rs": "pub struct X;\n", "src/bin/tool.rs": "use crate::cli::X;\n"},
    )
    assert target(root, "src/bin/tool.rs", 1) == [(None,)]
    assert dict(rows(root, "SELECT path, key FROM modules WHERE family = 'rs'"))["src/bin/tool.rs"] == "k::bin::tool"


def test_bin_names_its_library_by_crate_name(tmp_path):
    root = _crate_root_repo(
        tmp_path,
        {"src/cli.rs": "pub struct X;\n", "src/bin/tool.rs": "use k::cli::X;\n"},
    )
    assert target(root, "src/bin/tool.rs", 1) == [("src/cli.rs",)]


def test_bin_directory_and_test_directory_crate_roots(tmp_path):
    root = _crate_root_repo(
        tmp_path,
        {
            "src/bin/t/main.rs": "mod cmd;\nuse cmd::run;\nuse crate::cmd::run as r2;\n",
            "src/bin/t/cmd.rs": "pub fn run() {}\n",
            "tests/big/main.rs": "mod util;\nuse crate::util::f;\n",
            "tests/big/util.rs": "pub fn f() {}\n",
        },
    )
    assert target(root, "src/bin/t/main.rs", 2) == [("src/bin/t/cmd.rs",)]
    assert target(root, "src/bin/t/main.rs", 3) == [("src/bin/t/cmd.rs",)]
    assert target(root, "tests/big/main.rs", 2) == [("tests/big/util.rs",)]
