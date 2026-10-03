"""Agent executors. Each runs one turn as a subprocess with an explicit minimal environment, isolated harness
configuration and a tool allow-list, and returns a normalised result. They hold no research state."""

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from alphasieve.config import Settings

CODEX_BIN = shutil.which("codex") or "/usr/local/bin/codex"
CLAUDE_BIN = shutil.which("claude") or "/root/.local/bin/claude"
CODEX_HOME = os.environ.get("ALPHASIEVE_CODEX_HOME", "/data/root-home/.codex")
AIHUB_BASE_URL = "http://api.aihub.woa.com/standard"
PASSTHROUGH = ("http_proxy", "https_proxy", "no_proxy", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "LANG", "TZ")


@dataclass(frozen=True)
class AgentProfile:
    name: str
    claude_allowed: list[str]
    claude_denied: list[str]
    codex_network: bool
    writable_subdirs: tuple[str, ...]
    read_only_files: tuple[str, ...]


MINER = AgentProfile(
    name="miner",
    claude_allowed=[
        "Bash(alphasieve:*)", "Bash(git log:*)", "Bash(git diff:*)", "Bash(git status:*)",
        "Read(./**)", "Glob", "Grep", "Write(./candidates/**)", "Write(./notes/**)",
        "Write(./reports/**)", "Edit(./candidates/**)", "Edit(./notes/**)", "Edit(./reports/**)",
    ],
    claude_denied=[
        "WebFetch", "WebSearch", "Task", "NotebookEdit", "Write(./program.md)",
        "Write(./brief.md)", "Write(./memory.md)", "Write(./directives.md)",
    ],
    codex_network=False,
    writable_subdirs=("candidates", "notes", "reports"),
    read_only_files=("program.md", "brief.md", "memory.md", "directives.md"),
)
CLAUDE_ALLOWED = MINER.claude_allowed
CLAUDE_DENIED = MINER.claude_denied

_RESEARCH_READ_WEB = ["Read(./**)", "Glob(./**)", "Grep(./**)", "WebSearch", "WebFetch"]
_THESIS_READ_ONLY = ["Task", "NotebookEdit", "Write(./brief.md)", "Edit(./brief.md)",
                     "Write(./program.md)", "Edit(./program.md)"]

RESEARCHER = AgentProfile(
    name="researcher",
    claude_allowed=[
        *_RESEARCH_READ_WEB, "Write(./drafts/**)", "Edit(./drafts/**)",
        "Bash(alphasieve thesis:*)", "Bash(alphasieve forecast list:*)",
        "Bash(alphasieve forecast show:*)", "Bash(alphasieve library list:*)",
        "Bash(ls:*)", "Bash(cat:*)",
    ],
    claude_denied=_THESIS_READ_ONLY.copy(),
    codex_network=True,
    writable_subdirs=("drafts",),
    read_only_files=("brief.md", "program.md"),
)
REVIEWER = AgentProfile(
    name="reviewer",
    claude_allowed=[
        *_RESEARCH_READ_WEB, "Write(./reviews/**)", "Edit(./reviews/**)",
        "Bash(alphasieve thesis:*)", "Bash(ls:*)", "Bash(cat:*)",
    ],
    claude_denied=_THESIS_READ_ONLY.copy(),
    codex_network=True,
    writable_subdirs=("reviews",),
    read_only_files=("brief.md", "program.md"),
)
PROFILES = {profile.name: profile for profile in (MINER, RESEARCHER, REVIEWER)}


@dataclass
class TurnContext:
    campaign_id: str
    turn_id: str
    turn_index: int
    workspace: Path
    prompt: str
    model: str
    effort: str | None
    timeout_s: int
    transcript_path: Path
    trial_allowance: int
    profile: AgentProfile = MINER


@dataclass
class TurnResult:
    status: str
    summary: str = ""
    usage: dict = field(default_factory=dict)
    commands: list[str] = field(default_factory=list)
    reads: list[str] = field(default_factory=list)
    error: str | None = None
    exit_code: int | None = None


