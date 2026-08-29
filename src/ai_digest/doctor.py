from __future__ import annotations

import os
import pwd
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .config import REPO_ROOT, RuntimeConfig, SourcesConfig, resolve_binary
from .x_auth import XTokenStore


def run_doctor(runtime: RuntimeConfig, sources: SourcesConfig) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str, required: bool = True) -> None:
        checks.append({"name": name, "ok": ok, "required": required, "detail": detail})

    add("python", True, __import__("sys").version.split()[0])
    add("runtime_root", _writable(runtime.runtime_root), str(runtime.runtime_root))
    codex = resolve_binary(runtime.codex.binary)
    lark = resolve_binary(runtime.lark.binary)
    add("codex_cli", Path(codex).exists(), codex)
    add("lark_cli", Path(lark).exists(), lark, required=False)
    add("github_auth", _command_ok(["gh", "auth", "status"]), "gh keyring/login")
    x_enabled = bool(sources.x_list.get("enabled"))
    x_tokens = XTokenStore().load()
    add(
        "x_api",
        bool(x_tokens and sources.x_list.get("list_id")),
        "token/list configured" if x_enabled else "disabled",
        required=x_enabled,
    )
    executable = _playwright_executable()
    add(
        "playwright_chromium",
        bool(executable and executable.exists()),
        str(executable) if executable else "unable to resolve browser path",
        required=bool(sources.x_for_you.get("enabled")),
    )
    try:
        runner = pwd.getpwnam("ai-digest-runner")
        add("runner_user", True, f"uid={runner.pw_uid}, home={runner.pw_dir}")
    except KeyError:
        add("runner_user", False, "ai-digest-runner does not exist")
    add(
        "shared_runtime",
        runtime.shared_runtime_root.exists(),
        str(runtime.shared_runtime_root),
    )
    add(
        "lark_config",
        bool(runtime.lark.space_id and runtime.lark.receiver_open_id),
        "space and receiver configured" if runtime.lark.space_id else "not configured",
        required=False,
    )
    if runtime.lark.space_id:
        add(
            "lark_auth",
            _command_ok([lark, "auth", "status", "--verify"]),
            f"identity={runtime.lark.identity}",
        )
        add(
            "lark_space",
            _lark_space_access(lark, runtime.lark.space_id),
            runtime.lark.space_id,
        )
    return {
        "ok": all(check["ok"] for check in checks if check["required"]),
        "repo_root": str(REPO_ROOT),
        "checks": checks,
    }


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        return os.access(path, os.W_OK)
    except OSError:
        return False


def _command_ok(args: list[str]) -> bool:
    if not shutil.which(args[0]):
        return False
    try:
        return subprocess.run(args, capture_output=True, timeout=15).returncode == 0
    except subprocess.SubprocessError:
        return False


def _playwright_executable() -> Path | None:
    code = (
        "from playwright.sync_api import sync_playwright; "
        "p=sync_playwright().start(); print(p.chromium.executable_path); p.stop()"
    )
    try:
        process = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=15
        )
        if process.returncode == 0 and process.stdout.strip():
            return Path(process.stdout.strip())
    except subprocess.SubprocessError:
        pass
    return None


def _lark_space_access(binary: str, space_id: str) -> bool:
    try:
        process = subprocess.run(
            [binary, "wiki", "+space-list", "--page-all", "--as", "user"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return process.returncode == 0 and space_id in process.stdout
    except subprocess.SubprocessError:
        return False


def format_doctor(result: dict[str, Any]) -> str:
    lines = [f"AI Intelligence Radar doctor: {'OK' if result['ok'] else 'NOT READY'}"]
    for check in result["checks"]:
        marker = "✓" if check["ok"] else "!" if not check["required"] else "✗"
        lines.append(f"{marker} {check['name']}: {check['detail']}")
    return "\n".join(lines)
