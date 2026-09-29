"""Agent-style questions answered with ripgrep vs with duckgrep, on a real repo.

For each question we count tool calls and output bytes (≈ tokens × 4) an agent would
consume. The grep side is simulated generously: every rg call is exact, and "read the
file to find the enclosing function" is counted as one call per distinct file (its bytes
are not counted, which flatters grep).

usage: python bench/vs_grep.py <django-checkout>
"""

import subprocess
import sys

from duckgrep import query as q


def rg(root, *args):
    out = subprocess.run(["rg", "-n", "--no-heading", *args], cwd=root, capture_output=True, text=True).stdout
    return [ln for ln in out.splitlines() if ln]


def dg(root, sql):
    res = q.run(root, sql, max_rows=100_000, fresh=False)
    return res, len(q.format_tsv(res).encode())


def enclosing(root, hits):
    """(path, line) -> enclosing qualname, as an agent would learn by opening the file."""
    if not hits:
        return {}
    vals = ",".join(f"('{p}', {line})" for p, line in hits)
    rows = q.run(
        root,
        f"""
        WITH h(path, line) AS (VALUES {vals})
        SELECT h.path, h.line, s.qualname FROM h LEFT JOIN symbols s
          ON s.path = h.path AND h.line BETWEEN s.start_line AND s.end_line
        QUALIFY row_number() OVER (PARTITION BY h.path, h.line ORDER BY s.start_line DESC NULLS LAST) = 1
    """,
        max_rows=10**7,
        fresh=False,
    ).rows
    return {(p, line): qn for p, line, qn in rows}


def read_bytes(root, hits):
    """Bytes an agent reads to see the enclosing function of each hit (each function once)."""
    if not hits:
        return 0
    vals = ",".join(f"('{p}', {line})" for p, line in hits)
    return q.run(
        root,
        f"""
        WITH h(path, line) AS (VALUES {vals}),
        enc AS (
            SELECT DISTINCT s.path, s.start_line, s.end_line FROM h JOIN symbols s
              ON s.path = h.path AND h.line BETWEEN s.start_line AND s.end_line
            QUALIFY row_number() OVER (PARTITION BY h.path, h.line ORDER BY s.start_line DESC) = 1)
        SELECT coalesce(sum(length(l.text) + 1), 0) FROM enc JOIN lines l
          ON l.path = enc.path AND l.line BETWEEN enc.start_line AND enc.end_line
    """,
        fresh=False,
    ).rows[0][0]


def parse_hits(lines):
    out = []
    for ln in lines:
        p, line, _ = ln.split(":", 2)
        out.append((p, int(line)))
    return out


