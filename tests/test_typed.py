"""The typed tier: receiver calls resolved through the receiver's class, inferred from syntax (Python)."""

import pytest
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


def test_self_in_a_nested_class_names_that_class(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "m.py": "class Inner:\n    def run(self):\n        return 0\n\n\nclass Outer:\n    class Inner:\n"
            "        def run(self):\n            return 1\n\n        def make(self) -> 'Self':\n            return self\n\n\n"
            "def f():\n    x = Outer.Inner.make()\n    return x.run()\n",
        },
    )
    assert ("m.py", "Inner.run", "typed") not in edges_at(root, "m.py", "run")
    assert ("m.py", "Outer.Inner.run", "typed") in edges_at(root, "m.py", "run")


def test_a_rebound_capitalised_name_is_not_a_class_receiver(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": BASE,
            "other.py": "class OtherBase:\n    def run(self):\n        return 3\n\n\nclass Other(OtherBase):\n    pass\n",
            "use.py": "from base import Base\nfrom other import Other\n\n\ndef f():\n    Base = Other()\n"
            "    return Base.run()\n",
        },
    )
    # the qualified tier still reads `Base.run()` as the class's method: a pre-existing gap, out of scope here
    assert ("base.py", "Base.run", "typed") not in edges_at(root, "use.py", "run")
    assert ("other.py", "OtherBase.run", "typed") in edges_at(root, "use.py", "run")


def test_inheritance_order_follows_python_mro(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "m.py": "class A1:\n    def m(self):\n        return 'A1'\n\n\nclass A(A1):\n    pass\n\n\nclass B:\n"
            "    def m(self):\n        return 'B'\n\n\nclass C(A, B):\n    pass\n\n\n"
            "class Base:\n    def d(self):\n        return 'Base'\n\n\nclass L(Base):\n    pass\n\n\nclass R(Base):\n"
            "    def d(self):\n        return 'R'\n\n\nclass D(L, R):\n    pass\n\n\n"
            "def f():\n    c = C()\n    x = D()\n    c.m()\n    return x.d()\n",
        },
    )
    assert edges_at(root, "m.py", "m") == [("m.py", "A1.m", "typed")]  # left grandparent before right parent
    assert ("m.py", "Base.d", "typed") not in edges_at(root, "m.py", "d")  # diamond: R before the shared Base


def test_a_rebound_parameter_does_not_type_an_attribute(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": BASE,
            "other.py": OTHER,
            "svc.py": "from base import Base\nfrom other import Other\n\n\nclass Svc:\n    def __init__(self, p: Base):\n"
            "        p = Other()\n        self.h = p\n\n    def go(self):\n        return self.h.run()\n",
        },
    )
    assert ("base.py", "Base.run", "typed") not in edges_at(root, "svc.py", "run")


def test_a_deleted_name_is_not_inferred(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": BASE,
            "other.py": OTHER,
            "use.py": "from base import Base\n\n\ndef f():\n    x = Base()\n    del x\n    return x.run()\n",
        },
    )
    assert {r[2] for r in edges_at(root, "use.py", "run")} == {"name"}


SHADOWS = {
    "lambda parameter": "def f():\n    x = Base()\n    g = lambda x: x.run()\n    return g\n",
    "global rebinding in another function": "x = Base()\n\n\ndef g():\n    global x\n    x = Other()\n\n\ndef h():\n    return x.run()\n",
    "nonlocal rebinding": "def f():\n    x = Base()\n\n    def g():\n        nonlocal x\n        x = Other()\n\n    g()\n    return x.run()\n",
    "match capture": "def f(v):\n    x = Base()\n    match v:\n        case [x]:\n            pass\n    return x.run()\n",
    "match as capture": "def f(v):\n    x = Base()\n    match v:\n        case Other() as x:\n            pass\n    return x.run()\n",
    "module-level import of the same name": "try:\n    from extlib import x\nexcept ImportError:\n    x = Base()\n\n\ndef f():\n    return x.run()\n",
}


