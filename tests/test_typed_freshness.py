"""Incremental edges equal a full rebuild after edits that change what a typed receiver resolves to."""

import random

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
    "child.py": [
        "from base import Base\n\n\nclass Child(Base):\n    pass\n",
        "from other import Other\n\n\nclass Child(Other):\n    def run(self):\n        return 5\n",
        "from base import Base\n\n\nclass Child(Base):\n    def __init__(self):\n        self.o = Base()\n",
    ],
    "inst.py": ["from child import Child\n\nshared = Child()\n", "from other import Other\n\nshared = Other()\n", ""],
    "make.py": [
        "from child import Child\n\n\ndef make() -> Child:\n    return Child()\n",
        "from grand import Grand\n\n\ndef make() -> 'Grand':\n    return Grand()\n",
    ],
}


def test_random_edit_sequences(tmp_path):
    rng = random.Random(20261003)
    files = {
        "grand.py": GRAND,
        "other.py": OTHER,
        **{k: v[0] for k, v in POOL.items()},
        "use.py": "from child import Child\nfrom inst import shared\nfrom make import make\n\n\n"
        "def f():\n    x = Child()\n    y = make()\n    x.run()\n    y.deep()\n    shared.run()\n"
        "    return x.deep()\n",
    }
    root = make_repo(tmp_path / "r", files)
    same(root, tmp_path)
    for _ in range(12):
        path = rng.choice(sorted(POOL))
        write(root, path, rng.choice(POOL[path]))
        same(root, tmp_path)
