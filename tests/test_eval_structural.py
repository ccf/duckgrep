import subprocess

from bench.eval import scip_pb2
from bench.eval.tasks import structural as st

CORE = """class Store:
    def get(self, key):
        return key


def load(store: Store):
    return store.get("a")


def run():
    s = Store()
    return s.get("b")
"""

OTHER = """from pkg.core import Store, run


class Cache:
    def get(self, key):
        return None


def warm(c: Cache):
    return c.get("x")


def main():
    return run()
"""

TEST = """from pkg import core


def test_run():
    assert core.run() == "b"
"""


def repo(tmp_path, files):
    root = tmp_path / "repo"
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    return root


def python_repo(tmp_path, extra=""):
    return repo(
        tmp_path,
        {"pkg/__init__.py": "", "pkg/core.py": CORE + extra, "pkg/other.py": OTHER, "tests/test_core.py": TEST},
    )


def defn(a, qualname):
    return next(d for d in a.defs if d.qualname == qualname)


def test_python_callers_resolve_through_jedi(tmp_path):
    a = st.python_analysis(python_repo(tmp_path))
    assert "get" in st.common_names(a)
    assert st._entries(a.callers(defn(a, "Store.get"))) == {"pkg/core.py:load", "pkg/core.py:run"}
    assert st._entries(a.callers(defn(a, "Cache.get"))) == {"pkg/other.py:warm"}


def test_an_unresolved_call_site_makes_the_key_untrusted(tmp_path):
    a = st.python_analysis(python_repo(tmp_path, '\n\ndef mystery(x):\n    return x.get("z")\n'))
    assert a.callers(defn(a, "Store.get")) is None


def test_python_questions(tmp_path):
    root = python_repo(tmp_path)
    a = st.python_analysis(root)
    q, key = st.callers_question(a, defn(a, "Store.get"), "python")
    assert q == (
        "Which functions call `Store.get`, defined in `pkg/core.py`? List every one, including test functions and,"
        " if it calls itself, the function itself."
    )
    assert key == ("pkg/core.py:load", "pkg/core.py:run")
    q, key = st.two_hop_question(a, defn(a, "Store.get"), "python")
    assert "directly or through one intermediate function" in q
    assert key == ("pkg/core.py:load", "pkg/core.py:run", "pkg/other.py:main", "tests/test_core.py:test_run")
    module = next(m for m in a.modules if m.file == "pkg/core.py")
    assert module.importers == {"pkg/other.py": [1], "tests/test_core.py": [1]}
    q, key = st.importers_question(a, module, "python")
    assert q.startswith("Which files import the module `pkg.core`, or import names from it?")
    assert key == ("pkg/other.py", "tests/test_core.py")


def test_a_python_function_also_used_as_a_value_is_not_asked_about(tmp_path):
    # a callback is a reference but not a call: whether its user "calls" it is ambiguous
    a = st.python_analysis(python_repo(tmp_path))
    assert st._entries(a.callers(defn(a, "run"))) == {"pkg/other.py:main", "tests/test_core.py:test_run"}
    a = st.python_analysis(python_repo(tmp_path / "v", "\n\nHANDLERS = [run]\n"))
    assert a.callers(defn(a, "run")) is None


def test_a_repo_never_gets_two_questions_with_one_answer(tmp_path, monkeypatch):
    twins = "\n\ndef a1():\n    return 1\n\n\ndef a2():\n    return 2\n"
    twins += "\n\ndef c1():\n    return a1() + a2()\n\n\ndef c2():\n    return a1() * a2()\n"
    root = python_repo(tmp_path, twins)
    monkeypatch.setattr(st.workspace, "worktree", lambda *a, **k: root)
    pin = st.config.PinnedRepo("o/pkg", "python", "v1", "c" * 40)
    tasks = st.repo_tasks(pin, tmp_path / "cache", 1, callers=8, two_hop=8, importers=4)
    keys = [t.gold for t in tasks]
    assert len(keys) == len(set(keys)) and ("pkg/core.py:c1", "pkg/core.py:c2") in keys
    assert sum(t.id.startswith("pkg-callers-") for t in tasks) <= 8