@pytest.mark.parametrize("case", sorted(SHADOWS))
def test_other_bindings_block_inference(tmp_path, case):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": BASE,
            "other.py": OTHER,
            "use.py": "from base import Base\nfrom other import Other\n\n\n" + SHADOWS[case],
        },
    )
    assert ("base.py", "Base.run", "typed") not in edges_at(root, "use.py", "run")


def test_an_ambiguous_diamond_gets_no_wrong_edge(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "m.py": "class D:\n    def m(self):\n        return 'D'\n\n\nclass E:\n    def m(self):\n        return 'E'\n\n\n"
            "class F:\n    pass\n\n\nclass B(D, E):\n    pass\n\n\nclass C(D, F):\n    pass\n\n\nclass A(B, C):\n    pass\n\n\n"
            "def f():\n    a = A()\n    return a.m()\n",
        },
    )
    assert ("m.py", "E.m", "typed") not in edges_at(root, "m.py", "m")  # Python calls D.m


def test_an_external_base_listed_first_blocks_the_in_repo_method(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": BASE,
            "c.py": "from extlib import External\n\nfrom base import Base\n\n\nclass C(External, Base):\n    pass\n\n\n"
            "class D(Base, External):\n    pass\n\n\ndef f():\n    c = C()\n    d = D()\n    c.run()\n    return d.get()\n",
        },
    )
    assert ("base.py", "Base.run", "typed") not in edges_at(root, "c.py", "run")  # External.run may win
    assert edges_at(root, "c.py", "get") == [("base.py", "Base.get", "typed")]  # Base comes first in D


def test_a_diamond_class_that_defines_the_method_keeps_its_edge(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "m.py": "class Base:\n    def d(self):\n        return 0\n\n\nclass L(Base):\n    pass\n\n\nclass R(Base):\n"
            "    def d(self):\n        return 1\n\n\nclass D(L, R):\n    def d(self):\n        return 2\n\n\n"
            "def f():\n    x = D()\n    return x.d()\n",
        },
    )
    assert edges_at(root, "m.py", "d") == [("m.py", "D.d", "typed")]


def test_a_shared_external_base_comes_after_the_in_repo_definer(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "m.py": "from extlib import External\n\n\nclass B(External):\n    pass\n\n\nclass C(External):\n"
            "    def m(self):\n        return 1\n\n\nclass D(B, C):\n    pass\n\n\ndef f():\n    x = D()\n    return x.m()\n",
        },
    )
    assert edges_at(root, "m.py", "m") == [("m.py", "C.m", "typed")]  # MRO: D, B, C, External


def test_a_rebound_class_name_is_not_its_class(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": BASE,
            "other.py": OTHER,
            "m.py": "from other import Other\n\n\nclass Base:\n    def run(self):\n        return 9\n\n\nBase = Other\n",
            "use.py": "from m import Base\n\n\ndef f():\n    x = Base()\n    return x.run()\n",
        },
    )
    assert ("m.py", "Base.run", "typed") not in edges_at(root, "use.py", "run")
    assert ("m.py", "Base.run", "typed") not in edges_at(root, "m.py", "run")


REBINDS = {
    "match capture": "        match v:\n            case [p]:\n                pass\n",
    "function-level import": "        from other import Other as p\n",
    "nested nonlocal write": "        def g():\n            nonlocal p\n            p = Other()\n\n        g()\n",
}


@pytest.mark.parametrize("case", sorted(REBINDS))
def test_other_rebindings_stop_a_parameter_typing_an_attribute(tmp_path, case):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": BASE,
            "other.py": OTHER,
            "svc.py": "from base import Base\nfrom other import Other\n\n\nclass Svc:\n    def __init__(self, p: Base, v=None):\n"
            + REBINDS[case]
            + "        self.h = p\n\n    def go(self):\n        return self.h.run()\n",
        },
    )
    assert ("base.py", "Base.run", "typed") not in edges_at(root, "svc.py", "run")


def test_distinct_external_bases_with_one_name_stay_distinct(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "b.py": "from extone import External\n\n\nclass B(External):\n    pass\n",
            "c.py": "from exttwo import External\n\n\nclass C(External):\n    def m(self):\n        return 1\n",
            "d.py": "from b import B\nfrom c import C\n\n\nclass D(B, C):\n    pass\n\n\ndef f():\n    x = D()\n    return x.m()\n",
        },
    )
    assert ("c.py", "C.m", "typed") not in edges_at(root, "d.py", "m")  # extone.External may define m first


