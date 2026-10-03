"""Step 4: for the strict rule's in-repo resolutions, how often (a) the target lives in another file
than the call (a cross-file dependency the incremental dirty-marking must track) and (b) some
in-repo subclass of the receiver's static class overrides the method (the declared type's method may not be
the runtime one). Subclasses are matched by class name through refs(kind='inherit'), transitively.

usage (from /Users/ccf/git/duckgrep): uv run python overrides.py <repo> <ex-repo.txt.rows.jsonl>
"""

import collections
import json
import sys

from duckgrep import query as q


def main(root, rows_path):
    rows = [json.loads(ln) for ln in open(rows_path)]
    inh = q.run(
        root,
        "SELECT name, scope_class FROM refs WHERE kind = 'inherit' AND scope_class IS NOT NULL",
        max_rows=10_000_000,
        fresh=False,
    ).rows
    subs = collections.defaultdict(set)  # base short name -> subclass qualnames
    for base, sub in inh:
        subs[base].add(sub)
    meth = collections.defaultdict(set)  # class qualname -> method names
    for parent, name in q.run(
        root, "SELECT parent, name FROM symbols WHERE parent IS NOT NULL", max_rows=10_000_000, fresh=False
    ).rows:
        meth[parent].add(name)

    def descendants(cls):
        out, todo = set(), [cls]
        while todo:
            c = todo.pop()
            for s in subs.get(c.split(".")[-1], ()):
                if s not in out:
                    out.add(s)
                    todo.append(s)
        return out

    by = collections.defaultdict(collections.Counter)
    for r in rows:
        v = r["strict"]
        if not v or v == "ext" or r["tier"] not in ("name", "ambiguous"):
            continue
        qn = v[2]
        cls, _, m = qn.rpartition(".")
        c = by[r["cls"]]
        c["n"] += 1
        c["xfile"] += v[1] != r["path"]
        rt = r.get("rtype")
        # dispatch can reach an override only below the receiver's static class (super() is exact)
        if rt and rt[0] == "cls" and rt[1] and any(m in meth.get(d, ()) for d in descendants(rt[1])):
            c["overridden"] += 1
    tot = collections.Counter()
    print(f"{'class':40s} {'resolved':>8s} {'other file':>10s} {'overridden in a subclass':>25s}")
    for k, c in sorted(by.items(), key=lambda kv: -kv[1]["n"]):
        tot.update(c)
        print(f"{k[:40]:40s} {c['n']:8d} {c['xfile'] / c['n']:10.1%} {c['overridden'] / c['n']:25.1%}")
    print(f"{'TOTAL':40s} {tot['n']:8d} {tot['xfile'] / tot['n']:10.1%} {tot['overridden'] / tot['n']:25.1%}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