def load_secrets(settings: Settings) -> dict[str, str]:
    path = settings.hot_root / "secrets.env"
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def agent_env(settings: Settings, ctx: TurnContext, user: str) -> dict[str, str]:
    venv_bin = str(Path(sys.executable).parent)
    env = {k: os.environ[k] for k in PASSTHROUGH if k in os.environ}
    env.update({
        "PATH": f"{venv_bin}:/usr/local/bin:/usr/bin:/bin",
        "ALPHASIEVE_ROLE": "agent",
        "ALPHASIEVE_USER": user,
        "ALPHASIEVE_CAMPAIGN": ctx.campaign_id,
        "ALPHASIEVE_TURN_ALLOWANCE": str(ctx.trial_allowance),
        "ALPHASIEVE_TURN": ctx.turn_id,
        "ALPHASIEVE_HOT_ROOT": str(settings.hot_root),
        "ALPHASIEVE_STORE_ROOT": str(settings.store_root),
        "ALPHASIEVE_STORE_MOUNT": str(settings.store_mount or ""),
        "ALPHASIEVE_CONFIG_DIR": str(settings.config_dir),
        "PYTHONDONTWRITEBYTECODE": "1",
        "NUMBA_CACHE_DIR": str(settings.cache_dir / "numba"),
        "NO_COLOR": "1",
    })
    return env


def _run(cmd: list[str], env: dict, cwd: Path, timeout_s: int, transcript: Path,
         on_line: Callable[[dict], None]) -> tuple[int | None, bool, str]:
    transcript.parent.mkdir(parents=True, exist_ok=True)
    stderr_path = transcript.with_suffix(".stderr")
    with open(transcript, "w", encoding="utf-8") as out, open(stderr_path, "w", encoding="utf-8") as err:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=err,
                                text=True, encoding="utf-8", errors="replace", start_new_session=True)

        def pump() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                out.write(line)
                out.flush()
                try:
                    on_line(json.loads(line))
                except (json.JSONDecodeError, TypeError, KeyError, AttributeError):
                    pass

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        timed_out = False
        try:
            proc.wait(timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(15)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
        reader.join(30)
    stderr_tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-2000:]
    return proc.returncode, timed_out, stderr_tail


def _finish(result: TurnResult, code: int | None, timed_out: bool, stderr: str) -> TurnResult:
    result.exit_code = code
    if timed_out:
        result.status, result.error = "timeout", "turn exceeded its time limit"
    elif code != 0:
        result.status, result.error = "failed", result.error or f"exit {code}: {stderr[-500:]}"
    else:
        result.status = "completed"
    return result


class CodexExecutor:
    harness = "codex"

    def __init__(self, settings: Settings):
        self.settings = settings

    def command(self, ctx: TurnContext) -> list[str]:
        s = self.settings
        roots = [str(s.state_db.parent), str(s.cache_dir), str(s.artifacts_dir)]
        return [
            CODEX_BIN, "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral", "--skip-git-repo-check",
            "--json", "--color", "never", "-m", ctx.model, "-c", f'model_reasoning_effort="{ctx.effort or "high"}"',
            "-s", "workspace-write", "-c", f"sandbox_workspace_write.writable_roots={json.dumps(roots)}",
            "-c", f"sandbox_workspace_write.network_access={str(ctx.profile.codex_network).lower()}",
            "-C", str(ctx.workspace), ctx.prompt,
        ]

    def run(self, ctx: TurnContext) -> TurnResult:
        env = agent_env(self.settings, ctx, "codex")
        env.update({"CODEX_HOME": CODEX_HOME, "HOME": str(ctx.workspace)})
        result = TurnResult(status="running")
        messages: list[str] = []

        def on_line(event: dict) -> None:
            kind = event.get("type")
            item = event.get("item") or {}
            if kind == "item.completed" and item.get("type") == "agent_message":
                messages.append(item.get("text", ""))
            elif kind in ("item.started", "item.completed") and item.get("type") == "command_execution":
                if kind == "item.started" or item.get("command") not in result.commands[-1:]:
                    result.commands.append(item.get("command", ""))
            elif kind == "turn.completed":
                u = event.get("usage") or {}
                for key in ("input_tokens", "cached_input_tokens", "output_tokens"):
                    result.usage[key] = result.usage.get(key, 0) + (u.get(key) or 0)
            elif kind in ("turn.failed", "error"):
                result.error = json.dumps(event.get("error") or event)[:1000]

        code, timed_out, stderr = _run(self.command(ctx), env, ctx.workspace, ctx.timeout_s, ctx.transcript_path,
                                       on_line)
        result.summary = messages[-1] if messages else ""
        return _finish(result, code, timed_out, stderr)