def test_a_binding_before_the_class_does_not_hide_it(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "m.py": "Foo = None\n\n\nclass Foo:\n    def run(self):\n        return 1\n\n\ndef f():\n    x = Foo()\n    return x.run()\n"
        },
    )
    # bound twice in the file: no guess, but the class's method stays a candidate
    assert ("m.py", "Foo.run") in {(p, q) for p, q, _ in edges_at(root, "m.py", "run")}


def test_a_function_local_class_is_not_its_module_namesake(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "m.py": "class Foo:\n    def run(self):\n        return 1\n\n\ndef f():\n    class Foo:\n        def run(self):\n"
            "            return 2\n\n    x = Foo()\n    return x.run()\n",
        },
    )
    assert ("m.py", "Foo.run", "typed") not in edges_at(root, "m.py", "run")


def test_class_body_bindings_are_not_visible_in_methods(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": BASE,
            "other.py": OTHER,
            "m.py": "from base import Base\nfrom other import Other\n\nx = Base()\n\n\nclass C:\n    if True:\n        x = Other()\n\n"
            "    def go(self):\n        return x.run()\n",
        },
    )
    assert ("other.py", "Other.run", "typed") not in edges_at(root, "m.py", "run")


def test_a_nested_namesake_blocks_imports_of_the_name_too(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "other.py": "class Foo:\n    def run(self):\n        return 0\n",
            "m.py": "from other import Foo\n\n\nclass Foo:\n    def run(self):\n        return 1\n\n\ndef helper():\n    class Foo:\n"
            "        pass\n\n    return Foo\n\n\ndef f():\n    x = Foo()\n    return x.run()\n",
        },
    )
    assert ("other.py", "Foo.run", "typed") not in edges_at(root, "m.py", "run")


def test_the_later_import_of_a_name_wins(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "a.py": "\n" * 20 + "class Foo:\n    def run(self):\n        return 1\n",
            "b.py": "class Foo:\n    def run(self):\n        return 2\n",
            "m.py": "from a import Foo\nfrom b import Foo\n\n\ndef f():\n    x = Foo()\n    return x.run()\n",
        },
    )
    assert ("a.py", "Foo.run", "typed") not in edges_at(root, "m.py", "run")


def test_a_nested_namesake_in_another_file_does_not_block(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": BASE,
            "elsewhere.py": "def g():\n    class Base:\n        pass\n\n    return Base\n",
            "use.py": "from base import Base\n\n\ndef f():\n    x = Base()\n    return x.run()\n",
        },
    )
    assert edges_at(root, "use.py", "run") == [("base.py", "Base.run", "typed")]


def test_a_name_imported_after_its_use_does_not_retype_it(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "other.py": "class Foo:\n    def run(self):\n        return 0\n",
            "m.py": "class Foo:\n    def run(self):\n        return 1\n\n\nx = Foo()\n\nfrom other import Foo  # noqa\n\n\n"
            "def f():\n    return x.run()\n",
        },
    )
    assert ("other.py", "Foo.run", "typed") not in edges_at(root, "m.py", "run")


def test_a_function_level_import_does_not_retype_module_code(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "other.py": "class Foo:\n    def run(self):\n        return 0\n",
            "m.py": "class Foo:\n    def run(self):\n        return 1\n\n\ndef helper():\n    from other import Foo\n\n"
            "    return Foo\n\n\nx = Foo()\n\n\ndef f():\n    return x.run()\n",
        },
    )
    assert ("other.py", "Foo.run", "typed") not in edges_at(root, "m.py", "run")


def test_a_refused_name_is_not_called_external(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "m.py": "class Foo:\n    def run(self):\n        return 1\n\n\ndef helper():\n    from extlib import Foo\n\n    return Foo\n\n\n"
            "x = Foo()\n\n\ndef f():\n    return x.run()\n",
        },
    )
    assert edges_at(root, "m.py", "run") == [("m.py", "Foo.run", "name")]  # unknown, not unresolved


