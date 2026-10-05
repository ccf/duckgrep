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


def test_bindings_and_returns_are_stored_and_refreshed(tmp_path):
    from helpers import fresh_snapshot, make_repo, rows, snapshot, write

    root = make_repo(
        tmp_path / "r",
        {"a.py": "class Foo:\n    def make(self) -> 'Self':\n        return self\n\n\ndef f():\n    x = Foo()\n"},
    )
    assert rows(root, "SELECT scope, name, kind, type_text FROM bindings ORDER BY ALL") == [
        ("Foo.make", "self", "param", None),
        ("f", "x", "assign", "call:Foo"),
    ]
    assert rows(root, "SELECT qualname, returns FROM symbols WHERE returns IS NOT NULL") == [("Foo.make", "Foo")]
    write(root, "a.py", "def f():\n    x = Bar()\n")
    assert rows(root, "SELECT name, type_text FROM bindings") == [("x", "call:Bar")]
    tables = ("symbols", "bindings", "edges")
    assert snapshot(root, tables) == fresh_snapshot(root, tmp_path, tables)


def test_a_change_to_the_binding_rules_forces_a_reparse(tmp_path, monkeypatch):
    from duckgrep import bindings
    from duckgrep.index import extractor_version

    before = extractor_version()
    fake = tmp_path / "bindings.py"
    fake.write_text(open(bindings.__file__).read() + "\n# a rule changed\n")
    monkeypatch.setattr(bindings, "__file__", str(fake))
    assert extractor_version() != before


def test_a_deeply_nested_expression_does_not_stop_indexing(tmp_path):
    from helpers import make_repo, rows

    deep = "SQL = (" + " + ".join(['"part"'] * 5000) + ")\n"
    root = make_repo(
        tmp_path / "r",
        {"deep.py": deep + "\n\ndef f():\n    x = Foo()\n    return x\n", "ok.py": "def g():\n    y = Bar()\n"},
    )
    assert rows(root, "SELECT count(*) FROM files WHERE path = 'deep.py'") == [(1,)]
    assert ("y", "call:Bar") in rows(root, "SELECT name, type_text FROM bindings WHERE path = 'ok.py'")


def test_class_body_alias_of_a_dotted_name():
    rows = bind("class T:\n    form_class = forms.Base\n    other = Foo\n    n = 3\n    made = Foo()\n")
    assert ("m.py", "T", "self.form_class", "alias", "forms.Base", 2, 0) in rows
    assert ("m.py", "T", "self.other", "alias", "Foo", 3, 0) in rows
    assert ("m.py", "T", "self.form_class", "attr", None, 2, 0) in rows  # today's row stays
    assert not [r for r in rows if r[3] == "alias" and r[2] in ("self.n", "self.made")]


def test_receiver_that_is_one_call():
    rows = bind(
        "class C(B):\n"
        "    def m(self):\n"
        "        Foo(1, x=2).run()\n"
        "        mod.Foo('a').run()\n"
        "        super(C, self).run()\n"
        "        self.k(1).run()\n"
        "        Foo()[0].run()\n"
        "        f()().run()\n"
    )
    rc = sorted(r for r in rows if r[3] == "rcall")
    assert rc == [
        ("m.py", "C.m", "Foo(1, x=2)", "rcall", "call:Foo", 3, 20),
        ("m.py", "C.m", "mod.Foo('a')", "rcall", "call:mod.Foo", 4, 21),
        ("m.py", "C.m", "self.k(1)", "rcall", "call:self.k", 6, 18),
        ("m.py", "C.m", "super(C, self)", "rcall", "super:C", 5, 23),
    ]


def test_returns_of_unannotated_functions():
    rows = bind(
        "def a():\n    return Foo()\n\n"
        "def b():\n    x = Foo()\n    return x\n\n"
        "def c():\n    if p:\n        return None\n    return\n\n"
        "def d() -> Foo:\n    return make()\n\n"
        "def e():\n    yield Foo()\n    return Foo()\n\n"
        "def g():\n    def inner():\n        return Bar()\n    return 1 + 2\n"
    )
    ret = sorted(r[1:6] for r in rows if r[3] == "return")
    assert ret == [
        ("a", "a", "return", "call:Foo", 2),
        ("b", "b", "return", "var:x", 6),
        ("e", "e", "return", None, 17),
        ("g", "g", "return", None, 23),
        ("g.inner", "inner", "return", "call:Bar", 22),
    ]


def test_classes_defined_in_functions():
    rows = bind(
        "def t():\n    class Local(Base):\n        pass\n    if x:\n        class Two:\n            pass\n\n"
        "class Outer:\n    class Inner:\n        pass\n"
    )
    lc = sorted(r[1:5] for r in rows if r[3] == "local_class")
    assert lc == [("t", "Local", "local_class", "t.Local"), ("t", "Two", "local_class", "t.Two")]
