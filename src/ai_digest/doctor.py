from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .config import REPO_ROOT, RuntimeConfig, SourcesConfig, resolve_binary
from .x_provider import TwitterApiIOKeyStore

MIN_RUNTIME_FREE_BYTES = 5 * 1024**3


def run_doctor(runtime: RuntimeConfig, sources: SourcesConfig) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str, required: bool = True) -> None:
        checks.append({"name": name, "ok": ok, "required": required, "detail": detail})

    add("python", True, __import__("sys").version.split()[0])
    add("runtime_root", _writable(runtime.runtime_root), str(runtime.runtime_root))
    free_bytes = shutil.disk_usage(runtime.runtime_root).free
    add(
        "runtime_disk_free",
        free_bytes >= MIN_RUNTIME_FREE_BYTES,
        f"{free_bytes / 1024**3:.1f} GiB free (minimum 5.0 GiB)",
    )
    codex = resolve_binary(runtime.codex.binary)
    lark = resolve_binary(runtime.lark.binary)
    add("codex_cli", Path(codex).exists(), codex)
    for model in sorted(
        {
            runtime.codex.router_model,
            runtime.codex.research_model,
            runtime.codex.brief_model,
        }
    ):
        add(
            f"codex_model:{model}",
            _codex_model_access(codex, model),
            "minimal authenticated codex exec completed",
        )
    add("lark_cli", Path(lark).exists(), lark)
    add("github_auth", _command_ok(["gh", "auth", "status"]), "gh keyring/login")
    x_enabled = bool(sources.x_list.get("enabled"))
    x_required = bool(sources.x_list.get("required", False))
    add(
        "x_list_provider",
        bool(x_enabled and TwitterApiIOKeyStore().load() and sources.x_list.get("list_ids")),
        "TwitterAPI.io key and public Lists configured"
        if x_enabled
        else "disabled (required)"
        if x_required
        else "disabled",
        required=x_enabled or x_required,
    )
    executable = _playwright_executable()
    add(
        "playwright_chromium",
        bool(executable and executable.exists()),
        str(executable) if executable else "unable to resolve browser path",
        required=bool(sources.x_for_you.get("enabled")),
    )
    add(
        "x_for_you_policy",
        not bool(sources.x_for_you.get("enabled"))
        or bool(sources.x_for_you.get("personal_browser_risk_acknowledged")),
        "disabled"
        if not sources.x_for_you.get("enabled")
        else "personal browser risk explicitly acknowledged",
        required=True,
    )
    cookie_file = Path(
        str(sources.x_for_you.get("cookie_file", "config/twitter_cookies.json"))
    ).expanduser()
    if not cookie_file.is_absolute():
        cookie_file = REPO_ROOT / cookie_file
    add(
        "x_for_you_cookie",
        _valid_x_cookie_file(cookie_file),
        str(cookie_file),
        required=bool(sources.x_for_you.get("enabled")),
    )
    add("runner_identity", True, f"current user uid={os.getuid()}")
    add(
        "shared_runtime",
        runtime.shared_runtime_root.exists(),
        str(runtime.shared_runtime_root),
    )
    add(
        "lark_config",
        bool(runtime.lark.space_id and runtime.lark.receiver_open_id),
        "space and receiver configured" if runtime.lark.space_id else "not configured",
    )
    if runtime.lark.space_id:
        add(
            "lark_auth",
            _command_ok([lark, "auth", "status", "--verify"]),
            f"identity={runtime.lark.identity}",
        )
        add(
            "lark_space",
            _lark_space_access(lark, runtime.lark.space_id, runtime.lark.identity),
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


def _codex_model_access(binary: str, model: str) -> bool:
    if not Path(binary).exists():
        return False
    try:
        process = subprocess.run(
            [
                binary,
                "exec",
                "--ignore-user-config",
                "--skip-git-repo-check",
                "--json",
                "-m",
                model,
                "-c",
                'model_reasoning_effort="low"',
                "Return only OK.",
            ],
            cwd=REPO_ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=45,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return process.returncode == 0 and '"type":"turn.completed"' in process.stdout


def _playwright_executable() -> Path | None:
    code = (
        "from playwright.sync_api import sync_playwright; "
        "p=sync_playwright().start(); path=p.chromium.executable_path; "
        "b=p.chromium.launch(headless=True); b.close(); print(path); p.stop()"
    )
    try:
        process = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=30
        )
        if process.returncode == 0 and process.stdout.strip():
            return Path(process.stdout.strip())
    except subprocess.SubprocessError:
        pass
    return None


def _valid_x_cookie_file(path: Path) -> bool:
    try:
        cookies = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    names = {str(cookie.get("name")) for cookie in cookies if isinstance(cookie, dict)}
    return {"auth_token", "ct0"} <= names


def _lark_space_access(binary: str, space_id: str, identity: str) -> bool:
    try:
        process = subprocess.run(
            [binary, "wiki", "+space-list", "--page-all", "--as", identity],
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
