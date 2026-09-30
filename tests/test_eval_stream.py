"""Recorded stream-json runs (Claude Code 2.1.285, Haiku 4.5, paths sanitised) against the transcript parser."""

import os

import pytest

from bench.eval import stream

RUNS = os.path.join(os.path.dirname(__file__), "eval_runs")
HAIKU = "claude-haiku-4-5-20251001"
HAIKU_RATES = {"input": 1.00, "cache_write_5m": 1.25, "cache_write_1h": 2.00, "cache_read": 0.10, "output": 5.00}
BUILTINS = {"Bash", "Glob", "Grep", "Read"}


def run(name):
    with open(os.path.join(RUNS, f"{name}.jsonl")) as f:
        return stream.read(f)


def test_plain_run():
    tr = run("plain")
    m = stream.metrics(tr)
    assert tr.rounds == 3 and m["num_turns"] == 3
    assert m["tool_counts"] == {"Grep": 1, "Read": 1}
    assert (m["search_calls"], m["read_calls"], m["mcp_calls"]) == (1, 1, 0)
    assert not m["adopted"] and m["mcp_share"] == 0.0
    assert m["tokens"] == {
        "input": 26,
        "cache_write_5m": 419,
        "cache_write_1h": 1949,
        "cache_read": 26946,
        "output": 356,
    }
    assert m["tokens_total"] == 29696
    assert m["terminal_reason"] == "completed" and not m["is_error"] and m["api_error"] is None
    assert m["final_text"].startswith("`needle_fn` is defined in src/a.py:4")
    assert m["tool_seconds"] == {"Grep": 0.03, "Read": 0.013}


def test_cost_formula_reproduces_claude_codes_own_total():
    for name in ("plain", "mcp", "denied", "max_turns"):
        tr = run(name)
        assert stream.cost(stream.tokens(tr.result), HAIKU_RATES) == pytest.approx(
            tr.result["total_cost_usd"], abs=1e-12
        )


def test_cost_at_sonnet_rates():
    assert stream.metrics(run("plain"))["cost_usd"] == pytest.approx(0.017845, abs=1e-6)


def test_mcp_run_counts_the_mcp_tool_and_its_round():
    m = stream.metrics(run("mcp"))
    assert m["tool_counts"] == {"mcp__tiny__ping": 1, "Grep": 1, "Read": 1}
    assert m["rounds"] == 4 and m["mcp_calls"] == 1 and m["adopted"]
    assert m["mcp_share"] == pytest.approx(1 / 3)


def test_denied_call_is_excluded_and_counted_apart():
    tr = run("denied")
    m = stream.metrics(tr)
    assert m["tool_counts"] == {"Grep": 1, "Read": 1}
    assert m["denied"] == 1 and m["denied_tools"] == ["mcp__tiny__ping"]
    assert tr.rounds == 2  # the three calls of the first message are one round trip


def test_turn_cap_is_an_error_not_an_infrastructure_failure():
    tr = run("max_turns")
    m = stream.metrics(tr)
    assert m["is_error"] and m["terminal_reason"] == "max_turns" and m["final_text"] == ""
    assert not stream.infrastructure_error(tr)


def test_auth_failure_reports_success_but_is_an_infrastructure_error():
    tr = run("auth_failure")
    assert tr.result["subtype"] == "success"  # why the subtype alone is never trusted
    m = stream.metrics(tr)
    assert m["is_error"] and tr.api_error == "authentication_failed"
    assert stream.infrastructure_error(tr)
    assert tr.rounds == 0 and m["tool_calls"] == 0 and m["cost_usd"] == 0.0


@pytest.mark.parametrize(
    "error, permanent",
    [
        ("authentication_failed", True),
        ("billing_error", True),
        ("invalid_request", True),
        ("Unauthorized", True),  # any value naming auth or billing
        ("out_of_billing_credits", True),
        ("rate_limit", False),
        ("overloaded", False),
        ("server_error", False),
        ("unknown", False),
        ("synthetic", False),  # a synthetic message that says nothing
        (None, False),  # terminal_reason "api_error" with no error value
    ],
)
def test_an_api_error_is_permanent_only_when_a_retry_cannot_help(error, permanent):
    tr = stream.Transcript(api_error=error, result={"terminal_reason": "api_error"})
    assert stream.infrastructure_error(tr) and stream.permanent_error(tr) is permanent


