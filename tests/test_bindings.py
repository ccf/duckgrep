"""Binding facts for type inference (Python): normalisation and extraction, per file."""

import pytest

from duckgrep.bindings import extract_bindings, normalize_type
from duckgrep.extract import get_parser


@pytest.mark.parametrize(
    "text, cls, want",
    [
        ("Foo", None, "Foo"),
        ("mod.Foo", None, "mod.Foo"),
        ('"Foo"', None, "Foo"),
        ("Optional[Foo]", None, "Foo"),
        ("typing.Optional['Foo']", None, "Foo"),
        ("Foo | None", None, "Foo"),
        ("None | Foo", None, "Foo"),
        ("Union[Foo, None]", None, "Foo"),
        ("Union[Foo, Bar]", None, None),
        ("Foo | Bar", None, None),
        ("Annotated[Foo, 'x']", None, "Foo"),
        ("type[Foo]", None, "Foo"),
        ("ClassVar[Foo]", None, "Foo"),
        ("list[Foo]", None, None),
        ("Self", "Engine", "Engine"),
        ("Self", None, None),
        ("None", None, None),
        ("Callable[[int], Foo]", None, None),
    ],
)
def test_normalize_type(text, cls, want):
    assert normalize_type(text, cls) == want


def test_generic_heads_name_the_base():
    assert normalize_type("Base[T]", generic_head=True) == "Base"
    assert normalize_type("Base[T]") is None


def bind(src, path="m.py"):
    tree = get_parser("python").parse(src.encode())
    return sorted(extract_bindings(tree.root_node, src.encode(), path))


def test_locals_params_and_module_level():
    rows = bind(
        "x = Foo()\n"
        "def f(a, b: 'Bar', *args, c: int = 1, **kw) -> None:\n"
        "    y = mod.make(a)\n"
        "    z: Baz = get()\n"
        "    w = []\n"
        "    v = y\n"
    )
    assert ("m.py", "", "x", "assign", "call:Foo", 1, 0) in rows
    assert ("m.py", "f", "a", "param", None, 2, 0) in rows
    assert ("m.py", "f", "b", "param", "Bar", 2, 0) in rows
    assert ("m.py", "f", "args", "param", None, 2, 0) in rows
    assert ("m.py", "f", "c", "param", "int", 2, 0) in rows
    assert ("m.py", "f", "kw", "param", None, 2, 0) in rows
    assert ("m.py", "f", "y", "assign", "call:mod.make", 3, 0) in rows
    assert ("m.py", "f", "z", "annot", "Baz", 4, 0) in rows
    assert ("m.py", "f", "w", "assign", "list", 5, 0) in rows
    assert ("m.py", "f", "v", "assign", None, 6, 0) in rows


def test_classes_bases_and_attributes():
    rows = bind(
        "class Svc(Base, mod.Mixin, Generic[T], metaclass=Meta):\n"
        "    conn: Conn\n"
        "    pool = Pool()\n"
        "    def __init__(self, c: Conn, n):\n"
        "        self.c = c\n"
        "        self.e = Engine()\n"
        "        self.n = n\n"
        "        self.a, self.b = 1, 2\n"
        "    def make(self) -> 'Self':\n"
        "        return self\n"
    )
    assert ("m.py", "Svc", "", "base", "Base", 1, 0) in rows
    assert ("m.py", "Svc", "", "base", "mod.Mixin", 1, 1) in rows
    assert not [r for r in rows if r[3] == "base" and r[4] in ("Generic", "Meta")]
    assert ("m.py", "Svc", "self.conn", "attr", "Conn", 2, 0) in rows
    assert ("m.py", "Svc", "self.pool", "attr", "call:Pool", 3, 0) in rows
    assert ("m.py", "Svc", "self.c", "attr", "Conn", 5, 0) in rows
    assert ("m.py", "Svc", "self.e", "attr", "call:Engine", 6, 0) in rows
    assert ("m.py", "Svc", "self.n", "attr", None, 7, 0) in rows
    assert ("m.py", "Svc", "self.a", "attr", None, 8, 0) in rows
    assert ("m.py", "Svc.__init__", "c", "param", "Conn", 4, 0) in rows


def test_untyped_bindings_are_recorded_to_block_inference():
    rows = bind(
        "def f(items):\n"
        "    for x in items:\n"
        "        pass\n"
        "    with open('p') as fh, lock as (a, b):\n"
        "        pass\n"
        "    try:\n"
        "        pass\n"
        "    except ValueError as e:\n"
        "        pass\n"
        "    if (n := len(items)):\n"
        "        pass\n"
        "    ys = [y for y in items]\n"
        "    x += 1\n"
        "    import os\n"
        "    global G\n"
    )
    names = {(r[2], r[4]) for r in rows if r[1] == "f"}
    for nm in ("x", "fh", "a", "b", "e", "n", "y", "os", "G"):
        assert (nm, None) in names, nm
    assert ("G", None) in names and any(r[2] == "G" and r[3] == "global" for r in rows)


def test_nested_scopes_use_walker_qualnames():
    rows = bind("class A:\n    class B:\n        def m(self):\n            q = Q()\n")
    assert ("m.py", "A.B.m", "q", "assign", "call:Q", 4, 0) in rows


def test_bindings_survive_parse_errors():
    rows = bind("def f(:\n    pass\n\ndef g():\n    x = Foo()\n")
    assert ("m.py", "g", "x", "assign", "call:Foo", 5, 0) in rows