def test_template_generates_both_stages():
    from duckgrep import schema

    assert "py_scoped1 AS" in schema.EDGES_COMPUTE and "py_bind1 AS" in schema.EDGES_COMPUTE
    assert "py_scoped2 AS" in schema.EDGES_COMPUTE
    assert "@N@" not in schema.EDGES_COMPUTE


STAR = {
    "pkg/fields.py": "class FileField:\n    def clean(self):\n        return 1\n\n\ndef _private():\n    return 0\n",
    "pkg/__init__.py": "from pkg.fields import *\n",
    "ops/models.py": "class CreateModel:\n    def state_forwards(self):\n        return 1\n",
    "ops/__init__.py": "from ops.models import CreateModel\n",
    "mig/__init__.py": "from ops import *\n",
}


def test_star_and_multi_hop_reexports(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            **STAR,
            "use.py": "from pkg import FileField\nimport mig\n\n\n"
            "def f():\n    x = FileField()\n    x.clean()\n    op = mig.CreateModel()\n    return op.state_forwards()\n",
        },
    )
    assert edges_at(root, "use.py", "clean") == [("pkg/fields.py", "FileField.clean", "typed")]
    assert edges_at(root, "use.py", "state_forwards") == [("ops/models.py", "CreateModel.state_forwards", "typed")]


def test_star_reexports_refuse_two_sources_and_private_names(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "a.py": "class Foo:\n    def m(self):\n        return 1\n",
            "b.py": "class Foo:\n    def m(self):\n        return 2\n",
            "two/__init__.py": "from a import *\nfrom b import *\n",
            "p.py": "class _Hidden:\n    def go(self):\n        return 1\n",
            "q/__init__.py": "from p import *\n",
            "use.py": "from two import Foo\nfrom q import _Hidden\n\n\n"
            "def f():\n    x = Foo()\n    h = _Hidden()\n    h.go()\n    return x.m()\n",
        },
    )
    assert "typed" not in {r[2] for r in edges_at(root, "use.py", "m")}
    assert "typed" not in {r[2] for r in edges_at(root, "use.py", "go")}  # a star doesn't export _names


def test_cyclic_star_imports_terminate(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "a/__init__.py": "from b import *\n\n\nclass A:\n    def m(self):\n        return 1\n",
            "b/__init__.py": "from a import *\n",
            "use.py": "from b import A\n\n\ndef f():\n    x = A()\n    return x.m()\n",
        },
    )
    assert edges_at(root, "use.py", "m") == [("a/__init__.py", "A.m", "typed")]


def test_call_result_receivers_and_two_argument_super(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": BASE,
            "other.py": OTHER,
            "ext_use.py": "from extlib import Ext\n\n\ndef g():\n    return Ext().run()\n",
            "use.py": "import other\nfrom base import Base\n\n\n"
            "class Child(Base):\n    def get(self):\n        return super(Child, self).get()\n\n\n"
            "def f():\n    Base().run()\n    return other.Other(1).get()\n",
        },
    )
    assert edges_at(root, "use.py", "run") == [("base.py", "Base.run", "typed")]
    assert edges_at(root, "use.py", "get") == [("base.py", "Base.get", "typed"), ("other.py", "Other.get", "typed")]
    assert [r[2] for r in edges_at(root, "ext_use.py", "run")] == ["unresolved"]


def test_inferred_returns(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": BASE,
            "other.py": OTHER,
            "mk.py": "from base import Base\nfrom other import Other\n\n\n"
            "def make():\n    return Base()\n\n\n"
            "def via_local(c):\n    dk = Base()\n    if c:\n        return None\n    return dk\n\n\n"
            "def mixed(c):\n    if c:\n        return Base()\n    return Other()\n\n\n"
            "def gen():\n    yield Base()\n\n\n"
            "def rec():\n    return rec()\n\n\n"
            "def rebound():\n    x = Base()\n    x = Other()\n    return x\n",
            "use.py": "from mk import make, via_local, mixed, gen, rec, rebound\n\n\n"
            "def f():\n    a = make()\n    a.run()\n    b = via_local(1)\n    b.get()\n"
            "    c = mixed(1)\n    c.run()\n    d = gen()\n    d.get()\n    e = rec()\n    e.run()\n"
            "    g = rebound()\n    return g.get()\n",
        },
    )
    typed = {(r[0], r[1]) for n in ("run", "get") for r in edges_at(root, "use.py", n) if r[2] == "typed"}
    assert typed == {("base.py", "Base.run"), ("base.py", "Base.get")}
    lines = rows(root, "SELECT line FROM edges WHERE src_path = 'use.py' AND resolution = 'typed' ORDER BY line")
    assert lines == [(6,), (8,)]