class ClaudeExecutor:
    harness = "claude"

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def config_dir(self) -> Path:
        return self.settings.hot_root / "agents" / "claude-config"

    def command(self, ctx: TurnContext) -> list[str]:
        settings_json = json.dumps({"alwaysThinkingEnabled": False, "includeCoAuthoredBy": False,
                                    "permissions": {"defaultMode": "dontAsk"}})
        return [
            CLAUDE_BIN, "-p", ctx.prompt, "--bare", "--model", ctx.model, "--output-format", "stream-json",
            "--verbose", "--permission-mode", "dontAsk", "--no-session-persistence", "--strict-mcp-config",
            "--settings", settings_json, "--allowedTools", ",".join(ctx.profile.claude_allowed),
            "--disallowedTools", ",".join(ctx.profile.claude_denied),
        ]

    def run(self, ctx: TurnContext) -> TurnResult:
        secrets = load_secrets(self.settings)
        key = secrets.get("AIHUB_API_KEY")
        if not key:
            return TurnResult(status="failed", error="AIHUB_API_KEY missing from secrets.env")
        self.config_dir.mkdir(parents=True, exist_ok=True)
        env = agent_env(self.settings, ctx, "claude")
        env.update({
            "HOME": str(self.config_dir), "CLAUDE_CONFIG_DIR": str(self.config_dir),
            "ANTHROPIC_BASE_URL": AIHUB_BASE_URL, "ANTHROPIC_API_KEY": key, "MAX_THINKING_TOKENS": "0",
            "DISABLE_TELEMETRY": "1", "DISABLE_AUTOUPDATER": "1", "DISABLE_ERROR_REPORTING": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        })
        result = TurnResult(status="running")

        def on_line(event: dict) -> None:
            kind = event.get("type")
            if kind == "assistant":
                for block in (event.get("message") or {}).get("content") or []:
                    if block.get("type") != "tool_use":
                        continue
                    inp = block.get("input") or {}
                    if block.get("name") == "Bash":
                        result.commands.append(inp.get("command", ""))
                    elif block.get("name") in ("Read", "Glob", "Grep"):
                        result.reads.append(inp.get("file_path") or inp.get("path") or inp.get("pattern") or "")
            elif kind == "result":
                u = event.get("usage") or {}
                result.usage = {
                    "input_tokens": (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
                    + (u.get("cache_creation_input_tokens") or 0),
                    "cached_input_tokens": u.get("cache_read_input_tokens") or 0,
                    "output_tokens": u.get("output_tokens") or 0,
                    "cost_usd": event.get("total_cost_usd") or 0.0,
                    "num_turns": event.get("num_turns"),
                }
                result.summary = event.get("result") or ""
                if event.get("is_error"):
                    result.error = (event.get("result") or event.get("subtype") or "error")[:1000]

        code, timed_out, stderr = _run(self.command(ctx), env, ctx.workspace, ctx.timeout_s, ctx.transcript_path,
                                       on_line)
        if result.error and code == 0:
            code = 1
        return _finish(result, code, timed_out, stderr)


class FakeExecutor:
    """Runs a scripted turn: a callable receives (ctx, env, run_cli) and returns a summary string."""

    harness = "fake"

    def __init__(self, settings: Settings, script: Callable):
        self.settings = settings
        self.script = script

    def run(self, ctx: TurnContext) -> TurnResult:
        env = agent_env(self.settings, ctx, "fake")
        result = TurnResult(status="running")
        log = []

        def run_cli(*argv: str, extra_env: dict | None = None) -> dict:
            cmd = [sys.executable, "-m", "alphasieve.cli.main", *argv, "--json"]
            result.commands.append(" ".join(["alphasieve", *argv]))
            proc = subprocess.run(cmd, cwd=ctx.workspace, env={**env, **(extra_env or {})}, capture_output=True,
                                  text=True, timeout=ctx.timeout_s)
            log.append({"cmd": argv, "code": proc.returncode, "stdout": proc.stdout[-4000:]})
            try:
                return json.loads(proc.stdout.strip().splitlines()[-1])
            except (IndexError, json.JSONDecodeError):
                return {"status": "error", "error": {"message": proc.stderr[-1000:]}}

        try:
            result.summary = self.script(ctx, env, run_cli, result) or ""
            result.status = "completed"
        except Exception as exc:  # noqa: BLE001
            result.status, result.error = "failed", f"{type(exc).__name__}: {exc}"
        ctx.transcript_path.parent.mkdir(parents=True, exist_ok=True)
        ctx.transcript_path.write_text("\n".join(json.dumps(e) for e in log) + "\n", encoding="utf-8")
        return result


def make_executor(settings: Settings, harness: str, fake_script: Callable | None = None):
    if harness == "codex":
        return CodexExecutor(settings)
    if harness == "claude":
        return ClaudeExecutor(settings)
    if harness == "fake":
        if fake_script is None:
            raise ValueError("fake executor needs a script")
        return FakeExecutor(settings, fake_script)
    raise ValueError(f"unknown harness {harness}")