@pytest.mark.parametrize(
    "text, permanent",
    [
        ("Not logged in · Please run /login", True),
        ("Invalid API key · Please run /login", True),
        ("Credit balance is too low", True),
        ('API Error: 400 {"type":"error","error":{"type":"invalid_request_error","message":"bad"}}', True),
        ('API Error: 403 {"type":"error","error":{"type":"permission_error","message":"no"}}', True),
        ('API Error: 429 {"type":"error","error":{"type":"rate_limit_error","message":"slow down"}}', False),
        ('API Error: 529 {"type":"error","error":{"type":"overloaded_error","message":"busy"}}', False),
        ("", False),
    ],
)
def test_an_api_error_only_the_result_names_is_classed_by_its_text(text, permanent):
    tr = stream.Transcript(result={"terminal_reason": "api_error", "result": text})
    assert stream.infrastructure_error(tr) and stream.permanent_error(tr) is permanent


def test_a_final_answer_is_never_read_as_an_api_error():
    # an error message mid-run, then an answer that names auth code: only a run that ended on the error is read
    tr = stream.Transcript(api_error="overloaded", result={"terminal_reason": "completed", "result": "auth.py:login"})
    assert stream.infrastructure_error(tr) and not stream.permanent_error(tr)


def test_truncated_stream_is_an_error():
    with open(os.path.join(RUNS, "plain.jsonl")) as f:
        lines = f.readlines()[:-1] + ['{"type": "resu']
    m = stream.metrics(stream.read(lines))
    assert m["is_error"] and m["terminal_reason"] is None and m["tool_calls"] == 2


def test_config_check_passes_for_the_intended_setup():
    assert stream.config_problems(run("plain"), BUILTINS, set(), model=HAIKU) == []
    assert stream.config_problems(run("mcp"), BUILTINS | {"mcp__tiny__ping"}, {"tiny"}, model=HAIKU) == []


def test_config_check_catches_wrong_tools_model_and_servers():
    tr = run("mcp")
    problems = stream.config_problems(tr, BUILTINS, set())
    assert any("model is" in p for p in problems)
    assert any("mcp__tiny__ping" in p for p in problems)
    assert any("MCP servers" in p for p in problems)


def test_config_check_catches_a_failed_server_hooks_and_skills():
    tr = run("mcp")
    tr.init = {**tr.init, "mcp_servers": [{"name": "tiny", "status": "failed"}], "skills": ["x"]}
    tr.hooks = 2
    problems = stream.config_problems(tr, BUILTINS | {"mcp__tiny__ping"}, {"tiny"}, model=HAIKU)
    assert "MCP server tiny is failed" in problems
    assert "skills is not empty" in problems
    assert "2 hook events" in problems


def test_config_check_on_the_free_probe_sees_the_setup_before_the_login_fails():
    tr = run("auth_failure")
    assert stream.config_problems(tr, BUILTINS | {"mcp__tiny__ping"}, {"tiny"}, model="claude-sonnet-5-5") == []


def test_a_run_cut_off_before_its_result_is_measured_from_its_messages():
    import json

    with open(os.path.join(RUNS, "mcp.jsonl")) as f:
        lines = [line for line in f if json.loads(line).get("type") != "result"]
    m = stream.metrics(stream.read(lines))
    # the input side of each API call is known when it starts, so it matches the full run's totals
    assert {k: m["tokens"][k] for k in ("input", "cache_write_5m", "cache_write_1h", "cache_read")} == {
        "input": 34,
        "cache_write_5m": 547,
        "cache_write_1h": 9633,
        "cache_read": 29363,
    }
    assert 0 < m["tokens"]["output"] < 425  # output only as far as each message had got when it started
    assert not m["usage_complete"] and m["cost_usd"] > 0
    assert stream.metrics(run("mcp"))["usage_complete"]


def test_serenas_instructions_alone_are_not_adoption():
    def calls(*names):
        return stream.Transcript(calls=[stream.Call(str(i), n, i, {}) for i, n in enumerate(names, 1)])

    m = stream.metrics(calls("mcp__serena__initial_instructions", "Grep"))
    assert not m["adopted"] and m["mcp_calls"] == 1  # it still costs a call and its tokens
    assert m["mcp_share"] == 0.0  # and is no share of using the tool
    assert stream.metrics(calls("mcp__serena__initial_instructions", "mcp__serena__find_symbol"))["adopted"]
    assert stream.metrics(calls("mcp__duckgrep__query"))["adopted"]