def test_classes_defined_in_the_calling_function(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": BASE,
            "t.py": "from base import Base\n\n\n"
            "def test_a():\n    class Admin(Base):\n        pass\n\n    ma = Admin()\n    ma.run()\n    return Admin.get(ma)\n\n\n"
            "def test_b():\n    class Admin(Base):\n        pass\n\n    return Admin().get()\n\n\n"
            "def test_twice(c):\n    if c:\n        class Two(Base):\n            pass\n    else:\n"
            "        class Two(Base):\n            pass\n    return Two().run()\n\n\n"
            "def test_early():\n    x = Late()\n    x.run()\n\n    class Late(Base):\n        pass\n",
        },
    )
    got = rows(
        root, "SELECT line, dst_qualname FROM edges WHERE src_path = 't.py' AND resolution = 'typed' ORDER BY line"
    )
    assert got == [(9, "Base.run"), (10, "Base.get"), (17, "Base.get")]


def test_one_attribute_hop(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": BASE,
            "other.py": OTHER,
            "bot.py": "from base import Base\nfrom other import Other\n\n\n"
            "class Bot:\n    objects = Other()\n\n    def __init__(self):\n        self.exchange = Base()\n",
            "sig.py": "from base import Base\n\npost_save = Base()\n",
            "use.py": "import sig\nfrom bot import Bot\n\n\n"
            "class Svc:\n    def __init__(self):\n        self.bot = Bot()\n\n"
            "    def go(self):\n        return self.bot.exchange.run()\n\n\n"
            "def f():\n    b = Bot()\n    b.exchange.get()\n    Bot.objects.run()\n    return sig.post_save.get()\n",
        },
    )
    got = rows(
        root, "SELECT line, dst_qualname FROM edges WHERE src_path = 'use.py' AND resolution = 'typed' ORDER BY line"
    )
    assert got == [(10, "Base.run"), (15, "Base.get"), (16, "Other.run"), (17, "Base.get")]


def test_class_body_aliases(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": BASE,
            "other.py": OTHER,
            "views.py": "from base import Base\nfrom other import Other\n\n\n"
            "class View:\n    form_class = Base\n    k_class = Other\n\n"
            "    def __init__(self):\n        self.k_class = None\n\n"
            "    def go(self):\n        form = self.form_class()\n        form.run()\n"
            "        self.form_class().get()\n        self.form_class.get(form)\n"
            "        return self.k_class().run()\n\n\n"
            "class Sub(View):\n    def more(self):\n        return self.form_class().run()\n",
        },
    )
    got = rows(
        root, "SELECT line, dst_qualname FROM edges WHERE src_path = 'views.py' AND resolution = 'typed' ORDER BY line"
    )
    assert got == [(14, "Base.run"), (15, "Base.get"), (16, "Base.get"), (22, "Base.run")]


def test_method_return_through_a_typed_receiver(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": BASE,
            "other.py": OTHER,
            "eng.py": "from other import Other\n\n\nclass Engine:\n    def make(self) -> Other:\n        return Other()\n\n"
            "    def plain(self):\n        return Other()\n",
            "use.py": "from eng import Engine\n\n\nclass T:\n    def go(self):\n        e = Engine()\n"
            "        t = e.make()\n        t.run()\n        p = e.plain()\n        return p.get()\n",
        },
    )
    got = rows(
        root, "SELECT line, dst_qualname FROM edges WHERE src_path = 'use.py' AND resolution = 'typed' ORDER BY line"
    )
    assert (8, "Other.run") in got and (10, "Other.get") in got


