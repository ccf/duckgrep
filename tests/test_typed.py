"""The typed tier: receiver calls resolved through the receiver's class, inferred from syntax (Python)."""

from helpers import make_repo, rows


def edges_at(root, path, name):
    return rows(
        root,
        f"SELECT dst_path, dst_qualname, resolution FROM edges WHERE src_path = '{path}' AND name = '{name}' "
        "ORDER BY ALL",
    )


BASE = "class Base:\n    def run(self):\n        return 1\n\n    def get(self):\n        return 2\n"
OTHER = "class Other:\n    def run(self):\n        return 3\n\n    def get(self):\n        return 4\n"


def test_inherited_self_and_builtin_named_methods(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": BASE,
            "other.py": OTHER,
            "child.py": "from base import Base\n\n\nclass Child(Base):\n    def go(self):\n"
            "        self.run()\n        return self.get()\n",
        },
    )
    assert edges_at(root, "child.py", "run") == [("base.py", "Base.run", "typed")]
    assert edges_at(root, "child.py", "get") == [("base.py", "Base.get", "typed")]


def test_super_skips_the_class_itself(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": BASE,
            "child.py": "from base import Base\n\n\nclass Child(Base):\n    def run(self):\n"
            "        return super().run()\n",
        },
    )
    assert edges_at(root, "child.py", "run") == [("base.py", "Base.run", "typed")]


def test_constructor_and_annotations(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": BASE,
            "other.py": OTHER,
            "use.py": "from typing import Optional\n\nfrom base import Base\nimport other\n\n\n"
            "def f():\n    x = Base()\n    return x.run()\n\n\n"
            "def g(p: 'Optional[Base]'):\n    return p.get()\n\n\n"
            "def h():\n    o = other.Other()\n    return o.run()\n",
        },
    )
    assert edges_at(root, "use.py", "run") == [("base.py", "Base.run", "typed"), ("other.py", "Other.run", "typed")]
    assert edges_at(root, "use.py", "get") == [("base.py", "Base.get", "typed")]


def test_self_attr_from_param_constructor_and_class_annotation(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "conn.py": "class Conn:\n    def send(self):\n        return 1\n\n    def close(self):\n        return 2\n",
            "noise.py": "class Noise:\n    def send(self):\n        return 0\n\n    def close(self):\n        return 0\n",
            "svc.py": "from conn import Conn\n\n\nclass Svc:\n    pool: Conn\n\n"
            "    def __init__(self, c: Conn):\n        self.c = c\n        self.d = Conn()\n\n"
            "    def go(self):\n        self.c.send()\n        self.d.send()\n        return self.pool.close()\n",
        },
    )
    assert edges_at(root, "svc.py", "send") == [("conn.py", "Conn.send", "typed")] * 2
    assert edges_at(root, "svc.py", "close") == [("conn.py", "Conn.close", "typed")]


def test_return_annotation_one_hop_and_classmethod_factory(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": BASE + "\n    @classmethod\n    def create(cls) -> 'Self':\n        return cls()\n",
            "make.py": "from base import Base\n\n\ndef make() -> Base:\n    return Base()\n",
            "use.py": "from base import Base\nfrom make import make\n\n\n"
            "def f():\n    x = make()\n    y = Base.create()\n    x.run()\n    return y.get()\n",
        },
    )
    assert edges_at(root, "use.py", "run") == [("base.py", "Base.run", "typed")]
    assert edges_at(root, "use.py", "get") == [("base.py", "Base.get", "typed")]


def test_imported_module_level_instance(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": BASE + "\n\nshared = Base()\n",
            "other.py": OTHER,
            "use.py": "from base import shared\n\n\ndef f():\n    return shared.run()\n",
        },
    )
    assert edges_at(root, "use.py", "run") == [("base.py", "Base.run", "typed")]


def test_class_receiver_reaches_inherited_methods(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": "class Base:\n    @staticmethod\n    def helper():\n        return 1\n",
            "child.py": "from base import Base\n\n\nclass Child(Base):\n    pass\n\n\ndef f():\n"
            "    return Child.helper()\n",
        },
    )
    assert edges_at(root, "child.py", "helper") == [("base.py", "Base.helper", "typed")]


def test_nearest_ancestor_wins(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "m.py": "class A:\n    def m(self):\n        return 1\n\n\nclass B(A):\n    def m(self):\n        return 2\n\n\n"
            "class C(B):\n    pass\n\n\ndef g():\n    c = C()\n    return c.m()\n"
        },
    )
    assert edges_at(root, "m.py", "m") == [("m.py", "B.m", "typed")]


def test_closures_and_module_bindings(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": BASE,
            "use.py": "from base import Base\n\nx = Base()\n\n\ndef f():\n    return x.run()\n\n\n"
            "def g():\n    y = Base()\n\n    def inner():\n        return y.get()\n\n    return inner\n",
        },
    )
    assert edges_at(root, "use.py", "run") == [("base.py", "Base.run", "typed")]
    assert edges_at(root, "use.py", "get") == [("base.py", "Base.get", "typed")]