def main(root):
    report = []

    # 1. where is it defined
    g = rg(root, r"def get_or_create\b")
    res, b = dg(root, "SELECT * FROM defs('get_or_create')")
    report.append(
        (
            "Where is get_or_create defined?",
            1,
            sum(len(x) + 1 for x in g),
            f"{len(g)} matching lines",
            1,
            b,
            f"{res.total} defs with signature + qualname",
        )
    )

    # 2. who calls it, from which function
    g = rg(root, r"get_or_create\(")
    hits = parse_hits(g)
    py_hits = [h for h in hits if h[0].endswith(".py")]
    call_lines = {
        (p, line)
        for p, line in q.run(
            root,
            "SELECT DISTINCT path, line FROM refs WHERE name = 'get_or_create' AND kind = 'call'",
            max_rows=10**6,
            fresh=False,
        ).rows
    }
    real = sum(1 for h in hits if h in call_lines)
    files = len({p for p, _ in py_hits})
    res, b = dg(root, "SELECT * FROM callers('get_or_create')")
    report.append(
        (
            "Who calls get_or_create, from which function?",
            1 + files,
            sum(len(x) + 1 for x in g) + read_bytes(root, py_hits),
            f"{len(hits)} hits, {real} are call sites ({len(hits) - real} docs/comments/defs); "
            f"+{files} file reads for the enclosing function",
            1,
            b,
            f"{res.total} call sites with caller",
        )
    )

    # 3. what does a function call
    res, b = dg(root, "SELECT * FROM callees('SQLCompiler.execute_sql')")
    src, sb = dg(root, "SELECT * FROM source('SQLCompiler.execute_sql')")
    names = sorted({r[2] for r in res.rows})
    lookup = sum(sum(len(x) + 1 for x in rg(root, "--type", "py", rf"(def|class) {n}\b")) for n in names)
    report.append(
        (
            "What does SQLCompiler.execute_sql call, and where are those defined?",
            1 + len(names),
            sb + lookup,
            f"read the function, then one rg per callee name ({len(names)})",
            1,
            b,
            f"{res.total} refs with targets",
        )
    )

    # 4. transitive callers, 3 hops
    target = "execute_sql"
    frontier, seen, calls, gbytes = {target}, set(), 0, 0
    for _ in range(3):
        nxt = set()
        for name in sorted(frontier - seen):
            seen.add(name)
            lines = rg(root, "-w", "--type", "py", name.rsplit(".", 1)[-1] + r"\(")
            calls += 1
            gbytes += sum(len(x) + 1 for x in lines)
            hs = parse_hits(lines)
            calls += len({p for p, _ in hs})
            gbytes += read_bytes(root, hs)
            nxt |= {qn for qn in enclosing(root, hs).values() if qn}
        frontier = nxt
    res, b = dg(
        root,
        f"""
        WITH RECURSIVE up(q, d) AS (
          SELECT 'SQLCompiler.{target}', 0
          UNION SELECT e.src_scope, d + 1 FROM edges e JOIN up ON e.dst_qualname = up.q
          WHERE d < 3 AND e.resolution NOT IN ('name', 'ambiguous', 'unresolved') AND e.src_scope <> '')
        SELECT q, min(d) AS hops FROM up GROUP BY q ORDER BY hops, q""",
    )
    loose = q.run(
        root,
        f"""
        WITH RECURSIVE up(q, d) AS (
          SELECT 'SQLCompiler.{target}', 0
          UNION SELECT e.src_scope, d + 1 FROM edges e JOIN up ON e.dst_qualname = up.q
          WHERE d < 3 AND e.resolution NOT IN ('ambiguous', 'unresolved') AND e.src_scope <> '')
        SELECT count(DISTINCT q) FROM up""",
        fresh=False,
    ).rows[0][0]
    report.append(
        (
            "Everything that reaches SQLCompiler.execute_sql within 3 hops",
            calls,
            gbytes,
            f"iterative rg -w + reading each hit's function; {len(seen)} names explored",
            1,
            b,
            f"{res.total} functions via confident edges; {loose} if name-only edges are followed too",
        )
    )

    # 5. dead code in a package
    n_funcs = q.run(
        root, "SELECT count(*) FROM symbols WHERE path LIKE 'django/utils/%' AND kind = 'function'", fresh=False
    ).rows[0][0]
    res, b = dg(
        root,
        """
        SELECT s.path, s.qualname FROM symbols s
        WHERE s.path LIKE 'django/utils/%' AND s.kind = 'function' AND NOT starts_with(s.name, '_')
          AND NOT EXISTS (SELECT 1 FROM refs r WHERE r.name = s.name AND r.kind NOT IN ('import', 'write'))""",
    )
    report.append(
        (
            "Public functions in django/utils never referenced anywhere",
            n_funcs,
            None,
            f"one rg per function ({n_funcs})",
            1,
            b,
            f"{res.total} candidates",
        )
    )

    print("| Question | grep: calls | grep: bytes | grep notes | duckgrep: calls | duckgrep: bytes | duckgrep result |")
    print("|---|---:|---:|---|---:|---:|---|")
    for qn, gc, gb, gn, dc, db, dn in report:
        print(f"| {qn} | {gc} | {gb if gb is not None else '—'} | {gn} | {dc} | {db} | {dn} |")


if __name__ == "__main__":
    main(sys.argv[1])