def test_self_method_return_and_the_stage_cap(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": BASE,
            "eng.py": "from base import Base\n\n\nclass Engine:\n    def make(self):\n        return Base()\n",
            "use.py": "from eng import Engine\n\n\nclass T:\n    def _engine(self):\n        return Engine()\n\n"
            "    def go(self):\n        e = self._engine()\n        e.make()\n        t = e.make()\n        return t.run()\n",
        },
    )
    got = rows(
        root, "SELECT line, dst_qualname FROM edges WHERE src_path = 'use.py' AND resolution = 'typed' ORDER BY line"
    )
    assert got == [(10, "Engine.make"), (11, "Engine.make")]  # t.run() would need a third stage: none


SCOPE_CLASS = {  # a binding in one class's method, used inside a class nested in that method: the binding's class
    "self.make() bound outside a nested class that has its own make": (
        {
            "p.py": "class Part:\n    def run(self):\n        return 1\n\n\nclass Other:\n    def run(self):\n        return 2\n",
            "e.py": "from p import Part, Other\n\n\nclass Engine:\n    def make(self):\n        return Part()\n\n"
            "    def build(self):\n        part = self.make()\n\n        class Local:\n            def make(self):\n"
            "                return Other()\n\n            def go(self):\n                return part.run()\n\n"
            "        return Local\n",
        },
        "e.py",
        "run",
        "Other.run",
    ),
    "self.form_class() bound outside a nested class that has its own alias": (
        {
            "p.py": "class A:\n    def m(self):\n        return 1\n\n\nclass B:\n    def m(self):\n        return 2\n",
            "v.py": "from p import A, B\n\n\nclass View:\n    form_class = A\n\n    def go(self):\n"
            "        form = self.form_class()\n\n        class Local:\n            form_class = B\n\n"
            "            def run(self):\n                return form.m()\n\n        return Local\n",
        },
        "v.py",
        "m",
        "B.m",
    ),
}


@pytest.mark.parametrize("case", sorted(SCOPE_CLASS))
def test_a_binding_is_typed_in_its_own_class_not_the_callers(tmp_path, case):
    files, path, name, wrong = SCOPE_CLASS[case]
    root = make_repo(tmp_path / "r", files)
    assert wrong not in {r[1] for r in edges_at(root, path, name) if r[2] == "typed"}


def test_a_local_class_bound_before_its_class_line_is_not_inferred(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": BASE,
            "t.py": "from base import Base\n\n\ndef f():\n    x = Late()\n\n    class Late(Base):\n        pass\n\n"
            "    return x.run()\n",
        },
    )
    assert "typed" not in {r[2] for r in edges_at(root, "t.py", "run")}


def test_an_attribute_bound_through_a_class_alias(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": BASE,
            "t.py": "from base import Base\n\n\nclass T:\n    storage_class = Base\n\n    def setUp(self):\n"
            "        self.storage = self.storage_class()\n\n    def test(self):\n        return self.storage.run()\n",
        },
    )
    assert edges_at(root, "t.py", "run") == [("base.py", "Base.run", "typed")]


def test_a_class_body_assignment_overrides_an_inherited_method(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "base.py": BASE,
            "other.py": OTHER,
            "eng.py": "from base import Base\n\n\ndef other_make():\n    return None\n\n\nclass Root:\n    def make(self):\n"
            "        return Base()\n\n\nclass Child(Root):\n    make = other_make\n",
            "use.py": "from eng import Child\n\n\ndef f():\n    c = Child()\n    c.make()\n    x = c.make()\n    return x.run()\n",
        },
    )
    assert "typed" not in {r[2] for n in ("make", "run") for r in edges_at(root, "use.py", n)}


def test_a_class_rewrapping_its_own_method_keeps_it(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "qs.py": "class QuerySet:\n    def as_manager(cls):\n        return 1\n\n    as_manager = classmethod(as_manager)\n",
            "use.py": "from qs import QuerySet\n\n\nclass Mine(QuerySet):\n    pass\n\n\ndef f():\n    return Mine.as_manager()\n",
        },
    )
    assert ("qs.py", "QuerySet.as_manager", "typed") in edges_at(root, "use.py", "as_manager")