def test_python_module_names():
    assert st.python_module("src/requests/utils.py") == "requests.utils"
    assert st.python_module("pkg/__init__.py") == "pkg"
    assert st.python_module("README.md") is None


MAIN_RS = """mod walk;
use crate::walk::Walker;

fn main() {
    let w = Walker::new();
    helper();
}

fn helper() {
    Walker::new();
}
"""

WALK_RS = """pub struct Walker;

impl Walker {
    pub fn new() -> Self {
        Walker
    }
}

#[cfg(windows)]
fn win() {
    Walker::new();
}
"""

NEW = "rust-analyzer cargo demo 0.1.0 walk/Walker#new()."
HELPER = "rust-analyzer cargo demo 0.1.0 helper()."
MODULE = "rust-analyzer cargo demo 0.1.0 walk/"


def occurrence(doc, text, line, word, symbol, nth=0, definition=False):
    """An occurrence of `word` (its nth appearance on 0-based `line` of `text`)."""
    cols = [i for i in range(len(text.split("\n")[line])) if text.split("\n")[line].startswith(word, i)]
    o = doc.occurrences.add()
    o.range.extend([line, cols[nth], cols[nth] + len(word)])
    o.symbol = symbol
    o.symbol_roles = scip_pb2.SymbolRole.Definition if definition else 0


def rust_repo(tmp_path, resolve_windows_call):
    root = repo(tmp_path, {"src/main.rs": MAIN_RS, "src/walk.rs": WALK_RS})
    idx = scip_pb2.Index()
    main = idx.documents.add()
    main.relative_path = "src/main.rs"
    occurrence(main, MAIN_RS, 0, "walk", MODULE, definition=True)
    occurrence(main, MAIN_RS, 1, "walk", MODULE)
    occurrence(main, MAIN_RS, 4, "new", NEW)
    occurrence(main, MAIN_RS, 5, "helper", HELPER)
    occurrence(main, MAIN_RS, 8, "helper", HELPER, definition=True)
    occurrence(main, MAIN_RS, 9, "new", NEW)
    walk = idx.documents.add()
    walk.relative_path = "src/walk.rs"
    occurrence(walk, WALK_RS, 3, "new", NEW, definition=True)
    if resolve_windows_call:  # rust-analyzer leaves code behind an inactive cfg unresolved
        occurrence(walk, WALK_RS, 10, "new", NEW)
    path = tmp_path / "index.scip"
    path.write_bytes(idx.SerializeToString())
    return root, path


def test_rust_callers_from_scip(tmp_path):
    root, index = rust_repo(tmp_path, resolve_windows_call=True)
    a = st.rust_analysis(root, index)
    new = defn(a, "Walker.new")
    assert st._entries(a.callers(new)) == {"src/main.rs:main", "src/main.rs:helper", "src/walk.rs:win"}
    q, _ = st.callers_question(a, new, "rust")
    assert q.startswith("Which functions call `Walker::new`, defined in `src/walk.rs`?")


def test_rust_call_site_without_an_occurrence_makes_the_key_untrusted(tmp_path):
    root, index = rust_repo(tmp_path, resolve_windows_call=False)
    a = st.rust_analysis(root, index)
    assert a.callers(defn(a, "Walker.new")) is None


def test_rust_module_importers(tmp_path):
    root, index = rust_repo(tmp_path, resolve_windows_call=True)
    a = st.rust_analysis(root, index)
    [module] = a.modules
    assert (module.file, module.grep, module.importers) == ("src/walk.rs", "walk", {"src/main.rs": [2]})
    assert st.importers_question(a, module, "rust") is None  # one importer is too few


LIB_RS = """pub fn helper() -> u32 {
    1
}

/// Calls [helper] once.
pub fn user() -> u32 {
    helper()
}

#[cfg(test)]
mod tests {
    #[test]
    fn it_works() {
        assert_eq!(super::helper(), 1);
    }
}
"""

KEEPS_RS = """
pub fn keeps() -> fn() -> u32 {
    let f = helper;
    f
}
"""

LIB_HELPER = "rust-analyzer cargo demo 0.1.0 helper()."


