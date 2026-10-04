"""Command contracts and permissions for agent roles."""

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from alphasieve.agents.executors import (
    CLAUDE_ALLOWED,
    CLAUDE_BIN,
    CLAUDE_DENIED,
    CODEX_BIN,
    MINER,
    PROFILES,
    RESEARCHER,
    REVIEWER,
    ClaudeExecutor,
    CodexExecutor,
    TurnContext,
)
from alphasieve.agents.integrity import scan_commands
from alphasieve.config import Settings


def _context(profile=MINER):
    return TurnContext(
        campaign_id="campaign", turn_id="campaign-t001", turn_index=1,
        workspace=Path("/tmp/agent-profile/workspace"), prompt="fixed prompt", model="fixed-model",
        effort=None, timeout_s=60, transcript_path=Path("/tmp/agent-profile/turn.jsonl"),
        trial_allowance=2, profile=profile,
    )


def _settings():
    return Settings(
        hot_root=Path("/tmp/agent-profile/hot"), store_root=Path("/tmp/agent-profile/store"),
        store_mount=None, config_dir=Path("/tmp/agent-profile/config"), role="agent", user="test",
    )


def test_miner_commands_match_pre_refactor_exactly():
    ctx = _context()
    settings = _settings()
    assert ctx.profile is MINER
    assert CLAUDE_ALLOWED is MINER.claude_allowed
    assert CLAUDE_DENIED is MINER.claude_denied
    assert CodexExecutor(settings).command(ctx) == [
        CODEX_BIN, "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral", "--skip-git-repo-check",
        "--json", "--color", "never", "-m", "fixed-model", "-c", 'model_reasoning_effort="high"',
        "-s", "workspace-write", "-c",
        'sandbox_workspace_write.writable_roots=["/tmp/agent-profile/hot/state", '
        '"/tmp/agent-profile/hot/cache", "/tmp/agent-profile/store/artifacts"]',
        "-c", "sandbox_workspace_write.network_access=false", "-C", "/tmp/agent-profile/workspace",
        "fixed prompt",
    ]
    assert ClaudeExecutor(settings).command(ctx) == [
        CLAUDE_BIN, "-p", "fixed prompt", "--bare", "--model", "fixed-model", "--output-format",
        "stream-json", "--verbose", "--permission-mode", "dontAsk", "--no-session-persistence",
        "--strict-mcp-config", "--settings",
        json.dumps({"alwaysThinkingEnabled": False, "includeCoAuthoredBy": False,
                    "permissions": {"defaultMode": "dontAsk"}}),
        "--allowedTools", "Bash(alphasieve:*),Bash(git log:*),Bash(git diff:*),Bash(git status:*),"
        "Read(./**),Glob,Grep,Write(./candidates/**),Write(./notes/**),Write(./reports/**),"
        "Edit(./candidates/**),Edit(./notes/**),Edit(./reports/**)",
        "--disallowedTools", "WebFetch,WebSearch,Task,NotebookEdit,Write(./program.md),"
        "Write(./brief.md),Write(./memory.md),Write(./directives.md)",
    ]


@pytest.mark.parametrize(
    ("profile", "write_dir", "bash_commands"),
    [
        (RESEARCHER, "drafts", ["alphasieve thesis", "alphasieve forecast list",
                                "alphasieve forecast show", "alphasieve library list", "ls", "cat"]),
        (REVIEWER, "reviews", ["alphasieve thesis", "ls", "cat"]),
    ],
)
def test_thesis_profiles(profile, write_dir, bash_commands):
    assert PROFILES[profile.name] is profile
    assert profile.codex_network is True
    assert profile.writable_subdirs == (write_dir,)
    assert profile.read_only_files == ("brief.md", "program.md")
    assert profile.claude_allowed == [
        "Read(./**)", "Glob(./**)", "Grep(./**)", "WebSearch", "WebFetch",
        f"Write(./{write_dir}/**)", f"Edit(./{write_dir}/**)",
        *(f"Bash({command}:*)" for command in bash_commands),
    ]
    assert profile.claude_denied == [
        "Task", "NotebookEdit", "Write(./brief.md)", "Edit(./brief.md)",
        "Write(./program.md)", "Edit(./program.md)",
    ]
    ctx = _context(profile)
    assert "sandbox_workspace_write.network_access=true" in CodexExecutor(_settings()).command(ctx)
    command = ClaudeExecutor(_settings()).command(ctx)
    assert command[command.index("--allowedTools") + 1] == ",".join(profile.claude_allowed)
    assert command[command.index("--disallowedTools") + 1] == ",".join(profile.claude_denied)
    tools = command[command.index("--tools") + 1].split(",")
    assert {"Write", "WebSearch", "WebFetch"} <= set(tools)
    assert "--bare" not in command and "--disable-slash-commands" in command
    with pytest.raises(FrozenInstanceError):
        profile.codex_network = False


@pytest.mark.parametrize("reference", [
    "cat holdings_snapshots", "SELECT * FROM journal_entries", "alphasieve book show",
    "alphasieve journal list",
])
def test_personal_holdings_references_are_flagged(reference):
    assert any("personal holdings access" in finding
               for finding in scan_commands([reference], [], Path("/tmp/agent-profile/workspace")))
    assert any("personal holdings access" in finding
               for finding in scan_commands([], [reference], Path("/tmp/agent-profile/workspace")))


def test_unrelated_commands_are_not_flagged():
    assert scan_commands(["alphasieve thesis list"], ["brief.md"], Path("/tmp/agent-profile/workspace")) == []
