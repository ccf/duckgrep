"""Step 3: merge census.py's fine classes into the spec's taxonomy and print markdown tables.

usage: python summarize.py ex-<repo>.txt.rows.jsonl [...]
"""

import collections
import json
import sys

MERGE = [
    ("self.m() inherited / external base", ["self.m() (inherited/external base)"]),
    ("super().m()", ["super().m()"]),
    ("x = Foo() local", ["x = Foo()"]),
    ("x = Foo() module-level, same file", ["module-level x = Foo()"]),
    ("imported module-level instance", ["imported module-level instance"]),
    ("x = Foo.factory()", ["x = Foo.factory() [annotated]", "x = Foo.factory() [unannotated]"]),
    (
        "x = f() / obj.m(), annotated return",
        ["x = f() [annotated return]", "x = obj.method() [annotated]", "module-level x = f() [annotated return]"],
    ),
    (
        "x = f() / obj.m(), unannotated",
        ["x = f() [unannotated]", "x = obj.method() [unannotated]", "module-level x = f() [unannotated]"],
    ),
    ("param annotated", ["param annotated"]),
    ("x: T annotated local", ["x: T annotated local", "module-level x: T annotated local"]),
    ("param unannotated", ["param unannotated"]),
    ("param unannotated, pytest fixture", ["param unannotated (pytest fixture)"]),
    ("self.attr.m()", ["self.attr.m()"]),
    ("attribute chain a.b.m()", ["attribute chain a.b.m()"]),
    ("call result f().m()", ["call result f().m()"]),
    ("loop / with / comprehension / except var", ["loop/with/comprehension var", "except var"]),
    (
        "x = external call / ext_obj.m()",
        [
            "x = external call",
            "module-level x = external call",
            "x = ext_obj.method()",
            "module-level x = ext_obj.method()",
        ],
    ),
    ("literal (receiver or x = literal)", ["x = literal", "module-level x = literal", "literal receiver"]),
    ("external import / builtin receiver", ["external import receiver", "builtin name receiver"]),
    ("imported module receiver (re-export chain)", ["imported module receiver"]),
    ("class receiver Foo.m()", ["class receiver (Foo.m())"]),
    ("x = call of unknown callee", ["x = call of unknown callee", "module-level x = call of unknown callee"]),
    ("tuple unpack a, x = ...", ["x unpacked (a, x = ...)"]),
    ("subscript x[i].m()", ["subscript x[i].m()"]),
]
FINE2 = {f: c for c, fs in MERGE for f in fs}


def score(r, L):
    v = r[L]
    if v is None:
        return None
    if r["jedi"] in ("none", "in-unmapped"):
        return "unv-ext" if v == "ext" else "unv"
    if v == "ext":
        return "xok" if r["jedi"] == "ext" else "xbad"
    if r["jedi"] == "ext":
        return "bad"
    return "ok" if (v[1], v[2]) in {tuple(t) for t in r["truth"]} else "bad"


def pct(a, b):
    return (f"{100 * a / b:.1f}%" if a != b else "100%") if b else "–"


def main(path):
    rows = [json.loads(ln) for ln in open(path)]
    N = len(rows)
    NA = sum(r["tier"] in ("name", "ambiguous") for r in rows)
    agg = collections.defaultdict(collections.Counter)
    for r in rows:
        c = agg[FINE2.get(r["cls"], "other")]
        na = r["tier"] in ("name", "ambiguous")
        c["n"] += 1
        c["na"] += na
        c["j_" + ("in" if r["jedi"] == "in" else "ext" if r["jedi"] == "ext" else "unk")] += 1
        c["na_jin"] += na and r["jedi"] == "in"
        fc = r["jedi"] == "ext" and r["tier"] == "name"
        c["fc"] += fc
        c["fce"] += (r["n_cand"] or 0) if fc else 0
        for L in ("strict", "lenient"):
            s = score(r, L)
            if s:
                c[L + s] += 1
                if s == "xok" and r["tier"] == "name":
                    c[L + "fc_rm"] += 1
                    c[L + "fce_rm"] += r["n_cand"] or 0
                if s == "xok" and r["tier"] == "ambiguous":
                    c[L + "amb_rm"] += 1
                if s == "ok" and na:
                    c[L + "na_ok"] += 1
    order = [c for c, _ in MERGE] + ["other"]
    tot = collections.Counter()
    print(f"\n### {path}\n\n{N} gap receiver calls (name+ambiguous: {NA}).\n")
    if COMPACT:
        print(
            "| class | gap n | share | name+amb | jedi in/ext/unk | strict resolves | strict ok/bad (prec) | "
            "strict recall | lenient ok (recall) | ext marks ok/bad | false name sites (edges) → removed |"
        )
        print("|---|---:|---:|---:|---|---:|---|---:|---|---|---|")
    else:
        print(
            "| class | gap n | share | name+amb | jedi in / ext / unk | strict ok / bad / unverified | strict prec | "
            "strict recall | ext marks ok / bad | false name sites (edges) → removed | lenient ok / bad | "
            "lenient prec | lenient recall |"
        )
        print("|---|---:|---:|---:|---|---|---:|---:|---|---|---|---:|---:|")
    for cls in sorted(order, key=lambda k: -agg[k]["n"]):
        c = agg[cls]
        if not c["n"]:
            continue
        tot.update(c)
        print(_row(cls, c, N))
    print(_row("**total**", tot, N))
    s_ok = tot["strictna_ok"]
    l_ok = tot["lenientna_ok"]
    print(
        f"\nname+ambiguous sites with a jedi in-repo target: {tot['na_jin']}; strict turns {s_ok} "
        f"({pct(s_ok, tot['na_jin'])}) of them into the exact target, lenient {l_ok} ({pct(l_ok, tot['na_jin'])}); "
        f"ambiguous jedi-external sites marked external: strict {tot['strictamb_rm']}, lenient {tot['lenientamb_rm']}."
    )


COMPACT = "--compact" in sys.argv


def _row(cls, c, N):
    if COMPACT:
        sv = c["strictok"] + c["strictbad"]
        return (
            f"| {cls} | {c['n']} | {pct(c['n'], N)} | {c['na']} | {c['j_in']}/{c['j_ext']}/{c['j_unk']} | "
            f"{c['strictok'] + c['strictbad'] + c['strictunv']} | {c['strictok']}/{c['strictbad']} "
            f"({pct(c['strictok'], sv)}) | {pct(c['strictok'], c['j_in'])} | {c['lenientok']} "
            f"({pct(c['lenientok'], c['j_in'])}) | {c['strictxok']}/{c['strictxbad']} | "
            f"{c['fc']} ({c['fce']}) → {c['strictfc_rm']} ({c['strictfce_rm']}) |"
        )
    sv = c["strictok"] + c["strictbad"]
    lv = c["lenientok"] + c["lenientbad"]
    return (
        f"| {cls} | {c['n']} | {pct(c['n'], N)} | {c['na']} | {c['j_in']} / {c['j_ext']} / {c['j_unk']} | "
        f"{c['strictok']} / {c['strictbad']} / {c['strictunv']} | {pct(c['strictok'], sv)} | "
        f"{pct(c['strictok'], c['j_in'])} | {c['strictxok']} / {c['strictxbad']} | "
        f"{c['fc']} ({c['fce']}) → {c['strictfc_rm']} ({c['strictfce_rm']}) | "
        f"{c['lenientok']} / {c['lenientbad']} | {pct(c['lenientok'], lv)} | {pct(c['lenientok'], c['j_in'])} |"
    )


if __name__ == "__main__":
    for p in [a for a in sys.argv[1:] if not a.startswith("--")]:
        main(p)
