"""Incremental edges equal a full rebuild after edits that change what a typed receiver resolves to."""

import random

import pytest
from helpers import fresh_snapshot, make_repo, rows, snapshot, write

ALL = ("files", "symbols", "refs", "imports", "modules", "lines", "bindings", "edges")
BASE = "class Base:\n    def run(self):\n        return 1\n"
GRAND = "class Grand:\n    def deep(self):\n        return 1\n"
OTHER = "class Other:\n    def deep(self):\n        return 2\n\n    def run(self):\n        return 3\n"
USE = "from child import Child\n\n\ndef f():\n    x = Child()\n    x.run()\n    return x.deep()\n"


def same(root, tmp_path):
    rows(root, "SELECT count(*) FROM edges")  # freshen + sync
    assert snapshot(root, ALL) == fresh_snapshot(root, tmp_path, ALL)


def repo(tmp_path):
    return make_repo(
        tmp_path / "r",
        {
            "grand.py": GRAND,
            "other.py": OTHER,
            "base.py": "from grand import Grand\n\n\n" + BASE.replace("class Base:", "class Base(Grand):"),
            "child.py": "from base import Base\n\n\nclass Child(Base):\n    pass\n",
            "use.py": USE,
        },
    )


def test_base_changes_its_base_in_another_file(tmp_path):
    root = repo(tmp_path)
    same(root, tmp_path)
    write(root, "base.py", "from other import Other\n\n\n" + BASE.replace("class Base:", "class Base(Other):"))
    same(root, tmp_path)


def test_base_gains_and_loses_a_method(tmp_path):
    root = repo(tmp_path)
    same(root, tmp_path)
    write(root, "base.py", "from grand import Grand\n\n\nclass Base(Grand):\n    def deep(self):\n        return 9\n")
    same(root, tmp_path)


def test_return_annotation_changes(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "grand.py": GRAND,
            "other.py": OTHER,
            "make.py": "from grand import Grand\n\n\ndef make() -> Grand:\n    return Grand()\n",
            "use.py": "from make import make\n\n\ndef f():\n    y = make()\n    return y.deep()\n",
        },
    )
    same(root, tmp_path)
    write(root, "make.py", "from other import Other\n\n\ndef make() -> Other:\n    return Other()\n")
    same(root, tmp_path)


def test_constructor_class_renamed_or_deleted(tmp_path):
    root = repo(tmp_path)
    same(root, tmp_path)
    write(root, "child.py", "from base import Base\n\n\nclass Kid(Base):\n    pass\n")
    same(root, tmp_path)


def test_attr_binding_changes_in_a_base_class(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "grand.py": GRAND,
            "other.py": OTHER,
            "base.py": "from grand import Grand\n\n\nclass Base:\n    def __init__(self):\n        self.h = Grand()\n",
            "child.py": "from base import Base\n\n\nclass Child(Base):\n    def go(self):\n        return self.h.deep()\n",
        },
    )
    same(root, tmp_path)
    write(
        root, "base.py", "from other import Other\n\n\nclass Base:\n    def __init__(self):\n        self.h = Other()\n"
    )
    same(root, tmp_path)


def test_module_level_instance_changes_type(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "grand.py": GRAND,
            "other.py": OTHER,
            "inst.py": "from grand import Grand\n\nshared = Grand()\n",
            "use.py": "from inst import shared\n\n\ndef f():\n    return shared.deep()\n",
        },
    )
    same(root, tmp_path)
    write(root, "inst.py", "from other import Other\n\nshared = Other()\n")
    same(root, tmp_path)


def test_second_binding_drops_inference(tmp_path):
    root = repo(tmp_path)
    same(root, tmp_path)
    write(root, "use.py", USE.replace("    x.run()\n", "    x = object()\n    x.run()\n"))
    same(root, tmp_path)


