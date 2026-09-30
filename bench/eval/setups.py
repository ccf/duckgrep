"""The three setups. Same model, CLI, built-in tools and prompt; they differ only in one MCP server."""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from . import config

SERENA_DIR = Path(__file__).resolve().parent / "serena"


@dataclass(frozen=True)
class Setup:
    name: str
    servers: frozenset[str]  # MCP servers the init event must list, each connected
    tools: frozenset[str]  # MCP tools the init event must list besides the built-ins
    allow: tuple[str, ...]  # extra --allowedTools entries

    @property
    def expected_tools(self) -> set[str]:
        return set(config.BUILTIN_TOOLS) | set(self.tools)


SETUPS = {
    "baseline": Setup("baseline", frozenset(), frozenset(), ()),
    "duckgrep": Setup(
        "duckgrep", frozenset({"duckgrep"}), frozenset({"mcp__duckgrep__query"}), ("mcp__duckgrep__query",)
    ),
    "serena": Setup(
        "serena",
        frozenset({"serena"}),
        frozenset(f"mcp__serena__{t}" for t in config.SERENA_TOOLS),
        ("mcp__serena__*",),
    ),
}


def agent_path() -> str:
    """PATH without the harness's own environment: this repo and the virtualenv running the harness. Otherwise
    every setup's shell, the baseline's too, could run duckgrep."""
    own = [config.EVAL_DIR.parents[1].resolve()]
    if sys.prefix != sys.base_prefix:
        own.append(Path(sys.prefix).resolve())
    keep = []
    for entry in os.environ["PATH"].split(os.pathsep):
        where = Path(entry).resolve()
        if entry and not any(where == o or o in where.parents for o in own):
            keep.append(entry)
    return os.pathsep.join(keep)


RUN_MARKER = "CODE_TASKS_RUN"  # inherited by everything a run starts, so the harness can find it afterwards


def sweep(run_id: str) -> None:
    """Kill every process that still carries the run's marker. Serena starts each language server in a session
    of its own, so killing the run's process group does not reach them."""
    import psutil

    marked = []
    for p in psutil.process_iter():
        try:
            if p.environ().get(RUN_MARKER) == run_id:
                marked.append(p)
        except (psutil.Error, OSError):
            continue  # gone, a zombie, or another user's
    for p in marked:
        try:
            p.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(marked, timeout=5)
    for p in alive:
        try:
            p.kill()
        except psutil.Error:
            pass


def environment(with_user: bool = True, run_id: str | None = None) -> dict[str, str]:
    """A run's entire environment, as `env -i` would give it: nothing of the parent session leaks in. USER is
    what the keychain login needs; without it a run fails at login, before any model call. `run_id` marks
    every process the run starts, including those that leave its process group."""
    env = {"HOME": os.environ["HOME"], "PATH": agent_path(), "TMPDIR": os.environ.get("TMPDIR", "/tmp")}
    if with_user:
        env["USER"] = os.environ["USER"]
    if run_id:
        env[RUN_MARKER] = run_id
    env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
    env["ENABLE_TOOL_SEARCH"] = "false"  # MCP tools load directly, not through an extra ToolSearch call
    return env


def command(setup: Setup, prompt: str, mcp_config: Path | None, claude: str = "claude") -> list[str]:
    argv = [
        claude,
        "-p",
        prompt,
        "--model",
        config.MODEL,
        "--effort",
        config.EFFORT,
        "--output-format",
        "stream-json",
        "--verbose",
        "--setting-sources",
        "",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--tools",
        ",".join(config.BUILTIN_TOOLS),
        "--permission-mode",
        "dontAsk",
        "--allowedTools",
        ",".join(config.BUILTIN_TOOLS + setup.allow),
    ]
    if mcp_config is not None:
        argv += ["--mcp-config", str(mcp_config)]
    argv += [
        "--no-session-persistence",
        "--max-turns",
        str(config.MAX_TURNS),
        "--max-budget-usd",
        f"{config.MAX_BUDGET_USD:.2f}",
    ]
    return argv


def serena_home(dest: Path, cache: Path) -> Path:
    """A fresh SERENA_HOME: Serena rewrites its config file, so runs never share one. Project data (the symbol
    cache the warm-up fills) lives outside it, under the cache, and outside the worktree."""
    (dest / "contexts").mkdir(parents=True, exist_ok=True)
    shutil.copy(SERENA_DIR / "eval-nav.yml", dest / "contexts" / "eval-nav.yml")
    text = (SERENA_DIR / "serena_config.yml").read_text()
    (dest / "serena_config.yml").write_text(text.replace("@PROJECTS@", str(cache / "serena-projects")))
    return dest


def serena_argv(worktree: Path) -> list[str]:
    return [
        "--from",
        config.SERENA,
        "serena",
        "start-mcp-server",
        "--context",
        "eval-nav",
        "--project",
        str(worktree),
        "--enable-web-dashboard",
        "false",
        "--open-web-dashboard",
        "false",
        "--enable-gui-log-window",
        "false",
    ]


def mcp_config(setup: Setup, worktree: Path, repo: str, cache: Path, home: Path | None = None) -> dict | None:
    """The --mcp-config document for a run, or None for the baseline."""
    if setup.name == "baseline":
        return None
    if setup.name == "duckgrep":
        server = {"command": sys.executable, "args": ["-m", "duckgrep", "-C", str(worktree), "mcp"]}
        return {"mcpServers": {"duckgrep": server}}
    if home is None:
        raise ValueError("the serena setup needs a SERENA_HOME")
    env = {"SERENA_HOME": str(home), **rust_env(cache, repo)}
    server = {"command": shutil.which("uvx") or "uvx", "args": serena_argv(worktree), "env": env}
    return {"mcpServers": {"serena": server}}


def rust_env(cache: Path, repo: str) -> dict[str, str]:
    """Variables for anything that runs cargo: its caches live in the eval cache, the worktree stays clean, and
    the installed stable toolchain is used whatever the repo pins, so rustup never installs anything."""
    return {
        "PATH": f"{cache / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
        "CARGO_HOME": str(cache / "cargo-home"),
        "CARGO_TARGET_DIR": str(cache / "cargo-target" / repo.lower().replace("/", "__")),
        "RUSTUP_TOOLCHAIN": "stable",
    }
