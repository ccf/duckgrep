import pytest

from bench.eval.score import normalize, parse_answer, score

ANSWER = """I looked around.

```json
{"locations": ["old.py:f"]}
```

Final answer:

```json
{"locations": ["src/a.py:Session.request", "src/b.py:helper"]}
```
"""


def test_parse_answer_takes_the_last_block_with_locations():
    assert parse_answer(ANSWER) == ["src/a.py:Session.request", "src/b.py:helper"]


def test_parse_answer_skips_a_trailing_block_that_is_not_an_answer():
    text = ANSWER + '\n```json\n{"note": "no locations here"}\n```\n'
    assert parse_answer(text) == ["src/a.py:Session.request", "src/b.py:helper"]


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "no block at all",
        "```json\n{not json}\n```",
        '```json\n{"locations": "a.py"}\n```',
        "```\n[1, 2]\n```",
    ],
)
def test_parse_answer_returns_none_without_a_valid_block(text):
    assert parse_answer(text) is None


def test_parse_answer_accepts_an_unlabelled_fence():
    assert parse_answer('```\n{"locations": ["a.py:f"]}\n```') == ["a.py:f"]


@pytest.mark.parametrize(
    "entry, expected",
    [
        ("src/a.py:Session.request", ("src/a.py", "Session.request")),
        ("./src/a.py:Session.request", ("src/a.py", "Session.request")),
        ("/wt/repo/src/a.py:Session.request", ("src/a.py", "Session.request")),
        ("`src/a.py:f`", ("src/a.py", "f")),
        ("src/a.py", ("src/a.py", "")),
        ("src/a.py:12", ("src/a.py", "")),
        ("src/a.py:f()", ("src/a.py", "f")),
        ("src/requests/sessions.py:requests.sessions.Session.request", ("src/requests/sessions.py", "Session.request")),
        ("src/walk.rs:WorkerState::new", ("src/walk.rs", "WorkerState.new")),
        ("src/walk.rs::WorkerState::new", ("src/walk.rs", "WorkerState.new")),
        ("src/walk.rs:<WorkerState as Drop>::drop", ("src/walk.rs", "WorkerState.drop")),
        ("src/walk.rs:<Walker<'a> as Iterator<Item = X>>::next", ("src/walk.rs", "Walker.next")),
        ("src/walk.rs:impl WorkerState/new", ("src/walk.rs", "WorkerState.new")),
        ("src/walk.rs:crate::walk::WorkerState::new", ("src/walk.rs", "WorkerState.new")),
        ("src/walk.rs:Cache<K, V>::get", ("src/walk.rs", "Cache.get")),
    ],
)
def test_normalize(entry, expected):
    assert normalize(entry, roots=("/wt/repo",)) == expected


def test_normalize_is_case_sensitive():
    assert normalize("src/a.py:session.Request") == ("src/a.py", "session.Request")


GOLD = ("src/a.py:Session.request", "src/a.py:merge", "src/b.py")  # src/b.py: a class-level edit


def test_success_needs_every_function_and_every_file_only_key():
    s = score(["src/a.py:Session.request", "src/a.py:merge", "src/b.py:Thing"], GOLD, "functions")
    assert s.parsed and s.success
    assert s.precision == 1.0 and s.recall == 1.0 and s.f1 == 1.0 and s.file_recall == 1.0


def test_missing_file_only_key_fails_success_but_not_function_recall():
    s = score(["src/a.py:Session.request", "src/a.py:merge"], GOLD, "functions")
    assert not s.success
    assert s.recall == 1.0 and s.file_recall == 0.5


def test_nested_function_counts_for_its_outermost_key():
    s = score(["src/a.py:Session.request.inner", "src/a.py:merge", "src/b.py"], GOLD, "functions")
    assert s.success and s.precision == 1.0


def test_precision_and_recall():
    s = score(["src/a.py:Session.request", "src/a.py:other", "src/c.py:x", "src/b.py"], GOLD, "functions")
    assert not s.success
    assert s.precision == pytest.approx(1 / 3)
    assert s.recall == pytest.approx(1 / 2)
    assert s.f1 == pytest.approx(2 * (1 / 3) * (1 / 2) / (1 / 3 + 1 / 2))


def test_duplicates_count_once():
    s = score(["src/a.py:merge", "./src/a.py:merge", "src/a.py:merge()"], ("src/a.py:merge",), "functions")
    assert s.precision == 1.0 and s.success


def test_unparsed_answer_scores_zero():
    s = score(None, GOLD, "functions")
    assert not s.parsed and not s.success and s.f1 == 0.0 and s.file_recall == 0.0


def test_empty_answer_scores_zero():
    s = score([], ("src/a.py:f",), "functions")
    assert s.parsed and not s.success and s.precision == 0.0 and s.recall == 0.0


def test_files_answer():
    s = score(["a.py", "b.py:ignored_name", "c.py"], ("a.py", "b.py", "d.py"), "files")
    assert s.precision == pytest.approx(2 / 3) and s.recall == pytest.approx(2 / 3) and not s.success
    assert score(["a.py", "b.py"], ("a.py", "b.py"), "files").success


def test_only_file_only_keys():
    s = score(["src/b.py"], ("src/b.py",), "functions")
    assert s.success and s.precision is None and s.recall is None


def test_rust_inline_module_prefix_is_accepted():
    s = score(
        ["src/lib.rs:tests::it_works", "src/lib.rs:imp::Walker::next"],
        ("src/lib.rs:it_works", "src/lib.rs:Walker.next"),
        "functions",
    )
    assert s.success and s.precision == 1.0


def test_inline_module_prefix_is_rust_only():
    assert not score(["src/a.py:helpers.run"], ("src/a.py:run",), "functions").success