POOL = {
    "base.py": [
        "from grand import Grand\n\n\nclass Base(Grand):\n    def run(self):\n        return 1\n",
        "from other import Other\n\n\nclass Base(Other):\n    pass\n",
        "class Base:\n    def deep(self):\n        return 0\n",
    ],
    "inst.py": ["from child import Child\n\nshared = Child()\n", "from other import Other\n\nshared = Other()\n", ""],
    "make.py": [
        "from child import Child\n\n\ndef make() -> Child:\n    return Child()\n",
        "from grand import Grand\n\n\ndef make() -> 'Grand':\n    return Grand()\n",
        "import pkg\n\n\ndef make() -> pkg.Grand:\n    return None\n",
    ],
    # a plain re-export switching source (an aliased one is the module tier's known gap, see CLAUDE.md)
    "pkg/__init__.py": ["from grand import Grand\n", "from grand2 import Grand\n"],
    "child.py": [
        "from base import Base\n\n\nclass Child(Base):\n    pass\n",
        "from other import Other\n\n\nclass Child(Other):\n    def run(self):\n        return 5\n",
        "from base import Base\n\n\nclass Child(Base):\n    def __init__(self):\n        self.o = Base()\n",
        "from base import Base as B\n\n\nclass Child(B):\n    pass\n",
        "import pkg\n\n\nclass Child(pkg.Grand):\n    pass\n",
    ],
}


def test_random_edit_sequences(tmp_path):
    rng = random.Random(20261003)
    files = {
        "grand.py": GRAND,
        "grand2.py": GRAND.replace("return 1", "return 2"),
        "other.py": OTHER,
        **{k: v[0] for k, v in POOL.items()},
        "use.py": "import pkg\nfrom child import Child\nfrom inst import shared\nfrom make import make\n\n\n"
        "def f():\n    x = Child()\n    y = make()\n    x.run()\n    y.deep()\n    shared.run()\n"
        "    z = pkg.Grand()\n    z.deep()\n    return x.deep()\n",
    }
    root = make_repo(tmp_path / "r", files)
    same(root, tmp_path)
    for _ in range(24):
        path = rng.choice(sorted(POOL))
        write(root, path, rng.choice(POOL[path]))
        same(root, tmp_path)


def test_reexporting_module_switches_its_source(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "grand.py": GRAND,
            "other.py": OTHER.replace("class Other:", "class Grand:"),
            "pkg/__init__.py": "from grand import Grand\n",
            "use.py": "from pkg import Grand\n\n\ndef f():\n    x = Grand()\n    return x.deep()\n",
        },
    )
    same(root, tmp_path)
    write(root, "pkg/__init__.py", "from other import Grand\n")
    same(root, tmp_path)
    assert rows(root, "SELECT dst_path FROM edges WHERE src_path = 'use.py' AND name = 'deep'") == [("other.py",)]


