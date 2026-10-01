import pytest

from bench.eval import datasets
from bench.eval.suite import Task
from bench.eval.tasks import localization as loc

KEY = ("src/pkg/sessions.py:Session.merge_environment_settings", "src/pkg/sessions.py:write")


@pytest.mark.parametrize(
    "text, leaked",
    [
        ("The bug is in src/pkg/sessions.py.", True),
        ("See pkg/sessions.py line 3.", True),  # the path without src/
        ("Session.merge_environment_settings ignores proxies", True),
        ("merge_environment_settings ignores proxies", True),  # a code-like name
        ("Please write the docs.", False),  # a plain word
        ("Calling s.write(data) fails", True),  # a plain name used as a call
        ("nothing relevant here", False),
    ],
)
def test_leaks(text, leaked):
    assert loc.leaks(text, KEY) == leaked


def test_leaks_rust_spelling_and_dunders():
    assert loc.leaks("Walker::new panics", ("src/walk.rs:Walker.new",))
    assert not loc.leaks("__init__ is slow", ("pkg/a.py:Thing.__init__",))
    assert loc.leaks("Thing.__init__ is slow", ("pkg/a.py:Thing.__init__",))


def rows(repo_sizes):
    return [{"instance_id": f"{r}-{i}", "repo": r} for r, n in repo_sizes.items() for i in range(n)]


def fake_task(row):
    return Task(row["instance_id"], "localization", "python", row["repo"], "c", "p", ("a.py:f",), "s")


def test_select_is_deterministic_round_robin_and_capped():
    cands = rows({"a/x": 5, "b/y": 5, "c/z": 1})
    picked = loc.select(cands, 5, 2, seed=7, accept=fake_task)
    again = loc.select(list(reversed(cands)), 5, 2, seed=7, accept=fake_task)
    assert [t.id for t in picked] == [t.id for t in again]
    repos = [t.repo for t in picked]
    assert len(picked) == 5 and max(repos.count(r) for r in set(repos)) == 2 and "c/z" in repos


def test_select_skips_rejected_candidates():
    cands = rows({"a/x": 3})
    picked = loc.select(cands, 2, 3, seed=1, accept=lambda r: None if r["instance_id"] == "a/x-0" else fake_task(r))
    assert len(picked) == 2 and "a/x-0" not in [t.id for t in picked]


BASE = "def merge_helper(x):\n    return x\n\n\ndef other():\n    return 2\n"
PATCH = (
    "diff --git a/pkg/m.py b/pkg/m.py\n--- a/pkg/m.py\n+++ b/pkg/m.py\n"
    "@@ -1,2 +1,2 @@\n def merge_helper(x):\n-    return x\n+    return x + 1\n"
)


def lite_row(iid, statement, listed):
    return {
        "instance_id": iid,
        "repo": "o/r",
        "base_commit": "c" * 40,
        "patch": PATCH,
        "problem_statement": statement,
        "edit_functions": listed,
    }


def test_python_tasks_cross_check_and_leak_filter(monkeypatch, tmp_path):
    table = [
        lite_row("o__r-1", "Values are off by one.", ["pkg/m.py:merge_helper"]),
        lite_row("o__r-2", "merge_helper returns the wrong value", ["pkg/m.py:merge_helper"]),  # leaks
        lite_row("o__r-3", "Values are off by one.", ["pkg/m.py:other"]),  # the two derivations disagree
    ]
    monkeypatch.setattr(datasets, "rows", lambda name, cache=None: table)
    monkeypatch.setattr(datasets, "fetch_file", lambda repo, commit, path, cache=None: BASE)
    tasks = loc.python_tasks(10, 3, seed=1, cache=tmp_path)
    assert [t.id for t in tasks] == ["o__r-1"]
    t = tasks[0]
    assert t.gold == ("pkg/m.py:merge_helper",) and t.stratum == "lite" and t.lang == "python"
    assert t.prompt.startswith("<issue>\nValues are off by one.\n</issue>") and '"locations"' in t.prompt


