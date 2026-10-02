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


def hinted_records():
    rows = records()
    for i in range(6):
        for rep in (1, 2):
            rows.append(rec(f"t{i}", "duckgrep-hint", rep, calls=3 + i, tokens=800 + 100 * i, adopted=True))
            rows.append(rec(f"t{i}", "serena-hint", rep, calls=9 + i, tokens=2000 + 100 * i, adopted=True))
    return rows


def test_every_non_baseline_setup_is_a_contrast_in_a_fixed_order():
    rows = [rec("a", s) for s in ("zeta", "serena-hint", "baseline", "duckgrep-hint", "alpha", "serena", "duckgrep")]
    assert report.contrasts(rows) == ["duckgrep", "duckgrep-hint", "serena", "serena-hint", "alpha", "zeta"]
    assert report.contrasts([rec("a", "baseline"), rec("a", "serena")]) == ["serena"]


def test_hinted_setups_are_compared_with_the_baseline_in_every_table():
    found = report.comparisons(hinted_records())
    loc = [c for c in found if c.table == "Python localization" and c.metric.name == "tool calls"]
    assert [c.setup for c in loc] == ["duckgrep", "duckgrep-hint", "serena", "serena-hint"]
    [hint] = [c for c in loc if c.setup == "duckgrep-hint"]
    assert hint.n == 6 and hint.other < hint.base
    assert all(c.p_holm >= c.p for c in found)
    assert len(found) == 4 * len(report.METRICS)  # one table, four setups


def test_the_adoption_and_variance_tables_cover_every_setup():
    rows = hinted_records()
    text = "\n".join(report.adoption_table(rows))
    for setup in ("duckgrep", "duckgrep-hint", "serena", "serena-hint"):
        assert f"| {setup} | localization |" in text
    variance = "\n".join(report.variance_table(rows))
    assert "| serena-hint | log tokens |" in variance and "| baseline | log tokens |" in variance


def test_the_post_cutoff_table_is_the_live_stratum_for_every_setup():
    rows = []
    for i in range(4):
        for setup in ("baseline", "duckgrep", "duckgrep-hint"):
            live = i >= 2
            rows.append(
                rec(
                    f"r{i}", setup, lang="rust", stratum="live" if live else "live-earlier", calls=5 + i, tokens=900 + i
                )
            )
    found = report.comparisons(rows)
    post = [c for c in found if c.table == "Rust localization, post-cutoff issues"]
    assert {c.setup for c in post} == {"duckgrep", "duckgrep-hint"} and {c.n for c in post} == {2}
    assert {c.n for c in found if c.table == "Rust localization"} == {4}


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


def test_a_run_that_never_reached_the_model_is_left_out_of_token_and_cost_means():
    rows = [rec("a", "baseline", 1, tokens=0, cost=0.0), rec("a", "baseline", 2, tokens=1000, cost=0.2)]
    assert report.task_means(rows, M["tokens"]) == {("a", "baseline"): 1000.0}
    assert report.task_means(rows, M["cost ($)"]) == {("a", "baseline"): 0.2}
    assert "1 never reached the model" in report.build(rows, [], "x")


def test_setups_are_compared_on_the_repetitions_both_have():
    rows = []
    for i in range(3):  # the batch stopped before duckgrep's second repetitions
        rows += [rec(f"t{i}", "baseline", 1, calls=10), rec(f"t{i}", "baseline", 2, calls=20)]
        rows += [rec(f"t{i}", "duckgrep", 1, calls=10)]
    [c] = [c for c in report.comparisons(rows) if c.setup == "duckgrep" and c.metric.name == "tool calls"]
    assert c.effect == pytest.approx(1.0) and c.n == 3


def test_tables_show_raw_and_corrected_p_and_say_when_nothing_can_pass():
    text = report.build(records(), [], "pilot")
    assert "| p | p (Holm) |" in text
    # 6 tasks: the smallest two-sided Wilcoxon p is 2/2^6, and Holm multiplies it by the number of comparisons
    assert "no comparison can reach p < 0.05" in text


def test_shares_read_as_percentages_and_their_differences_as_points():
    rows = []
    for i in range(4):
        rows.append(rec(f"t{i}", "baseline", success=i < 2))
        rows.append(rec(f"t{i}", "duckgrep", success=True))
    text = report.build(rows, [], "x")
    line = next(ln for ln in text.splitlines() if ln.startswith("| success |") and "duckgrep" in ln)
    assert "| 50% |" in line and "duckgrep 100%" in line and "+50 pp [" in line


def test_the_report_says_how_count_ratios_are_taken():
    assert "1 + n" in report.build(records(), [], "x")


def test_by_repo_splits_task_kinds_and_ignores_case():
    rows = [
        rec("a", "baseline", repo="BurntSushi/ripgrep", kind="structural"),
        rec("b", "baseline", repo="burntsushi/ripgrep", kind="structural"),
        rec("c", "baseline", repo="burntsushi/ripgrep", kind="localization"),
    ]
    table = report.repo_table(rows)
    assert table[2].startswith("| kind | repo |")
    body = [ln for ln in table if ln.startswith("| localization") or ln.startswith("| structural")]
    assert len(body) == 2 and any("| structural | burntsushi/ripgrep | baseline | 2 |" in ln.lower() for ln in body)


def test_the_summary_names_what_happened_and_skips_what_did_not():
    rows = [rec("a", "baseline"), {**rec("a", "serena"), "worktree_changes": ["!! Cargo.lock"]}]
    bytecode = ["!! pkg/__pycache__/m.cpython-312.pyc", "!! pkg/sub/__pycache__/n.cpython-312.pyc"]
    rows.append({**rec("b", "baseline"), "worktree_changes": bytecode})
    text = "\n".join(report.summary(rows))
    assert "wall-clock" not in text and "configuration check" not in text
    assert "2 left files in their worktree" in text and "Cargo.lock (1 run)" in text
    assert "Python bytecode (1 run)" in text and "pkg/" not in text  # every __pycache__ is one item


def test_the_cost_counts_what_attempts_that_were_not_recorded_spent():
    rows = [rec("a", "baseline"), rec("a", "serena")]
    listed = sum(r["cost_usd"] for r in rows)
    unrecorded = [{"why": "interrupted", "cost_usd": 0.03}, {"why": "infrastructure error", "cost_usd": 0.0}]
    text = "\n".join(report.summary(rows, unrecorded))
    assert f"${listed + 0.03:,.2f} in all" in text and "2 attempts that were not recorded" in text
    assert "not recorded" not in "\n".join(report.summary(rows))  # nothing to say without them
    assert "not recorded" in report.build(rows, [], "x", unrecorded=unrecorded)


def test_the_cost_counts_an_attempt_a_configuration_retry_discarded():
    rows = [rec("a", "baseline"), {**rec("a", "serena"), "discarded_cost_usd": 0.05}]
    billed = sum(r["cli_cost_usd"] for r in rows)
    text = "\n".join(report.summary(rows))
    assert f"${billed + 0.05:,.2f} in all" in text and "1 attempt that was not recorded" in text


def test_setup_costs_show_a_dash_where_a_size_was_not_recorded():
    rows = [
        {"setup": "duckgrep", "index_seconds": 2.0, "index_mb": 5.0},
        {"setup": "serena", "serena_seconds": 3.0},
    ]
    table = report.setup_costs(rows)
    assert "| duckgrep | 1 | 2 | 5 |" in table and "| serena | 1 | 3 | – |" in table
    assert "| serena | 1 | 3 | 7 |" in report.setup_costs(
        [{"setup": "serena", "serena_seconds": 3.0, "serena_mb": 7.0}]
    )
