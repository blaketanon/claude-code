"""Runs one investigation with the Claude Agent SDK (the same harness as Claude Code) in the
read-only workspace, and returns the structured findings."""
import asyncio
import logging
import os
import socket

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ResultMessage, SystemMessage, TextBlock, ToolUseBlock, query)

from . import prompts
from .guard import check_fix_command, check_readonly

log = logging.getLogger(__name__)


class RunOutcome:
    def __init__(self):
        self.findings = None
        self.text = None
        self.cost_usd = None
        self.num_turns = None
        self.session_id = None
        self.subtype = None
        self.error = None


def _env(cfg):
    env = {}
    for k in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_USE_BEDROCK", "AWS_REGION", "AWS_DEFAULT_REGION", "AWS_PROFILE",
              "ANTHROPIC_BEDROCK_BASE_URL", "HOME", "PATH"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    env.setdefault("AWS_DEFAULT_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
    env["TZ"] = "America/New_York"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["SF_DISABLE_TELEMETRY"] = "true"
    env["PAGER"] = "cat"
    env["AWS_PAGER"] = ""
    if cfg.get("READONLY_DATABASE_URLS"):
        env["READONLY_DATABASE_URLS"] = cfg["READONLY_DATABASE_URLS"]
    return env


def make_readonly_hook(cfg, on_event=None):
    allow_psql = bool(cfg.get("READONLY_DATABASE_URLS"))

    async def pre_tool_use(input_data, tool_use_id, context):
        if input_data.get("tool_name") != "Bash":
            return {}
        cmd = (input_data.get("tool_input") or {}).get("command", "")
        verdict = check_readonly(cmd, allow_psql=allow_psql)
        if verdict:
            return {}
        if on_event:
            on_event("tool", f"[denied] {cmd[:300]} — {verdict.reason}")
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": f"Read-only shell: {verdict.reason}"}}
    return pre_tool_use


def make_fix_hook(on_event=None):
    async def pre_tool_use(input_data, tool_use_id, context):
        if input_data.get("tool_name") != "Bash":
            return {}
        cmd = (input_data.get("tool_input") or {}).get("command", "")
        verdict = check_fix_command(cmd)
        if verdict:
            return {}
        if on_event:
            on_event("tool", f"[denied] {cmd[:300]} — {verdict.reason}")
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": verdict.reason}}
    return pre_tool_use


def _describe_tool(block):
    inp = block.input or {}
    if block.name == "Bash":
        return f"$ {inp.get('command', '')}"[:600]
    if block.name in ("Read", "Edit", "Write"):
        return f"{block.name} {inp.get('file_path', '')}"
    if block.name in ("Grep", "Glob"):
        return f"{block.name} {inp.get('pattern', '')} {inp.get('path', '')}".strip()
    return f"{block.name} {str(inp)[:300]}"


async def _run(prompt, options, on_event):
    out = RunOutcome()
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, SystemMessage) and getattr(message, "subtype", "") == "init":
            on_event("status", "Claude started")
        elif isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    on_event("tool", _describe_tool(block))
                elif isinstance(block, TextBlock) and block.text.strip():
                    on_event("text", block.text.strip()[:2000])
        elif isinstance(message, ResultMessage):
            out.subtype = message.subtype
            out.cost_usd = message.total_cost_usd
            out.num_turns = message.num_turns
            out.session_id = message.session_id
            out.text = message.result
            out.findings = message.structured_output if isinstance(message.structured_output, dict) else None
            if message.is_error or message.subtype != "success":
                out.error = "; ".join(message.errors or []) or f"Claude stopped: {message.subtype}"
    return out


def run_investigation(inv, cfg, workspace_root, on_event=lambda kind, text: None):
    """Blocking. Returns RunOutcome. `on_event(kind, text)` receives progress lines."""
    options = ClaudeAgentOptions(
        cwd=workspace_root,
        system_prompt={"type": "preset", "preset": "claude_code", "append": prompts.INVESTIGATOR_APPEND},
        allowed_tools=["Read", "Grep", "Glob", "Bash", "TodoWrite"],
        disallowed_tools=["Edit", "Write", "MultiEdit", "NotebookEdit", "WebFetch", "WebSearch", "Task", "Agent", "KillShell"],
        permission_mode="dontAsk",
        model=cfg.get("ANTHROPIC_MODEL") or None,
        effort=cfg.get("CLAUDE_EFFORT") or None,
        max_turns=int(cfg.get("INVESTIGATION_MAX_TURNS") or 80),
        max_budget_usd=float(cfg.get("INVESTIGATION_BUDGET_USD") or 8.0),
        output_format={"type": "json_schema", "schema": prompts.FINDINGS_SCHEMA},
        hooks={"PreToolUse": [{"matcher": "Bash", "hooks": [make_readonly_hook(cfg, on_event)]}]},
        env=_env(cfg),
        setting_sources=[],          # ignore any CLAUDE.md / settings inside the cloned repos
        user=f"gero-trace@{socket.gethostname()}",
    )
    return asyncio.run(_run(prompts.investigation_prompt(inv), options, on_event))


def run_fix(inv, cfg, repo_dir, repo, report_md, on_event=lambda kind, text: None):
    options = ClaudeAgentOptions(
        cwd=repo_dir,
        system_prompt={"type": "preset", "preset": "claude_code", "append": prompts.FIXER_APPEND},
        allowed_tools=["Read", "Grep", "Glob", "Bash", "Edit", "Write", "MultiEdit", "TodoWrite"],
        disallowed_tools=["WebFetch", "WebSearch", "NotebookEdit", "Task", "Agent"],
        permission_mode="acceptEdits",
        model=cfg.get("ANTHROPIC_MODEL") or None,
        effort=cfg.get("CLAUDE_EFFORT") or None,
        max_turns=int(cfg.get("FIX_MAX_TURNS") or 120),
        max_budget_usd=float(cfg.get("FIX_BUDGET_USD") or 15.0),
        output_format={"type": "json_schema", "schema": prompts.FIX_SCHEMA},
        hooks={"PreToolUse": [{"matcher": "Bash", "hooks": [make_fix_hook(on_event)]}]},
        env=_env(cfg),
        setting_sources=["project"],   # the target repo's CLAUDE.md is useful here
        user=f"gero-trace-fix@{socket.gethostname()}",
    )
    return asyncio.run(_run(prompts.fix_prompt(inv, repo, report_md), options, on_event))