def test_rust_tasks_filter_on_date_repo_and_size(monkeypatch, tmp_path):
    base = "pub fn run() -> u32 {\n    1\n}\n"
    patch = (
        "diff --git a/src/lib.rs b/src/lib.rs\n--- a/src/lib.rs\n+++ b/src/lib.rs\n"
        "@@ -1,3 +1,3 @@\n pub fn run() -> u32 {\n-    1\n+    2\n }\n"
    )

    def row(iid, repo, created):
        return {
            "instance_id": iid,
            "repo": repo,
            "base_commit": "c" * 40,
            "patch": patch,
            "problem_statement": "It returns the wrong number.",
            "created_at": created,
        }

    table = [
        row("new-1", "o/r", "2026-07-01T00:00:00Z"),
        row("old-1", "o/r", "2025-01-01T00:00:00Z"),
        row("ruff-1", "astral-sh/ruff", "2026-07-01T00:00:00Z"),
    ]
    monkeypatch.setattr(datasets, "rows", lambda name, cache=None: table)
    monkeypatch.setattr(datasets, "fetch_file", lambda repo, commit, path, cache=None: base)
    live = loc.rust_tasks("live", 10, 2, seed=1, cache=tmp_path)
    assert [t.id for t in live] == ["new-1"] and live[0].gold == ("src/lib.rs:run",) and live[0].stratum == "live"
    assert sorted(t.id for t in loc.rust_tasks("multilingual", 10, 2, seed=1, cache=tmp_path)) == ["new-1", "old-1"]


def test_rust_tasks_of_every_date_are_split_by_the_cutoff_and_bounded_in_size(monkeypatch, tmp_path):
    base = "pub fn a() -> u32 {\n    1\n}\n\npub fn b() -> u32 {\n    1\n}\n\npub fn c() -> u32 {\n    1\n}\n\npub fn d() -> u32 {\n    1\n}\n"

    def row(iid, created, touched):
        after = base
        for name in touched:
            after = after.replace(f"pub fn {name}() -> u32 {{\n    1", f"pub fn {name}() -> u32 {{\n    2")
        diff = "".join(
            __import__("difflib").unified_diff(
                base.splitlines(True), after.splitlines(True), "a/src/lib.rs", "b/src/lib.rs"
            )
        )
        return {
            "instance_id": iid,
            "repo": "o/r",
            "base_commit": "c" * 40,
            "patch": "diff --git a/src/lib.rs b/src/lib.rs\n" + diff,
            "problem_statement": "It returns the wrong number.",
            "created_at": created,
        }

    table = [
        row("new", "2026-07-01T00:00:00Z", "a"),
        row("old", "2025-01-01T00:00:00Z", "ab"),
        row("big", "2026-07-01T00:00:00Z", "abcd"),  # four functions: over the bound of three
    ]
    monkeypatch.setattr(datasets, "rows", lambda name, cache=None: table)
    monkeypatch.setattr(datasets, "fetch_file", lambda repo, commit, path, cache=None: base)
    got = {
        t.id: t.stratum
        for t in loc.rust_tasks("live", 10, 10, seed=1, cache=tmp_path, every_date=True, max_functions=3)
    }
    assert got == {"new": "live", "old": "live-earlier"}


def test_the_full_profile_takes_every_eligible_task_and_the_pilot_keeps_its_draw():
    from bench.eval import config

    full, pilot = config.PROFILES["full"], config.PROFILES["pilot"]
    assert full.python >= 1000 and full.python_per_repo >= 1000 and full.live_every_date and full.rust_functions == 3
    assert (pilot.python, pilot.python_per_repo, pilot.multilingual, pilot.live, pilot.rust_per_repo) == (
        10,
        3,
        5,
        5,
        2,
    )
    assert not pilot.live_every_date and pilot.rust_functions == 10 and pilot.repos == config.STRUCTURAL_REPOS
    assert pilot.questions == (2, 2, 1) and full.questions == (8, 8, 4)
    assert set(config.STRUCTURAL_REPOS) <= set(full.repos) and len({r.repo for r in full.repos}) == len(full.repos)
