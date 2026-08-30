from __future__ import annotations

import json
import os
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
    add("lark_cli", Path(lark).exists(), lark)
    add("github_auth", _command_ok(["gh", "auth", "status"]), "gh keyring/login")
    x_enabled = bool(sources.x_list.get("enabled"))
    x_required = bool(sources.x_list.get("required", False))
    x_store = XTokenStore()
    x_tokens = x_store.load()
    add(
        "x_api",
        bool(x_enabled and x_tokens and sources.x_list.get("list_id")),
        "token/list configured" if x_enabled else "disabled (required)" if x_required else "disabled",
        required=x_required,
    )
    add(
        "x_refresh",
        bool(x_enabled and x_tokens and x_tokens.refresh_token and x_store.client_id),
        "refresh token and persisted client id configured"
        if x_enabled
        else "disabled",
        required=x_required,
    )
    add(
        "x_content_compliance",
        bool(x_enabled and sources.x_list.get("compliance_verified", False)),
        "verified deletion/update propagation"
        if x_enabled and sources.x_list.get("compliance_verified", False)
        else "not verified; downstream retention/deletion remains blocked",
        required=x_required,
    )
    add(
        "x_app_bearer",
        bool(not x_enabled or x_store.load_bearer()),
        "App bearer configured for Batch Compliance" if x_enabled else "disabled",
        required=x_required,
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
        or (
            bool(sources.x_for_you.get("personal_browser_risk_acknowledged"))
            and bool(sources.x_for_you.get("written_permission_confirmed"))
        ),
        "disabled"
        if not sources.x_for_you.get("enabled")
        else "risk acknowledged and X written permission confirmed"
        if sources.x_for_you.get("written_permission_confirmed")
        else "blocked: X written permission not confirmed",
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
