"""Turn a Claude Code stream-json transcript into one run's measurements."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from . import config

SEARCH_TOOLS = frozenset({"Grep", "Glob", "Bash"})
NOT_USE = frozenset({"mcp__serena__initial_instructions"})  # MCP calls that are not using the tool
BUILTIN_PLUGINS = frozenset({"cc-plugin-agents-md", "cc-plugin-telemetry"})  # cannot be removed; same in every setup


@dataclass
class Call:
    id: str
    name: str
    round: int  # the 1-based API round trip that issued it
    input: dict
    result: str = ""
    is_error: bool = False
    seconds: float | None = None  # from the tool_use event to its tool_result event


@dataclass
class Transcript:
    init: dict = field(default_factory=dict)
    calls: list[Call] = field(default_factory=list)  # issue order; denied calls excluded
    denied: list[str] = field(default_factory=list)  # names of the calls the permission mode refused
    rounds: int = 0  # distinct assistant message IDs; synthetic error messages excluded
    hooks: int = 0
    result: dict = field(default_factory=dict)  # the final result event, {} if the stream was cut off
    api_error: str | None = None  # e.g. "authentication_failed", from a synthetic assistant message
    usage_by_message: dict[str, dict] = field(default_factory=dict)  # each API call's usage, as streamed


def _time(stamp: str | None) -> datetime | None:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")) if stamp else None


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def read(lines: Iterable[str]) -> Transcript:
    tr = Transcript()
    round_of: dict[str, int] = {}
    calls: dict[str, Call] = {}
    issued: dict[str, datetime | None] = {}
    for raw in lines:
        try:
            e = json.loads(raw)
        except json.JSONDecodeError:
            continue  # blank, or the last line of a killed run
        kind = e.get("type")
        if kind == "system":
            sub = str(e.get("subtype", ""))
            if sub == "init":
                tr.init = e
            elif sub.startswith("hook"):
                tr.hooks += 1
        elif kind == "assistant":
            msg = e.get("message") or {}
            if e.get("error") or msg.get("model") == "<synthetic>":
                tr.api_error = tr.api_error or e.get("error") or "synthetic"
                continue
            mid = msg.get("id")
            round_of.setdefault(mid, len(round_of) + 1)
            if msg.get("usage"):
                tr.usage_by_message[mid] = msg["usage"]
            for b in msg.get("content") or []:
                if b.get("type") == "tool_use" and b.get("id") not in calls:
                    calls[b["id"]] = Call(b["id"], b.get("name", ""), round_of[mid], b.get("input") or {})
                    issued[b["id"]] = _time(e.get("timestamp"))
        elif kind == "user":
            content = (e.get("message") or {}).get("content")
            for b in content if isinstance(content, list) else []:
                call = calls.get(b.get("tool_use_id")) if isinstance(b, dict) else None
                if call is None or b.get("type") != "tool_result":
                    continue
                call.result = _text(b.get("content"))
                call.is_error = bool(b.get("is_error"))
                t0, t1 = issued.get(call.id), _time(e.get("timestamp"))
                if t0 and t1:
                    call.seconds = (t1 - t0).total_seconds()
        elif kind == "result":
            tr.result = e
    denied_ids = {d.get("tool_use_id") for d in tr.result.get("permission_denials") or []}
    tr.denied = [c.name for c in calls.values() if c.id in denied_ids]
    tr.calls = [c for c in calls.values() if c.id not in denied_ids]
    tr.rounds = len(round_of)
    return tr


def tokens(result: dict, messages: dict[str, dict] | None = None) -> dict[str, int]:
    """The session's token classes, from the result event. A run cut off before it (killed at the wall-clock
    limit) has none; then they are summed over its API calls (`messages`, from Transcript.usage_by_message).
    Each call's input side is known when it starts, so only output is then a lower bound."""
    if not result.get("usage") and messages:
        total = dict.fromkeys(config.RATES, 0)
        for usage in messages.values():
            for k, v in tokens({"usage": usage}).items():
                total[k] += v
        return total
    u = result.get("usage") or {}
    split = u.get("cache_creation") or {}
    write = int(u.get("cache_creation_input_tokens") or 0)
    write_1h = int(split.get("ephemeral_1h_input_tokens") or 0)
    return {
        "input": int(u.get("input_tokens") or 0),
        "cache_write_5m": write - write_1h,
        "cache_write_1h": write_1h,
        "cache_read": int(u.get("cache_read_input_tokens") or 0),
        "output": int(u.get("output_tokens") or 0),  # includes thinking
    }


