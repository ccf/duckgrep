import pytest

from bench.eval.suite import Task, load, load_suite, save


def task(**kw):
    base = dict(
        id="t1",
        kind="localization",
        lang="python",
        repo="o/r",
        commit="c" * 40,
        prompt="p",
        gold=("a.py:f",),
        source="s",
    )
    return Task(**{**base, **kw})


def test_round_trip(tmp_path):
    tasks = [task(), task(id="t2", kind="structural", answer="files", gold=["a.py", "b.py"], stratum="importers")]
    save(tmp_path / "s.jsonl", tasks)
    assert load(tmp_path / "s.jsonl") == tasks
    assert load(tmp_path / "s.jsonl")[1].gold == ("a.py", "b.py")


def test_duplicate_ids_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        save(tmp_path / "s.jsonl", [task(), task()])


@pytest.mark.parametrize("bad", [{"kind": "other"}, {"lang": "go"}, {"answer": "lines"}, {"gold": ()}])
def test_invalid_tasks_are_rejected(bad):
    with pytest.raises(ValueError):
        task(**bad)


def test_load_suite_reads_every_kind(tmp_path):
    save(tmp_path / "pilot-localization.jsonl", [task()])
    save(tmp_path / "pilot-structural.jsonl", [task(id="s1", kind="structural")])
    assert [t.id for t in load_suite("pilot", tmp_path)] == ["t1", "s1"]
    with pytest.raises(FileNotFoundError):
        load_suite("missing", tmp_path)