def test_two_bindings_block_inference(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": BASE,
            "other.py": OTHER,
            "use.py": "from base import Base\nfrom other import Other\n\n\ndef f():\n    x = Base()\n    x = Other()\n"
            "    return x.run()\n",
        },
    )
    assert {r[2] for r in edges_at(root, "use.py", "run")} == {"name"}


def test_branch_rebinding_blocks_inference(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": BASE,
            "other.py": OTHER,
            "use.py": "from base import Base\nfrom other import Other\n\n\ndef f(c):\n    if c:\n        x = Base()\n"
            "    else:\n        x = Other()\n    for _ in range(2):\n        x.run()\n",
        },
    )
    assert {r[2] for r in edges_at(root, "use.py", "run")} == {"name"}


def test_unannotated_param_and_inner_scope_shadowing_block_inference(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": BASE,
            "other.py": OTHER,
            "use.py": "from base import Base\n\nx = Base()\n\n\ndef f(x):\n    return x.run()\n",
        },
    )
    assert {r[2] for r in edges_at(root, "use.py", "run")} == {"name"}


def test_inner_scope_shadowing_blocks_inference(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": BASE,
            "other.py": OTHER,
            "use.py": "from base import Base\n\n\ndef f(items):\n    x = Base()\n\n    def g(x):\n        return x.run()\n\n"
            "    return [x.get() for x in items]\n",
        },
    )
    assert {r[2] for r in edges_at(root, "use.py", "run")} == {"name"}
    assert {r[2] for r in edges_at(root, "use.py", "get")} == {"ambiguous"}


def test_imports_decide_between_same_named_classes(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "a/m.py": "class Foo:\n    def run(self):\n        return 1\n",
            "b/m.py": "class Foo:\n    def run(self):\n        return 2\n",
            "use.py": "from b.m import Foo\n\n\ndef f():\n    return Foo().run()\n\n\ndef g():\n    x = Foo()\n"
            "    return x.run()\n",
        },
    )
    assert edges_at(root, "use.py", "run")[-1] == ("b/m.py", "Foo.run", "typed")
    assert ("a/m.py", "Foo.run", "typed") not in edges_at(root, "use.py", "run")


def test_metaclass_keyword_is_not_a_base(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "m.py": "class Meta(type):\n    def run(cls):\n        return 1\n\n\nclass C(metaclass=Meta):\n    def go(self):\n"
            "        return self.run()\n"
        },
    )
    assert ("m.py", "Meta.run", "typed") not in edges_at(root, "m.py", "run")


def test_opaque_base_keeps_todays_tiers(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": BASE,
            "m.py": "from collections import namedtuple\n\n\nclass C(namedtuple('P', 'a')):\n"
            "    def go(self):\n        return self.run()\n\n\ndef make_base():\n    return object\n\n\n"
            "class D(make_base()):\n    def go(self):\n        return self.run()\n",
        },
    )
    assert {r[2] for r in edges_at(root, "m.py", "run")} == {"name"}


def test_cyclic_and_deep_hierarchies_terminate(tmp_path):
    chain = (
        "".join(f"class K{i}(K{i + 1}):\n    pass\n\n\n" for i in range(12))
        + "class K12:\n    def deep(self):\n        return 1\n\n\n"
    )
    root = make_repo(
        tmp_path / "r",
        {
            "a.py": "from b import B\n\n\nclass A(B):\n    def go(self):\n        return self.nope()\n",
            "b.py": "from a import A\n\n\nclass B(A):\n    pass\n",
            "k.py": chain + "def f():\n    k = K0()\n    return k.deep()\n",
        },
    )
    assert {r[2] for r in edges_at(root, "a.py", "nope")} <= {"unresolved", "name"}
    assert ("k.py", "K12.deep", "typed") not in edges_at(root, "k.py", "deep")


def test_external_and_literal_types_are_unresolved(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "buf.py": "class Buf:\n    def getvalue(self):\n        return 1\n\n    def append(self, x):\n        return x\n",
            "use.py": "import io\n\n\ndef f():\n    out = io.StringIO()\n    return out.getvalue()\n\n\n"
            "def g():\n    xs = []\n    xs.append(1)\n    return xs\n",
        },
    )
    assert edges_at(root, "use.py", "getvalue") == [(None, None, "unresolved")]
    assert edges_at(root, "use.py", "append") == [(None, None, "unresolved")]


def test_methods_missing_from_a_class_with_an_external_base_are_unresolved(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "other.py": "class Other:\n    def assertEqual(self, a, b):\n        return a == b\n",
            "t.py": "import unittest\n\n\nclass T(unittest.TestCase):\n    def test(self):\n"
            "        self.assertEqual(1, 1)\n",
        },
    )
    assert edges_at(root, "t.py", "assertEqual") == [(None, None, "unresolved")]
