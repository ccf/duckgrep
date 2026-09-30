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
    assert q == "Which functions call `Store.get`, defined in `pkg/core.py`? List every one, including test functions."
    assert key == ("pkg/core.py:load", "pkg/core.py:run")
    q, key = st.two_hop_question(a, defn(a, "Store.get"), "python")
    assert "directly or through one intermediate function" in q
    assert key == ("pkg/core.py:load", "pkg/core.py:run", "pkg/other.py:main", "tests/test_core.py:test_run")
    module = next(m for m in a.modules if m.file == "pkg/core.py")
    assert module.importers == {"pkg/other.py": [1], "tests/test_core.py": [1]}
    q, key = st.importers_question(a, module, "python")
    assert q.startswith("Which files import the module `pkg.core`, or import names from it?")
    assert key == ("pkg/other.py", "tests/test_core.py")


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
