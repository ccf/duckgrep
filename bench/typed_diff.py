"""Per-rule precision gate: Python call refs whose confident targets differ between two indexes of one repo,
sampled and checked against jedi's goto-definition.

usage: python bench/typed_diff.py <repo> <before.duckdb> <after.duckdb> [n]
"""

import os
import random
import sys

import duckdb

CONFIDENT = "resolution NOT IN ('name', 'ambiguous', 'unresolved')"
QUERY = f"""
    SELECT e.src_path, e.line, e.col, e.name, list((e.dst_path, e.dst_qualname) ORDER BY e.dst_path, e.dst_qualname)
    FROM edges e JOIN files f ON f.path = e.src_path
    WHERE e.ref_kind = 'call' AND f.lang = 'python' AND {CONFIDENT}
    GROUP BY ALL
"""


def _targets(db):
    con = duckdb.connect(db, read_only=True)
    try:
        return {(p, ln, c, n): [tuple(t) for t in ts] for p, ln, c, n, ts in con.execute(QUERY).fetchall()}
    finally:
        con.close()


def changed_targets(before_db, after_db):
    """[(path, line, col, name, before targets, after targets)] for refs whose confident targets differ."""
    b, a = _targets(before_db), _targets(after_db)
    return [(*k, b.get(k, []), a.get(k, [])) for k in sorted(set(b) | set(a)) if b.get(k, []) != a.get(k, [])]


def main(root, before_db, after_db, n=30, seed=0):
    import jedi

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from accuracy import to_repo_path

    root = os.path.abspath(root)
    diff = changed_targets(before_db, after_db)
    gained = sum(1 for d in diff if not d[4])
    lost = sum(1 for d in diff if not d[5])
    print(f"gained {gained}, changed {len(diff) - gained - lost}, lost {lost}")
    with_target = [d for d in diff if d[5]]
    random.Random(seed).shuffle(with_target)
    con = duckdb.connect(after_db, read_only=True)
    ranges = {}
    for p, nm, s, e, qn in con.execute("SELECT path, name, start_line, end_line, qualname FROM symbols").fetchall():
        ranges.setdefault((p, nm), []).append((s, e, qn))
    repo_files = {r[0] for r in con.execute("SELECT path FROM files").fetchall()}
    con.close()
    # as accuracy.py: the repo's own packages resolve to the repo, and jedi's bundled django stubs are off
    import pathlib

    import jedi.inference.gradual.typeshed as typeshed

    typeshed.DJANGO_INIT_PATH = pathlib.Path("/nonexistent/django-stubs/__init__.pyi")
    extra = [os.path.join(root, d) for d in ("src", "lib") if os.path.isdir(os.path.join(root, d))]
    project = jedi.Project(root, added_sys_path=extra)
    agree = scored = 0
    for path, line, col, name, _before, after in with_target:
        if scored >= n:
            break
        try:
            gs = jedi.Script(path=os.path.join(root, path), project=project).goto(line, col, follow_imports=True)
        except Exception:
            continue
        truth = set()
        for g in gs:
            rel = to_repo_path(g.module_path, root, repo_files)
            for s, e, qn in ranges.get((rel, g.name), []) if rel and g.line else []:
                if s <= g.line <= e:
                    truth.add((rel, qn))
        if not truth:
            continue
        scored += 1
        ok = bool(truth & set(after))
        agree += ok
        if not ok:
            print(f"  DISAGREE {path}:{line} {name}: duckgrep {after} jedi {sorted(truth)}")
    print(f"agreement: {agree}/{scored}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 30)
