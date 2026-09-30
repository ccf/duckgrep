import pytest

from bench.eval import report

M = {m.name: m for m in report.METRICS}


def rec(
    task,
    setup,
    rep=1,
    calls=10,
    tokens=1000,
    cost=0.1,
    success=True,
    f1=1.0,
    located=2,
    ok=True,
    kind="localization",
    lang="python",
    stratum="lite",
    adopted=False,
    repo="o/r",
):
    return {
        "task": task,
        "setup": setup,
        "rep": rep,
        "tool_calls": calls,
        "rounds": calls + 1,
        "tokens_total": tokens,
        "cost_usd": cost,
        "score": {"success": success, "f1": f1},
        "turns_to_locate": located,
        "config_ok": ok,
        "kind": kind,
        "lang": lang,
        "stratum": stratum,
        "adopted": adopted,
        "mcp_share": 0.5 if adopted else 0.0,
        "repo": repo,
        "killed": False,
        "is_error": False,
        "worktree_changes": [],
        "cli_cost_usd": cost,
        "cli_version": "2.1.285",
        "model": "claude-sonnet-5-5",
    }


def test_holm():
    assert report.holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert report.holm([0.5, 0.9]) == pytest.approx([1.0, 1.0])


def test_task_means_average_repetitions_and_skip_failed_configurations():
    rows = [
        rec("a", "baseline", 1, calls=10),
        rec("a", "baseline", 2, calls=20),
        rec("b", "baseline", located=None),
        rec("c", "baseline", ok=False),
    ]
    assert report.task_means(rows, M["tool calls"]) == {("a", "baseline"): 15.0, ("b", "baseline"): 10.0}
    assert report.task_means(rows, M["turns to locate"]) == {("a", "baseline"): 2.0, ("b", "baseline"): 12.0}


def test_a_run_that_never_located_counts_as_its_rounds_plus_one():
    rows = [
        rec("a", "baseline", located=3),
        rec("a", "duckgrep", 1, located=2),
        rec("a", "duckgrep", 2, located=None, calls=5),  # 6 rounds, never saw the answer
        rec("b", "baseline", located=4),
        rec("b", "duckgrep", located=2),
    ]
    assert report.task_means(rows, M["turns to locate"])[("a", "duckgrep")] == (2 + 7) / 2
    assert report.task_means(rows, M["located"]) == {
        ("a", "baseline"): 1.0,
        ("a", "duckgrep"): 0.5,
        ("b", "baseline"): 1.0,
        ("b", "duckgrep"): 1.0,
    }


def test_compare_on_a_log_scale():
    c = report.compare("t", "duckgrep", M["tokens"], [(500.0, 1000.0), (50.0, 100.0), (5.0, 10.0)], seed=1)
    assert c.effect == pytest.approx(0.5) and c.low == pytest.approx(0.5) and c.high == pytest.approx(0.5)
    assert c.win == 1.0 and c.n == 3 and c.base == pytest.approx(100.0) and c.other == pytest.approx(50.0)


def test_compare_differences_and_ties():
    c = report.compare("t", "serena", M["success"], [(1.0, 0.0), (1.0, 1.0), (0.0, 0.0)], seed=1)
    assert c.effect == pytest.approx(1 / 3) and c.win == pytest.approx((1 + 0.5 + 0.5) / 3)
    same = report.compare("t", "serena", M["F1"], [(1.0, 1.0), (0.5, 0.5)], seed=1)
    assert same.p == 1.0 and same.effect == 0.0


def records():
    rows = []
    for i in range(6):
        for rep in (1, 2):
            rows.append(rec(f"t{i}", "baseline", rep, calls=12 + i, tokens=2000 + 100 * i, cost=0.2))
            rows.append(rec(f"t{i}", "duckgrep", rep, calls=4 + i, tokens=900 + 100 * i, cost=0.1, adopted=True))
            rows.append(rec(f"t{i}", "serena", rep, calls=15 + i, tokens=2600 + 100 * i, cost=0.3, adopted=rep == 1))
    return rows


def test_report_has_every_section():
    text = report.build(records(), [{"setup": "duckgrep", "index_seconds": 2.0, "index_mb": 5.0}], "pilot")
    assert text.startswith("## A/B evaluation: pilot")
    for heading in ("### Python localization", "### Adoption", "### By repo", "### Variance", "### Setup costs"):
        assert heading in text
    assert "| tool calls | 6 |" in text and "duckgrep" in text and "×" in text
    assert "### Rust localization" not in text  # no Rust runs


def test_comparisons_are_holm_corrected_together():
    found = report.comparisons(records())
    assert found and all(c.p_holm >= c.p for c in found)


def test_write_section_replaces_its_own_block(tmp_path):
    path = tmp_path / "RESULTS.md"
    path.write_text("# Benchmark results\n\nearlier text\n")
    report.write_section(path, "pilot", "first\n")
    report.write_section(path, "pilot", "second\n")
    text = path.read_text()
    assert text.count("<!-- eval:pilot -->") == 1 and "second" in text and "first" not in text
    assert text.startswith("# Benchmark results\n\nearlier text\n")