PAD = {
    f"pad/f{i}.py": f"def pad{i}():\n    return {i}\n" for i in range(12)
}  # keeps one edit under the rebuild threshold
FOO_A = "class Foo:\n    def m(self):\n        return 1\n"
FOO_B = "class Foo:\n    def m(self):\n        return 2\n"
CROSS_FILE = {  # name: (files, edits)
    "aliased base": (
        {
            "base.py": "class Base:\n    def m(self):\n        return 1\n",
            "foo.py": "from base import Base as B\n\n\nclass Foo(B):\n    pass\n",
            "other.py": "class Other:\n    def m(self):\n        return 2\n",
            "use.py": "from foo import Foo\n\n\ndef f():\n    x = Foo()\n    return x.m()\n",
        },
        {"foo.py": "from base import Base as B\n\n\nclass Foo:\n    pass\n"},
    ),
    "re-export through a module attribute": (
        {
            "a.py": FOO_A,
            "b.py": FOO_B,
            "pkg/__init__.py": "from a import Foo\n",
            "use.py": "import pkg\n\n\ndef f():\n    x = pkg.Foo()\n    return x.m()\n",
        },
        {"pkg/__init__.py": "from b import Foo\n"},
    ),
    "aliased re-export": (
        {
            "a.py": FOO_A,
            "b.py": FOO_B.replace("Foo", "Foo2"),
            "pkg/__init__.py": "from a import Foo as Bar\n",
            "use.py": "from pkg import Bar\n\n\ndef f():\n    x = Bar()\n    return x.m()\n",
        },
        {"pkg/__init__.py": "from b import Foo2 as Bar\n"},
    ),
    "re-export through a subpackage": (
        {
            "a.py": FOO_A,
            "b.py": FOO_B,
            "pkg/__init__.py": "",
            "pkg/sub/__init__.py": "from a import Foo\n",
            "use.py": "from pkg import sub\n\n\ndef f():\n    x = sub.Foo()\n    return x.m()\n",
        },
        {"pkg/sub/__init__.py": "from b import Foo\n"},
    ),
    "base class through a module-attribute re-export": (
        {
            "a.py": FOO_A.replace("Foo", "Base"),
            "b.py": FOO_B.replace("Foo", "Base"),
            "pkg/__init__.py": "from a import Base\n",
            "child.py": "import pkg\n\n\nclass Child(pkg.Base):\n    pass\n",
            "use.py": "from child import Child\n\n\ndef f():\n    x = Child()\n    return x.m()\n",
        },
        {"pkg/__init__.py": "from b import Base\n"},
    ),
    "return annotation through a module-attribute re-export": (
        {
            "a.py": FOO_A.replace("Foo", "Thing"),
            "b.py": FOO_B.replace("Foo", "Thing"),
            "pkg/__init__.py": "from a import Thing\n",
            "mk.py": "import pkg\n\n\ndef make() -> pkg.Thing:\n    return None\n",
            "use.py": "from mk import make\n\n\ndef f():\n    x = make()\n    return x.m()\n",
        },
        {"pkg/__init__.py": "from b import Thing\n"},
    ),
    "star source gains, loses or renames the name": (
        {
            "src.py": "class Foo:\n    def m(self):\n        return 1\n",
            "other.py": "class Foo:\n    def m(self):\n        return 2\n",
            "pkg/__init__.py": "from src import *\n",
            "use.py": "from pkg import Foo\n\n\ndef f():\n    x = Foo()\n    return x.m()\n",
        },
        {"src.py": "class Bar:\n    def m(self):\n        return 1\n"},
    ),
    "a second star source starts defining the name": (
        {
            "src.py": "class Foo:\n    def m(self):\n        return 1\n",
            "two.py": "class Two:\n    def m(self):\n        return 2\n",
            "pkg/__init__.py": "from src import *\nfrom two import *\n",
            "use.py": "from pkg import Foo\n\n\ndef f():\n    x = Foo()\n    return x.m()\n",
        },
        {"two.py": "class Foo:\n    def m(self):\n        return 2\n"},
    ),
    "a re-export hop is inserted": (
        {
            "src.py": "class Foo:\n    def m(self):\n        return 1\n",
            "mid/__init__.py": "",
            "pkg/__init__.py": "from mid import *\n",
            "use.py": "from pkg import Foo\n\n\ndef f():\n    x = Foo()\n    return x.m()\n",
        },
        {"mid/__init__.py": "from src import Foo\n"},
    ),
    "the class behind Foo().m() changes": (
        {
            "src.py": "class Foo:\n    def m(self):\n        return 1\n",
            "use.py": "from src import Foo\n\n\ndef f():\n    return Foo().m()\n",
        },
        {"src.py": "class Foo:\n    def n(self):\n        return 1\n"},
    ),
    "an unannotated function's return changes": (
        {
            "a.py": "class A:\n    def m(self):\n        return 1\n",
            "b.py": "class B:\n    def m(self):\n        return 2\n",
            "mk.py": "from a import A\nfrom b import B\n\n\ndef make():\n    return A()\n",
            "use.py": "from mk import make\n\n\ndef f():\n    x = make()\n    return x.m()\n",
        },
        {"mk.py": "from a import A\nfrom b import B\n\n\ndef make():\n    return B()\n"},
    ),
    "an unannotated function's returns stop agreeing": (
        {
            "a.py": "class A:\n    def m(self):\n        return 1\n",
            "b.py": "class B:\n    def m(self):\n        return 2\n",
            "mk.py": "from a import A\nfrom b import B\n\n\ndef make(c):\n    return A()\n",
            "use.py": "from mk import make\n\n\ndef f():\n    x = make(1)\n    return x.m()\n",
        },
        {
            "mk.py": "from a import A\nfrom b import B\n\n\ndef make(c):\n    if c:\n        return B()\n    return A()\n"
        },
    ),
    "a local class's base changes in another file": (
        {
            "a.py": "class A:\n    def m(self):\n        return 1\n",
            "b.py": "class B:\n    def m(self):\n        return 2\n",
            "base.py": "from a import A\n\n\nclass Base(A):\n    pass\n",
            "t.py": "from base import Base\n\n\ndef t():\n    class L(Base):\n        pass\n\n    return L().m()\n",
        },
        {"base.py": "from b import B\n\n\nclass Base(B):\n    pass\n"},
    ),
}
EXT_FLIPS = {  # a class's ancestry or a callee's return gains or loses an external type: name <-> unresolved
    "external base added": (
        {
            "base.py": "class Base:\n    def m(self):\n        return 1\n",
            "foo.py": "from base import Base\n\n\nclass Foo(Base):\n    pass\n",
            "other.py": "class Other:\n    def zzz(self):\n        return 2\n",
            "use.py": "from foo import Foo\n\n\ndef f():\n    x = Foo()\n    return x.zzz()\n",
        },
        {"base.py": "from extlib import Ext\n\n\nclass Base(Ext):\n    def m(self):\n        return 1\n"},
    ),
    "external return added": (
        {
            "mk.py": "def make():\n    return 1\n",
            "other.py": "class Other:\n    def zzz(self):\n        return 2\n",
            "use.py": "from mk import make\n\n\ndef f():\n    x = make()\n    return x.zzz()\n",
        },
        {"mk.py": "from extlib import Thing\n\n\ndef make() -> Thing:\n    return 1\n"},
    ),
    "external base removed": (
        {
            "base.py": "from extlib import Ext\n\n\nclass Base(Ext):\n    def m(self):\n        return 1\n",
            "other.py": "class Other:\n    def zzz(self):\n        return 2\n",
            "use.py": "from base import Base\n\n\nclass Sub(Base):\n    def g(self):\n        return self.zzz()\n",
        },
        {"base.py": "class Base:\n    def m(self):\n        return 1\n"},
    ),
}