def lib_repo(tmp_path, with_value_reference):
    text = LIB_RS + (KEEPS_RS if with_value_reference else "")
    root = repo(tmp_path, {"src/lib.rs": text})
    idx = scip_pb2.Index()
    doc = idx.documents.add()
    doc.relative_path = "src/lib.rs"
    occurrence(doc, text, 0, "helper", LIB_HELPER, definition=True)
    occurrence(doc, text, 4, "helper", LIB_HELPER)  # an intra-doc link, in a comment
    occurrence(doc, text, 6, "helper", LIB_HELPER)
    occurrence(doc, text, 13, "helper", LIB_HELPER)  # inside assert_eq!(...)
    if with_value_reference:
        occurrence(doc, text, 18, "helper", LIB_HELPER)  # `let f = helper;`: a reference, not a call
    path = tmp_path / "lib.scip"
    path.write_bytes(idx.SerializeToString())
    return root, path


def test_rust_calls_inside_macro_invocations_are_call_sites(tmp_path):
    a = st.rust_analysis(*lib_repo(tmp_path, with_value_reference=False))
    assert st._entries(a.callers(defn(a, "helper"))) == {"src/lib.rs:user", "src/lib.rs:it_works"}


def test_rust_reference_that_is_not_a_call_makes_the_key_untrusted(tmp_path):
    a = st.rust_analysis(*lib_repo(tmp_path, with_value_reference=True))
    assert a.callers(defn(a, "helper")) is None


ENTRY_RS = """pub struct Entry;

impl Entry {
    pub fn kind(&self) -> u32 {
        self.meta()
    }
    fn meta(&self) -> u32 {
        1
    }
}

pub trait Colorable {
    fn kind(&self) -> u32;
}

impl Colorable for Entry {
    fn kind(&self) -> u32 {
        0
    }
}

pub fn show(e: &Entry) -> u32 {
    e.kind()
}

pub fn paint(e: &Entry) -> u32 {
    e.kind() + e.meta()
}
"""

KIND = "rust-analyzer cargo demo 0.1.0 Entry#kind()."
KIND_TRAIT = "rust-analyzer cargo demo 0.1.0 impl#[Entry][Colorable]kind()."
META = "rust-analyzer cargo demo 0.1.0 Entry#meta()."


def entry_repo(tmp_path):
    """Entry.kind is both an inherent method and a trait impl method, in one file: two functions, one name."""
    root = repo(tmp_path, {"src/lib.rs": ENTRY_RS})
    idx = scip_pb2.Index()
    doc = idx.documents.add()
    doc.relative_path = "src/lib.rs"
    occurrence(doc, ENTRY_RS, 3, "kind", KIND, definition=True)
    occurrence(doc, ENTRY_RS, 4, "meta", META)
    occurrence(doc, ENTRY_RS, 6, "meta", META, definition=True)
    occurrence(doc, ENTRY_RS, 12, "kind", "rust-analyzer cargo demo 0.1.0 Colorable#kind().", definition=True)
    occurrence(doc, ENTRY_RS, 16, "kind", KIND_TRAIT, definition=True)
    occurrence(doc, ENTRY_RS, 21, "show", "rust-analyzer cargo demo 0.1.0 show().", definition=True)
    occurrence(doc, ENTRY_RS, 22, "kind", KIND)
    occurrence(doc, ENTRY_RS, 25, "paint", "rust-analyzer cargo demo 0.1.0 paint().", definition=True)
    occurrence(doc, ENTRY_RS, 26, "kind", KIND)
    occurrence(doc, ENTRY_RS, 26, "meta", META)
    path = tmp_path / "entry.scip"
    path.write_bytes(idx.SerializeToString())
    return st.rust_analysis(root, path)


def test_a_name_defined_twice_in_its_file_is_never_asked_about(tmp_path):
    import random

    a = entry_repo(tmp_path)
    picked = st.pick(a, "rust", st.callers_question, 1, 1, random.Random(0))
    assert picked and all(d.qualname != "Entry.kind" for d, *_ in picked)


def test_two_hop_expands_through_the_right_one_of_two_same_named_functions(tmp_path):
    a = entry_repo(tmp_path)
    made = st.two_hop_question(a, defn(a, "Entry.meta"), "rust")
    assert made is not None
    assert made[1] == ("src/lib.rs:Entry.kind", "src/lib.rs:paint", "src/lib.rs:show")
