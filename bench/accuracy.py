"""How right is the call graph? Compare duckgrep's edges with jedi's goto-definition on sampled Python calls.

jedi does real (static) type inference, so it is a fair, independent reference for
"which definition does this call reach". We only score references jedi resolves.

usage: python bench/accuracy.py <repo> [n_samples]
"""

import collections
import os
import random
import sys

import jedi

from duckgrep import query as q


def to_repo_path(module_path, root, repo_files):
    """Map a jedi module path to a repo file. An installed copy of the repo's own package
    (site-packages/requests/api.py when the repo has src/requests/api.py) counts as the repo."""
    if module_path is None:
        return None
    mp = str(module_path)
    if mp.startswith(root + os.sep):
        return os.path.relpath(mp, root)
    if "site-packages/" in mp:
        tail = mp.split("site-packages/", 1)[1]
        for f in repo_files:
            if f == tail or f.endswith("/" + tail):
                return f
    return None


def main(root, n=300, seed=0):
    root = os.path.abspath(root)
    refs = q.run(
        root,
        """
        SELECT src_path, line, col, name, receiver,
               list(DISTINCT resolution) AS res,
               list((dst_path, dst_qualname, dst_line)) FILTER (WHERE dst_path IS NOT NULL) AS dst
        FROM edges e JOIN files f ON f.path = e.src_path
        WHERE ref_kind = 'call' AND f.lang = 'python'
        GROUP BY ALL
    """,
        max_rows=10_000_000,
    ).rows
    random.Random(seed).shuffle(refs)
    # make jedi resolve the repo's own packages to the repo, not to an installed copy
    extra = [os.path.join(root, d) for d in ("src", "lib") if os.path.isdir(os.path.join(root, d))]
    # jedi bundles django-stubs and answers with them for the repo's own django: the reference must be the
    # repo's source, so point the stub path at nothing (harmless for repos that only use django)
    import pathlib

    import jedi.inference.gradual.typeshed as typeshed

    typeshed.DJANGO_INIT_PATH = pathlib.Path("/nonexistent/django-stubs/__init__.pyi")
    project = jedi.Project(root, added_sys_path=extra)
    scripts = {}
    ranges = {}  # (path, name) -> [(start, end, qualname)]
    for p, nm, s, e, qn in q.run(
        root, "SELECT path, name, start_line, end_line, qualname FROM symbols", max_rows=10_000_000, fresh=False
    ).rows:
        ranges.setdefault((p, nm), []).append((s, e, qn))

    repo_files = {r[0] for r in q.run(root, "SELECT path FROM files", max_rows=10_000_000, fresh=False).rows}
    stats = collections.Counter()
    by_tier = collections.defaultdict(collections.Counter)
    examples = []
    scored = 0
    for path, line, col, name, receiver, res, dst in refs:
        if scored >= n:
            break
        script = scripts.get(path)
        if script is None:
            script = scripts[path] = jedi.Script(path=os.path.join(root, path), project=project)
        try:
            gs = script.goto(line, col, follow_imports=True)
        except Exception:
            continue
        truth = set()
        external = False
        for g in gs:
            rel = to_repo_path(g.module_path, root, repo_files)
            if rel is None or g.line is None:
                external = True
                continue
            for s, e, qn in ranges.get((rel, g.name), []):
                if s <= g.line <= e:
                    truth.add((rel, qn))
        if not truth and not external:
            continue  # jedi could not resolve it either
        scored += 1
        tier = res[0] if len(res) == 1 else "+".join(sorted(res))
        predicted = {(d[0], d[1]) for d in (dst or [])}
        if not truth:
            stats["external"] += 1
            by_tier["external(jedi)"][tier] += 1
            continue
        stats["in_repo"] += 1
        hit = bool(truth & predicted)
        exact = hit and len(predicted) == 1
        by_tier[tier]["n"] += 1
        by_tier[tier]["hit"] += hit
        by_tier[tier]["exact"] += exact
        by_tier[tier]["cands"] += len(predicted)
        if tier not in ("name", "ambiguous", "unresolved") and not hit and len(examples) < 8:
            examples.append((path, line, name, receiver, sorted(truth)[:2], sorted(predicted)[:2]))

    print(f"repo: {root}  scored calls: {scored}  (in-repo target: {stats['in_repo']}, external: {stats['external']})")
    print(f"\n{'duckgrep tier':12s} {'share':>6s} {'contains jedi target':>21s} {'exactly it':>11s} {'avg cands':>10s}")
    confident = collections.Counter()
    for tier, c in sorted(by_tier.items(), key=lambda kv: -kv[1]["n"]):
        if tier == "external(jedi)":
            continue
        n_ = c["n"]
        print(
            f"{tier:12s} {n_ / stats['in_repo']:6.1%} {c['hit'] / n_:21.1%} {c['exact'] / n_:11.1%} "
            f"{c['cands'] / n_:10.1f}"
        )
        if tier in ("self", "local", "import", "module", "qualified", "package", "typed"):
            confident.update(c)
    if confident["n"]:
        print(
            f"\nconfident tiers: {confident['n'] / stats['in_repo']:.1%} of in-repo calls, "
            f"precision {confident['exact'] / confident['n']:.1%}"
        )
    ext = by_tier["external(jedi)"]
    if ext:
        tot = sum(ext.values())
        print(
            "calls jedi resolves outside the repo -> duckgrep: "
            + ", ".join(f"{k} {v / tot:.0%}" for k, v in ext.most_common())
        )
    if examples:
        print("\nconfident misses:")
        for ex in examples:
            print("  ", ex)


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 300)
