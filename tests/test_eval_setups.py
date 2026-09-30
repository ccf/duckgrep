import sys

import pytest

from bench.eval import config, setups

VERIFIED = [  # the recipe verified by hand on Claude Code 2.1.285
    "claude",
    "-p",
    "PROMPT",
    "--model",
    "claude-sonnet-5-5",
    "--effort",
    "medium",
    "--output-format",
    "stream-json",
    "--verbose",
    "--setting-sources",
    "",
    "--strict-mcp-config",
    "--disable-slash-commands",
    "--tools",
    "Bash,Read,Grep,Glob",
    "--permission-mode",
    "dontAsk",
]
TAIL = ["--no-session-persistence", "--max-turns", "40", "--max-budget-usd", "1.50"]


def test_baseline_command():
    argv = setups.command(setups.SETUPS["baseline"], "PROMPT", None)
    assert argv == VERIFIED + ["--allowedTools", "Bash,Read,Grep,Glob"] + TAIL


@pytest.mark.parametrize("name, allow", [("duckgrep", "mcp__duckgrep__query"), ("serena", "mcp__serena__*")])
def test_mcp_commands_differ_only_in_the_server_and_its_allow_entry(tmp_path, name, allow):
    argv = setups.command(setups.SETUPS[name], "PROMPT", tmp_path / "mcp.json")
    assert (
        argv
        == VERIFIED
        + ["--allowedTools", f"Bash,Read,Grep,Glob,{allow}", "--mcp-config", str(tmp_path / "mcp.json")]
        + TAIL
    )


def test_environment_is_scrubbed(monkeypatch):
    monkeypatch.setenv("GIT_DIR", "/elsewhere")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    env = setups.environment()
    assert set(env) == {"HOME", "PATH", "USER", "TMPDIR", "CLAUDE_CODE_DISABLE_AUTO_MEMORY", "ENABLE_TOOL_SEARCH"}
    assert env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1" and env["ENABLE_TOOL_SEARCH"] == "false"
    assert "USER" not in setups.environment(with_user=False)


def test_expected_tools():
    assert setups.SETUPS["baseline"].expected_tools == {"Bash", "Read", "Grep", "Glob"}
    assert setups.SETUPS["duckgrep"].expected_tools == {"Bash", "Read", "Grep", "Glob", "mcp__duckgrep__query"}
    assert len(setups.SETUPS["serena"].expected_tools) == 4 + len(config.SERENA_TOOLS)


def test_duckgrep_server_runs_this_environments_duckgrep(tmp_path):
    cfg = setups.mcp_config(setups.SETUPS["duckgrep"], tmp_path / "wt", "o/r", tmp_path)
    assert cfg == {
        "mcpServers": {
            "duckgrep": {"command": sys.executable, "args": ["-m", "duckgrep", "-C", str(tmp_path / "wt"), "mcp"]}
        }
    }
    assert setups.mcp_config(setups.SETUPS["baseline"], tmp_path, "o/r", tmp_path) is None


def test_serena_server(tmp_path):
    home = setups.serena_home(tmp_path / "home", tmp_path / "cache")
    cfg = setups.mcp_config(setups.SETUPS["serena"], tmp_path / "wt", "Owner/Repo", tmp_path / "cache", home)
    server = cfg["mcpServers"]["serena"]
    assert server["args"] == [
        "--from",
        "serena-agent==1.7.0",
        "serena",
        "start-mcp-server",
        "--context",
        "eval-nav",
        "--project",
        str(tmp_path / "wt"),
        "--enable-web-dashboard",
        "false",
        "--open-web-dashboard",
        "false",
        "--enable-gui-log-window",
        "false",
    ]
    env = server["env"]
    assert env["SERENA_HOME"] == str(home) and env["RUSTUP_TOOLCHAIN"] == "stable"
    assert env["CARGO_HOME"] == str(tmp_path / "cache" / "cargo-home")
    assert env["CARGO_TARGET_DIR"] == str(tmp_path / "cache" / "cargo-target" / "owner__repo")
    assert env["PATH"].startswith(str(tmp_path / "cache" / "bin"))
    with pytest.raises(ValueError):
        setups.mcp_config(setups.SETUPS["serena"], tmp_path, "o/r", tmp_path)


def test_serena_home_is_rendered(tmp_path):
    home = setups.serena_home(tmp_path / "home", tmp_path / "cache")
    text = (home / "serena_config.yml").read_text()
    assert (
        f'project_serena_folder_location: "{tmp_path / "cache" / "serena-projects"}/$projectFolderName/.serena"' in text
    )
    assert "projects: []" in text and "@PROJECTS@" not in text
    context = (home / "contexts" / "eval-nav.yml").read_text()
    assert all(f"  - {tool}" in context for tool in config.SERENA_TOOLS)


def test_the_harness_environment_is_off_the_agents_path(monkeypatch):
    import os
    from pathlib import Path

    venv_bin = str(Path(sys.prefix) / "bin")  # the tests run in the repo's virtualenv, like the harness
    inside_repo = str(config.EVAL_DIR.parents[1] / "scripts")
    monkeypatch.setenv("PATH", os.pathsep.join([venv_bin, inside_repo, "/usr/bin", "/bin"]))
    assert setups.environment()["PATH"] == os.pathsep.join(["/usr/bin", "/bin"])
