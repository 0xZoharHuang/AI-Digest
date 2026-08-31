from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import resolve_binary

CODEX_EVENT_STREAM_LIMIT_BYTES = 16 * 1024 * 1024
RETRYABLE_CODEX_ERROR_CLASSES = {
    "authentication",
    "idle_timeout",
    "network",
    "quota",
}


class RetryableCodexError(RuntimeError):
    def __init__(self, phase: str, result: CodexResult):
        self.error_class = result.error_class or "process_error"
        detail = result.error or f"Codex exited with {result.exit_code}"
        super().__init__(f"{phase}: {self.error_class}: {detail}")


@dataclass
class CodexResult:
    exit_code: int
    thread_id: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    raw_lines: list[str] = field(default_factory=list)
    error_class: str | None = None
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and self.error_class is None


class CodexRunner:
    def __init__(self, binary: str, idle_timeout_seconds: int = 900):
        self.binary = resolve_binary(binary)
        self.idle_timeout_seconds = idle_timeout_seconds

    async def run(
        self,
        *,
        workspace: Path,
        prompt: str,
        model: str,
        reasoning: str,
        sandbox: str,
        output_file: Path | None = None,
        output_schema: Path | None = None,
        web_search: bool = False,
        agents: bool = False,
        subagent_threads: int = 4,
        resume_thread_id: str | None = None,
    ) -> CodexResult:
        workspace.mkdir(parents=True, exist_ok=True)
        isolated_tmp = workspace / ".tmp"
        isolated_tmp.mkdir(parents=True, exist_ok=True)
        permission_name, permission_definition = _permission_profile(sandbox, workspace)
        args = [
            self.binary,
            "exec",
            "--ignore-user-config",
            "--skip-git-repo-check",
            "--json",
            "--disable",
            "shell_snapshot",
            "-m",
            model,
            "-c",
            f'model_reasoning_effort="{reasoning}"',
            "-c",
            'shell_environment_policy.inherit="none"',
            "-c",
            'cli_auth_credentials_store="file"',
            "-c",
            'shell_environment_policy.include_only=["PATH","TMPDIR","LANG","LC_ALL"]',
            "-c",
            "shell_environment_policy.ignore_default_excludes=false",
            "-c",
            f'default_permissions="{permission_name}"',
            "-c",
            permission_definition,
            "--disable",
            "multi_agent_v2",
            "-C",
            str(workspace),
        ]
        if agents:
            args.extend(
                [
                    "--enable",
                    "multi_agent",
                    "-c",
                    f"agents.max_threads={subagent_threads}",
                    "-c",
                    "agents.max_depth=1",
                ]
            )
        else:
            args.extend(["--disable", "multi_agent"])
        if web_search:
            args.extend(["-c", 'web_search="live"'])
        else:
            args.extend(["-c", 'web_search="disabled"'])
        if output_schema:
            args.extend(["--output-schema", str(output_schema)])
        if output_file:
            args.extend(["--output-last-message", str(output_file)])
        if resume_thread_id:
            args.extend(["resume", resume_thread_id, prompt])
        else:
            args.append(prompt)

        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=_safe_environment(isolated_tmp),
            limit=CODEX_EVENT_STREAM_LIMIT_BYTES,
        )
        assert process.stdout is not None
        result = CodexResult(exit_code=-1)
        try:
            while True:
                try:
                    line = await asyncio.wait_for(
                        process.stdout.readline(), timeout=self.idle_timeout_seconds
                    )
                except TimeoutError:
                    process.terminate()
                    await process.wait()
                    result.exit_code = process.returncode or -1
                    result.error_class = "idle_timeout"
                    result.error = f"no Codex event for {self.idle_timeout_seconds}s"
                    return result
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                result.raw_lines.append(text)
                try:
                    event = json.loads(text)
                except json.JSONDecodeError:
                    continue
                result.events.append(event)
                if event.get("type") == "thread.started":
                    result.thread_id = event.get("thread_id")
                if event.get("type") == "turn.completed":
                    usage = event.get("usage") or {}
                    result.usage = {
                        "input_tokens": int(usage.get("input_tokens", 0)),
                        "cached_input_tokens": int(usage.get("cached_input_tokens", 0)),
                        "output_tokens": int(usage.get("output_tokens", 0)),
                    }
            result.exit_code = await process.wait()
        except asyncio.CancelledError:
            process.terminate()
            await process.wait()
            raise
        if result.exit_code != 0:
            combined = "\n".join(result.raw_lines[-40:])
            result.error_class = classify_codex_error(combined)
            result.error = combined[-4000:]
        return result


def classify_codex_error(text: str) -> str:
    lowered = text.lower()
    if any(value in lowered for value in ("quota", "usage limit", "billing", "credits")):
        return "quota"
    if any(value in lowered for value in ("unauthorized", "authentication", "token expired")):
        return "authentication"
    if any(value in lowered for value in ("network", "connection", "timed out", "timeout")):
        return "network"
    return "process_error"


def _safe_environment(isolated_tmp: Path | None = None) -> dict[str, str]:
    allowed = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "TMPDIR", "CODEX_HOME")
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    if isolated_tmp is not None:
        environment["TMPDIR"] = str(isolated_tmp)
    return environment


def _permission_profile(sandbox: str, workspace: Path | None = None) -> tuple[str, str]:
    parents = {"read-only": ":read-only", "workspace-write": ":workspace"}
    if sandbox not in parents:
        raise ValueError(f"unsupported protected Codex sandbox: {sandbox}")
    name = "ai_digest_read" if sandbox == "read-only" else "ai_digest_workspace"
    home = Path(os.environ.get("HOME", str(Path.home()))).expanduser().resolve()
    codex_home = Path(os.environ.get("CODEX_HOME", str(home / ".codex"))).expanduser().resolve()
    explicit_denies = [codex_home, home / ".ssh", home / "Library" / "Keychains"]
    denied = ",".join(
        f"{json.dumps(str(path))}=\"deny\"" for path in explicit_denies
    )
    workspace_rule = ""
    if workspace is not None:
        access = "read" if sandbox == "read-only" else "write"
        workspace_rule = f',{json.dumps(str(workspace.resolve()))}="{access}"'
    filesystem = (
        '":root"="deny",":minimal"="read",":slash_tmp"="deny",'
        + denied
        + workspace_rule
    )
    definition = (
        f'permissions.{name}={{extends="{parents[sandbox]}",'
        f"filesystem={{{filesystem}}}}}"
    )
    return name, definition
