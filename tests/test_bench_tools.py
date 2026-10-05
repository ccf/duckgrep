"""The benchmark helpers: a deterministic accuracy sample and the per-rule diff of confident targets."""

import os
import shutil
import sys

from helpers import make_repo, rows

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bench"))
import typed_diff  # noqa: E402


def test_changed_targets_lists_gained_changed_and_lost_confident_targets(tmp_path):
    root = make_repo(
        tmp_path / "r",
        {
            "a.py": "class A:\n    def m(self):\n        return 1\n",
            "b.py": "class B:\n    def m(self):\n        return 2\n",
            "use.py": "from a import A\n\n\ndef f():\n    x = A()\n    return x.m()\n",
        },
    )
    rows(root, "SELECT count(*) FROM edges")
    before = str(tmp_path / "before.duckdb")
    shutil.copy(os.path.join(root, ".duckgrep", "index.duckdb"), before)
    with open(os.path.join(root, "use.py"), "w") as f:
        f.write("from b import B\n\n\ndef f():\n    x = B()\n    return x.m()\n")
    rows(root, "SELECT count(*) FROM edges")
    after = os.path.join(root, ".duckgrep", "index.duckdb")
    got = {(p, n): (b, a) for p, _l, _c, n, b, a in typed_diff.changed_targets(before, after)}
    assert got[("use.py", "m")] == ([("a.py", "A.m")], [("b.py", "B.m")])


def test_accuracy_query_is_ordered():
    with open(os.path.join(os.path.dirname(__file__), "..", "bench", "accuracy.py")) as f:
        src = f.read()
    assert "ORDER BY src_path, line, col" in src


def test_agreement_needs_every_confident_target_to_be_jedis():
    assert typed_diff.agrees([("a.py", "A.m")], {("a.py", "A.m")})
    assert not typed_diff.agrees([("a.py", "A.m"), ("b.py", "B.m")], {("a.py", "A.m")})
    assert not typed_diff.agrees([], {("a.py", "A.m")})


def test_a_call_jedi_resolves_outside_the_repo_is_a_disagreement():
    assert typed_diff.judge([("a.py", "A.m")], set(), external=True) is False
    assert typed_diff.judge([("a.py", "A.m")], set(), external=False) is None  # jedi has no answer: not scored
    assert typed_diff.judge([("a.py", "A.m")], {("a.py", "A.m")}, external=False) is True