def edit_and_compare(tmp_path, files, edits):
    root = make_repo(tmp_path / "r", {**PAD, **files})
    same(root, tmp_path)
    for rel, text in edits.items():
        write(root, rel, text)
    same(root, tmp_path)


@pytest.mark.parametrize("case", sorted(CROSS_FILE))
def test_cross_file_edits_keep_typed_edges_fresh(tmp_path, case):
    edit_and_compare(tmp_path, *CROSS_FILE[case])


@pytest.mark.xfail(strict=True, reason="known gap: an ancestry or return that turns external isn't marked dirty")
@pytest.mark.parametrize("case", sorted(EXT_FLIPS))
def test_external_flips_are_a_known_gap(tmp_path, case):
    edit_and_compare(tmp_path, *EXT_FLIPS[case])


def test_a_sync_that_runs_out_of_memory_falls_back_to_a_rebuild(tmp_path, monkeypatch):
    """An out-of-memory sync must not leave dirty rows behind that break every later call-graph query."""
    import duckdb

    from duckgrep import index

    root = repo(tmp_path)
    same(root, tmp_path)
    real = index._compute_edges
    calls = {"n": 0}

    def flaky(con, source, where="TRUE"):
        calls["n"] += 1
        if calls["n"] == 1:
            raise duckdb.OutOfMemoryException("Out of Memory Error: failed to pin block")
        return real(con, source, where)

    monkeypatch.setattr(index, "_compute_edges", flaky)
    write(root, "base.py", "from other import Other\n\n\n" + BASE.replace("class Base:", "class Base(Other):"))
    rows(root, "SELECT count(*) FROM edges")
    assert rows(root, "SELECT count(*) FROM edges_dirty") == [(0,)]
    monkeypatch.setattr(index, "_compute_edges", real)
    same(root, tmp_path)
