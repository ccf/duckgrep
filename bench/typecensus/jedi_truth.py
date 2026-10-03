"""Step 1 of the receiver-gap census: jedi goto-definition for every receiver call duckgrep
leaves at name / ambiguous / unresolved.

usage (from /Users/ccf/git/duckgrep):
  uv run --group bench python <scratch>/ti/jedi_truth.py <repo> <venv-for-jedi> <out.jsonl> [n|0=all] [workers]

Writes one JSON line per call site: the duckgrep row (path, line, col, name, receiver, tiers,
name candidates) plus jedi's raw goto results. census.py classifies and scores them.
"""

import json
import multiprocessing as mp
import os
import random
import signal
import sys
import time

from duckgrep import query as q

ROOT = ENV = None
EXTRA = []
_project = None


def gap_refs(root):
    return q.run(
        root,
        """
        SELECT src_path, line, col, name, receiver, src_scope,
               list(DISTINCT resolution) AS res,
               list((dst_path, dst_qualname)) FILTER (WHERE dst_path IS NOT NULL) AS dst,
               max(n_candidates) AS n_cand
        FROM edges e JOIN files f ON f.path = e.src_path
        WHERE ref_kind = 'call' AND f.lang = 'python' AND receiver IS NOT NULL
        GROUP BY ALL
        HAVING NOT list_has_any(list(resolution), ['self','local','package','import','module','qualified','typed'])
        """,
        max_rows=10_000_000,
    ).rows


class Timeout(Exception):
    pass


def _alarm(*_):
    raise Timeout()


def work(task):
    global _project
    import jedi

    if _project is None:
        if os.environ.get("NO_DJANGO_STUBS"):
            # jedi bundles django-stubs and serves them for the repo's own django: the reference
            # must be the repo's source, so point the stub path at nothing
            import pathlib

            import jedi.inference.gradual.typeshed as ts

            ts.DJANGO_INIT_PATH = pathlib.Path("/nonexistent/django-stubs/__init__.pyi")
        _project = jedi.Project(ROOT, added_sys_path=EXTRA, environment_path=ENV)
        signal.signal(signal.SIGALRM, _alarm)
    path, items = task
    out = []
    full = os.path.join(ROOT, path)
    try:
        src = open(full, encoding="utf-8", errors="replace").read().splitlines()
        script = jedi.Script(path=full, project=_project)
    except Exception as e:  # noqa: BLE001
        return [dict(it, jedi=None, err=f"script: {e!r}") for it in items]
    for it in items:
        line, col = it["line"], it["col"]
        # duckgrep columns are UTF-8 byte offsets; jedi wants code points
        try:
            text = src[line - 1]
            ccol = len(text.encode()[:col].decode(errors="ignore"))
        except IndexError:
            ccol = col
        t0 = time.time()
        rec = dict(it)
        try:
            signal.alarm(15)
            gs = script.goto(line, ccol, follow_imports=True)
            signal.alarm(0)
            rec["jedi"] = [
                {
                    "mp": str(g.module_path) if g.module_path else None,
                    "line": g.line,
                    "name": g.name,
                    "type": g.type,
                    "full_name": g.full_name,
                }
                for g in gs
            ]
        except Timeout:
            rec["jedi"] = None
            rec["err"] = "timeout"
        except Exception as e:  # noqa: BLE001
            signal.alarm(0)
            rec["jedi"] = None
            rec["err"] = repr(e)[:200]
        rec["t"] = round(time.time() - t0, 3)
        out.append(rec)
    return out


def main():
    global ROOT, ENV, EXTRA
    ROOT = os.path.abspath(sys.argv[1])
    ENV = os.path.abspath(sys.argv[2])
    out_path = sys.argv[3]
    n = int(sys.argv[4]) if len(sys.argv) > 4 else 0
    workers = int(sys.argv[5]) if len(sys.argv) > 5 else 12
    EXTRA = [os.path.join(ROOT, d) for d in ("src", "lib") if os.path.isdir(os.path.join(ROOT, d))]
    if os.path.isdir(os.path.join(ROOT, "tests")) and not os.path.exists(os.path.join(ROOT, "tests", "__init__.py")):
        EXTRA.append(os.path.join(ROOT, "tests"))  # django's runtests puts tests/ on sys.path
    rows = gap_refs(ROOT)
    print(f"gap receiver calls: {len(rows)}", file=sys.stderr)
    random.Random(0).shuffle(rows)
    if n:
        rows = rows[:n]
    by_file = {}
    for path, line, col, name, receiver, scope, res, dst, n_cand in rows:
        by_file.setdefault(path, []).append(
            dict(
                path=path,
                line=line,
                col=col,
                name=name,
                receiver=receiver,
                scope=scope,
                res=sorted(res),
                dst=[list(d) for d in (dst or [])],
                n_cand=n_cand,
            )
        )
    tasks = sorted(by_file.items(), key=lambda kv: -len(kv[1]))
    done = 0
    t0 = time.time()
    ctx = mp.get_context("fork")
    with open(out_path, "w") as f, ctx.Pool(workers) as pool:
        for recs in pool.imap_unordered(work, tasks):
            for r in recs:
                f.write(json.dumps(r) + "\n")
            done += len(recs)
            if done % 2000 < len(recs):
                print(f"{done}/{len(rows)} {time.time() - t0:.0f}s", file=sys.stderr)
    print(f"done {done} in {time.time() - t0:.0f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