def cost(tok: dict[str, int], rates: dict[str, float] = config.RATES) -> float:
    return sum(tok[k] * rates[k] for k in rates) / 1e6


def config_problems(tr: Transcript, tools: set[str], servers: set[str], model: str = config.MODEL) -> list[str]:
    """Why the session was not configured as the setup requires; empty when it was."""
    init = tr.init
    if not init:
        return ["no init event"]
    problems = []
    if init.get("model") != model:
        problems.append(f"model is {init.get('model')!r}, not {model!r}")
    got = set(init.get("tools") or [])
    if got != tools:
        problems.append(f"tools differ: extra {sorted(got - tools)}, missing {sorted(tools - got)}")
    status = {s.get("name"): s.get("status") for s in init.get("mcp_servers") or []}
    if set(status) != servers:
        problems.append(f"MCP servers are {sorted(status)}, not {sorted(servers)}")
    problems += [f"MCP server {n} is {s}" for n, s in sorted(status.items()) if s != "connected"]
    plugins = {p.get("name") for p in init.get("plugins") or []} - BUILTIN_PLUGINS
    if plugins:
        problems.append(f"plugins loaded: {sorted(plugins)}")
    for key in ("skills", "slash_commands", "memory_paths"):
        if init.get(key):
            problems.append(f"{key} is not empty")
    if init.get("permissionMode") != "dontAsk":
        problems.append(f"permission mode is {init.get('permissionMode')!r}")
    if tr.hooks:
        problems.append(f"{tr.hooks} hook events")
    return problems


def infrastructure_error(tr: Transcript) -> bool:
    """The run failed for reasons outside the agent (login, rate limit, API outage): rerun it, never score it."""
    return tr.api_error is not None or tr.result.get("terminal_reason") == "api_error"


def permanent_error(tr: Transcript) -> bool:
    """An infrastructure error no retry can fix: login, billing or a malformed request. Everything else (rate
    limit, overload, server errors, unknown, a bare synthetic message) is transient."""
    error = (tr.api_error or "").lower()
    return any(word in error for word in ("auth", "billing", "invalid_request"))


def metrics(tr: Transcript) -> dict:
    counts = Counter(c.name for c in tr.calls)
    total = sum(counts.values())
    mcp = sum(n for name, n in counts.items() if name.startswith("mcp__"))
    # Serena's instructions are a manual, not a lookup: calling only them is not using the tool
    used = sum(n for name, n in counts.items() if name.startswith("mcp__") and name not in NOT_USE)
    tok = tokens(tr.result, tr.usage_by_message)
    seconds: Counter[str] = Counter()
    for c in tr.calls:
        seconds[c.name] += c.seconds or 0.0
    res = tr.result
    return {
        "model": tr.init.get("model"),
        "cli_version": tr.init.get("claude_code_version"),
        "terminal_reason": res.get("terminal_reason"),
        "is_error": bool(res.get("is_error", True)),  # no result event: the run was cut off
        "api_error": tr.api_error,
        "num_turns": res.get("num_turns"),  # Claude Code's count (tool calls + 1); kept, not a metric
        "rounds": tr.rounds,
        "tool_calls": total,
        "tool_counts": dict(counts),
        "search_calls": sum(n for name, n in counts.items() if name in SEARCH_TOOLS),
        "read_calls": counts.get("Read", 0),
        "mcp_calls": mcp,
        "adopted": used > 0,
        "mcp_share": used / total if total else 0.0,
        "denied": len(tr.denied),
        "denied_tools": tr.denied,
        "tokens": tok,
        "usage_complete": bool(res.get("usage")),  # False: summed from the API calls, output a lower bound
        "tokens_total": sum(tok.values()),
        "cost_usd": round(cost(tok), 6),
        "cli_cost_usd": res.get("total_cost_usd"),  # swings with cache warmth; recorded, not used
        "models": sorted(res.get("modelUsage") or {}),
        "duration_ms": res.get("duration_ms"),
        "tool_seconds": {k: round(v, 3) for k, v in seconds.items()},
        "final_text": res.get("result") or "",
    }
